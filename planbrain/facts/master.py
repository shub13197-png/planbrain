"""Store and restore what the facts are about.

**The facts were persisted from the first release; the master data was not.**
Lead times, lot sizes, the BOM, locations, resources, the fleet and the working
calendar lived on ``Session.demo`` -- a per-process object set by exactly one
method -- so a planner could import a year of history, close the window, reopen
it, and be told there was no dataset loaded while their demand rows sat in the
file. On disk and unreachable is the same screen as lost.

Two rules this module keeps:

**Master data is not a fact.** A fact is a quantity at a
``(sku, loc, bucket, measure, scenario)``. A lead time is a property of a part
and has no bucket; forcing one on it would put a date on something that does not
have one. So these are their own tables and `planbrain/facts/access.py` is
untouched.

**Restoring nothing is not restoring an empty dataset.** A fresh install has no
part master, and `load_master` returns None there rather than a DemoDataset with
empty lists -- which every engine would happily plan against and report zero
requirements for.
"""

from datetime import date

from ..demo.generate import (
    BomEdge,
    DemoDataset,
    Location,
    Part,
    Resource,
    Routing,
    Truck,
)
from ..working_calendar import WorkingCalendar

#: Written and read in one place each, so a column added to one and forgotten in
#: the other is a mismatch at the SQL rather than a silently dropped field.
_PART_COLUMNS = ("sku_id", "name", "level", "lead_time_days", "safety_stock",
                 "lot_policy", "lot_qty", "unit_cost")

#: Deleted in this order so a child row never outlives the row it references.
_TABLES_NEWEST_FIRST = ("stock_on_hand", "routing", "bom", "truck", "resource",
                        "part", "location", "dataset")


def store_master(con, dataset) -> dict:
    """Write the reference data of ``dataset``, replacing whatever was there.

    Replacing rather than appending: importing a second time is a normal thing
    for a planner to do, and two part masters in one file would double every
    requirement in the plan.

    The caller owns the transaction, as everywhere else in this package.
    """
    counts = {}
    for table in _TABLES_NEWEST_FIRST:
        con.execute(f"DELETE FROM {table}")

    con.executemany(
        "INSERT INTO location (loc_id, name, kind) VALUES (?, ?, ?)",
        [(loc.loc_id, loc.name, loc.kind) for loc in dataset.locations],
    )
    counts["location"] = len(dataset.locations)

    con.executemany(
        f"INSERT INTO part ({', '.join(_PART_COLUMNS)}) "
        f"VALUES ({', '.join('?' * len(_PART_COLUMNS))})",
        [tuple(getattr(p, c) for c in _PART_COLUMNS) for p in dataset.parts],
    )
    counts["part"] = len(dataset.parts)

    con.executemany(
        "INSERT INTO bom (parent_sku_id, child_sku_id, qty_per) VALUES (?, ?, ?)",
        # `e.qty_per` directly, never a default: a quantity-per that silently
        # fell back to 1.0 would change every requirement in the explosion and
        # nothing would report it.
        [(e.parent_sku_id, e.child_sku_id, e.qty_per) for e in dataset.bom],
    )
    counts["bom"] = len(dataset.bom)

    con.executemany(
        "INSERT INTO resource (resource_id, name, kind) VALUES (?, ?, ?)",
        [(r.resource_id, r.name, r.kind) for r in dataset.resources],
    )
    counts["resource"] = len(dataset.resources)

    con.executemany(
        "INSERT INTO routing (sku_id, resource_id, hours_per_unit, setup_hours) "
        "VALUES (?, ?, ?, ?)",
        [(r.sku_id, r.resource_id, r.hours_per_unit, r.setup_hours)
         for r in dataset.routings],
    )
    counts["routing"] = len(dataset.routings)

    con.executemany(
        "INSERT INTO truck (truck_id, plate, capacity_kg, available) VALUES (?, ?, ?, ?)",
        [(t.truck_id, t.plate, t.capacity_kg, 1 if t.available else 0)
         for t in dataset.trucks],
    )
    counts["truck"] = len(dataset.trucks)

    con.executemany(
        "INSERT INTO stock_on_hand (sku_id, loc_id, qty) VALUES (?, ?, ?)",
        [(sku, loc, qty) for (sku, loc), qty in dataset.stock_on_hand.items()],
    )
    counts["stock_on_hand"] = len(dataset.stock_on_hand)

    con.execute(
        "INSERT INTO dataset (only_row, history_start, history_end, horizon_start, "
        "horizon_end, working_weekdays) VALUES (1, ?, ?, ?, ?, ?)",
        (dataset.history_start.isoformat(), dataset.history_end.isoformat(),
         dataset.horizon_start.isoformat(), dataset.horizon_end.isoformat(),
         ",".join(str(d) for d in sorted(dataset.calendar.working_weekdays))),
    )
    counts["dataset"] = 1
    return counts


def load_master(con):
    """Rebuild the reference data, or None when this database holds none.

    None rather than an empty dataset: a fresh install has no part master, and
    an empty one would be planned against and reported on as though it were a
    real portfolio that happened to need nothing.
    """
    present = con.execute(
        "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = 'dataset'"
    ).fetchone()
    if not present or not present[0]:
        return None
    meta = con.execute(
        "SELECT history_start, history_end, horizon_start, horizon_end, "
        "working_weekdays FROM dataset WHERE only_row = 1"
    ).fetchone()
    if meta is None:
        return None

    parts = [
        Part(**dict(zip(_PART_COLUMNS, row)))
        for row in con.execute(
            f"SELECT {', '.join(_PART_COLUMNS)} FROM part ORDER BY sku_id"
        )
    ]
    if not parts:
        return None

    locations = [Location(loc_id=r[0], name=r[1], kind=r[2]) for r in
                 con.execute("SELECT loc_id, name, kind FROM location ORDER BY loc_id")]
    bom = [BomEdge(parent_sku_id=r[0], child_sku_id=r[1], qty_per=r[2]) for r in
           con.execute("SELECT parent_sku_id, child_sku_id, qty_per FROM bom "
                       "ORDER BY parent_sku_id, child_sku_id")]
    resources = [Resource(resource_id=r[0], name=r[1], kind=r[2]) for r in
                 con.execute("SELECT resource_id, name, kind FROM resource ORDER BY resource_id")]
    routings = [Routing(sku_id=r[0], resource_id=r[1], hours_per_unit=r[2], setup_hours=r[3])
                for r in con.execute("SELECT sku_id, resource_id, hours_per_unit, setup_hours "
                                     "FROM routing ORDER BY sku_id, resource_id")]
    trucks = [Truck(truck_id=r[0], plate=r[1], capacity_kg=r[2], available=bool(r[3]))
              for r in con.execute("SELECT truck_id, plate, capacity_kg, available "
                                   "FROM truck ORDER BY truck_id")]
    stock = {(r[0], r[1]): r[2] for r in
             con.execute("SELECT sku_id, loc_id, qty FROM stock_on_hand")}

    weekdays = frozenset(int(d) for d in meta[4].split(",") if d != "")
    return DemoDataset(
        locations=locations, parts=parts, bom=bom, resources=resources,
        routings=routings, trucks=trucks,
        history_start=date.fromisoformat(meta[0]),
        history_end=date.fromisoformat(meta[1]),
        horizon_start=date.fromisoformat(meta[2]),
        horizon_end=date.fromisoformat(meta[3]),
        calendar=WorkingCalendar(weekdays),
        # Facts stay in their own tables and are read through the accessor on
        # demand. Loading 460,000 rows to answer "is there a dataset here" would
        # make opening the application slower than planning with it.
        facts={},
        stock_on_hand=stock,
    )
