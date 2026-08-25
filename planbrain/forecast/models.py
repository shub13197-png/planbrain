"""Model selection: which forecaster a series gets, and why.

One model over a mixed portfolio is the standard way to produce a respectable
average MASE and a useless plan. AutoETS on a series that is 80% zeros fits the
zeros; Croston on a fast mover throws away the weekly shape. So the pattern
decides:

| pattern | model | reason |
|---|---|---|
| smooth, erratic | `AutoETS` | frequent demand with a weekly shape worth fitting |
| intermittent | `CrostonOptimized` | the classic method for regular gaps, even sizes |
| lumpy | `TSB` | rare *and* variable; TSB updates demand probability every bucket |
| unusable | seasonal naive | too few observations to fit anything honestly |

**TSB for lumpy is partly an obsolescence choice.** Croston does not decay when
demand stops, so a discontinued SKU keeps forecasting its old rate forever. TSB
updates the probability every bucket and decays. The demo carries 11 mid-history
discontinuations for exactly this reason. If a customer's portfolio turns out to
discontinue often, TSB should take the intermittent class too -- that is a
tuning decision on real data, not something to settle here.

Two adjustments applied to every model's output, both standard and both
deliberate:

* **Clamped at zero.** ETS extrapolates freely and will predict negative demand
  on a declining series. Negative demand is not a plan number, it is a bug that
  nets backwards through MRP and manufactures supply.
* **Non-finite output falls back to the naive baseline**, and the fallback is
  recorded. A NaN written to the fact table would poison every downstream sum
  silently; `qty` has no NOT-NaN constraint and never will.
"""

from .backtest import seasonal_naive
from .classify import ERRATIC, INTERMITTENT, LUMPY, SMOOTH, UNUSABLE

MODEL_FOR_PATTERN = {
    SMOOTH: "AutoETS",
    ERRATIC: "AutoETS",
    INTERMITTENT: "CrostonOptimized",
    LUMPY: "TSB",
    UNUSABLE: "SeasonalNaive",
}



class Fallbacks:
    """Counts of forecasts that could not be produced by the chosen model.

    Reported rather than swallowed: a run where a third of the portfolio quietly
    fell back to the naive baseline is a different result from one where none
    did, and the MASE alone will not say which happened.
    """

    def __init__(self):
        self.non_finite = 0
        self.model_error = 0

    def total(self) -> int:
        return self.non_finite + self.model_error

    def as_dict(self) -> dict:
        return {"non_finite": self.non_finite, "model_error": self.model_error}


def make_forecaster(pattern: str, *, season_length: int, fallbacks: Fallbacks = None):
    """Return ``(model_name, forecaster)`` for a demand pattern.

    The forecaster is ``(train, horizon) -> list[float]``, the same signature the
    backtest harness uses, so the model that scores is literally the model that
    runs in production.
    """
    name = MODEL_FOR_PATTERN.get(pattern, "SeasonalNaive")
    fallbacks = fallbacks if fallbacks is not None else Fallbacks()

    if name == "SeasonalNaive":
        return name, lambda train, h: _clamp(seasonal_naive(train, h, season_length))

    def forecaster(train, horizon):
        try:
            values = _fit_and_predict(name, train, horizon, season_length)
        except Exception:
            # A single pathological series must not kill a 200-SKU run, but the
            # substitution has to show up in the report.
            fallbacks.model_error += 1
            return _clamp(seasonal_naive(train, horizon, season_length))

        if not all(_is_finite(v) for v in values):
            fallbacks.non_finite += 1
            return _clamp(seasonal_naive(train, horizon, season_length))
        return _clamp(values)

    return name, forecaster


def _fit_and_predict(name: str, train, horizon: int, season_length: int) -> list:
    """Fit one statsforecast model to one series.

    Imported lazily so that the pure metric and classification code -- and its
    tests -- do not require statsforecast to be installed.
    """
    import numpy as np
    from statsforecast.models import AutoETS, CrostonOptimized, TSB

    y = np.asarray(train, dtype=np.float64)
    if name == "AutoETS":
        model = AutoETS(season_length=season_length)
    elif name == "CrostonOptimized":
        model = CrostonOptimized()
    elif name == "TSB":
        # Smoothing constants for demand probability and size. 0.2 is the usual
        # starting point in the literature; tuning these needs real data.
        model = TSB(alpha_d=0.2, alpha_p=0.2)
    else:
        raise ValueError(f"unknown model {name!r}")

    return [float(v) for v in model.forecast(y=y, h=horizon)["mean"]]


def _clamp(values) -> list:
    """Demand cannot be negative. A negative forecast nets backwards through MRP
    and manufactures supply out of nothing."""
    return [v if v > 0 else 0.0 for v in values]


def _is_finite(value) -> bool:
    return value == value and value not in (float("inf"), float("-inf"))
