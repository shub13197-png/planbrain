"""Service-level backtest: fill rate against inventory held (build item 5).

The proof-of-value report. Replays held-out history one SKU at a time, applies
each candidate policy, and reports what service it achieved and what stock it
had to carry to achieve it.

Same layering as netreq and forecast: ``core`` and ``policies`` are pure and
know nothing about scenarios or dates; this module owns the adapters.

Why this exists is worth restating. Roughly half the demo portfolio is
intermittent or lumpy, which is where planning software earns its money, and
MASE demonstrably cannot judge that half -- it rewards a forecast of zero, which
is a policy that never orders. Fill rate against average on-hand is the number
that settles it, and the number a finance manager will actually read.
"""

from dataclasses import dataclass

from ..forecast import classify, make_forecaster
from ..facts.access import read_facts
from ..forecast.metrics import ScoredMean, scored_mean
from .core import Outcome, replay
from .policies import (
    demand_statistics,
    forecast_order_up_to,
    naive_zero_order_up_to,
    reorder_point,
)

TABLE = "fact_supply_demand"

POLICIES = ("forecast", "naive_zero", "reorder_point", "reorder_point_stale")

#: Fraction of the training history the stale reorder point is fitted on. It is
#: then never revisited, which is what an SME incumbent actually looks like: the
#: numbers were set once, by someone who may have left, and nobody re-derives
#: them quarterly. "Well-tuned" presupposes ongoing tuning nobody is doing.
STALE_FIT_FRACTION = 1 / 3

__all__ = [
    "Outcome",
    "POLICIES",
    "PolicyResult",
    "capacity_factor",
    "compare",
    "demand_statistics",
    "forecast_order_up_to",
    "naive_zero_order_up_to",
    "reorder_point",
    "replay",
]


@dataclass(frozen=True)
class PolicyResult:
    """One policy's outcome across a set of series, grouped by demand pattern.

    Fill rate and inventory are carried together and each as a ScoredMean, so
    neither can be quoted without its denominator and neither can be quoted
    without the other.
    """

    policy: str
    fill_rate: ScoredMean
    average_on_hand: ScoredMean
    units_short: float
    by_pattern: dict


def capacity_factor(con, demo, *, scenario_id: int = 0, holdout_days: int = 90) -> list:
    """Per-bucket share of ordered production the plant could actually make.

    **A crude sensitivity, and labelled as one.** It takes the per-bucket ratio
    of available hours to loaded hours from `rccp` on the forward horizon and
    applies it to the holdout window as a stationary approximation. The horizons
    are different, so this is not the true constraint on those buckets -- it is
    the shape of the constraint the same plant exhibits.

    Crude is the point. The service backtest currently assumes production is
    unconstrained while `rccp` reports the same plan infeasible in 147 of 450
    resource-buckets, and one unqualified number is worse than an honest range.
    """
    from .. import rccp

    report = rccp.run(con, demo, scenario_id=scenario_id)
    buckets = report["buckets"]

    loads = [0.0] * buckets
    available = [0.0] * buckets
    for resource_id in report["resources"]:
        rows = read_facts(
            con, "fact_capacity", scenario_id=scenario_id,
            measure="capacity_load_hours",
            start=demo.horizon_start, end=demo.horizon_end, keys=[(resource_id,)],
        )
        avail = read_facts(
            con, "fact_capacity", scenario_id=scenario_id,
            measure="capacity_avail_hours",
            start=demo.horizon_start, end=demo.horizon_end, keys=[(resource_id,)],
        )
        for i, row in enumerate(rows):
            loads[i] += row.qty
        for i, row in enumerate(avail):
            available[i] += row.qty

    # A bucket with no load is unconstrained, not zero-capacity.
    horizon = [
        1.0 if load <= 0 else min(1.0, avail / load)
        for load, avail in zip(loads, available)
    ]
    return [horizon[t % len(horizon)] for t in range(holdout_days)]


def compare(
    con,
    demo,
    *,
    scenario_id: int = 0,
    keys=None,
    holdout_days: int = 90,
    safety_days: float = 0.0,
    delivery_factor: list = None,
) -> dict:
    """Replay the holdout window under every policy. Returns a report per policy.

    The forecast policy is fitted on the training portion only. Fitting on the
    full history and then replaying it would let the policy see the demand it is
    being judged on, which is the inventory equivalent of scaling MASE on the
    test window -- and would make every number here meaningless in a way that
    nothing would flag.
    """
    from ..forecast import demand_keys, read_history

    all_keys = demand_keys(demo)
    keys = list(keys) if keys is not None else all_keys
    history = read_history(
        con, scenario_id=scenario_id, keys=keys,
        history_start=demo.history_start, history_end=demo.history_end,
    )
    season_length = demo.calendar.seasonal_period
    by_sku = {p.sku_id: p for p in demo.parts}

    outcomes = {policy: {} for policy in POLICIES}
    patterns = {}
    unmatched = []

    for key in keys:
        series = history.get(key, [])
        if len(series) <= holdout_days:
            continue
        train, holdout = series[:-holdout_days], series[-holdout_days:]
        profile = classify(train)
        patterns[key] = profile.pattern

        part = by_sku.get(key[0])
        if part is None:
            # A demand series whose SKU is not in the part master is a broken
            # import, not a SKU with default parameters. Defaulting a lead time
            # would produce a service figure for a product that does not exist.
            unmatched.append(key)
            continue
        lead_time = part.lead_time_days
        lot = part.lot_qty
        mean, sd = demand_statistics(train)
        stale_window = train[: max(1, int(len(train) * STALE_FIT_FRACTION))]
        stale_mean, stale_sd = demand_statistics(stale_window)
        safety = mean * safety_days
        # Start every policy from the same position, or the comparison measures
        # the opening stock rather than the policy.
        opening = mean * (lead_time + 1)

        _, forecaster = make_forecaster(profile.pattern, season_length=season_length)
        fitted = forecaster(train, holdout_days)

        runs = {
            "forecast": forecast_order_up_to(
                fitted, lead_time_days=lead_time, safety_stock=safety, lot_multiple=lot
            ),
            "naive_zero": naive_zero_order_up_to(
                lead_time_days=lead_time, safety_stock=safety
            ),
            "reorder_point": reorder_point(
                mean_demand=mean, lead_time_days=lead_time, demand_sd=sd,
                safety_factor=1.0, order_quantity=lot or None,
            ),
            # Same rule, parameters frozen from the first third of history and
            # never revisited. A SKU launched after that window has a mean of
            # zero here and never gets ordered -- which is exactly what happens
            # in the field, and why it belongs in the comparison.
            "reorder_point_stale": reorder_point(
                mean_demand=stale_mean, lead_time_days=lead_time, demand_sd=stale_sd,
                safety_factor=1.0, order_quantity=lot or None,
            ),
        }
        for name, policy in runs.items():
            outcomes[name][key] = replay(
                holdout, policy, initial_on_hand=opening, lead_time_days=lead_time,
                delivery_factor=delivery_factor,
            )

    if unmatched:
        raise ValueError(
            f"{len(unmatched)} demand series have no part master entry, e.g. "
            f"{unmatched[:3]}; the reference data and the facts disagree"
        )

    return {
        "evaluated": len(patterns),
        "portfolio": len(all_keys),
        "holdout_days": holdout_days,
        "capacity_constrained": delivery_factor is not None,
        "pattern_mix": _tally(patterns.values()),
        "policies": {
            name: _summarise_policy(name, runs, patterns)
            for name, runs in outcomes.items()
        },
    }


def _summarise_policy(name, runs, patterns) -> PolicyResult:
    fills = {key: outcome.fill_rate for key, outcome in runs.items()}
    stock = {key: outcome.average_on_hand for key, outcome in runs.items()}
    by_pattern = {}
    for key, outcome in runs.items():
        bucket = by_pattern.setdefault(patterns[key], {"fill": {}, "stock": {}})
        bucket["fill"][key] = outcome.fill_rate
        bucket["stock"][key] = outcome.average_on_hand
    return PolicyResult(
        policy=name,
        fill_rate=scored_mean(fills),
        average_on_hand=scored_mean(stock),
        units_short=sum(o.units_short for o in runs.values()),
        by_pattern={
            pattern: {
                "fill_rate": scored_mean(v["fill"]),
                "average_on_hand": scored_mean(v["stock"]),
            }
            for pattern, v in sorted(by_pattern.items())
        },
    )


def _tally(values) -> dict:
    counts = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))
