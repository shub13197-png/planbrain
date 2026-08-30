"""Fairness measurement for the long-haul ledger.

Metric and thresholds committed in `docs/haulplan.md` before any assignment was
run. Pure arithmetic; no fleet, no dates, no database.
"""

import math

#: Committed thresholds. J >= FAIR needs no action; below ACCEPTABLE the ledger
#: should be visibly correcting.
FAIR = 0.95
ACCEPTABLE = 0.85


def jain_index(values):
    """Jain's fairness index over a distribution. 1.0 is perfect equality.

        J(x) = (sum x)^2 / (n * sum x^2)

    Chosen over Gini because it reads directly: J = 0.8 across ten trucks means
    the allocation is as fair as an equal split among eight of them, which is a
    sentence a fleet manager can act on.

    Returns **None** for an empty fleet or one that has driven nothing. Not 1.0:
    a fleet with no kilometres is unmeasured rather than perfectly fair, and
    reporting perfection would flatter every empty run — the same reason a fill
    rate over zero demand is None rather than 100%.
    """
    values = [float(v) for v in values]
    if not values:
        return None
    total = sum(values)
    if total == 0:
        return None
    squares = sum(v * v for v in values)
    return (total * total) / (len(values) * squares)


def spread(values) -> float:
    """Max minus min. Reported alongside Jain because the index is
    scale-invariant and can hide a large absolute gap on a big fleet."""
    values = [float(v) for v in values]
    return max(values) - min(values) if values else 0.0


def coefficient_of_variation(values):
    """Standard deviation over mean. None when the mean is zero."""
    values = [float(v) for v in values]
    if not values:
        return None
    mean = sum(values) / len(values)
    if mean == 0:
        return None
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance) / mean


def ceiling(opening, work_available: float):
    """Best Jain index any assignment could reach, given the work there is.

    Water-filling: pour the available long-haul kilometres into the trucks
    furthest behind until they level. Deliberately **ignores** the 420 km trip
    granularity and the one-trip-per-truck-per-bucket limit, so it is an
    unreachable upper bound rather than an optimum.

    That is what makes it useful. If a greedy rule sits close to this ceiling,
    no optimiser has room to do better on fairness alone, and a solver would be
    reporting an improvement over a baseline that was never the constraint. It
    turns "greedy is probably enough" from an assertion into a measurement.
    """
    values = sorted(float(v) for v in opening)
    if not values:
        return None
    remaining = float(work_available)

    for i in range(1, len(values) + 1):
        if i == len(values):
            level = sum(values) / len(values) + remaining / len(values)
            values = [level] * len(values)
            break
        step = (values[i] - values[i - 1]) * i
        if remaining < step:
            level = values[i - 1] + remaining / i
            values[:i] = [level] * i
            break
        for j in range(i):
            values[j] = values[i]
        remaining -= step

    return jain_index(values)


def verdict(index) -> str:
    """Plain-language reading of the index against the committed thresholds."""
    if index is None:
        return "unmeasured"
    if index >= FAIR:
        return "fair"
    if index >= ACCEPTABLE:
        return "acceptable"
    return "unfair"


def equivalent_equal_share(index, n: int):
    """How many trucks an equal split among would be equally fair.

    The property that makes the index actionable. J = 0.8 over ten trucks
    reports 8.0.
    """
    if index is None or n <= 0:
        return None
    return index * n
