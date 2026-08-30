"""Every masking default found in the second sweep, asserted to now be loud.

The class: an operation whose no-op outcome is indistinguishable from success.
Four instances of it were caught one at a time across items 4-8 -- a skipped
series-length check, a gate that globbed nothing, a codegen step that wrote
`null`, and an order lost at zero lead time. This file is the result of hunting
the rest in one pass instead of waiting for the fifth.

Each test below corresponds to a `.get(key, default)` or equivalent where the
default would have silently changed a published number. Legitimate sparse
lookups -- a SKU with no demand, a bucket with no row -- were deliberately left
alone, because there absence is meaningful rather than unexpected.
"""

import pytest

from planbrain.demo import build_demo


@pytest.fixture(scope="module")
def demo():
    return build_demo(seed=7)


def test_an_unrecognised_pack_size_is_refused():
    """It would have made packaging free for every finished good, and the cost
    roll-up would still have looked plausible."""
    from planbrain.demo.generate import _cost_parts, Part, BomEdge

    parts = [
        Part(1000, "SN-150 lot 00", "raw", 14, 0.0, "fixed_qty", 100.0),
        Part(3000, "15W-40 500ml", "finished", 2, 0.0, "lot_for_lot", 0.0),
    ]
    with pytest.raises(ValueError, match="no recognised pack size"):
        _cost_parts(parts, [BomEdge(3000, 1000, 1.0)])


def test_a_routing_without_a_costed_part_is_refused():
    """Zero holding would drop the SKU back to lot-for-lot, changing its policy
    with nothing in the report to show for it."""
    from planbrain.netreq import cost_lot_sizing

    class _R:
        def __init__(self, sku_id):
            self.sku_id, self.setup_hours, self.hours_per_unit = sku_id, 2.0, 0.001

    class _P:
        def __init__(self, sku_id, unit_cost):
            self.sku_id, self.unit_cost = sku_id, unit_cost

    with pytest.raises(ValueError, match="no costed part"):
        cost_lot_sizing([_R(10)], [_P(99, 100.0)])


def test_an_unknown_demand_pattern_is_refused():
    """classify() returns a closed set. Falling back to naive would degrade
    every forecast in the run while the model mix still looked populated."""
    from planbrain.forecast import make_forecaster

    with pytest.raises(ValueError, match="no model for demand pattern"):
        make_forecaster("bimodal", season_length=7)


def test_the_service_backtest_refuses_a_series_with_no_part(demo, con):
    """A demand series whose SKU is not in the part master is a broken import,
    not a SKU with default parameters. Defaulting its lead time would produce a
    service figure for a product that does not exist."""
    import dataclasses

    from planbrain import simulate
    from planbrain.demo import populate
    from planbrain.forecast import demand_keys

    populate(con, demo)
    keys = demand_keys(demo)[:4]
    stripped = dataclasses.replace(
        demo, parts=[p for p in demo.parts if p.sku_id != keys[0][0]]
    )
    with pytest.raises(ValueError, match="no part master entry"):
        simulate.compare(con, stripped, keys=keys, holdout_days=90)


def test_the_reconciliation_refuses_a_series_with_no_part(demo, con):
    """It used to drop these silently, shrinking the sample without saying so."""
    import dataclasses

    from planbrain import reconcile
    from planbrain.demo import populate
    from planbrain.forecast import demand_keys

    populate(con, demo)
    keys = demand_keys(demo)[:4]
    stripped = dataclasses.replace(
        demo, parts=[p for p in demo.parts if p.sku_id != keys[0][0]]
    )
    with pytest.raises(ValueError, match="no part master entry"):
        reconcile.reconcile(con, stripped, keys=keys, holdout_days=90)


def test_the_reconciliation_refuses_an_empty_sample(demo, con):
    """A residual of zero over nothing looks exactly like perfect agreement."""
    from planbrain import reconcile
    from planbrain.demo import populate
    from planbrain.forecast import demand_keys

    populate(con, demo)
    with pytest.raises(ValueError, match="no series survived"):
        reconcile.reconcile(
            con, demo, keys=demand_keys(demo)[:2], holdout_days=10_000
        )


def test_legitimate_sparse_lookups_were_left_alone(demo, con):
    """The distinction that makes this sweep a judgement rather than a rule.

    A SKU with no independent demand, a bucket with no fact row, a resource with
    no routed work: absence there is meaningful and the default encodes it.
    Hardening those would turn ordinary sparsity into an error.
    """
    from planbrain.demo import populate
    from planbrain.facts.access import read_facts

    populate(con, demo)
    rows = read_facts(
        con, "fact_supply_demand", scenario_id=0, measure="demand_actual",
        start=demo.history_start, end=demo.history_start, keys=[(999_999, 11)],
    )
    assert [r.qty for r in rows] == [0]

    unrouted = {r.resource_id for r in demo.routings}
    assert any(r.resource_id not in unrouted for r in demo.resources)
