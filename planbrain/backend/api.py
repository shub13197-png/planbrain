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


#: The closed method table. Adding an entry is a deliberate act.
METHODS = {
    "ping": ping,
    "demo.build": demo_build,
    "import.check": import_check,
    "import.columns": import_columns,
}
