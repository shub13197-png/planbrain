"""Rolling-origin backtest harness.

Model-agnostic on purpose: a forecaster is any callable ``(train, horizon) ->
list of length horizon``. That keeps the harness testable without statsforecast
installed, and means the naive baseline and an AutoARIMA are scored by exactly
the same code path rather than by two implementations that might differ.

This is the proof-of-value report the build order asks for. It is only worth
anything if it cannot flatter itself, so:

* Training data never includes the evaluation window, and the MASE scale factor
  is computed from training alone.
* Series that cannot be scored are counted and reported, never dropped.
* The baseline is seasonal naive, not the ``naive_replay`` placeholder that
  netreq uses -- that one has no model and its accuracy must never be quoted.
"""

from dataclasses import dataclass, field

from .metrics import ScoredMean, UndefinedMASE, mase


@dataclass(frozen=True)
class Window:
    """One evaluation fold. Indices are half-open on the right."""

    train_end: int
    test_start: int
    test_end: int


@dataclass
class SeriesResult:
    key: object
    pattern: str
    model: str
    window_scores: list = field(default_factory=list)
    n_unscored_windows: int = 0
    unscored_reason: str = ""

    @property
    def mase(self) -> ScoredMean:
        """Mean MASE across folds, carrying the folds it could not score.

        Returns a ScoredMean rather than a float on purpose: a caller cannot
        take the number without the denominators being in the same object.
        """
        return ScoredMean(
            value=(sum(self.window_scores) / len(self.window_scores))
            if self.window_scores else None,
            n_scored=len(self.window_scores),
            n_unscored=self.n_unscored_windows,
        )


def rolling_origin_windows(n: int, *, horizon: int, n_windows: int, min_train: int) -> list:
    """Expanding-window folds, earliest origin first.

    The last window ends at the final observation, and each earlier one steps
    back by one horizon. Windows whose training span would fall below
    ``min_train`` are dropped rather than shortened -- a fold trained on almost
    nothing produces a MASE that is noise, and noise averaged into a portfolio
    number is indistinguishable from a result.
    """
    windows = []
    for w in range(n_windows, 0, -1):
        test_end = n - (w - 1) * horizon
        test_start = test_end - horizon
        if test_start < min_train:
            continue
        windows.append(Window(train_end=test_start, test_start=test_start, test_end=test_end))
    return windows


def seasonal_naive(train: list, horizon: int, seasonal_period: int) -> list:
    """The baseline: repeat the last full season forward.

    Deliberately implemented here rather than pulled from a library. It is the
    number every model is judged against, so it should be readable in full on
    one screen.
    """
    m = seasonal_period
    if not train:
        return [0.0] * horizon
    if len(train) < m:
        return [float(train[-1])] * horizon
    season = train[-m:]
    return [float(season[t % m]) for t in range(horizon)]


def backtest_series(
    series: list,
    forecaster,
    *,
    key=None,
    pattern: str = "",
    model: str = "",
    horizon: int,
    n_windows: int,
    min_train: int,
    seasonal_period: int,
) -> SeriesResult:
    """Score one series across every usable fold."""
    result = SeriesResult(key=key, pattern=pattern, model=model)
    windows = rolling_origin_windows(
        len(series), horizon=horizon, n_windows=n_windows, min_train=min_train
    )
    if not windows:
        result.unscored_reason = (
            f"history of {len(series)} buckets is too short for {n_windows} folds "
            f"of {horizon} at min_train {min_train}"
        )
        result.n_unscored_windows = n_windows
        return result

    for window in windows:
        train = series[: window.train_end]
        actual = series[window.test_start : window.test_end]
        prediction = forecaster(train, len(actual))
        if len(prediction) != len(actual):
            raise ValueError(
                f"forecaster returned {len(prediction)} values for a horizon of "
                f"{len(actual)}; a short forecast would shift the whole comparison"
            )
        try:
            result.window_scores.append(
                mase(actual, prediction, train, seasonal_period)
            )
        except UndefinedMASE as exc:
            # Counted, not silently skipped: a portfolio mean that quietly
            # excludes its hard series is an advertisement, not a measurement.
            result.n_unscored_windows += 1
            result.unscored_reason = str(exc)
    return result
