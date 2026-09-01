"""The methods the shell may call. A closed table, not a dispatcher on getattr.

Closed because `getattr(module, request["method"])` lets a frontend bug — or
anything that reaches the pipe — call arbitrary code. The table is the boundary.

Everything here is thin. The engines are tested elsewhere and this layer exists
to marshal, not to compute; a rule that has kept the arithmetic testable without
a database since item 3.
"""

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from ..demo import build_demo, populate
from ..importer import ErrorLog, load_table, validate_cross_references
from ..importer.fields import SCHEMAS

SCHEMA_SQL = Path(__file__).resolve().parents[1] / "facts" / "schema.sql"


@dataclass
class Session:
    """Per-process state. One database, held open for the run."""

    con: sqlite3.Connection = None
    demo: object = None
    imported: dict = field(default_factory=dict)


def session(path: str = ":memory:") -> Session:
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    return Session(con=con)


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
METHODS = {
    "ping": ping,
    "demo.build": demo_build,
    "import.check": import_check,
    "import.columns": import_columns,
    "mapping.inspect": mapping_inspect,
    "mapping.preview": mapping_preview,
    "mapping.commit": mapping_commit,
    "profile.list": profile_list,
    "profile.save": profile_save,
}
