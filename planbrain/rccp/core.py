"""Rough-cut capacity planning: load against available hours, per resource.

Build item 6, and the claim the service backtest could not make. A reorder point
structurally cannot produce a capacity-feasible plan -- it has no concept of a
blender being full. This is where this tool differs in kind rather than degree.

Pure functions over dense series, like every other engine here. No database.

**Load is placed when work starts, not when goods appear.** A planned order
released on day 8 with a two-day lead time occupies the blender on days 8-10, so
the hours land in bucket 8. Loading at the receipt bucket would report a plant
that looks free exactly when it is busiest.

Rough-cut front-loads the whole order into the release bucket rather than
spreading it across the lead time. That is deliberate and it is what "rough" in
rough-cut means: it is conservative (it surfaces an overload earlier rather than
later) and it is honest about its own resolution. Exact timing within the lead
time is finite scheduling -- PyJobShop's job, not this one.

Two exceptions are reported separately because they are different problems:

* **Overloaded** -- more hours of work than hours available. Normal, expected,
  and the thing a planner resolves by moving orders.
* **Load without capacity** -- work scheduled into a bucket with *zero* hours
  available: a closed day, or a resource down for maintenance. Not a degree of
  overload but a different mistake, and it is invisible in a utilisation figure
  because dividing by zero has no honest answer.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Routing:
    """Hours one unit of a SKU places on a resource."""

    sku_id: int
    resource_id: int
    hours_per_unit: float
    setup_hours: float = 0.0


@dataclass(frozen=True)
class ResourceLoad:
    resource_id: int
    capacity_load_hours: list
    capacity_avail_hours: list
    utilisation: list
    overloaded_buckets: list
    load_without_capacity: list

    @property
    def total_load(self) -> float:
        return sum(self.capacity_load_hours)

    @property
    def total_capacity(self) -> float:
        return sum(self.capacity_avail_hours)

    @property
    def overall_utilisation(self):
        """Load over capacity across the whole horizon.

        None when there is no capacity at all, rather than infinity or zero:
        a resource with no available hours has no utilisation, and reporting
        either number would be a statement nobody can act on.
        """
        if self.total_capacity == 0:
            return None
        return self.total_load / self.total_capacity


def compute_load(
    *,
    resource_id: int,
    capacity_avail_hours: list,
    routings: list,
    planned_order_release: dict,
) -> ResourceLoad:
    """Load one resource from the planned releases routed to it.

    ``planned_order_release`` is a dense series per sku_id. Setup hours are a
    flat adder in any bucket where the SKU is produced at all, which is why they
    cannot simply be folded into hours_per_unit.
    """
    buckets = len(capacity_avail_hours)
    mine = [r for r in routings if r.resource_id == resource_id]
    load = [0.0] * buckets

    for routing in mine:
        series = planned_order_release.get(routing.sku_id)
        if series is None:
            continue
        if len(series) != buckets:
            raise ValueError(
                f"release series for sku {routing.sku_id} has {len(series)} buckets, "
                f"resource {resource_id} has {buckets}; series must be spine-aligned"
            )
        for t, quantity in enumerate(series):
            if quantity <= 0:
                continue
            load[t] += quantity * routing.hours_per_unit + routing.setup_hours

    load = [round(v, 6) for v in load]
    utilisation, overloaded, no_capacity = [], [], []
    for t, hours in enumerate(load):
        available = capacity_avail_hours[t]
        if available > 0:
            utilisation.append(round(hours / available, 6))
            if hours > available:
                overloaded.append(t)
        else:
            # Dividing by zero has no honest answer, so utilisation stays 0.0
            # and the real signal goes in its own list where it cannot be
            # mistaken for a busy day.
            utilisation.append(0.0)
            if hours > 0:
                overloaded.append(t)
                no_capacity.append(t)

    return ResourceLoad(
        resource_id=resource_id,
        capacity_load_hours=load,
        capacity_avail_hours=list(capacity_avail_hours),
        utilisation=utilisation,
        overloaded_buckets=overloaded,
        load_without_capacity=no_capacity,
    )


def load_all(*, resources: dict, routings: list, planned_order_release: dict) -> list:
    """Load every resource. ``resources`` maps resource_id to its capacity series."""
    return [
        compute_load(
            resource_id=resource_id,
            capacity_avail_hours=capacity,
            routings=routings,
            planned_order_release=planned_order_release,
        )
        for resource_id, capacity in sorted(resources.items())
    ]
