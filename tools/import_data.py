"""Import a customer's spreadsheets, or check them without importing.

    python -m tools.import_data --check data/incoming/
    python -m tools.import_data data/incoming/ --out data/local/customer.sqlite3

Looks for parts, bom, routings, fleet and history as .csv or .xlsx in the given
directory. Reports every problem it finds, with the row and column, and writes
nothing unless the whole set is clean.

`--check` is the mode a prospective user actually wants first: point it at the
spreadsheets they already have and find out what stands between them and a plan,
without committing to anything.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from planbrain.facts.access import Fact, write_facts  # noqa: E402
from planbrain.importer import ErrorLog, load_table, validate_cross_references  # noqa: E402

SCHEMA = Path(__file__).resolve().parents[1] / "planbrain" / "facts" / "schema.sql"
TABLES = ("parts", "bom", "routings", "fleet", "history")
SUFFIXES = (".csv", ".xlsx", ".xlsm")


def find_file(directory: Path, table: str):
    for suffix in SUFFIXES:
        candidate = directory / f"{table}{suffix}"
        if candidate.exists():
            return candidate
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--out", type=Path, default=Path("data/local/imported.sqlite3"))
    parser.add_argument("--check", action="store_true",
                        help="validate only; write nothing")
    args = parser.parse_args(argv)

    if not args.directory.is_dir():
        print(f"{args.directory} is not a directory", file=sys.stderr)
        return 2

    log = ErrorLog()
    tables, found, missing, clean = {}, [], [], set()

    for table in TABLES:
        path = find_file(args.directory, table)
        if path is None:
            missing.append(table)
            continue
        found.append(table)
        before = len(log)
        rows, log = load_table(path, table, log=log)
        tables[table] = rows
        if len(log) == before:
            clean.add(table)

    if not found:
        print(f"no importable files in {args.directory}. Expected any of: "
              f"{', '.join(t + '.csv' for t in TABLES)}", file=sys.stderr)
        return 2

    validate_cross_references(tables, log, clean=clean)

    print(f"Found: {', '.join(found)}")
    if missing:
        # Not an error. Someone importing only a part master should get on with it.
        print(f"Not present (skipped): {', '.join(missing)}")
    for table in found:
        print(f"  {table:10s} {len(tables[table]):>7,} rows accepted")

    if log:
        print()
        print(f"{len(log)} problem(s) found. Nothing was imported.")
        print()
        print(log.report())
        return 1

    print()
    print("No problems found.")
    if args.check:
        print("--check was set, so nothing was written.")
        return 0

    written = _write(args.out, tables)
    print(f"Wrote {args.out}")
    for measure, rows in written.items():
        print(f"  {measure:16s} {rows:>7,} fact rows")
    return 0


def _write(out: Path, tables) -> dict:
    """Write history to the fact tables. Reference data stays the caller's.

    Only `history` becomes facts. Parts, BOM, routings and fleet are reference
    data that lives in the system of record -- InvenTree is read-only and the
    importer does not own that boundary.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()

    con = sqlite3.connect(out)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA.read_text(encoding="utf-8"))

    written = {}
    history = tables.get("history", [])
    if history:
        written["demand_actual"] = write_facts(
            con, "fact_supply_demand", scenario_id=0, measure="demand_actual",
            facts=[
                Fact((r["sku_id"], r["loc_id"]), r["bucket_date"], r["qty"])
                for r in history
            ],
        )
    con.commit()
    con.close()
    return written


if __name__ == "__main__":
    raise SystemExit(main())
