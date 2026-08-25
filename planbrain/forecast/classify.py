"""Demand pattern classification, which decides what model a series gets.

Syntetos, Boylan & Croston (2005) categorisation on two statistics:

* **ADI** -- average demand interval, buckets per non-zero bucket. High means
  intermittent.
* **CV²** -- squared coefficient of variation of the *non-zero* demand sizes.
  High means erratic magnitude.

Cutoffs 1.32 and 0.49 are the published boundaries, derived from where Croston's
method starts to beat exponential smoothing.

This exists because one model over a mixed portfolio is the standard way to get
a respectable-looking average MASE and a useless plan. AutoETS on a series that
is 80% zeros fits the zeros; Croston on a fast mover throws away the seasonality.
"""

from dataclasses import dataclass

ADI_CUTOFF = 1.32
CV2_CUTOFF = 0.49

SMOOTH, ERRATIC, INTERMITTENT, LUMPY = "smooth", "erratic", "intermittent", "lumpy"
UNUSABLE = "unusable"


@dataclass(frozen=True)
class Profile:
    pattern: str
    adi: float
    cv2: float
    n_nonzero: int
    n_buckets: int


def classify(series: list, min_nonzero: int = 3) -> Profile:
    """Categorise one demand history.

    A series with too few non-zero buckets is ``unusable`` rather than being
    forced into a category. Fitting any model to two observations produces a
    number, and that number is indistinguishable from a real forecast once it is
    in the fact table.
    """
    n = len(series)
    nonzero = [v for v in series if v > 0]

    if n == 0 or len(nonzero) < min_nonzero:
        return Profile(UNUSABLE, float("inf"), 0.0, len(nonzero), n)

    adi = n / len(nonzero)
    mean = sum(nonzero) / len(nonzero)
    variance = sum((v - mean) ** 2 for v in nonzero) / len(nonzero)
    cv2 = variance / (mean ** 2) if mean else 0.0

    intermittent = adi >= ADI_CUTOFF
    erratic = cv2 >= CV2_CUTOFF
    if intermittent and erratic:
        pattern = LUMPY
    elif intermittent:
        pattern = INTERMITTENT
    elif erratic:
        pattern = ERRATIC
    else:
        pattern = SMOOTH

    return Profile(pattern, adi, cv2, len(nonzero), n)


def is_intermittent(pattern: str) -> bool:
    """Whether a pattern needs an intermittent-demand method rather than ETS/ARIMA."""
    return pattern in (INTERMITTENT, LUMPY)
