"""Read a spreadsheet as a raw grid, before anything is assumed about it.

The importer's reader assumes row 1 is a header. This one assumes nothing: it
returns the cells as they sit, so `detect` can decide where the header is.

Two Excel realities are handled here because they are properties of the file
rather than of the mapping:

**Merged cells.** openpyxl returns the value only for the top-left cell of a
merge and `None` for the rest. A header merged across three columns therefore
arrives as `["Quantity", None, None]`, which looks like two empty columns. The
value is filled across the merged range instead.

**Leading zeros.** Material number `007821` is a string, and any code that lets
it become the integer 7821 has silently renamed a part. Everything is read as
text and converted later, by the mapping, against a field whose type is known.
"""

import csv
from pathlib import Path

#: Enough to detect a header and preview, without loading a 400 MB file to show
#: someone twenty rows.
DEFAULT_MAX_ROWS = 200


class UnreadableFile(ValueError):
    """The file could not be read at all -- wrong format, corrupt, or locked."""


def read_grid(path, sheet=None, max_rows: int = DEFAULT_MAX_ROWS) -> list:
    """Return rows of cells, unmodified and unparsed."""
    path = Path(path)
    if not path.exists():
        raise UnreadableFile(f"{path} does not exist")
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        return _read_excel(path, sheet, max_rows)
    return _read_csv(path, max_rows)


def sheet_names(path) -> list:
    """Every sheet in a workbook. A CSV reports one, named for the file.

    The UI needs this before anything else: picking the wrong sheet is the
    fastest way to a confidently wrong import, and workbooks that open on a
    'Summary' tab with the data on 'Sheet2' are common.
    """
    path = Path(path)
    if path.suffix.lower() not in (".xlsx", ".xlsm"):
        return [path.stem]
    from openpyxl import load_workbook

    book = load_workbook(path, read_only=True, data_only=True)
    try:
        return list(book.sheetnames)
    finally:
        book.close()


def _read_csv(path: Path, max_rows: int) -> list:
    # utf-8-sig strips the BOM Excel writes, which otherwise becomes part of the
    # first header name and makes it match nothing.
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
        sample = handle.read(8192)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel  # a single-column file sniffs as nothing
        rows = []
        for i, row in enumerate(csv.reader(handle, dialect)):
            if i >= max_rows:
                break
            rows.append([c if c != "" else None for c in row])
    return rows


def _read_excel(path: Path, sheet, max_rows: int) -> list:
    from openpyxl import load_workbook

    # read_only=False: merged-cell ranges are not available in read-only mode,
    # and losing them would turn every merged header into blank columns. Costs
    # memory, which is why max_rows exists.
    book = load_workbook(path, data_only=True)
    try:
        worksheet = book[sheet] if sheet else book.worksheets[0]
        grid = []
        for i, row in enumerate(worksheet.iter_rows(values_only=True)):
            if i >= max_rows:
                break
            grid.append(list(row))
        _fill_merged(worksheet, grid)
    finally:
        book.close()
    return grid


def _fill_merged(worksheet, grid) -> None:
    """Spread each merged range's value across the cells it covers.

    Without this a header merged across three columns reads as one name and two
    blanks, and the two columns beneath it silently lose their labels.
    """
    for merged in worksheet.merged_cells.ranges:
        top, left = merged.min_row - 1, merged.min_col - 1
        if top >= len(grid) or left >= len(grid[top]):
            continue
        value = grid[top][left]
        if value is None:
            continue
        for r in range(merged.min_row - 1, min(merged.max_row, len(grid))):
            for c in range(merged.min_col - 1, merged.max_col):
                while len(grid[r]) <= c:
                    grid[r].append(None)
                if grid[r][c] is None:
                    grid[r][c] = value


def as_text(value) -> str:
    """A cell as the string a person typed, not as Python re-rendered it.

    Excel hands back `7821.0` for a column formatted as text containing
    `007821`, and `datetime` for anything date-shaped. Both need care: an
    integer-valued float rendered with `str()` gains a `.0` that becomes part of
    a material number and silently renames the part.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10] if not hasattr(value, "hour") else value.isoformat()
    return str(value).strip()
