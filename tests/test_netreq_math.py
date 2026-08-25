"""netreq arithmetic, tested in memory against known-correct answers.

No database, no scenarios, no fact tables. If something here fails, the bug is
in the math rather than the plumbing -- accessor round trips are tested
separately in test_netreq_pipeline.py. Keeping the two apart is the whole point:
a fixture failure should point at one page of code.
"""

import pytest
from stockpyl.instances import load_instance

from planbrain.netreq import (
    BomCycleError,
    Item,
    LotSizing,
    explode,
    low_level_codes,
    plan_item,
)


def test_shipped_package_never_imports_stockpyl():
    """stockpyl is a test-only oracle and must stay one.

    It declares sphinx==4.5.0 as an install requirement. A pinned documentation
    toolchain in an InvenTree plugin's runtime tree is a dependency conflict
    waiting to happen, and the kind that surfaces at deploy time rather than here.
    """
    from pathlib import Path

    package = Path(__file__).resolve().parents[1] / "planbrain"
    offenders = [
        f"{path.relative_to(package.parent).as_posix()}:{lineno}"
        for path in package.rglob("*.py")
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if line.strip().startswith(("import stockpyl", "from stockpyl"))
    ]
    assert offenders == []


def _item(**kw):
    defaults = dict(
        sku_id=1, loc_id=1, lead_time_days=0, on_hand=0.0, safety_stock=0.0,
        lot_sizing=LotSizing(policy="lot_for_lot"),
        scheduled_receipt=None,
    )
    defaults.update(kw)
    if defaults["scheduled_receipt"] is None:
        defaults["scheduled_receipt"] = [0.0] * len(defaults["gross_req"])
    return Item(**defaults)


# --------------------------------------------------------------------------
# textbook fixture: Wagner-Whitin
# --------------------------------------------------------------------------

def test_wagner_whitin_matches_snyder_shen_example_3_9():
    """stockpyl's published instance: order 210 in period 1, 150 in period 3.

    Snyder & Shen, *Fundamentals of Supply Chain Theory*, Example 3.9, shipped
    as a stockpyl built-in instance with a published optimal cost of 1380.
    """
    inst = load_instance("example_3_9")
    assert inst == {"num_periods": 4, "holding_cost": 2, "fixed_cost": 500,
                    "demand": [90, 120, 80, 70]}

    plan = plan_item(_item(
        gross_req=[float(d) for d in inst["demand"]],
        lot_sizing=LotSizing(
            policy="wagner_whitin",
            setup_cost=inst["fixed_cost"],
            holding_cost=inst["holding_cost"],
        ),
    ))

    assert plan.planned_order_receipt == [210.0, 0.0, 150.0, 0.0]
    assert plan.net_req == [90.0, 120.0, 80.0, 70.0]


def test_wagner_whitin_projected_balance_reproduces_the_published_cost():
    """Ties the balance series to the published 1380, not just the lot sizes.

    Cost = setups x fixed + holding x sum of end-of-period inventory.
    Two setups (1000) plus 190 unit-periods held at 2 (380) = 1380. If the
    projected balance series were wrong, this arithmetic would not land on the
    textbook answer even with correct lot sizes.
    """
    inst = load_instance("example_3_9")
    plan = plan_item(_item(
        gross_req=[float(d) for d in inst["demand"]],
        lot_sizing=LotSizing(policy="wagner_whitin", setup_cost=500, holding_cost=2),
    ))

    assert plan.projected_on_hand == [120.0, 0.0, 70.0, 0.0]

    setups = sum(1 for q in plan.planned_order_receipt if q > 0)
    holding = sum(plan.projected_on_hand)
    assert setups * 500 + holding * 2 == 1380.0


def test_wagner_whitin_needs_both_costs():
    """Defaulting the costs to zero would degenerate to lot-for-lot silently."""
    with pytest.raises(ValueError, match="setup_cost and holding_cost"):
        LotSizing(policy="wagner_whitin", setup_cost=500)


def test_wagner_whitin_on_flat_demand_batches_it():
    """Sanity check in the other direction: a high setup cost forces one order."""
    plan = plan_item(_item(
        gross_req=[10.0] * 6,
        lot_sizing=LotSizing(policy="wagner_whitin", setup_cost=10_000, holding_cost=0.01),
    ))
    assert plan.planned_order_receipt == [60.0, 0.0, 0.0, 0.0, 0.0, 0.0]


def test_wagner_whitin_cheap_setup_degenerates_to_lot_for_lot():
    """The other extreme: when ordering is nearly free, order every bucket."""
    plan = plan_item(_item(
        gross_req=[10.0, 20.0, 30.0],
        lot_sizing=LotSizing(policy="wagner_whitin", setup_cost=0.01, holding_cost=100.0),
    ))
    assert plan.planned_order_receipt == [10.0, 20.0, 30.0]


@pytest.mark.parametrize("demand,setup,holding", [
    ([90, 120, 80, 70], 500, 2),
    ([10, 62, 12, 130, 154], 300, 1),
    ([0, 40, 0, 0, 25, 0, 60], 120, 3),
    ([5] * 12, 90, 1.5),
])
def test_our_dp_agrees_with_stockpyls_solver(demand, setup, holding):
    """Cross-check the hand-written DP against the reference implementation.

    stockpyl is the locked stack's inventory library but declares sphinx==4.5.0
    as a runtime requirement, so it is a test-only oracle here rather than a
    dependency of the shipped plugin. This is what keeps that trade honest: if
    our DP and stockpyl's ever disagree, this fails.
    """
    from stockpyl.wagner_whitin import wagner_whitin

    ours = plan_item(_item(
        gross_req=[float(d) for d in demand],
        lot_sizing=LotSizing(
            policy="wagner_whitin", setup_cost=setup, holding_cost=holding
        ),
    )).planned_order_receipt

    theirs, *_ = wagner_whitin(len(demand), holding, setup, [float(d) for d in demand])
    assert ours == [float(q) for q in theirs[1:]]


# --------------------------------------------------------------------------
# netting, hand-derived and shown in full
# --------------------------------------------------------------------------

def test_netting_consumes_on_hand_before_planning_anything():
    """Opening 120, demand 80, no safety stock: nothing needs to be ordered.

    balance = 120 - 80 = 40, which is >= 0, so net requirement is zero.
    """
    plan = plan_item(_item(on_hand=120.0, gross_req=[80.0, 0.0]))
    assert plan.net_req == [0.0, 0.0]
    assert plan.planned_order_receipt == [0.0, 0.0]
    assert plan.projected_on_hand == [40.0, 40.0]


def test_safety_stock_is_a_floor_not_a_buffer_to_consume():
    """Opening 120, demand 80, safety stock 50.

    balance = 40, which is below the floor of 50, so 10 is short even though
    physical stock is positive. Lot-for-lot orders exactly the 10.
    """
    plan = plan_item(_item(on_hand=120.0, safety_stock=50.0, gross_req=[80.0, 0.0]))
    assert plan.net_req == [10.0, 0.0]
    assert plan.planned_order_receipt == [10.0, 0.0]
    assert plan.projected_on_hand == [50.0, 50.0]


def test_scheduled_receipts_are_netted_before_planning_new_orders():
    """A confirmed open order must reduce the requirement, or netreq double-orders."""
    plan = plan_item(_item(
        on_hand=0.0, gross_req=[100.0, 0.0], scheduled_receipt=[100.0, 0.0],
    ))
    assert plan.net_req == [0.0, 0.0]
    assert plan.planned_order_receipt == [0.0, 0.0]


def test_fixed_quantity_excess_carries_forward_and_suppresses_later_orders():
    """Opening 0, demand 30 a bucket for four buckets, fixed lot of 100.

    Bucket 0 is short 30, orders 100, leaving 70. That 70 covers buckets 1 and 2
    and leaves 10, so bucket 3 is short 20 and orders another 100. A netting loop
    that lot-sized separately from netting would order in every bucket.
    """
    plan = plan_item(_item(
        gross_req=[30.0] * 4,
        lot_sizing=LotSizing(policy="fixed_qty", fixed_qty=100.0),
    ))
    assert plan.planned_order_receipt == [100.0, 0.0, 0.0, 100.0]
    assert plan.projected_on_hand == [70.0, 40.0, 10.0, 80.0]


def test_fixed_quantity_rounds_up_to_whole_lots():
    plan = plan_item(_item(
        gross_req=[250.0], lot_sizing=LotSizing(policy="fixed_qty", fixed_qty=100.0),
    ))
    assert plan.planned_order_receipt == [300.0]


def test_fixed_quantity_does_not_over_round_an_exact_multiple():
    """Floating point must not turn a requirement of exactly 200 into 300."""
    plan = plan_item(_item(
        gross_req=[200.0], lot_sizing=LotSizing(policy="fixed_qty", fixed_qty=100.0),
    ))
    assert plan.planned_order_receipt == [200.0]


def test_min_max_lifts_a_small_requirement_to_the_minimum():
    plan = plan_item(_item(
        gross_req=[7.0], lot_sizing=LotSizing(policy="min_max", min_qty=50.0),
    ))
    assert plan.planned_order_receipt == [50.0]


def test_min_max_rounds_to_a_multiple_when_one_is_set():
    plan = plan_item(_item(
        gross_req=[130.0],
        lot_sizing=LotSizing(policy="min_max", min_qty=50.0, multiple_of=24.0),
    ))
    assert plan.planned_order_receipt == [144.0]  # 6 cases of 24


# --------------------------------------------------------------------------
# lead-time offset
# --------------------------------------------------------------------------

def test_release_is_offset_backward_by_the_lead_time():
    """Daily buckets mean days and buckets coincide -- the reason for the grain."""
    plan = plan_item(_item(
        lead_time_days=2, gross_req=[0.0, 0.0, 0.0, 50.0, 0.0],
    ))
    assert plan.planned_order_receipt == [0.0, 0.0, 0.0, 50.0, 0.0]
    assert plan.planned_order_release == [0.0, 50.0, 0.0, 0.0, 0.0]


def test_a_release_before_the_horizon_is_reported_and_placed_in_bucket_zero():
    """It cannot be scheduled, so release now and say how late it already is.

    Dropping it would understate the plan; placing it silently would hide that
    the order is overdue. Both are wrong numbers that look right.
    """
    plan = plan_item(_item(lead_time_days=5, gross_req=[100.0, 0.0]))
    assert plan.planned_order_release == [100.0, 0.0]

    late = [e for e in plan.exceptions if e.kind == "past_due_release"]
    assert len(late) == 1
    assert late[0].bucket_index == 0
    assert late[0].qty == 100.0
    assert late[0].days_late == 5


def test_several_receipts_can_collapse_onto_one_release_bucket():
    plan = plan_item(_item(lead_time_days=3, gross_req=[10.0, 20.0, 0.0]))
    assert plan.planned_order_release[0] == 30.0


# --------------------------------------------------------------------------
# shortages
# --------------------------------------------------------------------------

def test_a_shortage_is_reported_at_its_true_depth():
    """Zero lead time cannot fix a bucket whose scheduled supply never arrives."""
    plan = plan_item(_item(
        lead_time_days=10, gross_req=[500.0, 0.0], safety_stock=0.0,
    ))
    assert plan.projected_on_hand[0] == 0.0  # the late release still lands in bucket 0

    plan = plan_item(_item(
        gross_req=[500.0], lot_sizing=LotSizing(policy="fixed_qty", fixed_qty=100.0),
        on_hand=0.0,
    ))
    assert all(b >= 0 for b in plan.projected_on_hand)


def test_negative_balance_is_not_clamped():
    """A shortage the plan cannot cover must show its magnitude."""
    item = Item(
        sku_id=1, loc_id=1, lead_time_days=0, on_hand=10.0, safety_stock=0.0,
        lot_sizing=LotSizing(policy="lot_for_lot"),
        gross_req=[100.0], scheduled_receipt=[0.0],
    )
    # lot_for_lot covers it, so force the shortage by planning nothing:
    from planbrain.netreq.core import _project
    assert _project(item, [0.0]) == [-90.0]


# --------------------------------------------------------------------------
# input guards
# --------------------------------------------------------------------------

def test_misaligned_series_are_rejected():
    """A short series shifts the plan by a bucket and stays plausible."""
    with pytest.raises(ValueError, match="spine-aligned"):
        plan_item(Item(
            sku_id=1, loc_id=1, lead_time_days=0, on_hand=0.0, safety_stock=0.0,
            lot_sizing=LotSizing(policy="lot_for_lot"),
            gross_req=[1.0, 2.0, 3.0], scheduled_receipt=[0.0, 0.0],
        ))


def test_unknown_lot_policy_is_rejected():
    with pytest.raises(ValueError, match="unknown lot policy"):
        LotSizing(policy="eoq")


def test_fixed_qty_policy_needs_a_quantity():
    with pytest.raises(ValueError, match="positive fixed_qty"):
        LotSizing(policy="fixed_qty")


# --------------------------------------------------------------------------
# BOM explosion
# --------------------------------------------------------------------------

class _Part:
    def __init__(self, sku_id, lead_time_days=0, safety_stock=0.0,
                 lot_policy="lot_for_lot", lot_qty=0.0):
        self.sku_id = sku_id
        self.lead_time_days = lead_time_days
        self.safety_stock = safety_stock
        self.lot_policy = lot_policy
        self.lot_qty = lot_qty


class _Edge:
    def __init__(self, parent, child, qty_per):
        self.parent_sku_id = parent
        self.child_sku_id = child
        self.qty_per = qty_per


def test_low_level_codes_take_the_deeper_position():
    """A part used at two depths must be planned at the deeper one.

    Here 30 is both a direct component of 10 and a component of 20. Planning it
    at depth 1 would net it before 20 had contributed its requirement.
    """
    bom = [_Edge(10, 20, 1), _Edge(10, 30, 1), _Edge(20, 30, 1)]
    codes = low_level_codes(bom, {10, 20, 30})
    assert codes == {10: 0, 20: 1, 30: 2}


def test_a_cycle_is_detected_rather_than_looping_forever():
    bom = [_Edge(10, 20, 1), _Edge(20, 10, 1)]
    with pytest.raises(BomCycleError):
        low_level_codes(bom, {10, 20})


def test_dependent_demand_follows_the_parent_release_not_its_receipt():
    """Components must be there when production starts, not when it finishes.

    Parent 10 has a 2-day lead time and needs 100 in bucket 3, so it releases in
    bucket 1. Child 20, at 2 per parent, must therefore see 200 in bucket 1.
    """
    plans = explode(
        parts=[_Part(10, lead_time_days=2), _Part(20)],
        bom=[_Edge(10, 20, 2.0)],
        independent_demand={10: [0.0, 0.0, 0.0, 100.0, 0.0]},
        on_hand={},
        scheduled_receipt={},
        buckets=5,
        production_loc=1,
    )
    by_sku = {plan.sku_id: (plan, gross) for plan, gross in plans}

    parent, _ = by_sku[10]
    assert parent.planned_order_release == [0.0, 100.0, 0.0, 0.0, 0.0]

    _child_plan, child_gross = by_sku[20]
    assert child_gross == [0.0, 200.0, 0.0, 0.0, 0.0]


def test_explosion_carries_through_three_levels():
    plans = explode(
        parts=[_Part(10), _Part(20), _Part(30)],
        bom=[_Edge(10, 20, 2.0), _Edge(20, 30, 3.0)],
        independent_demand={10: [5.0]},
        on_hand={},
        scheduled_receipt={},
        buckets=1,
        production_loc=1,
    )
    gross = {plan.sku_id: g for plan, g in plans}
    assert gross[10] == [5.0]
    assert gross[20] == [10.0]
    assert gross[30] == [30.0]


def test_component_stock_reduces_the_exploded_order():
    plans = explode(
        parts=[_Part(10), _Part(20)],
        bom=[_Edge(10, 20, 1.0)],
        independent_demand={10: [100.0]},
        on_hand={20: 40.0},
        scheduled_receipt={},
        buckets=1,
        production_loc=1,
    )
    child = next(plan for plan, _ in plans if plan.sku_id == 20)
    assert child.net_req == [60.0]


def test_misaligned_independent_demand_is_rejected():
    with pytest.raises(ValueError, match="spine-aligned"):
        explode(
            parts=[_Part(10)], bom=[],
            independent_demand={10: [1.0, 2.0]},
            on_hand={}, scheduled_receipt={}, buckets=3, production_loc=1,
        )
