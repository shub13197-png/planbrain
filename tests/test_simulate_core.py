"""The inventory replay and its policies, in memory.

This is the arithmetic behind the number the whole positioning rests on, so it
is tested against hand-worked cases where the expected answer is stated in full.
"""

import pytest

from planbrain.simulate.core import replay
from planbrain.simulate.policies import (
    demand_statistics,
    forecast_order_up_to,
    naive_zero_order_up_to,
    reorder_point,
)

NEVER = lambda t, on_hand, inbound: 0.0  # noqa: E731


# --------------------------------------------------------------------------
# the replay
# --------------------------------------------------------------------------

def test_demand_is_served_from_opening_stock():
    outcome = replay([4.0, 4.0], NEVER, initial_on_hand=10.0, lead_time_days=1)
    assert outcome.units_demanded == 8.0
    assert outcome.units_served == 8.0
    assert outcome.fill_rate == 1.0
    assert outcome.average_on_hand == 4.0  # closes at 6 then 2


def test_unmet_demand_is_lost_not_backordered():
    """A customer who cannot get it today buys elsewhere; they do not wait.

    If demand were backordered, the second bucket would have to serve 10 and the
    shortfall would eventually be recovered -- which flatters any policy that
    under-stocks.
    """
    outcome = replay([10.0, 5.0], NEVER, initial_on_hand=6.0, lead_time_days=1)
    assert outcome.units_served == 6.0
    assert outcome.units_demanded == 15.0
    assert outcome.units_short == 9.0
    assert outcome.stockout_buckets == 2


def test_an_order_arrives_after_the_lead_time_not_before():
    """Order placed in bucket 0 with a 2-day lead time is available in bucket 2."""
    once = lambda t, on_hand, inbound: 100.0 if t == 0 else 0.0  # noqa: E731
    outcome = replay([50.0, 50.0, 50.0], once, initial_on_hand=0.0, lead_time_days=2)
    assert outcome.units_served == 50.0, "only bucket 2 can be served"
    assert outcome.fill_rate == pytest.approx(1 / 3)


def test_goods_received_today_serve_today():
    """Stated because it moves the numbers and is a modelling choice."""
    once = lambda t, on_hand, inbound: 50.0 if t == 0 else 0.0  # noqa: E731
    outcome = replay([0.0, 50.0], once, initial_on_hand=0.0, lead_time_days=1)
    assert outcome.units_served == 50.0


def test_the_policy_sees_stock_already_on_its_way():
    """Without inbound visibility a policy re-orders every bucket until arrival."""
    seen = []

    def policy(t, on_hand, inbound):
        seen.append((on_hand, inbound))
        return 100.0 if t == 0 else 0.0

    replay([0.0, 0.0, 0.0], policy, initial_on_hand=0.0, lead_time_days=2)
    assert seen[1] == (0.0, 100.0), "bucket 1 must see the order in transit"


def test_fill_rate_is_none_when_nothing_was_demanded():
    """Reporting 1.0 would lift every portfolio average with untested SKUs."""
    outcome = replay([0.0, 0.0], NEVER, initial_on_hand=5.0, lead_time_days=1)
    assert outcome.fill_rate is None
    assert outcome.units_demanded == 0.0


def test_average_on_hand_covers_every_bucket_not_just_stocked_ones():
    outcome = replay([5.0, 0.0, 0.0, 0.0], NEVER, initial_on_hand=5.0, lead_time_days=1)
    assert outcome.average_on_hand == 0.0
    assert outcome.peak_on_hand == 0.0


def test_orders_and_units_ordered_are_counted():
    every = lambda t, on_hand, inbound: 10.0  # noqa: E731
    outcome = replay([0.0] * 4, every, initial_on_hand=0.0, lead_time_days=1)
    assert outcome.orders_placed == 4
    assert outcome.units_ordered == 40.0


def test_review_period_limits_when_orders_can_be_placed():
    every = lambda t, on_hand, inbound: 10.0  # noqa: E731
    outcome = replay([0.0] * 8, every, initial_on_hand=0.0, lead_time_days=1, review_every=4)
    assert outcome.orders_placed == 2


def test_negative_lead_time_is_refused():
    with pytest.raises(ValueError, match="lead time"):
        replay([1.0], NEVER, initial_on_hand=0.0, lead_time_days=-1)


def test_zero_review_period_is_refused():
    with pytest.raises(ValueError, match="review period"):
        replay([1.0], NEVER, initial_on_hand=0.0, lead_time_days=1, review_every=0)


# --------------------------------------------------------------------------
# policies
# --------------------------------------------------------------------------

def test_order_up_to_covers_lead_time_plus_review_period():
    """A decision today must carry the position until the next order can land.

    Lead time 2, review 1, so the protection window is 3 buckets: 10+10+10 = 30.
    """
    policy = forecast_order_up_to([10.0] * 10, lead_time_days=2, review_every=1)
    assert policy(0, 0.0, 0.0) == 30.0


def test_order_up_to_subtracts_what_is_already_held_and_inbound():
    policy = forecast_order_up_to([10.0] * 10, lead_time_days=2, review_every=1)
    assert policy(0, 12.0, 8.0) == 10.0
    assert policy(0, 40.0, 0.0) == 0.0


def test_order_up_to_adds_safety_stock():
    policy = forecast_order_up_to([10.0] * 10, lead_time_days=2, safety_stock=15.0)
    assert policy(0, 0.0, 0.0) == 45.0


def test_order_up_to_rounds_to_a_lot_multiple():
    """A real plan cannot order 37.4 litres; without this the comparison would
    flatter the forecast policy against what netreq would actually do."""
    policy = forecast_order_up_to([10.0] * 10, lead_time_days=2, lot_multiple=25.0)
    assert policy(0, 0.0, 0.0) == 50.0


def test_lot_rounding_does_not_over_round_an_exact_multiple():
    policy = forecast_order_up_to([10.0] * 10, lead_time_days=2, lot_multiple=10.0)
    assert policy(0, 0.0, 0.0) == 30.0


def test_a_zero_forecast_with_no_safety_stock_never_orders():
    """The policy implied by the forecast that wins on MASE for intermittent
    demand. It is also a policy that serves nobody once opening stock runs out."""
    policy = naive_zero_order_up_to(lead_time_days=5)
    assert policy(0, 0.0, 0.0) == 0.0

    outcome = replay([3.0] * 30, policy, initial_on_hand=6.0, lead_time_days=5)
    assert outcome.units_served == 6.0
    assert outcome.fill_rate == pytest.approx(6 / 90)


def test_reorder_point_holds_fire_above_the_trigger():
    policy = reorder_point(mean_demand=10.0, lead_time_days=2)
    assert policy(0, 1000.0, 0.0) == 0.0


def test_reorder_point_orders_up_to_S_when_the_position_drops():
    """s = 10 x (2+1) = 30, and S = s + 10 x 3 = 60, so from zero it orders 60."""
    policy = reorder_point(mean_demand=10.0, lead_time_days=2)
    assert policy(0, 0.0, 0.0) == 60.0


def test_reorder_point_counts_stock_in_transit():
    policy = reorder_point(mean_demand=10.0, lead_time_days=2)
    assert policy(0, 0.0, 60.0) == 0.0


def test_demand_statistics_average_over_every_bucket_including_zeros():
    """The classic intermittent-demand bug, and it does not raise.

    Three buckets of 40 in twelve: the demand *rate* is 10 a bucket, while the
    mean of non-zero sizes is 40. A reorder point built on 40 orders four times
    too much, and nothing anywhere would flag it.
    """
    history = [0.0, 0.0, 0.0, 40.0] * 3
    mean, sd = demand_statistics(history)
    assert mean == 10.0
    assert sd > 0

    nonzero_mean = sum(v for v in history if v) / len([v for v in history if v])
    assert nonzero_mean == 40.0


def test_demand_statistics_of_nothing_is_zero_not_an_error():
    assert demand_statistics([]) == (0.0, 0.0)
