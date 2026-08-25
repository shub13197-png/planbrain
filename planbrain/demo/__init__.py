"""Seeded demo dataset for a fake blending plant (build item 2).

``build_demo`` produces the data; ``populate`` writes it. Nothing here bypasses
the fact accessor, so the demo exercises the same sparsify-on-write and
frozen-scenario guards that a real importer will.
"""

from .generate import (
    BomEdge,
    DemoDataset,
    Location,
    Part,
    Resource,
    Routing,
    Truck,
    build_demo,
)
from ..facts.access import write_facts

__all__ = [
    "BomEdge",
    "DemoDataset",
    "Location",
    "Part",
    "Resource",
    "Routing",
    "Truck",
    "build_demo",
    "populate",
]


def populate(con, demo: DemoDataset, scenario_id: int = 0) -> dict:
    """Write every fact in ``demo`` through the accessor.

    Returns rows persisted per (table, measure). That count is lower than the
    number of cells generated because zeros are stored as absent rows, which is
    the sparse rule doing its job rather than data going missing.
    """
    counts = {}
    for (table, measure), facts in demo.facts.items():
        counts[(table, measure)] = write_facts(
            con, table, scenario_id=scenario_id, measure=measure, facts=facts
        )
    return counts
