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
from ..simulate.policies import demand_statistics
from .core import (
    RUNGS,
    TERMS,
    Ladder,
    build_ladder,
    replay_schedule,
    simulate_schedule,
    total_change,
)

__all__ = [
    "Ladder",
    "RUNGS",
    "TERMS",
    "build_ladder",
    "reconcile",
    "replay_schedule",
    "simulate_schedule",
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
    construction_check: float
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
    by_sku = {p.sku_id: p for p in demo.parts}

    ladders = []
    unmatched = []
    for key in keys:
        series = history.get(key, [])
        if len(series) <= holdout_days:
            continue
        train, actual = series[:-holdout_days], series[-holdout_days:]
        profile = classify(train)
        part = by_sku.get(key[0])
        if part is None:
            # Dropping these silently would shrink the sample without saying so,
            # and a reconciliation over an unnamed subset explains nothing.
            unmatched.append(key)
            continue

        _, forecaster = make_forecaster(profile.pattern, season_length=season_length)
        forecast = forecaster(train, holdout_days)
        mean, _sd = demand_statistics(train)
        lead_time = part.lead_time_days

        ladders.append(build_ladder(
            key=key,
            pattern=profile.pattern,
            forecast=forecast,
            actual=actual,
            opening=mean * (lead_time + 1),
            lead_time_days=lead_time,
            safety_stock=part.safety_stock,
            lot_sizing=_lot_sizing_for(part),
        ))

    if unmatched:
        raise ValueError(
            f"{len(unmatched)} demand series have no part master entry, e.g. "
            f"{unmatched[:3]}; the reference data and the facts disagree"
        )
    if not ladders:
        raise ValueError(
            "no series survived the holdout filter; a reconciliation over "
            "nothing would report a residual of zero and mean it"
        )
    return _aggregate(ladders, len(all_keys), holdout_days)


def _aggregate(ladders, portfolio, holdout_days) -> Reconciliation:
    plan = scored_mean({l.key: l.plan_on_hand for l in ladders})
    replayed = scored_mean({l.key: l.replayed_on_hand for l in ladders})

    terms = {
        term: sum(l.terms[term] for l in ladders) / len(ladders) if ladders else 0.0
        for term in TERMS
    }

    # The term sum is an ALGEBRAIC IDENTITY and cannot fail: each term is a
    # difference between adjacent rungs, so they collapse to the gap whatever the
    # rungs contain. Kept as a guard against coding slips, reported as such, and
    # never as evidence.
    if plan.value is None or replayed.value is None:
        raise ValueError(
            "nothing could be scored, so there is no gap to decompose; "
            "reporting zero here would look like perfect agreement"
        )
    observed = plan.value - replayed.value
    explained = -(terms["forecast_error"] + terms["stockout_truncation"])
    construction_check = observed - explained

    # The falsifiable residual: rung 4 from the ladder against rung 4 from a
    # separately written engine. An error in the schedule, the opening balance
    # or the truncation rule moves this. Nothing moves the term sum.
    residual = sum(l.cross_check_residual for l in ladders) / len(ladders) if ladders else 0.0
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
        construction_check=construction_check,
        ladders=ladders,
    )


def _lot_sizing_for(part):
    from ..netreq.explode import _lot_sizing_for as build

    return build(part)
