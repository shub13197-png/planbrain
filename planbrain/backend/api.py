"""The methods the shell may call. A closed table, not a dispatcher on getattr.

Closed because `getattr(module, request["method"])` lets a frontend bug — or
anything that reaches the pipe — call arbitrary code. The table is the boundary.

Everything here is thin. The engines are tested elsewhere and this layer exists
to marshal, not to compute; a rule that has kept the arithmetic testable without
a database since item 3.
"""

import os
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..demo import build_demo, populate
from ..facts.master import load_master, store_master
from ..importer import ErrorLog, load_table, validate_cross_references
from ..importer.fields import SCHEMAS

SCHEMA_SQL = Path(__file__).resolve().parents[1] / "facts" / "schema.sql"


@dataclass
class Session:
    """Per-process state. One database, held open for the run."""

    con: sqlite3.Connection = None
    demo: object = None
    imported: dict = field(default_factory=dict)
    #: Set by the stdio loop for the duration of one request. None everywhere
    #: else, so calling progress() from a test or a script is a no-op rather
    #: than an error.
    emit: object = None

    def progress(self, stage: str, **detail) -> None:
        """Tell the interface what is happening, if anyone is listening."""
        if self.emit is not None:
            self.emit({"stage": stage, **detail})


def default_database() -> Path:
    """The file `docs/install.md` tells the user their planning data lives in.

    One directory per platform convention, and the uninstall section of the
    guide names all three so a user can delete their data deliberately. The
    paths are asserted against that prose in `tests/test_persistence.py`: if
    these two drift, the guide tells someone to delete a folder that is not the
    one holding their history.

    `sys.platform` rather than `os.name`, because macOS and Linux need
    different answers and both are `posix`.
    """
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return root / "PlanningBrain" / "planning.db"


def session(path: str = ":memory:") -> Session:
    """Open the planning database, creating or upgrading it as needed.

    **The schema is applied every time, and is written to be safe to reapply.**
    It used to run only against a database with no schema at all, keyed on the
    `scenario` table -- correct while the schema never changed, and silently
    wrong the moment it did. When the master-data tables were added, an existing
    file kept its old six and the first statement to touch a new one died with
    `no such table`. A user who had ever opened the application before would
    have been upgraded into a broken one.

    So every `CREATE` is `IF NOT EXISTS` and both seeds are `INSERT OR IGNORE`:
    reapplying adds what is missing and cannot produce a second scenario 0,
    which would have every read keyed on it finding two.
    """
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    con.commit()
    # The dataset the last session left behind, if any.
    #
    # **This is what made the application usable twice.** Facts were persisted
    # from the first release and the part master was not, so reopening the file
    # found the demand rows and no lead times -- and every method that needs a
    # dataset refused, while the data sat there. `load_master` returns None on a
    # fresh install rather than an empty dataset, so `state.demo is None` still
    # means exactly what it meant.
    return Session(con=con, demo=load_master(con))


def _has_schema(con) -> bool:
    """Whether this database has already been set up.

    Keyed on `scenario` because it is the one table `schema.sql` also seeds, so
    its presence means the script ran far enough to matter.
    """
    return con.execute(
        "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = 'scenario'"
    ).fetchone()[0] > 0


# --------------------------------------------------------------------------
# methods
# --------------------------------------------------------------------------

def ping(state) -> dict:
    """Liveness, and the offline state the shell displays in the status bar."""
    from ..offline import is_engaged

    return {"alive": True, "offline": is_engaged()}


def demo_build(state, seed: int = 7) -> dict:
    state.demo = build_demo(seed=seed)
    counts = populate(state.con, state.demo)
    # Written beside the facts, in the same request and so the same transaction.
    # Persisting one without the other is what produced a file with demand in it
    # and nothing that could plan against it.
    store_master(state.con, state.demo)
    return {
        "parts": len(state.demo.parts),
        "bom_edges": len(state.demo.bom),
        "trucks": len(state.demo.trucks),
        "rows_written": {f"{t}.{m}": n for (t, m), n in counts.items()},
    }


def import_check(state, directory: str) -> dict:
    """Validate a directory of spreadsheets. Writes nothing.

    The mode a prospective user wants first: point it at what they already have
    and find out what stands between them and a plan.
    """
    root = Path(directory)
    if not root.is_dir():
        raise ValueError(f"{directory!r} is not a directory")

    log, tables, found, clean = ErrorLog(), {}, [], set()
    for table in SCHEMAS:
        path = _find(root, table)
        if path is None:
            continue
        found.append(table)
        before = len(log)
        rows, log = load_table(path, table, log=log)
        tables[table] = rows
        if len(log) == before:
            clean.add(table)

    validate_cross_references(tables, log, clean=clean)
    return {
        "found": found,
        "accepted": {t: len(rows) for t, rows in tables.items()},
        "problems": [
            {"source": e.source, "row": e.row, "column": e.column,
             "value": str(e.value), "message": e.message}
            for e in log.errors
        ],
        "suppressed": log.suppressed,
        "clean": not log,
    }


def import_columns(state, path: str, table: str) -> dict:
    """The header of a file, against the canonical field names for a table.

    This is the input the manual column-mapping UI works from, and later the
    input the mapping model sees. Kept separate from `import_check` because
    mapping happens *before* validation can say anything useful -- a file whose
    columns are named differently is not a file with four thousand bad rows.
    """
    from ..importer import read_rows

    if table not in SCHEMAS:
        raise ValueError(f"unknown table {table!r}; expected one of {sorted(SCHEMAS)}")
    _rows, header, problem = read_rows(Path(path))
    if problem:
        raise ValueError(problem)

    canonical = list(SCHEMAS[table]["columns"])
    return {
        "header": header,
        "canonical": canonical,
        "exact": {h: h for h in header if h in canonical},
        "unmapped": [h for h in header if h not in canonical],
        "missing": [c for c in canonical if c not in header],
    }


def _find(root: Path, table: str):
    for suffix in (".csv", ".xlsx", ".xlsm"):
        candidate = root / f"{table}{suffix}"
        if candidate.exists():
            return candidate
    return None


# --------------------------------------------------------------------------
# manual column mapping
# --------------------------------------------------------------------------

def mapping_inspect(state, path: str, sheet: str = None, table: str = "history") -> dict:
    """Open a file and report its shape: sheets, header row, columns, a guess.

    Returns *why* the header row was chosen, not just which one. A wrong header
    shifts every column by one and produces a file that imports cleanly and
    means nothing, so the user sees the reasoning and can override it.
    """
    from ..mapping import inspect, read_grid, required_fields, sheet_names, suggest

    target = Path(path)
    grid = read_grid(target, sheet=sheet)
    guess = inspect(grid)
    return {
        "path": str(target),
        "sheets": sheet_names(target),
        "sheet": sheet,
        "table": table,
        "header_row": guess.header_row + 1,
        "first_data_row": guess.first_data_row + 1,
        "skipped_rows": [r + 1 for r in guess.skipped_rows],
        "confidence": guess.confidence,
        "reasons": guess.reasons,
        "columns": guess.raw_columns,
        "canonical": list(SCHEMAS[table]["columns"]),
        "required": required_fields(table),
        "suggested": suggest(guess.raw_columns, table),
        "sample": [
            [None if c is None else str(c) for c in row]
            for row in grid[guess.first_data_row:guess.first_data_row + 5]
        ],
    }


def _resolve(grid, header_row):
    """Header names and the first data row, honouring a user override.

    ``header_row`` is 1-based when supplied, because it comes from a person
    reading row numbers off a spreadsheet.
    """
    from ..mapping import inspect

    if header_row is not None:
        headers = ["" if c is None else str(c).strip() for c in grid[header_row - 1]]
        return headers, header_row
    guess = inspect(grid)
    return guess.raw_columns, guess.first_data_row


def mapping_preview(state, path: str, table: str, columns: dict,
                    sheet: str = None, header_row: int = None) -> dict:
    """Parse the file through a mapping WITHOUT writing anything.

    The centre of the feature. A user sees their own rows parsed and catches a
    lost leading zero or a date read as next year before it reaches the
    database, which is worth more than any message afterwards.
    """
    from ..mapping import apply_mapping, read_grid

    grid = read_grid(Path(path), sheet=sheet)
    headers, first_data_row = _resolve(grid, header_row)
    result = apply_mapping(grid, columns, table, first_data_row=first_data_row,
                           headers=headers, source=Path(path).name)
    return {
        "ok": result.ok,
        "total_rows": result.total_rows,
        "preview": result.preview,
        "warnings": result.warnings,
        "missing_required": result.missing_required,
        "unmapped_sources": result.unmapped_sources,
        "problems": [
            {"row": e.row, "column": e.column, "value": str(e.value),
             "message": e.message}
            for e in result.log.errors
        ],
        "suppressed": result.log.suppressed,
    }


def mapping_commit(state, path: str, table: str, columns: dict,
                   sheet: str = None, header_row: int = None,
                   scenario_id: int = 0) -> dict:
    """Write the mapped rows. Refuses unless the whole mapping validates.

    Same all-or-nothing rule as the importer: a partial import leaves a database
    that looks populated and is missing rows nobody finds until a plan comes out
    wrong.
    """
    from ..facts.access import Fact, write_facts
    from ..mapping import apply_mapping, read_grid

    grid = read_grid(Path(path), sheet=sheet)
    headers, first_data_row = _resolve(grid, header_row)
    result = apply_mapping(grid, columns, table, first_data_row=first_data_row,
                           headers=headers, source=Path(path).name)

    if not result.ok:
        raise ValueError(
            f"nothing was written: {len(result.log)} problem(s), "
            f"{len(result.missing_required)} required field(s) unmapped"
        )
    if table != "history":
        # Reference data belongs to the system of record; the importer does not
        # own that boundary. Only history becomes facts.
        return {"written": 0, "rows": result.total_rows,
                "note": f"{table} is reference data and is not written to facts"}

    written = write_facts(
        state.con, "fact_supply_demand", scenario_id=scenario_id,
        measure="demand_actual",
        facts=[Fact((r["sku_id"], r["loc_id"]), r["bucket_date"], r["qty"])
               for r in result.rows],
    )
    return {"written": written, "rows": result.total_rows,
            "warnings": result.warnings}


def profile_list(state, directory: str = None) -> dict:
    """Shipped profiles, plus the user's own if a directory is given."""
    from ..mapping import BUILTIN_DIR, discover

    directories = [BUILTIN_DIR] + ([Path(directory)] if directory else [])
    profiles, problems = discover(*directories)
    return {
        "profiles": [
            {"name": p.name, "table": p.table, "columns": p.columns,
             "sheet": p.sheet,
             "header_row": None if p.header_row is None else p.header_row + 1,
             "source_hint": p.source_hint,
             "builtin": p.path.parent == BUILTIN_DIR}
            for p in profiles
        ],
        "problems": problems,
    }


def profile_save(state, directory: str, name: str, table: str, columns: dict,
                 sheet: str = None, header_row: int = None) -> dict:
    """Save a mapping so the next file from the same source is one click.

    Written as YAML a person can open, read and hand-edit. That is the point: a
    profile is data, and a user should be able to write one without us.
    """
    from ..mapping import Profile, save

    profile = Profile(
        name=name, table=table, columns=columns, sheet=sheet,
        header_row=None if header_row is None else header_row - 1,
    )
    written = save(profile, Path(directory))
    return {"path": str(written), "name": name}


#: The closed method table. Adding an entry is a deliberate act.
def scenario_growth(state, scenario_id: int = 0, demand_growth_pct: float = None,
                    capacity_growth_pct: float = None) -> dict:
    """Read or set a scenario's growth assumptions.

    With neither rate given this reads; with either given it writes that one and
    leaves the other alone. Both are annual percentages compounded daily from
    the last actual -- see docs/forecast.md, which also says why demand and
    capacity are two parameters and never one.

    The engines read these from the scenario rather than from a request, so the
    interface cannot put one assumption on screen while the plan was built under
    another.
    """
    from ..facts.scenario import growth_of, set_growth

    if demand_growth_pct is None and capacity_growth_pct is None:
        growth = growth_of(state.con, scenario_id)
    else:
        growth = set_growth(
            state.con, scenario_id=scenario_id,
            demand_growth_pct=demand_growth_pct,
            capacity_growth_pct=capacity_growth_pct,
        )
    return {
        "scenario_id": scenario_id,
        "demand_growth_pct": growth.demand_pct,
        "capacity_growth_pct": growth.capacity_pct,
    }


def plan_run(state, source: str = "forecast", lot_sizing: str = "cost_based") -> dict:
    """Run the whole plan and return what a planner actually looks at.

    forecast -> net requirements -> rough-cut capacity, in that order, because
    each reads what the previous one wrote. Returns the capacity verdict with
    the assumptions that produced it, never the verdict alone: a feasibility
    answer is the number most likely to be repeated out of context.

    This is the long call in the protocol -- the better part of a minute on a
    200-SKU portfolio -- so it reports progress between stages.
    """
    from .. import forecast, netreq, rccp
    from ..facts.scenario import growth_of

    if state.demo is None:
        raise ValueError(
            "no dataset loaded; call demo.build for the worked example, or "
            "import your own data first"
        )

    growth = growth_of(state.con, 0)

    state.progress("fitting demand models")
    fitted = forecast.run(state.con, state.demo)

    state.progress("netting requirements")
    netreq.run(state.con, state.demo, source=source, lot_sizing=lot_sizing)

    state.progress("checking capacity")
    capacity = rccp.run(state.con, state.demo)

    names = {r.resource_id: r.name for r in state.demo.resources}
    load = sum(d["load_hours"] for d in capacity["resources"].values())
    available = sum(d["capacity_hours"] for d in capacity["resources"].values())

    return {
        "assumptions": {
            "demand_growth_pct": growth.demand_pct,
            "capacity_growth_pct": growth.capacity_pct,
            "anchor": capacity["growth_anchor"],
            "source": source,
            "lot_sizing": lot_sizing,
        },
        "forecast": {
            "series": fitted["series"],
            "model_mix": fitted["model_mix"],
            "pattern_mix": fitted["pattern_mix"],
            "fallbacks": fitted["fallbacks"],
            "trends_suppressed": fitted["trends_suppressed"],
        },
        "capacity": {
            "feasible": capacity["feasible"],
            "buckets": capacity["buckets"],
            "load_hours": round(load, 1),
            "capacity_hours": round(available, 1),
            "utilisation": (load / available) if available else None,
            "overloaded_buckets": sum(
                len(d["overloaded_buckets"]) for d in capacity["resources"].values()
            ),
            "resources": [
                {
                    "resource_id": rid,
                    "name": names.get(rid, rid),
                    "load_hours": round(d["load_hours"], 1),
                    "capacity_hours": round(d["capacity_hours"], 1),
                    "utilisation": d["utilisation"],
                    "overloaded_buckets": len(d["overloaded_buckets"]),
                }
                for rid, d in capacity["resources"].items()
            ],
        },
    }


def plan_orders(state, limit: int = 100, action: str = None) -> dict:
    """The planned orders a planner acts on, newest deadline first.

    Separate from `plan.run` rather than folded into it. A plan run takes the
    better part of a minute and re-reading the list must not cost that again --
    the planner filters this list, sorts it and comes back to it, and every one
    of those is a read of rows that are already in the fact table.

    `limit` bounds what crosses the pipe, not what is counted: `total` is the
    whole list and `shown` is what came back, so a screen showing 100 of 2001
    can say so instead of implying there are 100.
    """
    from .. import orders

    if state.demo is None:
        raise ValueError(
            "no dataset loaded; call demo.build for the worked example, or "
            "import your own data first"
        )

    rows = orders.order_list(state.con, state.demo)
    if action is not None:
        if action not in ("make", "buy"):
            raise ValueError(f"unknown action {action!r}; expected 'make' or 'buy'")
        rows = [o for o in rows if o.action == action]

    return {
        "total": len(rows),
        "shown": min(limit, len(rows)),
        "totals": {
            "make": sum(1 for o in rows if o.action == "make"),
            "buy": sum(1 for o in rows if o.action == "buy"),
        },
        "orders": [
            {
                "release_date": o.release_date.isoformat(),
                "sku_id": o.sku_id,
                "name": o.name,
                "action": o.action,
                "qty": o.qty,
                "loc_id": o.loc_id,
                "location": o.location,
                "lead_time_days": o.lead_time_days,
            }
            for o in rows[:limit]
        ],
    }


def plan_risks(state, limit: int = 50, worst_first: bool = False) -> dict:
    """What is going to go wrong: shortages, and orders that are already late.

    Two lists, never one total. A shortage is "we will run out"; a past-due
    release is "we should have ordered this weeks ago". They are different
    questions and the second is the one that fires on a real portfolio -- see
    `planbrain/alerts.py`.

    Separate from `plan.run` for the same reason `plan.orders` is: the rows are
    already in the fact table, and re-reading them must not cost another minute
    of fitting.

    An empty result here is a real answer -- the material plan covers demand --
    and the interface has to say so in words, because "no shortages" and "no
    plan has been run" produce the same empty table and mean opposite things.
    """
    from .. import alerts

    if state.demo is None:
        raise ValueError(
            "no dataset loaded; call demo.build for the worked example, or "
            "import your own data first"
        )
    return alerts.risk_summary(
        state.con, state.demo, limit=limit, worst_first=worst_first
    )


def override_set(state, sku_id: int, bucket_date: str, qty: float,
                 author: str, reason: str, loc_id: int = None) -> dict:
    """Fix a quantity, with a person and a reason against it.

    `bucket_date` is the date the material is NEEDED, not the date the order
    goes out. A firm planned order fixes the receipt; the release is derived
    from it by the lead-time offset like any other receipt. Fixing a release
    date instead would put supply in a bucket the plan was not receiving in,
    which adds to the plan rather than replacing anything.
    """
    from datetime import date

    from .. import overrides

    if state.demo is None:
        raise ValueError("no dataset loaded; nothing to override")
    loc = loc_id if loc_id is not None else _production_location(state)
    overrides.set_override(
        state.con, sku_id=sku_id, loc_id=loc,
        bucket_date=date.fromisoformat(bucket_date), qty=float(qty),
        author=author, reason=reason,
    )
    state.con.commit()
    return {"sku_id": sku_id, "loc_id": loc, "bucket_date": bucket_date,
            "qty": float(qty),
            "rerun_required": True}


def override_clear(state, sku_id: int, bucket_date: str, loc_id: int = None) -> dict:
    """Hand the bucket back to the engine."""
    from datetime import date

    from .. import overrides

    if state.demo is None:
        raise ValueError("no dataset loaded; nothing to clear")
    loc = loc_id if loc_id is not None else _production_location(state)
    overrides.clear_override(
        state.con, sku_id=sku_id, loc_id=loc,
        bucket_date=date.fromisoformat(bucket_date),
    )
    state.con.commit()
    return {"sku_id": sku_id, "loc_id": loc, "bucket_date": bucket_date,
            "rerun_required": True}


def override_list(state, limit: int = 50) -> dict:
    """Which numbers in this plan are a person's, and whose.

    Also returns `disagreements` from `overrides.audit`: a fixed quantity with
    nobody behind it, or a reason with no quantity. Either leaves a plan
    half-explained, which is worse than an unexplained one because a reader
    cannot tell which they are looking at.
    """
    from .. import overrides

    if state.demo is None:
        raise ValueError(
            "no dataset loaded; call demo.build for the worked example, or "
            "import your own data first"
        )
    report = overrides.summary(state.con, state.demo, limit=limit)
    report["disagreements"] = overrides.audit(state.con, state.demo)
    return report


def _production_location(state) -> int:
    """Where the plan is netted, which is where a firm order applies.

    `netreq` explodes against a single production location, so an override
    without an explicit location belongs there rather than at whichever depot
    happens to be first.
    """
    return next(
        loc.loc_id for loc in state.demo.locations if loc.kind == "plant"
    )


def plan_export(state, path: str, action: str = None) -> dict:
    """Write the whole order list to a file the user named. Returns what it wrote.

    The *whole* list, never the page the screen is showing -- an export that
    silently carried the 100 rows on screen would be the worst kind of wrong,
    because the file looks complete.

    Format follows the suffix the user typed in the save dialog rather than a
    separate control, because a file that will not open in the application whose
    name is in its extension is a support call.

    The backend writes the file, not the frontend: the shell holds no filesystem
    permission and the save dialog returns a path, not a handle.
    """
    from .. import orders

    if state.demo is None:
        raise ValueError("no dataset loaded; nothing to export")

    rows = orders.order_list(state.con, state.demo)
    if action is not None:
        rows = [o for o in rows if o.action == action]

    suffix = Path(path).suffix.lower()
    writer = {".csv": orders.to_csv, ".xlsx": orders.to_xlsx}.get(suffix)
    if writer is None:
        raise ValueError(
            f"cannot write {suffix or 'a file with no extension'}; "
            "name the file .xlsx or .csv"
        )

    written = writer(rows, path)
    return {"path": str(path), "rows": written, "format": suffix.lstrip(".")}


METHODS = {
    "ping": ping,
    "demo.build": demo_build,
    "scenario.growth": scenario_growth,
    "plan.run": plan_run,
    "plan.orders": plan_orders,
    "plan.risks": plan_risks,
    "override.set": override_set,
    "override.clear": override_clear,
    "override.list": override_list,
    "plan.export": plan_export,
    "import.check": import_check,
    "import.columns": import_columns,
    "mapping.inspect": mapping_inspect,
    "mapping.preview": mapping_preview,
    "mapping.commit": mapping_commit,
    "profile.list": profile_list,
    "profile.save": profile_save,
}
