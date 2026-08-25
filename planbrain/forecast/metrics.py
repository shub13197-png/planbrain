"""Forecast accuracy metrics, with the traps made explicit.

MASE (Hyndman & Koehler 2006, *Another look at measures of forecast accuracy*)
is the headline number because it is scale-free and therefore comparable across
a portfolio where volumes differ by three orders of magnitude. Percentage errors
cannot do that job here: MAPE is undefined the moment demand is zero, and this
data is roughly half zeros by design.

Three ways to get a flatteringly wrong number, all silent:

1. **Scaling on the test window.** The denominator must come from training data
   only. Using the evaluation window leaks the answer.
2. **The wrong seasonal period.** On daily data from a plant closed one day a
   week, a one-step naive baseline is wrong at every weekend boundary. That
   inflates the denominator and flatters every model that beats it. The period
   is derived from the working calendar, never hardcoded.
3. **Quietly dropping what could not be scored.** A mean over the easy half of
   a portfolio is an advertisement. Enforced structurally below: no function
   here returns a bare mean.
"""

import math
from dataclasses import dataclass


class UndefinedMASE(ValueError):
    """The scale factor is zero, so MASE cannot be computed for this series."""


@dataclass(frozen=True)
class ScoredMean:
    """A mean that cannot be separated from its denominators.

    Deliberately not a float and deliberately without ``__float__``. A caller
    that wants the number has to take ``.value``, and at that point the counts
    are right there. A caller who has to *remember* to report the denominator
    will eventually forget, and a mean quoted without it is unfalsifiable.
    """

    value: float | None
    n_scored: int
    n_unscored: int

    @property
    def total(self) -> int:
        return self.n_scored + self.n_unscored

    @property
    def coverage(self) -> float:
        """Fraction of candidates that could actually be scored."""
        return self.n_scored / self.total if self.total else 0.0

    def __str__(self) -> str:
        if self.value is None:
            return f"unscored ({self.n_unscored} of {self.total} could not be scored)"
        return f"{self.value:.3f} ({self.n_scored} scored, {self.n_unscored} unscored)"


@dataclass(frozen=True)
class Summary:
    """Portfolio-level accuracy. Carries its own denominators, same reason."""

    mean: ScoredMean
    median: float | None
    worse_than_baseline: int
    best: float | None
    worst: float | None

    @property
    def n_scored(self) -> int:
        return self.mean.n_scored

    @property
    def n_unscored(self) -> int:
        return self.mean.n_unscored


def naive_scale(train: list, seasonal_period: int) -> float:
    """Mean absolute error of the in-sample seasonal naive forecast.

    This is the MASE denominator. Computed from ``train`` alone, never from the
    evaluation window. ``seasonal_period`` is required rather than defaulted --
    see planbrain.working_calendar for where it comes from.
    """
    m = seasonal_period
    if m < 1:
        raise ValueError(f"seasonal period must be at least 1, got {m}")
    if len(train) <= m:
        raise UndefinedMASE(
            f"training window of {len(train)} is too short for seasonal period {m}"
        )
    diffs = [abs(train[t] - train[t - m]) for t in range(m, len(train))]
    return sum(diffs) / len(diffs)


def mase(actual: list, forecast: list, train: list, seasonal_period: int) -> float:
    """Mean absolute scaled error. Below 1 beats the seasonal naive baseline.

    Raises UndefinedMASE when the scale factor is zero -- a series perfectly
    periodic in training, such as an all-zero intermittent SKU or one on a fixed
    weekly rhythm. Those are not scored rather than being silently dropped.
    """
    if len(actual) != len(forecast):
        raise ValueError(f"actual has {len(actual)} buckets, forecast has {len(forecast)}")
    if not actual:
        raise ValueError("cannot score an empty evaluation window")

    scale = naive_scale(train, seasonal_period)
    if scale == 0:
        raise UndefinedMASE(
            "seasonal naive error is zero on the training window; the series is "
            "perfectly periodic there and MASE has no meaning"
        )
    return sum(abs(a - f) for a, f in zip(actual, forecast)) / len(actual) / scale


def scored_mean(values: dict) -> ScoredMean:
    """Mean of the scorable values, carrying what it could not score."""
    scored = [v for v in values.values() if v is not None and math.isfinite(v)]
    return ScoredMean(
        value=(sum(scored) / len(scored)) if scored else None,
        n_scored=len(scored),
        n_unscored=len(values) - len(scored),
    )


def summarise(scores: dict, *, threshold: float = 1.0) -> Summary:
    """Portfolio summary. ``threshold`` is the line above which a score is worse
    than the baseline -- 1.0 for MASE by construction."""
    mean = scored_mean(scores)
    values = sorted(v for v in scores.values() if v is not None and math.isfinite(v))
    return Summary(
        mean=mean,
        median=_median(values),
        worse_than_baseline=sum(1 for v in values if v > threshold),
        best=values[0] if values else None,
        worst=values[-1] if values else None,
    )


def _median(values: list):
    if not values:
        return None
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2
