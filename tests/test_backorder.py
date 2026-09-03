"""Backordered demand: served late rather than lost.

The risk in this feature is not the arithmetic, it is the reporting. Backorders
let a late delivery still count as served, so a single "fill rate" would rise
across the board the moment the flag was set, with nothing shipping any sooner.
The headline figure therefore stays **on-time** under both rules and the softer
number is reported beside it, never instead of it.
"""

import pytest

from planbrain.simulate.core import replay


def _always(quantity):
    """A policy that orders a fixed amount every bucket."""
    return lambda t, on_hand, inbound: quantity


def _never(t, on_hand, inbound):
    return 0.0


# --------------------------------------------------------------------------
# the rule is explicit
# --------------------------------------------------------------------------

def test_lost_sales_is_the_default():
    """The conservative reading stays the one you get without asking."""
    outcome = replay([10, 10], _never, initial_on_hand=5.0, lead_time_days=1)
    assert outcome.average_backlog == 0.0
    assert outcome.backlog_buckets == 0
    assert outcome.units_served == outcome.units_served_on_time


@pytest.mark.parametrize("rule", ["backordered", "BACKORDER", "", None, "lost_sales"])
def test_an_unrecognised_rule_is_refused(rule):
    """No silent default for a business rule this consequential: the difference
    between lost and backordered is the difference between two businesses."""
    with pytest.raises(ValueError, match="lost"):
        replay([1], _never, initial_on_hand=0.0, lead_time_days=1, unmet=rule)


# --------------------------------------------------------------------------
# what actually happens to the units
# --------------------------------------------------------------------------

def test_demand_missed_today_is_served_when_stock_arrives():
    """The whole feature in one assertion."""
    #      bucket 0: demand 10, nothing on hand -> 10 owed
    #      bucket 1: 10 arrive (ordered at 0, lead time 1), no new demand
    demand = [10, 0]
    policy = lambda t, on_hand, inbound: 10.0 if t == 0 else 0.0

    lost = replay(demand, policy, initial_on_hand=0.0, lead_time_days=1, unmet="lost")
    kept = replay(demand, policy, initial_on_hand=0.0, lead_time_days=1, unmet="backorder")

    assert lost.units_served == 0.0, "lost sales must not serve yesterday's demand"
    assert kept.units_served == 10.0, "the backlog was never filled"
    assert kept.units_served_on_time == 0.0, (
        "serving a day late must not count as on time"
    )


def test_the_headline_fill_rate_is_unmoved_by_switching_rules():
    """The reporting guarantee. If `fill_rate` were eventual, turning
    backorders on would raise every policy's service with nothing shipping any
    sooner -- a number that improves because an assumption changed."""
    demand = [10, 0]
    policy = lambda t, on_hand, inbound: 10.0 if t == 0 else 0.0

    lost = replay(demand, policy, initial_on_hand=0.0, lead_time_days=1, unmet="lost")
    kept = replay(demand, policy, initial_on_hand=0.0, lead_time_days=1, unmet="backorder")

    assert lost.fill_rate == kept.fill_rate == 0.0
    assert kept.eventual_fill_rate == 1.0
    assert lost.eventual_fill_rate == 0.0


def test_eventual_service_is_never_below_on_time_service():
    """An invariant of the two definitions, whatever the demand does."""
    outcome = replay([5, 0, 7, 2, 0], _always(4.0), initial_on_hand=3.0,
                     lead_time_days=2, unmet="backorder")
    assert outcome.eventual_fill_rate >= outcome.fill_rate


def test_owed_demand_is_served_before_todays():
    """Otherwise the oldest customer waits longest, which is neither what
    happens nor anything anyone would defend."""
    #  bucket 0: demand 10, nothing on hand      -> 10 owed
    #  bucket 1: 6 arrive, demand 4              -> the 6 go to the backlog
    demand = [10, 4]
    policy = lambda t, on_hand, inbound: 6.0 if t == 0 else 0.0

    outcome = replay(demand, policy, initial_on_hand=0.0, lead_time_days=1,
                     unmet="backorder")
    assert outcome.units_served == 6.0
    assert outcome.units_served_on_time == 0.0, (
        "today's demand was served while older demand was still owed"
    )


def test_nothing_is_served_twice():
    """The accounting has to close: served can never exceed demanded."""
    outcome = replay([4, 9, 0, 6], _always(5.0), initial_on_hand=2.0,
                     lead_time_days=1, unmet="backorder")
    assert outcome.units_served <= outcome.units_demanded
    assert outcome.units_served_on_time <= outcome.units_served


def test_the_policy_is_shown_net_stock_not_gross():
    """A policy shown on-hand while a backlog is outstanding would decide it has
    enough while owing a fortnight of demand, and would never catch up."""
    seen = []

    def watching(t, position, inbound):
        seen.append(position)
        return 0.0

    replay([10, 0, 0], watching, initial_on_hand=0.0, lead_time_days=1,
           unmet="backorder")
    assert seen[0] == 0.0
    assert seen[1] == -10.0, (
        f"the policy saw {seen[1]}, so it was shown stock rather than net stock "
        f"and cannot know it owes anything"
    )


def test_the_backlog_is_reported_not_just_absorbed():
    """A run that quietly carried a permanent backlog would show a respectable
    eventual fill rate and nothing to say the customer waited every week."""
    outcome = replay([10, 10, 10], _never, initial_on_hand=0.0, lead_time_days=1,
                     unmet="backorder")
    assert outcome.backlog_buckets == 3
    assert outcome.average_backlog == pytest.approx((10 + 20 + 30) / 3)


def test_a_backlog_that_never_clears_still_reports_zero_service():
    """The failure mode worth naming: eventual service is not a promise that
    anything ever arrives."""
    outcome = replay([5, 5], _never, initial_on_hand=0.0, lead_time_days=1,
                     unmet="backorder")
    assert outcome.fill_rate == 0.0
    assert outcome.eventual_fill_rate == 0.0
