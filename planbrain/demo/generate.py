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
from planbrain.working_calendar import SIX_DAY_WEEK

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
    #: Standard cost per unit, rolled up through the BOM. See docs/unit-costs.md.
    #: Zero until _cost_parts() runs, which needs the BOM to exist first.
    unit_cost: float = 0.0


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
    #: Every demo truck is on the road. Off-road maintenance would change the
    #: fairness result, and the demo fleet rule committed in docs/haulplan.md
    #: does not include it, so it is not invented here.
    available: bool = True


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
    #: Reference data like parts and locations. Everything that needs a seasonal
    #: period derives it from here rather than assuming a week.
    calendar: object = SIX_DAY_WEEK
    facts: dict = field(default_factory=dict)
    #: Opening stock at horizon_start, keyed (sku_id, loc_id). A stock position
    #: at a single instant, not a time-phased series -- in production this comes
    #: from InvenTree stock, so it is deliberately NOT written as fact rows.
    stock_on_hand: dict = field(default_factory=dict)
    launched_mid_history: list = field(default_factory=list)
    discontinued_mid_history: list = field(default_factory=list)
    stockout_windows: list = field(default_factory=list)
    #: (key, total multiplicative change across the history). Sustained trend,
    #: which is what makes a set-once reorder point go stale -- lifecycle events
    #: alone do not.
    drifting_series: list = field(default_factory=list)
    #: Sized hours per full working day, per resource. See docs/capacity-sizing.md.
    resource_day_hours: dict = field(default_factory=dict)
    #: Dispatch trips over the forward horizon. See docs/haulplan.md.
    trips: list = field(default_factory=list)
    #: Opening year-to-date long-haul km per truck. A scalar position, like
    #: opening stock: in production it comes from the system of record.
    truck_ytd_long_haul_km: dict = field(default_factory=dict)


def build_demo(seed: int = 7) -> DemoDataset:
    """Generate the whole dataset deterministically from ``seed``."""
    rng = random.Random(seed)

    locations = [Location(PLANT_ID, "Plant Bhiwadi", "plant")] + [
        Location(i, name, "depot") for i, name in DEPOTS
    ]
    parts, bom = _build_parts_and_bom(rng)
    parts = _cost_parts(parts, bom)
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
    demo.resource_day_hours = _size_resources(demo)
    demo.facts[("fact_capacity", "capacity_avail_hours")] = _build_capacity(rng, demo)
    demo.stock_on_hand = _build_stock(rng, demo)
    demo.trips = _build_trips(rng, demo)
    demo.truck_ytd_long_haul_km = _build_fleet_ledger(rng, demo)
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


#: docs/unit-costs.md, all committed before any cost was computed. Synthetic
#: currency; a real deployment reads every one of these from the system of record.
BASE_OIL_COST = 90.0
ADDITIVE_COST = 350.0
CONVERSION_ADDER = 8.0
PACKAGING_COST = {"1L": 12.0, "5L": 22.0, "20L": 45.0, "26L": 52.0, "210L": 180.0}
CAPACITY_COST_PER_HOUR = 1500.0


def _cost_parts(parts, bom):
    """Standard cost roll-up: raws priced by type, everything else from its BOM.

    Runs after the BOM exists because an intermediate's cost is the sum of its
    children's. Parents are costed after children, which the level order below
    guarantees -- the demo BOM is exactly three deep, so no general topological
    sort is needed and pretending otherwise would be over-engineering.
    """
    cost = {}
    for part in parts:
        if part.level == "raw":
            # Base oils occupy the even ids; additives the odd. Same split the
            # naming uses, so a part called ZDDP is priced as an additive.
            is_base_oil = (part.sku_id - 1000) % 2 == 0
            cost[part.sku_id] = BASE_OIL_COST if is_base_oil else ADDITIVE_COST

    children = {}
    for edge in bom:
        children.setdefault(edge.parent_sku_id, []).append(edge)

    for level in ("intermediate", "finished"):
        for part in parts:
            if part.level != level:
                continue
            rolled = sum(
                cost.get(edge.child_sku_id, 0.0) * edge.qty_per
                for edge in children.get(part.sku_id, ())
            )
            if level == "intermediate":
                cost[part.sku_id] = rolled + CONVERSION_ADDER
            else:
                pack = part.name.rsplit(" ", 1)[-1]
                if pack not in PACKAGING_COST:
                    # Defaulting to zero would make packaging free for every
                    # finished good the moment the naming convention shifted,
                    # and the roll-up would still look plausible.
                    raise ValueError(
                        f"{part.name!r} has no recognised pack size; expected one "
                        f"of {sorted(PACKAGING_COST)}"
                    )
                cost[part.sku_id] = rolled + PACKAGING_COST[pack]

    return [
        Part(
            sku_id=p.sku_id, name=p.name, level=p.level,
            lead_time_days=p.lead_time_days, safety_stock=p.safety_stock,
            lot_policy=p.lot_policy, lot_qty=p.lot_qty,
            unit_cost=round(cost.get(p.sku_id, 0.0), 2),
        )
        for p in parts
    ]


def _build_resources():
    return [
        # Work centres, not individual machines. A centre may hold parallel
        # equipment, so its available hours can exceed 24 in a day -- see
        # docs/capacity-sizing.md. Which vessel does which job is finite
        # scheduling, which rough-cut deliberately does not know.
        Resource(101, "Blending, large batch", "blend"),
        Resource(102, "Blending, medium batch", "blend"),
        Resource(103, "Blending, small batch", "blend"),
        Resource(104, "Filling, small pack", "fill"),
        Resource(105, "Filling, drum", "fill"),
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
        if not SIX_DAY_WEEK.is_working(day):  # closed day: structural zero
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

    _apply_drift(rng, values, span, demo, part, loc_id)
    _apply_lifecycle(rng, values, span, demo, part, loc_id)
    return [int(round(v)) for v in values]


#: Share of series carrying a sustained trend, and how far it can travel across
#: the whole history. Drift is what makes a set-once reorder point go stale;
#: launches and discontinuations do not, because they are visible as steps.
DRIFT_SHARE = 0.35
DRIFT_RANGE = (-0.55, 1.20)


def _apply_drift(rng, values, span, demo, part, loc_id):
    """Multiply the series by a trend that grows or decays across the history."""
    if rng.random() >= DRIFT_SHARE:
        return
    total_change = rng.uniform(*DRIFT_RANGE)
    for t in range(span):
        values[t] *= 1.0 + total_change * (t / max(1, span - 1))
    demo.drifting_series.append(((part.sku_id, loc_id), round(total_change, 3)))


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


#: docs/haulplan.md, committed before generation. Road distances from Bhiwadi.
DEPOT_DISTANCE_KM = {11: 80.0, 12: 180.0, 13: 420.0}
LONG_HAUL_KM = 250.0
TRUCKLOAD_KG = 12_000.0
KG_PER_LITRE = 0.9
REPLENISH_EVERY_DAYS = 7

#: Opening ledger band. Deliberately skewed -- a fairness ledger exists because
#: fleets drift out of balance, and a level demo would have nothing to correct.
YTD_BAND_KM = (8_000.0, 46_000.0)


def _build_trips(rng, demo):
    """Dispatch trips from plant to depots over the forward horizon.

    Demand each depot takes, converted to truckloads on a weekly replenishment
    cadence. Distances and the long-haul threshold are committed in
    docs/haulplan.md; nothing here is derived from a fairness outcome.
    """
    from planbrain.haulplan.ledger import Trip

    horizon_days = (demo.horizon_end - demo.horizon_start).days + 1
    recent_cutoff = demo.horizon_start - timedelta(days=horizon_days)

    litres = {}
    for fact in demo.facts[("fact_supply_demand", "demand_actual")]:
        if fact.bucket_date >= recent_cutoff:
            litres[fact.keys[1]] = litres.get(fact.keys[1], 0.0) + fact.qty

    trips, trip_id = [], 900
    for bucket in range(0, horizon_days, REPLENISH_EVERY_DAYS):
        day = demo.horizon_start + timedelta(days=bucket)
        if not demo.calendar.is_working(day):
            continue
        for loc_id, distance in sorted(DEPOT_DISTANCE_KM.items()):
            weekly_kg = litres.get(loc_id, 0.0) * KG_PER_LITRE * REPLENISH_EVERY_DAYS / horizon_days
            loads = int(weekly_kg // TRUCKLOAD_KG)
            remainder = weekly_kg - loads * TRUCKLOAD_KG
            for _ in range(loads):
                trips.append(Trip(trip_id, bucket, distance, TRUCKLOAD_KG,
                                  distance >= LONG_HAUL_KM))
                trip_id += 1
            if remainder > TRUCKLOAD_KG * 0.2:
                trips.append(Trip(trip_id, bucket, distance, round(remainder, 1),
                                  distance >= LONG_HAUL_KM))
                trip_id += 1
    return trips


def _build_fleet_ledger(rng, demo):
    """Opening year-to-date long-haul kilometres, deliberately uneven."""
    return {
        truck.truck_id: round(rng.uniform(*YTD_BAND_KM), 0)
        for truck in demo.trucks
    }


def _build_stock(rng, demo):
    """Opening stock at horizon_start, as a snapshot rather than fact rows.

    Sized off recent demand so the netting in item 3 has something realistic to
    consume: a few weeks of cover for most SKUs, nothing at all for some, and
    the occasional overstock. Raw materials are held at the plant only.
    """
    recent = {}
    cutoff = demo.horizon_start - timedelta(days=28)
    for fact in demo.facts[("fact_supply_demand", "demand_actual")]:
        if fact.bucket_date >= cutoff:
            recent[fact.keys] = recent.get(fact.keys, 0.0) + fact.qty

    stock = {}
    for key, total in recent.items():
        daily = total / 28.0
        cover = rng.choice([0.0, 0.0, 7.0, 14.0, 21.0, 45.0])  # some SKUs are simply out
        stock[key] = round(daily * cover, 1)

    for part in demo.parts:
        if part.level == "raw":
            stock[(part.sku_id, PLANT_ID)] = float(rng.choice([0, 4000, 12000, 30000]))
        elif part.level == "intermediate":
            stock[(part.sku_id, PLANT_ID)] = float(rng.choice([0, 0, 500, 2000]))
    return stock


#: See docs/capacity-sizing.md. Both committed before any capacity was computed.
CAMPAIGN_CYCLE_DAYS = 14
TARGET_UTILISATION = 0.82

#: Relative capacity of a full working day, by weekday. Two shifts Mon-Fri, one
#: on Saturday, closed Sunday.
DAY_SHAPE = (1.0, 1.0, 1.0, 1.0, 1.0, 0.5, 0.0)


def _size_resources(demo) -> dict:
    """Hours per full working day per resource, sized from DEMAND not from load.

    Steps 1-6 of docs/capacity-sizing.md. Nothing here reads a plan, a load
    figure, or anything netreq produced -- that is the whole point of the rule.
    """
    span_days = (demo.history_end - demo.history_start).days + 1
    annual = {}
    for fact in demo.facts[("fact_supply_demand", "demand_actual")]:
        sku_id = fact.keys[0]
        annual[sku_id] = annual.get(sku_id, 0.0) + fact.qty * 365.0 / span_days

    children = {}
    for edge in demo.bom:
        children.setdefault(edge.parent_sku_id, []).append(edge)
    for level in ("finished", "intermediate"):
        for part in demo.parts:
            if part.level != level:
                continue
            volume = annual.get(part.sku_id, 0.0)
            for edge in children.get(part.sku_id, ()):
                annual[edge.child_sku_id] = (
                    annual.get(edge.child_sku_id, 0.0) + volume * edge.qty_per
                )

    setups_per_year = 365.0 / CAMPAIGN_CYCLE_DAYS
    required = {}
    for routing in demo.routings:
        hours = annual.get(routing.sku_id, 0.0) * routing.hours_per_unit
        hours += setups_per_year * routing.setup_hours
        required[routing.resource_id] = required.get(routing.resource_id, 0.0) + hours

    full_days_per_year = 52.0 * sum(DAY_SHAPE)
    return {
        resource.resource_id: round(
            required.get(resource.resource_id, 0.0)
            / TARGET_UTILISATION
            / full_days_per_year,
            2,
        )
        for resource in demo.resources
    }


def _build_capacity(rng, demo):
    """Available hours per resource over the forward horizon.

    Two shifts on weekdays, one on Saturday, closed Sunday. The Sunday zeros are
    dropped on write and reappear on read -- a small live demonstration that the
    sparse round trip works on a grain other than supply and demand.
    """
    facts = []
    for resource in demo.resources:
        day_hours = demo.resource_day_hours.get(resource.resource_id, 0.0)
        for i in range(HORIZON_DAYS):
            day = demo.horizon_start + timedelta(days=i)
            hours = round(day_hours * DAY_SHAPE[day.weekday()], 2)
            if hours and rng.random() < 0.04:  # planned maintenance
                hours = round(hours / 2, 2)
            facts.append(Fact((resource.resource_id,), day, hours))
    return facts
