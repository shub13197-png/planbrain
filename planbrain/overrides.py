"""Firm planned orders: the numbers a person fixed, and who fixed them.

    from planbrain import overrides
    overrides.set_override(con, sku_id=1001, loc_id=1, bucket_date=day,
                           qty=500.0, author="priya",
                           reason="Kapoor confirmed 500 on the phone")

**Why this exists.** A planner knows things the data does not, and until this
the application had no way to be told any of them: the only controls were two
growth percentages. A planner who cannot disagree with a system stops using it
the first time it is wrong about something they know, so being able to overrule
it -- and having the overrule survive the next run *as an overrule* -- is what
makes the rest of the output believable.

**The mechanism is the textbook firm planned order**, not a new invention: the
planner fixes quantity and date, and subsequent runs net around it rather than
resizing or rescheduling it. The arithmetic is in `netreq.core.plan_item`; this
module is storage and provenance.

**Two stores, one number.** The quantity lives in the `firm_planned_order`
measure and only there. `plan_override` carries who and why and *not* the
quantity, because two copies of a number is two numbers and nothing would say
which one the plan used. `audit()` asserts the two agree about which addresses
exist.

**`firm_planned_order` is `derived = 0`**, which is the whole protection. It is
an input in the same class as a confirmed purchase order, so `write_plans`'s
existing refusal to write a non-derived measure already stops a planning run
from overwriting a human's number. No new rule was added; this feature inherits
one that was already enforced in a single place.
"""

from dataclasses import dataclass
from datetime import date, datetime

from .facts.access import Fact, read_facts, write_facts

TABLE = "fact_supply_demand"
MEASURE = "firm_planned_order"


@dataclass(frozen=True)
class Override:
    """One quantity a person fixed, with the person and the reason."""

    bucket_date: date
    sku_id: int
    name: str
    loc_id: int
    location: str
    qty: float
    author: str
    reason: str
    created_at: str


def set_override(con, *, sku_id: int, loc_id: int, bucket_date: date, qty: float,
                 author: str, reason: str, scenario_id: int = 0) -> None:
    """Fix a quantity, with a person and a reason against it.

    Both are required and neither may be blank. An override with no reason is
    indistinguishable in three weeks from a typo, and the person who has to work
    that out is usually the person who typed it.

    The fact row and the provenance row are written in one transaction, so a
    quantity can never end up in the plan with nobody behind it.
    """
    if not (author or "").strip():
        raise ValueError("an override needs an author; who is fixing this number?")
    if not (reason or "").strip():
        raise ValueError(
            "an override needs a reason; in three weeks it is indistinguishable "
            "from a typo without one"
        )
    if qty < 0:
        raise ValueError(f"a negative fixed quantity ({qty}) is not a supply")

    with con:
        write_facts(
            con, TABLE, scenario_id=scenario_id, measure=MEASURE,
            facts=[Fact(keys=(sku_id, loc_id), bucket_date=bucket_date, qty=qty)],
        )
        con.execute(
            "INSERT INTO plan_override"
            " (sku_id, loc_id, bucket_date, scenario_id, author, reason, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (sku_id, loc_id, bucket_date, scenario_id) DO UPDATE SET"
            "   author = excluded.author,"
            "   reason = excluded.reason,"
            "   created_at = excluded.created_at",
            (sku_id, loc_id, bucket_date.isoformat(), scenario_id,
             author.strip(), reason.strip(), datetime.now().isoformat(timespec="seconds")),
        )


def clear_override(con, *, sku_id: int, loc_id: int, bucket_date: date,
                   scenario_id: int = 0) -> None:
    """Hand the bucket back to the engine. Idempotent.

    Clearing something that was never set is not an error, because a planner
    clicking twice is not a fault.
    """
    with con:
        # Writing zero rather than deleting: `write_facts` sparsifies, so a zero
        # removes the row through the same path every other quantity takes.
        write_facts(
            con, TABLE, scenario_id=scenario_id, measure=MEASURE,
            facts=[Fact(keys=(sku_id, loc_id), bucket_date=bucket_date, qty=0.0)],
        )
        con.execute(
            "DELETE FROM plan_override"
            " WHERE sku_id = ? AND loc_id = ? AND bucket_date = ? AND scenario_id = ?",
            (sku_id, loc_id, bucket_date.isoformat(), scenario_id),
        )


def list_overrides(con, demo, *, scenario_id: int = 0) -> list:
    """Every fixed quantity in the horizon, soonest first, with its reason."""
    parts = {p.sku_id: p for p in demo.parts}
    locations = {loc.loc_id: loc.name for loc in demo.locations}

    rows = read_facts(
        con, TABLE,
        scenario_id=scenario_id, measure=MEASURE,
        start=demo.horizon_start, end=demo.horizon_end,
        keys=[(p.sku_id, loc_id) for p in demo.parts for loc_id in locations],
    )
    provenance = {
        (r[0], r[1], r[2]): (r[3], r[4], r[5])
        for r in con.execute(
            "SELECT sku_id, loc_id, bucket_date, author, reason, created_at"
            " FROM plan_override WHERE scenario_id = ?", (scenario_id,)
        )
    }

    found = []
    for fact in rows:
        if not fact.qty:
            continue
        key = (fact.keys[0], fact.keys[1], fact.bucket_date.isoformat())
        author, reason, created_at = provenance.get(key, ("", "", ""))
        found.append(Override(
            bucket_date=fact.bucket_date,
            sku_id=fact.keys[0],
            name=parts[fact.keys[0]].name,
            loc_id=fact.keys[1],
            location=locations[fact.keys[1]],
            qty=fact.qty,
            author=author,
            reason=reason,
            created_at=created_at,
        ))
    found.sort(key=lambda o: (o.bucket_date, o.sku_id, o.loc_id))
    return found


def audit(con, demo, *, scenario_id: int = 0) -> list:
    """Addresses where the quantity and its provenance disagree about existing.

    A fixed quantity with nobody behind it is an unattributable number in a
    plan. A reason with no quantity is a reason for nothing. Both are bugs in
    whatever wrote them, and both leave a plan half-explained -- which is worse
    than either an unexplained plan or an unaltered one, because the reader
    cannot tell which they are looking at.
    """
    quantities = {
        (o.sku_id, o.loc_id, o.bucket_date.isoformat())
        for o in list_overrides(con, demo, scenario_id=scenario_id)
    }
    reasons = {
        (r[0], r[1], r[2])
        for r in con.execute(
            "SELECT sku_id, loc_id, bucket_date FROM plan_override"
            " WHERE scenario_id = ?", (scenario_id,)
        )
    }
    return (
        [f"fixed quantity with no author or reason: {a}"
         for a in sorted(quantities - reasons)]
        + [f"reason with no fixed quantity behind it: {a}"
           for a in sorted(reasons - quantities)]
    )


def summary(con, demo, *, scenario_id: int = 0, limit: int = 50) -> dict:
    """Overrides for a screen: which numbers are the human's, and whose.

    Reported beside the plan's verdict rather than in a panel of its own, for
    the same reason the growth assumptions are: "the plan fits" means something
    different when a person moved three numbers to make it fit.
    """
    found = list_overrides(con, demo, scenario_id=scenario_id)
    return {
        "total": len(found),
        "items_affected": len({o.sku_id for o in found}),
        "authors": sorted({o.author for o in found if o.author}),
        "shown": min(limit, len(found)),
        "overrides": [
            {
                "bucket_date": o.bucket_date.isoformat(),
                "sku_id": o.sku_id,
                "name": o.name,
                "loc_id": o.loc_id,
                "location": o.location,
                "qty": o.qty,
                "author": o.author,
                "reason": o.reason,
                "created_at": o.created_at,
            }
            for o in found[:limit]
        ],
    }
