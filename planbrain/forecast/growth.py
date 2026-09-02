"""The growth overlay: one annual rate, compounded daily.

Deliberately the whole of the arithmetic, in one pure function with no database
and no model in it, because this is the part that has to be obviously right. The
rules it implements were committed in `docs/forecast.md` before the code existed.
"""


def growth_factors(spine, *, anchor, annual_pct: float) -> list:
    """Multiplicative factor per bucket for an annual growth rate.

        factor(bucket) = (1 + g) ** ((bucket - anchor).days / 365)

    ``anchor`` is the last actual observation, not the first planned bucket. The
    horizon usually starts after the history ends, and anchoring at the horizon
    would give the first planned bucket no growth at all despite it sitting some
    way past the level the history establishes -- an error that is small, always
    in the same direction, and invisible in any output.

    365 rather than 365.25 or the working calendar: this is a business
    assumption quoted per year, and a leap day is noise against a number someone
    typed to the nearest percent.

    Buckets before the anchor shrink rather than being clamped to 1.0, so a
    backtest window spanning the anchor does not meet a step change in the
    middle of its evaluation.
    """
    if annual_pct <= -100.0:
        # (1 + g) would be zero or negative, and a negative base under a
        # fractional exponent is complex. Refused rather than clamped, so the
        # caller finds out the input was nonsense.
        raise ValueError(
            f"annual growth of {annual_pct}% is at or below -100%, which is not "
            f"a rate; demand cannot shrink by more than all of itself"
        )
    if annual_pct == 0.0:
        # Exactly 1.0, not 0.9999999999999999. The committed kill condition is
        # that zero growth produces identical numbers, and a float that is
        # merely close would fail it on the multiply.
        return [1.0] * len(spine)

    rate = 1.0 + annual_pct / 100.0
    return [rate ** ((bucket - anchor).days / 365) for bucket in spine]
