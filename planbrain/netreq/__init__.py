"""Time-phased material requirements planning (build item 3).

Layering, deliberately strict:

* ``core`` -- netting, lot sizing and lead-time offset for one item. Pure lists,
  no database. This is what the textbook fixtures test.
* ``explode`` -- multi-level BOM explosion in low-level-code order. Still pure.
* ``adapters`` -- the only part that knows about scenarios, measures and dates.

A fixture failure therefore points at the arithmetic, not the plumbing.
"""

from .adapters import (
    OUTPUT_MEASURES,
    GrossReqSourceError,
    read_scheduled_receipts,
    resolve_gross_req,
    write_plans,
)
from .core import Item, ItemPlan, LotSizing, PlanException, plan_item
from .explode import BomCycleError, cost_lot_sizing, explode, low_level_codes

__all__ = [
    "BomCycleError",
    "GrossReqSourceError",
    "cost_lot_sizing",
    "Item",
    "ItemPlan",
    "LotSizing",
    "OUTPUT_MEASURES",
    "PlanException",
    "explode",
    "low_level_codes",
    "plan_item",
    "read_scheduled_receipts",
    "resolve_gross_req",
    "run",
    "write_plans",
]


def _days(n):
    from datetime import timedelta

    return timedelta(days=n)


def run(con, demo, *, scenario_id: int = 0, source: str = "naive_replay",
        lot_sizing: str = "as_master") -> dict:
    """End-to-end netreq run against a dataset's reference data.

    Reads independent demand through the named adapter, explodes the BOM, and
    writes every derived output. Returns rows written per measure.

    Takes reference data as an argument rather than reading it: parts, BOM and
    lead times live in InvenTree, which is read-only, and the importer -- not
    this engine -- owns getting them out.
    """
    sku_ids = [p.sku_id for p in demo.parts]
    loc_ids = [loc.loc_id for loc in demo.locations]
    production_loc = next(loc.loc_id for loc in demo.locations if loc.kind == "plant")

    independent = resolve_gross_req(
        con,
        scenario_id=scenario_id,
        sku_ids=sku_ids,
        loc_ids=loc_ids,
        horizon_start=demo.horizon_start,
        horizon_end=demo.horizon_end,
        source=source,
    )
    receipts = read_scheduled_receipts(
        con,
        scenario_id=scenario_id,
        sku_ids=sku_ids,
        loc_id=production_loc,
        horizon_start=demo.horizon_start,
        horizon_end=demo.horizon_end,
    )
    buckets = (demo.horizon_end - demo.horizon_start).days + 1

    # Opening stock is a position at one instant, so it is aggregated across
    # locations to match the single-location explosion rather than read as facts.
    on_hand = {}
    for (sku_id, _loc_id), qty in demo.stock_on_hand.items():
        on_hand[sku_id] = on_hand.get(sku_id, 0.0) + qty

    planned = explode(
        parts=demo.parts,
        bom=demo.bom,
        independent_demand=independent,
        on_hand=on_hand,
        scheduled_receipt=receipts,
        buckets=buckets,
        production_loc=production_loc,
        working_buckets=[
            demo.calendar.is_working(demo.horizon_start + _days(i))
            for i in range(buckets)
        ],
        lot_sizing_override=(
            cost_lot_sizing(demo.routings) if lot_sizing == "cost_based" else None
        ),
    )
    plans = [plan for plan, _gross in planned]
    gross_by_sku = {plan.sku_id: gross for plan, gross in planned}

    return write_plans(
        con, plans,
        scenario_id=scenario_id,
        horizon_start=demo.horizon_start,
        horizon_end=demo.horizon_end,
        gross_by_sku=gross_by_sku,
    )
