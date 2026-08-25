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
            lot_sizing=_lot_sizing_for(part),
            gross_req=gross,
            scheduled_receipt=scheduled_receipt.get(sku_id, zeros),
        ))
        plans.append((plan, gross))

        # Push dependent demand down to components, timed to the release.
        for edge in children.get(sku_id, ()):
            child_series = dependent[edge.child_sku_id]
            for t in range(buckets):
                child_series[t] += plan.planned_order_release[t] * edge.qty_per

    return plans


def _lot_sizing_for(part) -> LotSizing:
    """Map a part's ordering rule onto a LotSizing.

    Wagner-Whitin is not inferred from reference data: it needs costs the part
    master does not carry, so a part asking for it without costs is an error
    rather than a silent fallback to lot-for-lot.
    """
    if part.lot_policy == "fixed_qty":
        return LotSizing(policy="fixed_qty", fixed_qty=part.lot_qty)
    if part.lot_policy == "min_max":
        return LotSizing(policy="min_max", min_qty=part.lot_qty)
    return LotSizing(policy=part.lot_policy)
