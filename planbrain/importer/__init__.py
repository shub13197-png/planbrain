"""Spreadsheet import with row-level validation (build item 12).

The adoption on-ramp. Every prospective user is on spreadsheets with no clean
master data, so the importer's real job is not parsing — it is **telling them
precisely what is wrong** with a file they have been maintaining by hand for
years.

Three rules the whole module is built around:

* **Locate every error.** File, row as the spreadsheet numbers it, column name,
  and the value that was actually there.
* **Report them all at once.** Stopping at the first bad row makes someone fix
  one cell and re-run, forty times.
* **All or nothing.** A partial import leaves a database that looks populated
  and is missing rows nobody finds until a plan comes out wrong.

Cross-file checks run too, because the commonest real-world failure is not a bad
cell — it is a BOM that references a SKU the part master does not contain.
"""

import csv
from pathlib import Path

from .errors import ErrorLog, ImportRejected, RowError
from .fields import SCHEMAS

__all__ = [
    "ErrorLog",
    "ImportRejected",
    "RowError",
    "SCHEMAS",
    "load_table",
    "read_rows",
    "validate_cross_references",
]


def read_rows(path, sheet=None):
    """Rows as dicts, with the spreadsheet's own row numbers.

    Returns ``(rows, header, error)``. Row numbers start at 2 because row 1 is
    the header, which is what the user sees in the corner of their screen.
    """
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        return _read_excel(path, sheet)
    return _read_csv(path)


def _read_csv(path):
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return [], [], "the file is empty"
        header = [h.strip() for h in reader.fieldnames]
        rows = []
        for number, raw in enumerate(reader, start=2):
            rows.append((number, {k.strip(): v for k, v in raw.items() if k}))
    return rows, header, None


def _read_excel(path, sheet):
    try:
        from openpyxl import load_workbook
    except ImportError:
        return [], [], (
            "reading .xlsx needs openpyxl; install it, or save the sheet as CSV"
        )

    book = load_workbook(path, read_only=True, data_only=True)
    worksheet = book[sheet] if sheet else book.worksheets[0]
    rows_iter = worksheet.iter_rows(values_only=True)
    try:
        header_row = next(rows_iter)
    except StopIteration:
        return [], [], "the sheet is empty"

    header = [str(h).strip() if h is not None else "" for h in header_row]
    rows = []
    for number, raw in enumerate(rows_iter, start=2):
        if all(cell is None for cell in raw):
            continue  # a blank spacer row is not an error
        rows.append((number, dict(zip(header, raw))))
    return rows, header, None


def load_table(path, table, *, sheet=None, log=None):
    """Parse and validate one table. Returns ``(rows, log)``.

    ``rows`` is empty when anything failed: the caller decides whether to raise,
    and nothing is written until every table has been checked.
    """
    if table not in SCHEMAS:
        raise ValueError(f"unknown table {table!r}; expected one of {sorted(SCHEMAS)}")

    schema = SCHEMAS[table]
    log = log if log is not None else ErrorLog()
    source = Path(path).name

    raw_rows, header, problem = read_rows(path, sheet)
    if problem:
        log.add(source, 1, "", "", problem)
        return [], log

    missing = [c for c in schema["columns"] if c not in header]
    if missing:
        # Reported against the header row, because that is the row to fix, and
        # a missing column would otherwise produce one identical error per row.
        log.add(source, 1, ", ".join(missing), "",
                f"required column(s) missing from the header; found {header}")
        return [], log

    parsed, seen = [], {}
    for number, raw in raw_rows:
        record, ok = {}, True
        for column, parse in schema["columns"].items():
            value, message = parse(raw.get(column))
            if message:
                log.add(source, number, column, raw.get(column), message)
                ok = False
            else:
                record[column] = value
        if not ok:
            continue

        key = tuple(record[c] for c in schema["key"])
        if key in seen:
            log.add(source, number, ", ".join(schema["key"]), key,
                    f"duplicates row {seen[key]}; each row must be unique")
            continue
        seen[key] = number
        parsed.append(record)

    return parsed, log


def validate_cross_references(tables, log=None, *, clean=None):
    """Check that the tables agree with each other.

    The commonest real failure is not a malformed cell: it is a BOM naming a SKU
    the part master has never heard of, usually because the two sheets were
    maintained by different people. Nothing in a single-file parse catches it.

    ``clean`` names the tables that parsed without errors. Cross-checks against
    a table that did **not** parse cleanly are skipped, because its rows are
    incomplete and every reference to a row that failed validation would be
    reported as missing. A user chasing four phantom "not in the part master"
    errors caused by one bad cell three files away is worse off than one who is
    told to fix the part master first.
    """
    log = log if log is not None else ErrorLog()
    parts_usable = clean is None or "parts" in clean

    if not parts_usable:
        log.add("cross-checks", 0, "", "",
                "skipped against the part master because parts did not parse "
                "cleanly; fix those rows and re-run to see reference problems")

    known_skus = {row["sku_id"] for row in tables.get("parts", [])}

    def check(table, column, universe, what):
        for index, row in enumerate(tables.get(table, []), start=2):
            if row[column] not in universe:
                log.add(f"{table}", index, column, row[column],
                        f"is not in the {what}")

    if known_skus and parts_usable:
        check("bom", "parent_sku_id", known_skus, "part master")
        check("bom", "child_sku_id", known_skus, "part master")
        check("routings", "sku_id", known_skus, "part master")
        check("history", "sku_id", known_skus, "part master")

    for index, row in enumerate(tables.get("bom", []), start=2):
        if row["parent_sku_id"] == row["child_sku_id"]:
            log.add("bom", index, "child_sku_id", row["child_sku_id"],
                    "makes a part a component of itself")

    return log
