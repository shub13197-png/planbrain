"""The service backtest against the demo dataset.

The replay arithmetic is tested purely in test_simulate_core.py. This covers the
adapter: that the holdout is genuinely held out, that fill rate and inventory
travel together, and that the naive-zero experiment produces the result the
whole intermittent argument rests on.
"""

import pytest

from planbrain import simulate
from planbrain.demo import build_demo, populate
from planbrain.forecast import demand_keys
from planbrain.forecast.metrics import ScoredMean
from planbrain.simulate.core import replay
from planbrain.simulate.policies import forecast_order_up_to

SAMPLE = 16


@pytest.fixture(scope="module")
def demo():
    return build_demo(seed=7)


@pytest.fixture(scope="module")
def sample_keys(demo):
    keys = demand_keys(demo)
    step = len(keys) / SAMPLE
    return [keys[int(i * step)] for i in range(SAMPLE)]


@pytest.fixture
def seeded(con, demo):
    populate(con, demo)
    return con


@pytest.fixture(scope="module")
def report(demo, sample_keys):
    import sqlite3
    from pathlib import Path

    schema = Path(__file__).resolve().parents[1] / "planbrain" / "facts" / "schema.sql"
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(schema.read_text(encoding="utf-8"))
    populate(con, demo)
    result = simulate.compare(con, demo, keys=sample_keys, holdout_days=90, safety_days=7.0)
    con.close()
    return result


# --------------------------------------------------------------------------
# the holdout is genuinely held out
# --------------------------------------------------------------------------

def test_a_policy_fitted_on_training_cannot_anticipate_the_holdout():
    """The inventory equivalent of scaling MASE on the test window.

    Flat demand of 10 for a long training run, then a step change to 200. A
    forecast fitted on training alone cannot see the step, so service must
    collapse. If it did not, the holdout would be leaking into the fit and every
    number in this report would be meaningless with nothing to flag it.
    """
    train_level, holdout_level = 10.0, 200.0
    forecast_from_training = [train_level] * 90
    policy = forecast_order_up_to(forecast_from_training, lead_time_days=3)
    outcome = replay(
        [holdout_level] * 90, policy, initial_on_hand=train_level * 4, lead_time_days=3
    )
    assert outcome.fill_rate < 0.3


def test_compare_reports_sample_against_portfolio(report):
    assert report["evaluated"] <= SAMPLE
    assert report["portfolio"] > SAMPLE
    assert sum(report["pattern_mix"].values()) == report["evaluated"]


def test_every_policy_is_evaluated(report):
    assert set(report["policies"]) == set(simulate.POLICIES)


# --------------------------------------------------------------------------
# service and inventory never travel apart
# --------------------------------------------------------------------------

def test_fill_rate_and_inventory_are_both_scored_means(report):
    """A policy hits any fill rate by holding enough stock, and holds almost no
    stock by serving nobody. Neither number means anything alone."""
    for result in report["policies"].values():
        assert isinstance(result.fill_rate, ScoredMean)
        assert isinstance(result.average_on_hand, ScoredMean)
        assert result.fill_rate.n_scored + result.fill_rate.n_unscored == report["evaluated"]


def test_a_scored_mean_cannot_be_used_as_a_bare_number(report):
    result = report["policies"]["forecast"]
    with pytest.raises(TypeError):
        float(result.fill_rate)


def test_results_are_broken_down_by_demand_pattern(report):
    for result in report["policies"].values():
        assert result.by_pattern
        for block in result.by_pattern.values():
            assert set(block) == {"fill_rate", "average_on_hand"}


# --------------------------------------------------------------------------
# the experiment that settles the intermittent question
# --------------------------------------------------------------------------

def test_the_naive_zero_forecast_collapses_on_service(report):
    """This is the whole argument, as a number rather than an assertion.

    A forecast of zero is close to optimal on MASE for intermittent demand. It
    is also a policy that barely orders. If accuracy were the objective, this is
    what would ship.
    """
    fitted = report["policies"]["forecast"].fill_rate.value
    naive = report["policies"]["naive_zero"].fill_rate.value
    assert fitted > naive
    assert naive < 0.9


def test_the_naive_zero_forecast_also_holds_far_less_stock(report):
    """Which is exactly why fill rate alone would not settle it either."""
    fitted = report["policies"]["forecast"].average_on_hand.value
    naive = report["policies"]["naive_zero"].average_on_hand.value
    assert naive < fitted


def test_both_reorder_points_are_reported_separately(report):
    """Tuned and stale are different claims and must not be collapsed.

    "Well-tuned" presupposes ongoing tuning nobody is doing. The stale row is
    what an SME incumbent actually looks like: parameters set once and never
    revisited.
    """
    tuned = report["policies"]["reorder_point"]
    stale = report["policies"]["reorder_point_stale"]
    assert tuned.fill_rate.n_scored == stale.fill_rate.n_scored
    assert tuned.fill_rate.value != stale.fill_rate.value


def test_a_stale_rule_never_orders_a_sku_launched_after_it_was_set():
    """The mechanism by which staleness bites, isolated from the portfolio.

    A SKU with no demand in the fitting window has a mean of zero, so s and S are
    both zero and the rule never triggers. In the field this is the SKU nobody
    noticed was launched.
    """
    from planbrain.simulate.policies import reorder_point

    stale = reorder_point(mean_demand=0.0, lead_time_days=5)
    assert stale(0, 0.0, 0.0) == 0.0

    outcome = replay([8.0] * 60, stale, initial_on_hand=0.0, lead_time_days=5)
    assert outcome.units_served == 0.0
    assert outcome.fill_rate == 0.0


def test_the_reorder_point_is_a_real_incumbent_not_a_straw_man(report):
    """A tool that cannot roughly match a spreadsheet rule is not worth installing.

    Asserted as a floor rather than as a win: the reorder point performing well
    is a finding to report, not a bug to tune away.
    """
    reorder = report["policies"]["reorder_point"].fill_rate.value
    assert reorder > 0.8


# --------------------------------------------------------------------------
# the capacity sensitivity (item 10)
# --------------------------------------------------------------------------

def test_a_delivery_factor_reduces_what_arrives():
    """The crude capacity cap. The planner still orders; less turns up."""
    def order_50(t, on_hand, inbound):
        return 50.0 if t == 0 else 0.0

    # Demand lands in bucket 1, which is when the bucket-0 order arrives.
    full = replay([0.0, 50.0], order_50, initial_on_hand=0.0, lead_time_days=1)
    half = replay([0.0, 50.0], order_50, initial_on_hand=0.0, lead_time_days=1,
                  delivery_factor=[1.0, 0.5])
    assert full.units_served == 50.0
    assert half.units_served == 25.0
    assert half.units_ordered == full.units_ordered, "ordering is unaffected"


def test_the_capacity_factor_is_bounded_and_per_bucket(seeded, demo):
    """A bucket with no load is unconstrained, never zero-capacity."""
    from planbrain import netreq, simulate

    netreq.run(seeded, demo)
    factor = simulate.capacity_factor(seeded, demo, holdout_days=90)
    assert len(factor) == 90
    assert all(0.0 < f <= 1.0 for f in factor)
    assert any(f < 1.0 for f in factor), "rccp reports this plan infeasible"


def test_the_capped_run_is_labelled_as_such(seeded, demo, sample_keys):
    """A reader must be able to tell the two numbers apart."""
    from planbrain import netreq, simulate

    netreq.run(seeded, demo)
    factor = simulate.capacity_factor(seeded, demo, holdout_days=90)
    capped = simulate.compare(seeded, demo, keys=sample_keys, delivery_factor=factor)
    plain = simulate.compare(seeded, demo, keys=sample_keys)
    assert capped["capacity_constrained"] is True
    assert plain["capacity_constrained"] is False


def test_series_with_no_holdout_demand_are_unscored_not_perfect(report):
    """Counting them as 100% would lift the average with SKUs never tested."""
    for result in report["policies"].values():
        assert result.fill_rate.n_scored <= report["evaluated"]
        assert result.fill_rate.coverage <= 1.0


# --------------------------------------------------------------------------
# two fill rates, never merged
# --------------------------------------------------------------------------

def test_the_weighted_fill_rate_answers_a_different_question(report):
    """**A mean across parts is not a fraction of demand served.**

    `fill_rate` weights a part with two units of annual demand exactly as much
    as one with two million. That is defensible -- a stockout can halt a line
    whatever the part costs -- and it is not the number a business is paid on.
    `weighted_fill_rate` is units served over units demanded across the whole
    portfolio, and the two are reported side by side because they can disagree,
    and the disagreement is the information.
    """
    for result in report["policies"].values():
        assert result.weighted_fill_rate is not None
        assert 0.0 <= result.weighted_fill_rate <= 1.0


def test_the_two_fill_rates_are_not_the_same_number(report):
    """If they agreed, one of them would be redundant and the distinction would
    be ceremony."""
    gaps = [abs(r.fill_rate.value - r.weighted_fill_rate)
            for r in report["policies"].values() if r.fill_rate.value is not None]
    assert max(gaps) > 0.001, (
        "the demand-weighted and per-part fill rates agree to a thousandth on "
        "every policy, so this portfolio has no size spread to reveal"
    )


def test_total_on_hand_is_the_portfolio_not_an_average(report):
    """The weighted fill rate needs a stock figure on the same footing: what the
    portfolio holds, not what an average part holds."""
    for result in report["policies"].values():
        assert result.total_on_hand >= result.average_on_hand.value
