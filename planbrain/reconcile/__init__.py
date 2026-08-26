"""Reconciling netreq's planned stock with the simulation's replayed stock.

Build item 8. `core` is pure; this module owns scenarios and dates.

**Scope note that matters for reading the numbers.** This reconciles at the
level the service backtest works at: one series per (sku, depot), single
echelon, no BOM explosion. `netreq` in production explodes a multi-level BOM at
the plant, which the simulation has never done. Comparing those directly would
put the whole distribution and explosion structure into an unnamed residual, so
the reconciliation deliberately puts both engines on the simulation's footing.

It also runs on a **holdout inside history**, not the forward horizon. There are
no realised actuals for the future, so "forecast error" would be unmeasurable
there.
"""

from dataclasses import dataclass

from ..forecast import classify, make_forecaster
from ..forecast.metrics import ScoredMean, scored_mean
from ..netreq.core import plan_item
from ..simulate.policies import demand_statistics
from .core import RUNGS, TERMS, Ladder, build_ladder, replay_schedule, total_change
from .terms import item_factory, lot_for_lot

__all__ = [
    "Ladder",
    "RUNGS",
    "TERMS",
    "build_ladder",
    "reconcile",
    "replay_schedule",
    "total_change",
]

#: Residual above this share of plan on-hand is a bug, not float noise.
#: Committed in docs/reconciliation.md before the reconciliation was run.
RESIDUAL_TOLERANCE = 0.005


@dataclass(frozen=True)
class Reconciliation:
    evaluated: int
    portfolio: int
    holdout_days: int
    plan_on_hand: ScoredMean
    replayed_on_hand: ScoredMean
    terms: dict
    by_pattern: dict
    residual: float
    residual_share: float
    ladders: list

    @property
    def within_tolerance(self) -> bool:
        return self.residual_share <= RESIDUAL_TOLERANCE


def reconcile(con, demo, *, scenario_id: int = 0, keys=None,
              holdout_days: int = 90) -> Reconciliation:
    """Walk the ladder for every series and aggregate the terms."""
    from ..forecast import demand_keys, read_history

    all_keys = demand_keys(demo)
    keys = list(keys) if keys is not None else all_keys
    history = read_history(
        con, scenario_id=scenario_id, keys=keys,
        history_start=demo.history_start, history_end=demo.history_end,
    )
    season_length = demo.calendar.seasonal_period
    lead_times = {p.sku_id: p.lead_time_days for p in demo.parts}
    by_sku = {p.sku_id: p for p in demo.parts}

    ladders = []
    for key in keys:
        series = history.get(key, [])
        if len(series) <= holdout_days:
            continue
        train, actual = series[:-holdout_days], series[-holdout_days:]
        profile = classify(train)
        part = by_sku.get(key[0])
        if part is None:
            continue

        _, forecaster = make_forecaster(profile.pattern, season_length=season_length)
        forecast = forecaster(train, holdout_days)
        mean, _sd = demand_statistics(train)
        lead_time = lead_times.get(key[0], 7)

        ladders.append(build_ladder(
            key=key,
            pattern=profile.pattern,
            forecast=forecast,
            actual=actual,
            opening=mean * (lead_time + 1),
            lead_time_days=lead_time,
            safety_stock=part.safety_stock,
            lot_sizing=_lot_sizing_for(part),
            plan_item=plan_item,
            item_factory=item_factory(key[0], key[1]),
        ))

    return _aggregate(ladders, len(all_keys), holdout_days)


def _aggregate(ladders, portfolio, holdout_days) -> Reconciliation:
    plan = scored_mean({l.key: l.plan_on_hand for l in ladders})
    replayed = scored_mean({l.key: l.replayed_on_hand for l in ladders})

    terms = {
        term: sum(l.terms[term] for l in ladders) / len(ladders) if ladders else 0.0
        for term in TERMS
    }

    # The observed gap is plan minus replayed. The two terms that separate them
    # are forecast error and truncation; safety stock and lot granularity explain
    # the plan's own stock instead. Summing all four against the wrong gap would
    # produce a residual that means nothing.
    observed = (plan.value or 0.0) - (replayed.value or 0.0)
    explained = -(terms["forecast_error"] + terms["stockout_truncation"])
    residual = observed - explained
    share = abs(residual) / abs(plan.value) if plan.value else 0.0

    by_pattern = {}
    for ladder in ladders:
        by_pattern.setdefault(ladder.pattern, []).append(ladder)

    return Reconciliation(
        evaluated=len(ladders),
        portfolio=portfolio,
        holdout_days=holdout_days,
        plan_on_hand=plan,
        replayed_on_hand=replayed,
        terms=terms,
        by_pattern={
            pattern: {
                "n": len(group),
                "plan_on_hand": scored_mean({l.key: l.plan_on_hand for l in group}),
                "replayed_on_hand": scored_mean(
                    {l.key: l.replayed_on_hand for l in group}
                ),
                **{
                    term: sum(l.terms[term] for l in group) / len(group)
                    for term in TERMS
                },
            }
            for pattern, group in sorted(by_pattern.items())
        },
        residual=residual,
        residual_share=share,
        ladders=ladders,
    )


def _lot_sizing_for(part):
    from ..netreq.explode import _lot_sizing_for as build

    return build(part)
