"""Statistical forecasting and its backtest harness (build item 4).

Same layering as netreq. ``metrics``, ``classify`` and ``backtest`` are pure and
need no statsforecast; ``models`` wraps statsforecast; this module is the only
part that knows about scenarios and dates.

Forecasts are produced and stored **per depot**, not aggregated. Reconciliation
to plant level is bottom-up and happens in ``netreq.resolve_gross_req`` at the
moment of netting, so the stored forecast stays at the grain it was fitted at
and the aggregation is visible at the point of use rather than baked into
storage.
"""

from ..facts.access import Fact, read_facts, write_facts
from ..facts.scenario import growth_of
from .backtest import backtest_series, rolling_origin_windows, seasonal_naive
from .classify import classify, is_intermittent
from .metrics import (
    ScoredMean,
    Summary,
    UndefinedMASE,
    mase,
    naive_scale,
    scored_mean,
    summarise,
)
from .growth import growth_factors
from .models import Fallbacks, MODEL_FOR_PATTERN, Suppressions, make_forecaster

TABLE = "fact_supply_demand"
SOURCE_MEASURE = "demand_actual"
OUTPUT_MEASURE = "forecast"

__all__ = [
    "Fallbacks",
    "MODEL_FOR_PATTERN",
    "OUTPUT_MEASURE",
    "UndefinedMASE",
    "backtest",
    "backtest_series",
    "classify",
    "is_intermittent",
    "make_forecaster",
    "mase",
    "naive_scale",
    "read_history",
    "ScoredMean",
    "Summary",
    "rolling_origin_windows",
    "run",
    "scored_mean",
    "seasonal_naive",
    "summarise",
]


def read_history(con, *, scenario_id, keys, history_start, history_end) -> dict:
    """Dense demand history per (sku_id, loc_id)."""
    rows = read_facts(
        con, TABLE,
        scenario_id=scenario_id, measure=SOURCE_MEASURE,
        start=history_start, end=history_end, keys=keys,
    )
    history = {}
    for row in rows:
        history.setdefault(row.keys, []).append(row.qty)
    return history


def demand_keys(demo) -> list:
    """Every (sku, depot) pair the demo actually sells at.

    Taken from the dataset rather than the cross product of SKUs and locations:
    a pack is not stocked everywhere, and forecasting a series that has never
    existed produces a confident zero that looks like a real prediction.
    """
    return sorted({
        fact.keys for fact in demo.facts[(TABLE, SOURCE_MEASURE)]
    })


def run(con, demo, *, scenario_id: int = 0, keys=None, season_length: int = None) -> dict:
    """Fit a model per series and write the forecast measure. Returns a report.

    The report carries the model mix and the fallback counts, not just a row
    count -- a run where a third of the portfolio silently fell back to the
    naive baseline is a different result, and no row count would say so.
    """
    season_length = _season_length(demo, season_length)
    keys = list(keys) if keys is not None else demand_keys(demo)
    history = read_history(
        con, scenario_id=scenario_id, keys=keys,
        history_start=demo.history_start, history_end=demo.history_end,
    )
    horizon = (demo.horizon_end - demo.horizon_start).days + 1
    spine = [demo.horizon_start + _days(i) for i in range(horizon)]

    # Read from the scenario, never from an argument: two engines running the
    # same scenario under different assumptions would be invisible in every
    # output and irreproducible afterwards.
    growth = growth_of(con, scenario_id)
    suppress = growth.demand_pct != 0.0
    # Anchored at the last actual, not the first planned bucket. See
    # docs/forecast.md -- anchoring at the horizon drops the gap between them,
    # an error that is small, always in the same direction, and invisible.
    factors = growth_factors(spine, anchor=demo.history_end, annual_pct=growth.demand_pct)

    fallbacks = Fallbacks()
    suppressions = Suppressions()
    patterns, models, facts = {}, {}, []
    for key in keys:
        series = history.get(key, [])
        profile = classify(series)
        name, forecaster = make_forecaster(
            profile.pattern, season_length=season_length, fallbacks=fallbacks,
            suppress_trend=suppress, suppressions=suppressions,
        )
        patterns[key] = profile.pattern
        models[key] = name
        facts.extend(
            Fact(key, bucket, qty * factor)
            for bucket, qty, factor in zip(spine, forecaster(series, horizon), factors)
        )

    rows = write_facts(
        con, TABLE, scenario_id=scenario_id, measure=OUTPUT_MEASURE, facts=facts
    )
    return {
        "series": len(keys),
        "rows_written": rows,
        "pattern_mix": _tally(patterns.values()),
        "model_mix": _tally(models.values()),
        "fallbacks": fallbacks.as_dict(),
        "demand_growth_pct": growth.demand_pct,
        "growth_anchor": demo.history_end.isoformat(),
        # What the assumption displaced. For these series AutoETS had inferred a
        # trend from the data and it was discarded in favour of the number
        # someone typed; a planner setting a growth rate should be able to see
        # how much of the portfolio that overrode.
        "trends_suppressed": suppressions.count,
    }


def backtest(con, demo, *, scenario_id: int = 0, keys=None, horizon: int = 28,
             n_windows: int = 3, min_train: int = 180,
             season_length: int = None) -> dict:
    """Score the chosen models against seasonal naive on held-out history.

    Reports the sample size against the portfolio size. Evaluating a subset is
    fine; not saying so is not -- a MASE quoted over an unnamed sample is
    unfalsifiable.
    """
    season_length = _season_length(demo, season_length)
    all_keys = demand_keys(demo)
    keys = list(keys) if keys is not None else all_keys
    history = read_history(
        con, scenario_id=scenario_id, keys=keys,
        history_start=demo.history_start, history_end=demo.history_end,
    )

    fallbacks = Fallbacks()
    model_scores, naive_scores, results = {}, {}, []
    for key in keys:
        series = history.get(key, [])
        profile = classify(series)
        name, forecaster = make_forecaster(
            profile.pattern, season_length=season_length, fallbacks=fallbacks
        )
        chosen = backtest_series(
            series, forecaster, key=key, pattern=profile.pattern, model=name,
            horizon=horizon, n_windows=n_windows, min_train=min_train,
            seasonal_period=season_length,
        )
        baseline = backtest_series(
            series, lambda train, h: seasonal_naive(train, h, season_length),
            key=key, pattern=profile.pattern, model="SeasonalNaive",
            horizon=horizon, n_windows=n_windows, min_train=min_train,
            seasonal_period=season_length,
        )
        # .value, not the ScoredMean itself: these feed a portfolio summarise()
        # which recounts scored and unscored across the whole sample.
        model_scores[key] = chosen.mase.value
        naive_scores[key] = baseline.mase.value
        results.append(chosen)

    return {
        "evaluated": len(keys),
        "portfolio": len(all_keys),
        "horizon": horizon,
        "n_windows": n_windows,
        "seasonal_period": season_length,
        "model": summarise(model_scores),
        "baseline": summarise(naive_scores),
        "by_pattern": _by_pattern(results, model_scores),
        "fallbacks": fallbacks.as_dict(),
    }


def _by_pattern(results, scores) -> dict:
    grouped = {}
    for result in results:
        grouped.setdefault(result.pattern, {})[result.key] = scores[result.key]
    return {pattern: summarise(s) for pattern, s in sorted(grouped.items())}


def _season_length(demo, override):
    """Seasonal period from the customer's working calendar, never a constant.

    An explicit override is honoured so a test can pin it, but nothing in normal
    operation passes one -- the calendar is the source of truth.
    """
    if override is not None:
        return override
    return demo.calendar.seasonal_period


def _tally(values) -> dict:
    counts = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _days(n):
    from datetime import timedelta

    return timedelta(days=n)
