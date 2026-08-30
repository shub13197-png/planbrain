"""The lot-sizing baseline the ladder's first rung uses."""

from ..netreq.core import LotSizing


def lot_for_lot() -> LotSizing:
    """The zero-granularity baseline: order exactly what is needed, when needed."""
    return LotSizing(policy="lot_for_lot")
