"""Forecast accuracy metrics, with the traps made explicit.

MASE (Hyndman & Koehler 2006, *Another look at measures of forecast accuracy*)
is the headline number for item 4 because it is scale-free and therefore
comparable across a 200-SKU portfolio where volumes differ by three orders of
magnitude. Percentage errors cannot do that job here: MAPE is undefined the
moment demand is zero, and this dataset is roughly half zeros by design.

Two ways to get a flatteringly wrong MASE, both easy and both silent:

1. **Scaling on the test window.** The denominator must come from the training
   data only. Using the test window leaks the answer and makes every model look
   better than it is.
2. **The wrong seasonal period.** On daily data with structural weekly zeros --
   a plant that is closed on Sunday -- a one-step naive baseline is wrong every
   Saturday and every Monday. That inflates the denominator and flatters every
   model that beats it. The honest baseline here is seasonal naive at m=7.

Neither failure raises. Both are guarded below.
"""

import math

#: Weekly. The demo (and any real plant with a closed day) has structural
#: seven-day periodicity, so the naive baseline must be seasonal or the scale
#: factor is measuring the calendar rather than the model.
DEFAULT_SEASONAL_PERIOD = 7


class UndefinedMASE(ValueError):
    """The scale factor is zero, so MASE cannot be computed for this series."""


def naive_scale(train: list, seasonal_period: int = DEFAULT_SEASONAL_PERIOD) -> float:
    """Mean absolute error of the in-sample seasonal naive forecast.

    This is the MASE denominator. Computed from ``train`` alone, never from the
    evaluation window.
    """
    m = seasonal_period
    if len(train) <= m:
        raise UndefinedMASE(
            f"training window of {len(train)} is too short for seasonal period {m}"
        )
    diffs = [abs(train[t] - train[t - m]) for t in range(m, len(train))]
    return sum(diffs) / len(diffs)


def mase(
    actual: list,
    forecast: list,
    train: list,
    seasonal_period: int = DEFAULT_SEASONAL_PERIOD,
) -> float:
    """Mean absolute scaled error. Below 1 beats the seasonal naive baseline.

    Raises UndefinedMASE when the scale factor is zero, which happens for a
    series that is perfectly flat in training -- an all-zero intermittent SKU,
    or one on a fixed weekly rhythm. Those series are not scored rather than
    being silently dropped or counted as zero error: excluding them without
    saying so is how a portfolio MASE gets quietly improved.
    """
    if len(actual) != len(forecast):
        raise ValueError(
            f"actual has {len(actual)} buckets, forecast has {len(forecast)}"
        )
    if not actual:
        raise ValueError("cannot score an empty evaluation window")

    scale = naive_scale(train, seasonal_period)
    if scale == 0:
        raise UndefinedMASE(
            "seasonal naive error is zero on the training window; the series is "
            "perfectly periodic there and MASE has no meaning"
        )

    errors = [abs(a - f) for a, f in zip(actual, forecast)]
    return sum(errors) / len(errors) / scale


def summarise(scores: dict) -> dict:
    """Portfolio summary that reports what it could not score.

    A mean MASE quoted without the count of unscored series is not a result, it
    is an advertisement -- dropping the hard series is exactly what makes the
    average look good.
    """
    scored = {k: v for k, v in scores.items() if v is not None and math.isfinite(v)}
    unscored = [k for k in scores if k not in scored]
    values = sorted(scored.values())
    return {
        "n_scored": len(scored),
        "n_unscored": len(unscored),
        "mean_mase": (sum(values) / len(values)) if values else None,
        "median_mase": _median(values),
        "worse_than_naive": sum(1 for v in values if v > 1.0),
        "best": values[0] if values else None,
        "worst": values[-1] if values else None,
    }


def _median(values: list):
    if not values:
        return None
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2
