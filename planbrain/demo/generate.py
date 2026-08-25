"""A seeded demo dataset: a fake lubricant blending plant.

Build item 2. Items 3-5 are developed and tested against this, so the generator
*is* the dataset -- it is deterministic from a seed and nothing is committed as
data. Rebuild it anywhere with ``build_demo(seed=7)``.

The history is deliberately messy. A forecast backtest against clean synthetic
demand proves nothing: it would make a naive mean look excellent and hide
exactly the failure modes Croston, TSB and IMAPA exist for. So the generator
injects, on purpose:

* intermittent and lumpy series alongside smooth fast movers
* SKUs launched partway through history, and SKUs discontinued partway through
* stockout windows -- zero demand that is censored *supply*, not real demand
* promotional spikes and occasional outliers
* a closed day every Sunday, which produces structural zeros

Structural zeros and censored zeros look identical in the fact table. That is
not an oversight: telling them apart is the forecaster's problem, and pretending
the dataset knows the difference would make item 4 easier than reality.
"""

import random
from dataclasses import dataclass, field
from datetime import date, timedelta

from planbrain.facts.access import Fact

PLANT_ID = 1
DEPOTS = ((11, "Depot Delhi"), (12, "Depot Jaipur"), (13, "Depot Ludhiana"))

HISTORY_START = date(2025, 1, 1)
HISTORY_END = date(2026, 6, 30)
HORIZON_DAYS = 90

N_RAW, N_INTERMEDIATE, N_FINISHED = 40, 40, 120

BASE_OILS = ("SN-150", "SN-500", "BS-150", "PAO-6", "Group III 4cSt")
ADDITIVES = ("ZDDP", "Detergent OB", "Dispersant PIB", "VII OCP", "Pour Depressant")
GRADES = ("15W-40", "20W-50", "5W-30", "10W-30", "80W-90", "68 HYD", "46 HYD")
PACKS = ("1L", "5L", "20L", "26L", "210L")


@dataclass(frozen=True)
class Location:
    loc_id: int
    name: str
    kind: str  # 'plant' | 'depot'


@dataclass(frozen=True)
class Part:
    sku_id: int
    name: str
    level: str  # 'raw' | 'intermediate' | 'finished'
    lead_time_days: int
    safety_stock: float
    lot_policy: str
    lot_qty: float


@dataclass(frozen=True)
class BomEdge:
    parent_sku_id: int
    child_sku_id: int
    qty_per: float


@dataclass(frozen=True)
class Resource:
    resource_id: int
    name: str
    kind: str


@dataclass(frozen=True)
class Routing:
    sku_id: int
    resource_id: int
    hours_per_unit: float
    setup_hours: float


@dataclass(frozen=True)
class Truck:
    truck_id: int
    plate: str
    capacity_kg: float


@dataclass
class DemoDataset:
    """Reference data plus the facts to write. Reference data stands in for what
    the importer would pull from InvenTree, which core is read-only."""

    locations: list
    parts: list
    bom: list
    resources: list
    routings: list
    trucks: list
    history_start: date
    history_end: date
    horizon_start: date
    horizon_end: date
    facts: dict = field(default_factory=dict)
    launched_mid_history: list = field(default_factory=list)
    discontinued_mid_history: list = field(default_factory=list)
    stockout_windows: list = field(default_factory=list)


def build_demo(seed: int = 7) -> DemoDataset:
    """Generate the whole dataset deterministically from ``seed``."""
    rng = random.Random(seed)

    locations = [Location(PLANT_ID, "Plant Bhiwadi", "plant")] + [
        Location(i, name, "depot") for i, name in DEPOTS
    ]
    parts, bom = _build_parts_and_bom(rng)
    resources = _build_resources()
    routings = _build_routings(rng, parts, resources)
    trucks = [
        Truck(500 + i, f"RJ14-{2000 + i * 137:04d}", rng.choice([9000.0, 16000.0, 25000.0]))
        for i in range(1, 13)
    ]

    horizon_start = HISTORY_END + timedelta(days=1)
    horizon_end = horizon_start + timedelta(days=HORIZON_DAYS - 1)

    demo = DemoDataset(
        locations=locations,
        parts=parts,
        bom=bom,
        resources=resources,
        routings=routings,
        trucks=trucks,
        history_start=HISTORY_START,
        history_end=HISTORY_END,
        horizon_start=horizon_start,
        horizon_end=horizon_end,
    )

    demo.facts[("fact_supply_demand", "demand_actual")] = _build_demand(rng, demo)
    demo.facts[("fact_supply_demand", "scheduled_receipt")] = _build_receipts(rng, demo)
    demo.facts[("fact_capacity", "capacity_avail_hours")] = _build_capacity(rng, demo)
    return demo


# --------------------------------------------------------------------------
# reference data
# --------------------------------------------------------------------------

def _build_parts_and_bom(rng):
    parts, bom = [], []

    for i in range(N_RAW):
        oil = BASE_OILS[i % len(BASE_OILS)] if i % 2 == 0 else ADDITIVES[i % len(ADDITIVES)]
        parts.append(Part(
            sku_id=1000 + i,
            name=f"{oil} lot {i:02d}",
            level="raw",
            # Base oils come by tanker on long lead times; additives are imported.
            lead_time_days=rng.choice([14, 21, 30, 45]),
            safety_stock=float(rng.choice([2000, 5000, 8000])),
            lot_policy="fixed_qty",
            lot_qty=float(rng.choice([5000, 10000, 20000])),
        ))

    for i in range(N_INTERMEDIATE):
        parts.append(Part(
            sku_id=2000 + i,
            name=f"Blend {GRADES[i % len(GRADES)]} batch-{i:02d}",
            level="intermediate",
            lead_time_days=rng.choice([1, 2, 3]),
            safety_stock=0.0,
            lot_policy="lot_for_lot",
            lot_qty=0.0,
        ))
        # Each blend draws 2-4 raws: one base oil plus additives.
        for child in rng.sample(range(N_RAW), rng.randint(2, 4)):
            bom.append(BomEdge(2000 + i, 1000 + child, round(rng.uniform(0.05, 0.7), 3)))

    for i in range(N_FINISHED):
        grade = GRADES[i % len(GRADES)]
        pack = PACKS[i % len(PACKS)]
        parts.append(Part(
            sku_id=3000 + i,
            name=f"{grade} {pack}",
            level="finished",
            lead_time_days=rng.choice([2, 3, 5]),
            safety_stock=float(rng.choice([0, 200, 500, 1000])),
            lot_policy=rng.choice(["fixed_qty", "min_max", "lot_for_lot"]),
            lot_qty=float(rng.choice([500, 1000, 2000])),
        ))
        # A pack size is filled from one blend, occasionally two on shared lines.
        for child in rng.sample(range(N_INTERMEDIATE), rng.randint(1, 2)):
            bom.append(BomEdge(3000 + i, 2000 + child, round(rng.uniform(0.8, 1.05), 3)))

    return parts, bom


def _build_resources():
    return [
        Resource(101, "Blender A 20kL", "blend"),
        Resource(102, "Blender B 10kL", "blend"),
        Resource(103, "Blender C 5kL", "blend"),
        Resource(104, "Fill Line 1 small pack", "fill"),
        Resource(105, "Fill Line 2 drum", "fill"),
        Resource(106, "QC lab", "qc"),
    ]


def _build_routings(rng, parts, resources):
    blenders = [r for r in resources if r.kind == "blend"]
    fillers = [r for r in resources if r.kind == "fill"]
    routings = []
    for part in parts:
        if part.level == "intermediate":
            resource = blenders[part.sku_id % len(blenders)]
            routings.append(Routing(
                part.sku_id, resource.resource_id,
                hours_per_unit=round(rng.uniform(0.0008, 0.0025), 5),
                # Grade changeover: the flush between incompatible blends.
                setup_hours=round(rng.uniform(1.0, 3.5), 2),
            ))
        elif part.level == "finished":
            resource = fillers[part.sku_id % len(fillers)]
            routings.append(Routing(
                part.sku_id, resource.resource_id,
                hours_per_unit=round(rng.uniform(0.0005, 0.004), 5),
                setup_hours=round(rng.uniform(0.3, 1.2), 2),
            ))
    return routings


# --------------------------------------------------------------------------
# demand history
# --------------------------------------------------------------------------

PATTERNS = (
    ("smooth", 0.22),
    ("erratic", 0.18),
    ("seasonal", 0.15),
    ("intermittent", 0.27),
    ("lumpy", 0.18),
)


def _build_demand(rng, demo):
    span = (demo.history_end - demo.history_start).days + 1
    days = [demo.history_start + timedelta(days=i) for i in range(span)]
    finished = [p for p in demo.parts if p.level == "finished"]
    depots = [loc.loc_id for loc in demo.locations if loc.kind == "depot"]

    facts = []
    for part in finished:
        # A pack is not stocked everywhere; national coverage would be unrealistic
        # and would hide the sparse-series problem entirely.
        for loc_id in rng.sample(depots, rng.randint(1, len(depots))):
            pattern = _weighted_choice(rng, PATTERNS)
            series = _series_for(rng, pattern, span, demo, part, loc_id)
            facts.extend(
                Fact((part.sku_id, loc_id), day, qty)
                for day, qty in zip(days, series)
                if qty > 0
            )
    return facts


def _series_for(rng, pattern, span, demo, part, loc_id):
    base = rng.uniform(20, 400) if pattern in ("smooth", "seasonal") else rng.uniform(5, 120)
    values = [0.0] * span

    for t in range(span):
        day = demo.history_start + timedelta(days=t)
        if day.weekday() == 6:  # plant and depots closed Sunday: structural zero
            continue

        if pattern == "smooth":
            qty = base * rng.lognormvariate(0, 0.20)
        elif pattern == "erratic":
            qty = base * rng.lognormvariate(0, 0.75)
        elif pattern == "seasonal":
            # Lubricant demand lifts before monsoon servicing and in winter.
            season = 1 + 0.45 * _annual_cycle(day)
            qty = base * season * rng.lognormvariate(0, 0.25)
        elif pattern == "intermittent":
            qty = base * rng.lognormvariate(0, 0.4) if rng.random() < 0.22 else 0.0
        else:  # lumpy: rare, and huge when it happens
            qty = base * rng.lognormvariate(0, 1.1) if rng.random() < 0.12 else 0.0

        if rng.random() < 0.015:  # promotion or a bulk order
            qty *= rng.uniform(2.5, 6.0)
        values[t] = qty

    _apply_lifecycle(rng, values, span, demo, part, loc_id)
    return [int(round(v)) for v in values]


def _apply_lifecycle(rng, values, span, demo, part, loc_id):
    """Launches, discontinuations and stockouts, recorded so tests can find them."""
    key = (part.sku_id, loc_id)
    roll = rng.random()

    if roll < 0.08:  # launched partway through history
        launch = rng.randint(span // 5, span // 2)
        for t in range(launch):
            values[t] = 0.0
        demo.launched_mid_history.append((key, launch))
    elif roll < 0.14:  # discontinued partway through
        stop = rng.randint(span // 2, span - span // 6)
        for t in range(stop, span):
            values[t] = 0.0
        demo.discontinued_mid_history.append((key, stop))

    if rng.random() < 0.12:  # stockout: censored supply, not absent demand
        start = rng.randint(0, span - 25)
        length = rng.randint(5, 20)
        for t in range(start, min(start + length, span)):
            values[t] = 0.0
        demo.stockout_windows.append((key, start, length))


def _annual_cycle(day):
    import math

    return math.sin(2 * math.pi * (day.timetuple().tm_yday / 365.25))


def _weighted_choice(rng, weighted):
    r = rng.random() * sum(w for _, w in weighted)
    for value, weight in weighted:
        r -= weight
        if r <= 0:
            return value
    return weighted[-1][0]


# --------------------------------------------------------------------------
# supply side and capacity
# --------------------------------------------------------------------------

def _build_receipts(rng, demo):
    """Open purchase orders for raw materials, landing over the forward horizon."""
    raws = [p for p in demo.parts if p.level == "raw"]
    facts = []
    for part in raws:
        for _ in range(rng.randint(0, 3)):
            offset = rng.randint(0, HORIZON_DAYS - 1)
            facts.append(Fact(
                (part.sku_id, PLANT_ID),
                demo.horizon_start + timedelta(days=offset),
                float(rng.choice([5000, 10000, 20000, 24000])),
            ))
    return facts


def _build_capacity(rng, demo):
    """Available hours per resource over the forward horizon.

    Two shifts on weekdays, one on Saturday, closed Sunday. The Sunday zeros are
    dropped on write and reappear on read -- a small live demonstration that the
    sparse round trip works on a grain other than supply and demand.
    """
    facts = []
    for resource in demo.resources:
        for i in range(HORIZON_DAYS):
            day = demo.horizon_start + timedelta(days=i)
            if day.weekday() == 6:
                hours = 0.0
            elif day.weekday() == 5:
                hours = 8.0
            else:
                hours = 16.0
            if hours and rng.random() < 0.04:  # planned maintenance
                hours = round(hours / 2, 1)
            facts.append(Fact((resource.resource_id,), day, hours))
    return facts
