"""Why a bucket is overloaded, and where the nearest slack is.

`rccp` answers "does the plan fit". When it does not, the next question is
always "what do I do about it", and this module answers as much of that as can
be answered **without solving anything**.

It attributes: for each overloaded bucket, which SKUs put the hours there and
how much room exists nearby. It does not choose. Choosing what to move, subject
to per-bucket capacity, is the capacitated lot-sizing problem -- a different
algorithm, out of scope, and named as such in `README.md` rather than
approximated here with something that looks like an answer.

**The distinction matters more than it might seem.** A tool that says "move
3,200 units of SKU-104 to Tuesday" has made two claims: that Tuesday has the
hours, and that the material will be there. This module can see the first and
cannot see the second -- component availability is `netreq`'s question and it
depends on lead times, on-hand stock and the BOM. Presenting the first as though
it settled both would be a plan number that looks right, which is the one thing
this project treats as unforgivable.

So the output is a diagnosis a planner reads, not an instruction a planner
follows.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Contributor:
    """One SKU's share of the hours in an overloaded bucket."""

    sku_id: int
    hours: float
    units: float
    setup_hours: float

    @property
    def run_hours(self) -> float:
        return self.hours - self.setup_hours


@dataclass(frozen=True)
class Overload:
    """One resource-bucket that asks for more hours than it has.

    ``slack_before`` and ``slack_after`` are spare hours on the *same resource*
    within the window. They say where room exists, not that work can move there:
    moving production earlier needs the components to be there earlier, which
    this cannot see.
    """

    resource_id: int
    bucket: int
    bucket_date: object
    load_hours: float
    capacity_hours: float
    contributors: tuple
    slack_before: float
    slack_after: float

    @property
    def over_hours(self) -> float:
        return self.load_hours - self.capacity_hours

    @property
    def relieved_by_moving_earlier(self) -> bool:
        """Enough spare hours exist earlier in the window to absorb the excess.

        A necessary condition and never a sufficient one, in two separate ways.
        Moving production earlier needs the components to be there earlier,
        which this cannot see; and a neighbouring overloaded bucket is measured
        against the same spare hours, so two buckets can both report room that
        only one of them can use.

        **False is the informative direction.** False means no amount of
        rescheduling within the window helps and the plant is genuinely short.
        True means "worth a look".
        """
        return self.slack_before >= self.over_hours


def explain_overload(*, resource_id, capacity_avail_hours, routings,
                     planned_order_release, spine=None, window: int = 7) -> list:
    """Attribute every overloaded bucket on one resource.

    Returns one ``Overload`` per bucket that asks for more hours than it has,
    each carrying its contributors ranked by hours. Same arithmetic as
    ``compute_load`` -- deliberately, so a contributor list can never disagree
    with the total it is explaining.
    """
    if window < 0:
        raise ValueError(f"window cannot be negative, got {window}")

    buckets = len(capacity_avail_hours)
    mine = [r for r in routings if r.resource_id == resource_id]

    load = [0.0] * buckets
    per_bucket = [dict() for _ in range(buckets)]
    for routing in mine:
        series = planned_order_release.get(routing.sku_id)
        if series is None:
            continue
        for t, quantity in enumerate(series):
            if quantity <= 0:
                continue
            hours = quantity * routing.hours_per_unit + routing.setup_hours
            load[t] += hours
            entry = per_bucket[t].setdefault(
                routing.sku_id, {"hours": 0.0, "units": 0.0, "setup": 0.0}
            )
            entry["hours"] += hours
            entry["units"] += quantity
            entry["setup"] += routing.setup_hours

    slack = [max(0.0, capacity_avail_hours[t] - load[t]) for t in range(buckets)]

    results = []
    for t in range(buckets):
        available = capacity_avail_hours[t]
        # A bucket with no capacity and no load is not overloaded; a bucket with
        # no capacity and any load is, and is the more serious of the two cases.
        if not (load[t] > available if available > 0 else load[t] > 0):
            continue
        contributors = tuple(
            Contributor(sku_id=sku, hours=round(v["hours"], 6),
                        units=v["units"], setup_hours=round(v["setup"], 6))
            for sku, v in sorted(
                per_bucket[t].items(), key=lambda kv: (-kv[1]["hours"], kv[0])
            )
        )
        results.append(Overload(
            resource_id=resource_id,
            bucket=t,
            bucket_date=spine[t] if spine else None,
            load_hours=round(load[t], 6),
            capacity_hours=available,
            contributors=contributors,
            slack_before=round(sum(slack[max(0, t - window):t]), 6),
            slack_after=round(sum(slack[t + 1:t + 1 + window]), 6),
        ))
    return results


def summarise(overloads: list) -> dict:
    """Portfolio-level shape of the overload, for a report header.

    ``concentration`` is the share of excess hours contributed by the single
    worst SKU across all overloaded buckets. It is the number that decides
    whether this is one product's problem or the plant's: at 60% a planner has
    one conversation to have, at 5% they have a capacity decision.
    """
    if not overloads:
        return {"buckets": 0, "over_hours": 0.0, "resources": 0,
                "worst_sku": None, "concentration": None}

    by_sku = {}
    total_excess = 0.0
    for overload in overloads:
        total_excess += overload.over_hours
        # Attribute the excess in proportion to each SKU's share of the load.
        # The excess is not any one SKU's fault -- it is the last hour over the
        # line -- so splitting it by contribution is the only defensible
        # attribution, and it is stated rather than implied.
        for contributor in overload.contributors:
            share = contributor.hours / overload.load_hours if overload.load_hours else 0.0
            by_sku[contributor.sku_id] = by_sku.get(contributor.sku_id, 0.0) + \
                share * overload.over_hours

    worst_sku, worst_hours = max(by_sku.items(), key=lambda kv: kv[1])
    return {
        "buckets": len(overloads),
        "over_hours": round(total_excess, 2),
        "resources": len({o.resource_id for o in overloads}),
        "worst_sku": worst_sku,
        "worst_sku_hours": round(worst_hours, 2),
        "concentration": round(worst_hours / total_excess, 4) if total_excess else None,
        # COUNTED INDEPENDENTLY, AND THAT IS A REAL LIMITATION. Each bucket is
        # tested against the slack in the days before it, and neighbouring
        # overloaded buckets are tested against the SAME slack. If a fortnight
        # is overloaded throughout, every day in it can report "there is room
        # last week" and they cannot all use it.
        #
        # So this is an upper bound on how much of the overload is a scheduling
        # problem rather than a capacity problem, and it is named as one. The
        # honest lower bound needs an allocation across buckets, which is the
        # solver this module exists in order not to be.
        "relievable_by_moving_earlier_upper_bound": sum(
            1 for o in overloads if o.relieved_by_moving_earlier
        ),
    }
