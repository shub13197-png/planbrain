"""Long-haul fairness ledger and assignment (build item 9).

Pre-commitments -- the fact_fleet measures, Jain fairness with thresholds,
zero-offset handling and feasibility filters -- are in docs/haulplan.md, written
before any assignment ran.
"""

from .fairness import (
    ACCEPTABLE,
    FAIR,
    ceiling,
    coefficient_of_variation,
    equivalent_equal_share,
    jain_index,
    spread,
    verdict,
)
from .ledger import MEASURES, Ledger, Trip, Truck

__all__ = [
    "ACCEPTABLE",
    "FAIR",
    "MEASURES",
    "ceiling",
    "Ledger",
    "Trip",
    "Truck",
    "coefficient_of_variation",
    "equivalent_equal_share",
    "jain_index",
    "spread",
    "verdict",
]
