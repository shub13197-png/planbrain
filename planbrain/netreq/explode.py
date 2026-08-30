"""Multi-level BOM explosion: the loop that turns end-item demand into raw orders.

Items are netted in low-level-code order, so a component is planned only after
every parent that consumes it has produced its planned releases. Getting that
order wrong does not crash -- it silently plans a component against an
incomplete requirement and understates the order. That is why the level
computation is asserted rather than assumed.

Dependent demand is taken from the parent's planned order *release*, not its
receipt: the components must be on hand when production starts, not when it
finishes.

**Scope limit, deliberate:** this explodes at one production location.
Independent demand for a finished good is aggregated across depots before
netting. Time-phased distribution between plant and depot is DRP, which is a
separate problem and is not part of item 3. See docs/decisions.md.
"""

from .core import Item, ItemPlan, LotSizing, plan_item


class BomCycleError(ValueError):
    """The bill of materials contains a cycle and cannot be levelled."""


def low_level_codes(bom, sku_ids) -> dict:
    """Depth of each SKU below the deepest end item that consumes it.

    Standard MRP low-level coding. A SKU used at two different depths gets the
    deeper code, which is exactly what guarantees its requirements are complete
    before it is planned.
    """
    codes = {sku: 0 for sku in sku_ids}
    edges = [(e.parent_sku_id, e.child_sku_id) for e in bom]

    # Relaxation rather than a topological sort: the BOM is small, and this
    # detects a cycle by failing to converge instead of recursing forever.
    for _ in range(len(codes) + 1):
        changed = False
        for parent, child in edges:
            if child not in codes or parent not in codes:
                raise ValueError(f"BOM edge {parent}->{child} references an unknown SKU")
            if codes[child] < codes[parent] + 1:
                codes[child] = codes[parent] + 1
                changed = True
        if not changed:
            return codes
    raise BomCycleError("BOM did not converge to stable low-level codes; it contains a cycle")


def explode(
    *,
    parts,
    bom,
    independent_demand: dict,
    on_hand: dict,
    scheduled_receipt: dict,
    buckets: int,
    production_loc: int,
    working_buckets: list = None,
    lot_sizing_override: dict = None,
) -> list:
    """Plan every SKU, top-down through the BOM. Returns one ItemPlan per SKU.

    ``independent_demand`` and ``scheduled_receipt`` are dense series keyed by
    sku_id; ``on_hand`` is a scalar per sku_id, being a stock position rather
    than a series.
    """
    by_sku = {p.sku_id: p for p in parts}
    codes = low_level_codes(bom, by_sku)
    children = {}
    for edge in bom:
        children.setdefault(edge.parent_sku_id, []).append(edge)

    zeros = [0.0] * buckets
    dependent = {sku: list(zeros) for sku in by_sku}
    plans = []

    for sku_id in sorted(by_sku, key=lambda s: (codes[s], s)):
        part = by_sku[sku_id]
        independent = independent_demand.get(sku_id, zeros)
        if len(independent) != buckets:
            raise ValueError(
                f"independent demand for {sku_id} has {len(independent)} buckets, "
                f"horizon has {buckets}; series must be spine-aligned"
            )
        gross = [independent[t] + dependent[sku_id][t] for t in range(buckets)]

        plan = plan_item(Item(
            sku_id=sku_id,
            loc_id=production_loc,
            lead_time_days=part.lead_time_days,
            on_hand=float(on_hand.get(sku_id, 0.0)),
            safety_stock=part.safety_stock,
            lot_sizing=_lot_sizing_for(part, lot_sizing_override),
            gross_req=gross,
            scheduled_receipt=scheduled_receipt.get(sku_id, zeros),
            working_buckets=working_buckets,
        ))
        plans.append((plan, gross))

        # Push dependent demand down to components, timed to the release.
        for edge in children.get(sku_id, ()):
            child_series = dependent[edge.child_sku_id]
            for t in range(buckets):
                child_series[t] += plan.planned_order_release[t] * edge.qty_per

    return plans


def _lot_sizing_for(part, override: dict = None) -> LotSizing:
    """Map a part's ordering rule onto a LotSizing.

    Wagner-Whitin is not inferred from the part master: it needs costs the part
    master does not carry, so a part asking for it without costs is an error
    rather than a silent fallback to lot-for-lot. Costs arrive through
    ``override``, built by cost_lot_sizing() from routing data.
    """
    if override and part.sku_id in override:
        return override[part.sku_id]
    if part.lot_policy == "fixed_qty":
        return LotSizing(policy="fixed_qty", fixed_qty=part.lot_qty)
    if part.lot_policy == "min_max":
        return LotSizing(policy="min_max", min_qty=part.lot_qty)
    return LotSizing(policy=part.lot_policy)


#: Annual carrying charge. A business assumption, stated rather than fitted,
#: and explicitly out of bounds for tuning -- see docs/unit-costs.md.
ANNUAL_CARRYING_RATE = 0.25

#: Cost of an hour of production time: line crew, energy, lost throughput.
CAPACITY_COST_PER_HOUR = 1500.0


def cost_lot_sizing(routings, parts=None, *,
                    annual_carrying_rate: float = ANNUAL_CARRYING_RATE,
                    capacity_cost_per_hour: float = CAPACITY_COST_PER_HOUR) -> dict:
    """Wagner-Whitin parameters per SKU, priced in money.

    Lot-for-lot minimises inventory and is blind to changeover: it makes a blend
    on every day it is needed, and rough-cut showed the demo paying a full setup
    roughly every third day as a result. Trading setup against holding is what
    the Wagner-Whitin DP in core.py already does; it only ever lacked costs.

    * a changeover costs ``setup_hours x capacity_cost_per_hour``;
    * a unit held for a bucket costs ``unit_cost x carrying_rate / 365``.

    **Money, not hours.** An earlier version priced holding by the capacity
    hours embedded in a unit. That made holding nearly free against a two-hour
    changeover and produced campaigns of a quarter's supply -- economically
    consistent and operationally absurd. A litre of lubricant costs money to
    hold because of the material in it, not the machine-minutes. Without
    ``parts`` this falls back to the old hours basis, which no caller should do.

    **Stated limit, and it matters.** This is *cost-based* lot sizing, not a
    capacity constraint. It reduces load by batching and may or may not reach
    feasibility; it cannot be steered to a per-bucket capacity limit because it
    never sees one. Genuinely capacity-constrained lot sizing is the CLSP -- a
    different and much harder problem, and out of scope. If the plan is still
    infeasible after this, that is a real residual and not an oversight.
    """
    per_bucket_rate = annual_carrying_rate / 365.0
    unit_cost = {p.sku_id: p.unit_cost for p in parts} if parts else {}
    sizing = {}
    for routing in routings:
        if routing.setup_hours <= 0:
            continue
        if unit_cost:
            if routing.sku_id not in unit_cost:
                # Silently defaulting to zero holding would drop this SKU out of
                # cost-based sizing and back to lot-for-lot, changing its policy
                # with nothing to show for it.
                raise ValueError(
                    f"routing for sku {routing.sku_id} has no costed part; "
                    f"cost-based lot sizing cannot price its holding"
                )
            holding = unit_cost[routing.sku_id] * per_bucket_rate
            setup = routing.setup_hours * capacity_cost_per_hour
        else:
            holding = routing.hours_per_unit * per_bucket_rate
            setup = routing.setup_hours
        if holding <= 0 or setup <= 0:
            continue
        sizing[routing.sku_id] = LotSizing(
            policy="wagner_whitin", setup_cost=setup, holding_cost=holding,
        )
    return sizing
