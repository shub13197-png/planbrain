"""Forecast where the signal is, then bring the answer back down.

**Why this exists.** Ninety-six per cent of the manufacturing portfolio and
ninety-three per cent of the retail one classify as lumpy at daily grain, which
routes them to TSB, which emits a constant rate. That is the correct thing for
TSB to do -- a series that is mostly zeros carries no reliable day-to-day timing
signal -- but it means years of history reach the planner as one number per SKU.
Seasons, occasions and trend are all discarded.

The structure is still in the data; the bucket destroys it. Summing the same
series into four-weekly buckets, the same classifier finds a forecastable
pattern in 44% of manufacturing series and 52% of retail ones. And a yearly
cycle cannot be represented at daily grain here at all, because the only
seasonal period the product ever passes is ``WorkingCalendar.seasonal_period``,
which is 7.

So: aggregate the series until it is forecastable, fit there -- where a year is
a period of 13 rather than 365 -- and disaggregate the answer back to daily.

Algorithm: temporal aggregation for intermittent demand, the ADIDA family.
Nikolopoulos, Syntetos, Boylan, Petropoulos & Assimakopoulos, "An aggregate
-disaggregate intermittent demand approach (ADIDA) to forecasting", *Journal of
the Operational Research Society* 62(3), 2011. Single aggregation level chosen
per series, equal-weight disaggregation.
"""

from .classify import classify
from .models import make_forecaster

#: Bucket widths tried, in days, narrowest first: weekly, fortnightly,
#: four-weekly, quarterly. Stopping at 91 is deliberate -- past a quarter a
#: series with a couple of years of history has too few buckets left to fit
#: anything, and a season found in six observations is noise with a period.
BUCKET_WIDTHS = (7, 14, 28, 91)

#: Patterns a model can fit structure to. `intermittent` and `lumpy` are exactly
#: the ones whose models return a constant, which is what this module exists to
#: escape.
FORECASTABLE = ("smooth", "erratic")

#: Cycles of history required before a season is fitted at all. Two is the
#: arithmetic minimum for a repeat and is not enough to tell one from noise.
#:
#: **Measured, not chosen for tidiness.** The manufacturer has six years, which
#: is 75 four-weekly buckets against a 13-bucket year, and aggregation helps
#: there. The retailer has two, which is 26 buckets against the same year, and
#: aggregation makes it *worse* -- 0.705 against 0.717 at matched stock. Three
#: cycles excludes the retailer and keeps the manufacturer.
MIN_CYCLES = 3


def aggregate(series, bucket_days: int) -> list:
    """Sum demand into consecutive buckets, oldest first.

    Demand is conserved exactly -- a rollup that loses or invents units would
    move every number downstream of it. A short final bucket is kept rather than
    dropped, because dropping it would silently discard the most recent demand.
    """
    if bucket_days < 1:
        raise ValueError(f"bucket width must be at least one day, got {bucket_days}")
    values = [float(v) for v in series]
    return [sum(values[i:i + bucket_days]) for i in range(0, len(values), bucket_days)]


def choose_bucket(series) -> int:
    """The narrowest bucket at which this series becomes forecastable.

    Returns 1 when the daily series already is, and 1 again when no width helps
    -- in both cases the caller should use the ordinary daily model rather than
    pay for resolution it cannot use.

    **Narrowest, not smoothest.** Every series looks smoother in a wider bucket,
    so choosing by smoothness would always choose the widest and throw away
    resolution to buy a tautology.
    """
    values = [float(v) for v in series]
    if not values:
        return 1
    if classify(values).pattern in FORECASTABLE:
        return 1
    for width in BUCKET_WIDTHS:
        rolled = aggregate(values, width)
        # A year in buckets of this width, seen MIN_CYCLES times over.
        if len(rolled) < MIN_CYCLES * max(1, round(365 / width)):
            continue
        if classify(rolled).pattern in FORECASTABLE:
            return width
    return 1


def aggregated_forecaster(train, horizon: int, *, bucket_days: int = None,
                          season_length: int = None) -> list:
    """Forecast ``horizon`` daily buckets by fitting a coarser series.

    Falls back to the ordinary daily forecaster whenever aggregation will not
    help, so a caller may use this unconditionally.

    The seasonal period is derived from the bucket width rather than passed in:
    at four-weekly buckets a year is 13 of them, which is the entire point --
    a yearly cycle is unrepresentable at daily grain and ordinary at this one.
    """
    values = [float(v) for v in train]
    width = bucket_days if bucket_days is not None else choose_bucket(values)

    if width <= 1:
        _, daily = make_forecaster(classify(values).pattern, season_length=7)
        return list(daily(values, horizon))

    rolled = aggregate(values, width)
    # A year, in buckets of this width. One means the width cannot carry a
    # season and the model is asked for a level and a trend instead.
    period = season_length if season_length is not None else max(1, round(365 / width))

    _, coarse = make_forecaster(classify(rolled).pattern, season_length=period)

    # Enough buckets to cover the horizon, plus one for the partial bucket the
    # horizon almost never starts on.
    predicted = list(coarse(rolled, horizon // width + 2))

    # Spread each bucket evenly across its days. Even weighting is what ADIDA
    # specifies and is the honest default: the premise of the whole module is
    # that this series carries no reliable within-bucket timing signal, so
    # inventing a daily shape would invent exactly what we established is absent.
    daily_values = []
    for bucket in predicted:
        share = max(0.0, bucket) / width
        daily_values.extend([share] * width)

    return daily_values[:horizon]
