"""What is going to go wrong, and when.

    from planbrain import alerts
    for shortage in alerts.shortages(con, demo):
        ...

**The plan is only interesting where it fails.** `netreq.core.plan_item` built a
`PlanException` for every projected shortage and every past-due release, and
until 2026-09-05 every one of them was dropped: `netreq.run` returned a count of
rows written and the only reader of the exception list anywhere was
`tools/make_examples.py`. So the application could tell a planner that a plan
did not fit and could not tell them which item, on which day -- which is the
question they came to answer.

Nothing is computed here. Both measures are written by `netreq`, so a shortage
is a fact row with a negative quantity and a past-due release is a fact row
under its own measure, read back through the accessor and given a name. If a
number is wrong, the bug is in `netreq`.

**Two different failure modes, never added together.** A *shortage* is "we will
run out"; a *past-due release* is "we are already late". A screen that summed
them would report a number meaning neither.

**Past-due is the one that actually fires**, and finding that out cost a wrong
design. The first version of this module reported shortages only, on the
reasoning that a past-due release sits in bucket zero where it cannot be told
apart from an order that legitimately starts there. Then the planner was run
over 2,947 real stock codes and returned **zero shortages** -- because `netreq`
dates a planned receipt at the bucket the material is needed, so
`projected_on_hand` balances even when the order covering it should have gone
out weeks ago. A "what is going to go wrong" screen that says "nothing runs
out" on that plan is worse than no screen at all.

So `past_due_release` is now a measure, written by `netreq` at the bucket the
material is needed rather than at the bucket-zero release that covers it: the
releases all pile into bucket zero by definition, and dating them there would
merge every overdue order into one number with no due date to chase.
"""

from dataclasses import dataclass
from datetime import date

from .facts.access import read_facts

TABLE = "fact_supply_demand"
#: Named per concern rather than one MEASURE, because there are now two and a
#: single module-level constant would have to be right about which.
SHORTAGE_MEASURE = "projected_on_hand"
PAST_DUE_MEASURE = "past_due_release"


@dataclass(frozen=True)
class Shortage:
    """One bucket where the plan projects less stock than nothing."""

    bucket_date: date
    sku_id: int
    name: str
    loc_id: int
    location: str
    #: Positive: how far below zero the projection goes. `projected_on_hand`
    #: stores this negative, and the sign is flipped once here rather than in
    #: every caller that displays it -- "-5" next to a shortage invites the
    #: reading that stock is minus five, which is not a thing.
    short_by: float


def shortages(con, demo, *, scenario_id: int = 0) -> list:
    """Every bucket whose projected balance is negative, soonest first.

    Soonest first because that is the one still fixable: a shortage eight weeks
    out can be covered by an order placed today, and one next Tuesday cannot.
    """
    parts = {p.sku_id: p for p in demo.parts}
    locations = {loc.loc_id: loc.name for loc in demo.locations}

    rows = read_facts(
        con, TABLE,
        scenario_id=scenario_id, measure=SHORTAGE_MEASURE,
        start=demo.horizon_start, end=demo.horizon_end,
        keys=[(p.sku_id, loc_id) for p in demo.parts for loc_id in locations],
    )

    found = [
        Shortage(
            bucket_date=fact.bucket_date,
            sku_id=fact.keys[0],
            name=parts[fact.keys[0]].name,
            loc_id=fact.keys[1],
            location=locations[fact.keys[1]],
            short_by=-fact.qty,
        )
        for fact in rows
        if fact.qty < 0
    ]
    found.sort(key=lambda s: (s.bucket_date, s.sku_id, s.loc_id))
    return found


@dataclass(frozen=True)
class PastDue:
    """An order whose release date fell before the horizon opened.

    The plan needs this quantity on `needed_on`, and the lead time means it
    should already have been placed. It is not a shortage -- the projection
    balances, because the plan assumes the receipt lands when needed -- it is
    the reason that assumption is not safe.
    """

    needed_on: date
    sku_id: int
    name: str
    loc_id: int
    location: str
    qty: float
    lead_time_days: int


def past_due(con, demo, *, scenario_id: int = 0) -> list:
    """Releases that should already have gone out, soonest-needed first."""
    parts = {p.sku_id: p for p in demo.parts}
    locations = {loc.loc_id: loc.name for loc in demo.locations}

    rows = read_facts(
        con, TABLE,
        scenario_id=scenario_id, measure=PAST_DUE_MEASURE,
        start=demo.horizon_start, end=demo.horizon_end,
        keys=[(p.sku_id, loc_id) for p in demo.parts for loc_id in locations],
    )

    found = [
        PastDue(
            needed_on=fact.bucket_date,
            sku_id=fact.keys[0],
            name=parts[fact.keys[0]].name,
            loc_id=fact.keys[1],
            location=locations[fact.keys[1]],
            qty=fact.qty,
            lead_time_days=parts[fact.keys[0]].lead_time_days,
        )
        for fact in rows
        if fact.qty
    ]
    found.sort(key=lambda o: (o.needed_on, o.sku_id, o.loc_id))
    return found


def risk_summary(con, demo, *, scenario_id: int = 0, limit: int = 50,
                 worst_first: bool = False) -> dict:
    """Both failure modes, for one screen, reported separately.

    No combined total, deliberately. "We will run out" and "we are already
    late" are different questions with different answers, and a single count
    covering both would mean neither.
    """
    shorts = shortage_summary(con, demo, scenario_id=scenario_id, limit=limit,
                              worst_first=worst_first)
    overdue = past_due(con, demo, scenario_id=scenario_id)
    ordered = (
        sorted(overdue, key=lambda o: (-o.qty, o.needed_on))
        if worst_first else overdue
    )
    return {
        "shortages": shorts,
        "past_due": {
            "total": len(overdue),
            "items_affected": len({o.sku_id for o in overdue}),
            "units": round(sum(o.qty for o in overdue), 6),
            "first_needed": overdue[0].needed_on.isoformat() if overdue else None,
            "shown": min(limit, len(overdue)),
            "ranked_by": "size" if worst_first else "date",
            "orders": [
                {
                    "needed_on": o.needed_on.isoformat(),
                    "sku_id": o.sku_id,
                    "name": o.name,
                    "loc_id": o.loc_id,
                    "location": o.location,
                    "qty": o.qty,
                    "lead_time_days": o.lead_time_days,
                }
                for o in ordered[:limit]
            ],
        },
    }


def shortage_summary(con, demo, *, scenario_id: int = 0, limit: int = 50,
                     worst_first: bool = False) -> dict:
    """Shortages for a screen: a bounded page, and the totals it is a page of.

    `total` and `items_affected` are of the whole list, never of the page. A
    screen showing fifty rows out of four hundred without saying so reads as a
    plan with fifty problems.

    `worst_first` ranks by depth instead of by date, because a truncated list
    should show the shortages that matter rather than the ones that happen to
    fall earliest.
    """
    found = shortages(con, demo, scenario_id=scenario_id)
    ordered = (
        sorted(found, key=lambda s: (-s.short_by, s.bucket_date))
        if worst_first else found
    )
    return {
        "total": len(found),
        "items_affected": len({s.sku_id for s in found}),
        "units_short": round(sum(s.short_by for s in found), 6),
        "first_date": found[0].bucket_date.isoformat() if found else None,
        "shown": min(limit, len(found)),
        "ranked_by": "size" if worst_first else "date",
        "shortages": [
            {
                "bucket_date": s.bucket_date.isoformat(),
                "sku_id": s.sku_id,
                "name": s.name,
                "loc_id": s.loc_id,
                "location": s.location,
                "short_by": s.short_by,
            }
            for s in ordered[:limit]
        ],
    }
