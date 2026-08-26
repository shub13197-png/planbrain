"""Rough-cut capacity planning (build item 6).

Reads the planned order releases `netreq` produced, loads them onto resources
through routings, and compares against available hours. Writes
``capacity_load_hours`` at the capacity grain.

Same layering as every other engine: ``core`` is pure arithmetic over lists,
this module owns scenarios and dates.

This is the first engine to read one fact grain and write another --
``fact_supply_demand`` in, ``fact_capacity`` out. The multi-grain registry
established at item 2 pays for itself here.
"""

from ..facts.access import Fact, bucket_spine, read_facts, write_facts
from .core import ResourceLoad, Routing, compute_load, load_all

DEMAND_TABLE = "fact_supply_demand"
CAPACITY_TABLE = "fact_capacity"
RELEASE_MEASURE = "planned_order_release"
AVAIL_MEASURE = "capacity_avail_hours"
OUTPUT_MEASURE = "capacity_load_hours"

__all__ = [
    "AVAIL_MEASURE",
    "OUTPUT_MEASURE",
    "RELEASE_MEASURE",
    "ResourceLoad",
    "Routing",
    "compute_load",
    "load_all",
    "run",
]


class NoPlanError(ValueError):
    """There are no planned releases to load onto anything."""


def run(con, demo, *, scenario_id: int = 0) -> dict:
    """Load the current plan onto resources and write the result.

    Returns a report per resource: hours loaded, hours available, overall
    utilisation, and the two exception lists. A row count would not say whether
    the plant can actually make the plan.
    """
    spine = bucket_spine(demo.horizon_start, demo.horizon_end)
    production_loc = next(loc.loc_id for loc in demo.locations if loc.kind == "plant")
    routings = [
        Routing(
            sku_id=r.sku_id, resource_id=r.resource_id,
            hours_per_unit=r.hours_per_unit, setup_hours=r.setup_hours,
        )
        for r in demo.routings
    ]

    routed_skus = sorted({r.sku_id for r in routings})
    releases = _read_series(
        con, DEMAND_TABLE, scenario_id=scenario_id, measure=RELEASE_MEASURE,
        keys=[(sku, production_loc) for sku in routed_skus], spine=spine, key_index=0,
    )
    if not any(any(series) for series in releases.values()):
        # Every series zero means netreq has not run for this horizon, not that
        # the plant has nothing to make. Loading that would report a comfortably
        # idle factory, which is the most reassuring possible wrong answer.
        raise NoPlanError(
            "no planned order releases for this horizon and scenario; run "
            "planbrain.netreq.run() before loading capacity"
        )

    resource_ids = sorted({r.resource_id for r in routings})
    capacity = _read_series(
        con, CAPACITY_TABLE, scenario_id=scenario_id, measure=AVAIL_MEASURE,
        keys=[(rid,) for rid in resource_ids], spine=spine, key_index=0,
    )

    loads = load_all(
        resources={rid: capacity.get(rid, [0.0] * len(spine)) for rid in resource_ids},
        routings=routings,
        planned_order_release=releases,
    )

    facts = [
        Fact((load.resource_id,), bucket, hours)
        for load in loads
        for bucket, hours in zip(spine, load.capacity_load_hours)
    ]
    rows = write_facts(
        con, CAPACITY_TABLE, scenario_id=scenario_id, measure=OUTPUT_MEASURE, facts=facts
    )

    return {
        "rows_written": rows,
        "buckets": len(spine),
        "resources": {
            load.resource_id: {
                "load_hours": round(load.total_load, 2),
                "capacity_hours": round(load.total_capacity, 2),
                "utilisation": load.overall_utilisation,
                "overloaded_buckets": load.overloaded_buckets,
                "load_without_capacity": load.load_without_capacity,
            }
            for load in loads
        },
        "feasible": all(not load.overloaded_buckets for load in loads),
    }


def _read_series(con, table, *, scenario_id, measure, keys, spine, key_index) -> dict:
    """Dense series keyed by one grain column."""
    rows = read_facts(
        con, table, scenario_id=scenario_id, measure=measure,
        start=spine[0], end=spine[-1], keys=keys,
    )
    series = {}
    for row in rows:
        target = series.setdefault(row.keys[key_index], [0.0] * len(spine))
        target[(row.bucket_date - spine[0]).days] += row.qty
    return series
