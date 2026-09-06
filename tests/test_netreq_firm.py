"""Firm planned orders: the planner overruling the arithmetic.

The textbook mechanism (Orlicky; Nahmias ch. 7): a planner *firms* a planned
order, fixing its quantity and date, and subsequent planning runs net around it
rather than resizing or rescheduling it.

**The rule that makes it worth anything: the engine does not top a firm order
up.** If a planner writes 500 where the plan wanted 860 and the run quietly adds
360 in the same bucket, the override did nothing. So a firm bucket is fixed
entirely — the engine plans nothing there — and a shortfall becomes a projected
shortage, which is the honest answer and is exactly what the risk screen is for.

The same rule holds under every lot-sizing policy, including Wagner-Whitin,
which is why the DP takes a mask of buckets it may not order in rather than
having the firm quantity bolted on afterwards.
"""

import pytest

from planbrain.netreq.core import Item, LotSizing, plan_item


def _item(**kw):
    base = dict(
        sku_id=1, loc_id=1, lead_time_days=0, on_hand=0.0, safety_stock=0.0,
        lot_sizing=LotSizing(policy="lot_for_lot"),
        gross_req=[0.0], scheduled_receipt=[0.0],
    )
    base.update(kw)
    n = len(base["gross_req"])
    base.setdefault("scheduled_receipt", [0.0] * n)
    if len(base["scheduled_receipt"]) != n:
        base["scheduled_receipt"] = [0.0] * n
    return Item(**base)


# --------------------------------------------------------------------------
# the rule
# --------------------------------------------------------------------------

def test_a_firm_order_replaces_what_the_engine_would_have_planned():
    """500 where the arithmetic wanted 100. The plan carries 500."""
    plan = plan_item(_item(
        gross_req=[100.0, 0.0],
        firm_planned_order=[500.0, 0.0],
    ))
    assert plan.planned_order_receipt == [500.0, 0.0]


def test_the_engine_does_not_top_up_a_firm_order_in_its_own_bucket():
    """The whole point. Topping it up would make an override decorative.

    "In its own bucket" is the precise claim, and the first version of this test
    over-claimed. The 360 that was not supplied is still *required*, so ordinary
    netting plans it in the next bucket -- the deficit carries, exactly as it
    would after any other shortfall. That is correct and it is useful: the plan
    says both "you will be 360 short on day one" and "here is when it can be
    made up". What must never happen is the engine quietly restoring 860 in the
    bucket the planner capped, which is what this asserts.
    """
    plan = plan_item(_item(
        gross_req=[860.0, 0.0],
        firm_planned_order=[500.0, 0.0],
    ))
    assert plan.planned_order_receipt[0] == 500.0
    assert plan.planned_order_receipt[1] == 360.0, "the deficit carries forward"


def test_a_firm_order_that_is_too_small_shows_up_as_a_shortage():
    """Not a silent difference: the plan must say it will run out.

    This is the pairing that makes overriding safe to offer at all. The planner
    is allowed to be wrong, and the consequence is reported rather than
    absorbed.
    """
    plan = plan_item(_item(
        gross_req=[860.0, 0.0],
        firm_planned_order=[500.0, 0.0],
    ))
    assert plan.projected_on_hand[0] == -360.0
    assert [e.kind for e in plan.exceptions] == ["negative_on_hand"]


def test_a_firm_order_larger_than_needed_carries_forward_as_stock():
    """Excess is real stock and must reduce later requirements."""
    plan = plan_item(_item(
        gross_req=[100.0, 100.0],
        firm_planned_order=[500.0, 0.0],
    ))
    assert plan.planned_order_receipt == [500.0, 0.0]
    assert plan.projected_on_hand == [400.0, 300.0]


def test_buckets_without_a_firm_order_are_planned_as_usual():
    """An override fixes one bucket, not the item."""
    plan = plan_item(_item(
        gross_req=[100.0, 250.0],
        firm_planned_order=[500.0, 0.0],
    ))
    assert plan.planned_order_receipt == [500.0, 0.0]

    plan = plan_item(_item(
        gross_req=[100.0, 700.0],
        firm_planned_order=[500.0, 0.0],
    ))
    # 500 covers bucket 0 and leaves 400; bucket 1 needs 300 more.
    assert plan.planned_order_receipt == [500.0, 300.0]


def test_a_firm_order_is_still_offset_by_the_lead_time():
    """It fixes quantity and date of the receipt; releasing is arithmetic."""
    plan = plan_item(_item(
        lead_time_days=2,
        gross_req=[0.0, 0.0, 100.0],
        firm_planned_order=[0.0, 0.0, 500.0],
    ))
    assert plan.planned_order_receipt == [0.0, 0.0, 500.0]
    assert plan.planned_order_release == [500.0, 0.0, 0.0]


def test_a_firm_order_is_netted_against_stock_already_on_hand():
    """It is fixed supply, not fixed demand: on-hand still counts."""
    plan = plan_item(_item(
        on_hand=200.0,
        gross_req=[100.0, 0.0],
        firm_planned_order=[500.0, 0.0],
    ))
    assert plan.projected_on_hand == [600.0, 600.0]


# --------------------------------------------------------------------------
# the same rule under every policy
# --------------------------------------------------------------------------

def test_a_fixed_quantity_rule_does_not_round_a_firm_order():
    """The planner's number is the planner's number."""
    plan = plan_item(_item(
        lot_sizing=LotSizing(policy="fixed_qty", fixed_qty=200.0),
        gross_req=[860.0, 0.0],
        firm_planned_order=[500.0, 0.0],
    ))
    # 500 is not a multiple of 200 and is left alone anyway. What the rule does
    # apply to is the 360 deficit it carries into the next bucket, rounded up
    # to 400 like any other requirement.
    assert plan.planned_order_receipt[0] == 500.0
    assert plan.planned_order_receipt[1] == 400.0


def test_wagner_whitin_does_not_order_in_a_firm_bucket():
    """The DP cannot top up a firm bucket, and does not need to be told not to.

    A firm bucket contributes **zero** to the requirement vector the DP sees --
    the planner has fixed it, so there is nothing left there to order for. The
    only way a lot could land in one is if the DP chose it as the order point
    for *later* demand, and that is never cheaper: ordering earlier holds the
    same units for more periods, `LotSizing` already refuses a Wagner-Whitin
    policy with a non-positive holding cost, and the tie-break prefers the
    latest order among equal-cost plans.

    So this is a property of the DP rather than a constraint bolted onto it,
    which is why the published instance below is untouched.
    """
    plan = plan_item(_item(
        lot_sizing=LotSizing(policy="wagner_whitin", setup_cost=500, holding_cost=2),
        gross_req=[90.0, 120.0, 80.0, 70.0],
        firm_planned_order=[50.0, 0.0, 0.0, 0.0],
    ))
    assert plan.planned_order_receipt[0] == 50.0


def test_wagner_whitin_is_unchanged_when_nothing_is_firm():
    """The published instance still lands on the published answer.

    Snyder & Shen Example 3.9: order 210 in period 1, 150 in period 3. The mask
    must be inert when it is empty, or every existing lot-sizing result moves.
    """
    plan = plan_item(_item(
        lot_sizing=LotSizing(policy="wagner_whitin", setup_cost=500, holding_cost=2),
        gross_req=[90.0, 120.0, 80.0, 70.0],
    ))
    assert plan.planned_order_receipt == [210.0, 0.0, 150.0, 0.0]


# --------------------------------------------------------------------------
# shape
# --------------------------------------------------------------------------

def test_a_misaligned_firm_series_is_refused():
    """Spine alignment is checked for every series, not only the first two."""
    with pytest.raises(ValueError, match="firm_planned_order"):
        plan_item(_item(gross_req=[1.0, 2.0], firm_planned_order=[1.0]))


def test_no_firm_orders_at_all_behaves_exactly_as_before():
    """The default has to be inert; every existing plan depends on it."""
    without = plan_item(_item(gross_req=[100.0, 250.0]))
    with_empty = plan_item(_item(gross_req=[100.0, 250.0],
                                 firm_planned_order=[0.0, 0.0]))
    assert without.planned_order_receipt == with_empty.planned_order_receipt
    assert without.projected_on_hand == with_empty.projected_on_hand
