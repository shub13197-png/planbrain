"""Find the header row in a spreadsheet that was written for a human.

The importer assumed row 1 is the header. Real files do not oblige: they open
with a company name, a report title, a blank row, a date range, and *then* the
headers — often split across two rows with the top one merged across a group.

Nothing here guesses silently. `inspect()` returns what it decided **and why**,
and the mapping UI shows both, because a wrong header row shifts every column by
one and produces a file that imports cleanly and means nothing.

Pure: takes a grid of cells, returns a description. Reading files is `io.py`.
"""

import re
from dataclasses import dataclass, field

#: How far down to look. A title block longer than this is not a spreadsheet
#: someone expects software to read.
MAX_SCAN_ROWS = 25

#: Unit and qualifier noise that appears inside header text. Stripped for
#: matching, never from what is shown to the user -- "Qty (Kgs)" tells a person
#: something "qty" does not, and the UI shows the original.
UNIT_SUFFIX = re.compile(
    r"""[\s_\-]*(?:
        \(\s*(?:in\s+)?(?:kg|kgs|kilos?|kilograms?|g|gm|grams?|mt|tonnes?|tons?|
                        l|ltr|ltrs|litres?|liters?|ml|units?|nos?|pcs?|pieces?|
                        cases?|ctns?|cartons?|boxes?|days?|hrs?|hours?|inr|rs|
                        usd|eur|%|pct)\s*\)
        |
        [\s_\-](?:kg|kgs|mt|ltr|ltrs|nos|pcs|units|inr|rs|usd|pct)
      )\s*$""",
    re.IGNORECASE | re.VERBOSE,
)

_NUMERIC = re.compile(r"^[\s₹$£€]*-?[\d,]+(?:\.\d+)?\s*%?$")


@dataclass
class HeaderGuess:
    """What was decided about a file's shape, and the reasoning behind it."""

    header_row: int                 # 0-based index into the grid
    header_rows: list = field(default_factory=list)   # all rows forming it
    columns: list = field(default_factory=list)       # cleaned names, in order
    raw_columns: list = field(default_factory=list)   # exactly as written
    first_data_row: int = 0
    skipped_rows: list = field(default_factory=list)  # title/blank rows above
    confidence: float = 0.0
    reasons: list = field(default_factory=list)


def normalise(text) -> str:
    """A header as written, tidied for matching only.

    Collapses whitespace including the non-breaking spaces Excel leaves behind,
    lowercases, strips a trailing unit qualifier, and folds separators. What the
    user sees is always the original.
    """
    if text is None:
        return ""
    cleaned = str(text).replace(" ", " ").replace("\n", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = UNIT_SUFFIX.sub("", cleaned)
    cleaned = re.sub(r"[\s_\-./]+", "_", cleaned.lower()).strip("_")
    return cleaned


def looks_numeric(value) -> bool:
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return True
    return bool(_NUMERIC.match(str(value).strip()))


def _filled(row) -> list:
    return [c for c in row if c is not None and str(c).strip() != ""]


def score_row(grid, index) -> tuple:
    """How much row ``index`` looks like a header. Returns (score, reasons)."""
    row = grid[index]
    filled = _filled(row)
    reasons = []
    if not filled:
        return 0.0, ["row is empty"]

    density = len(filled) / max(1, len(row))
    textish = sum(1 for c in filled if not looks_numeric(c)) / len(filled)
    distinct = len({normalise(c) for c in filled}) / len(filled)

    score = density * 0.30 + textish * 0.40 + distinct * 0.30
    if textish == 1.0:
        reasons.append("every filled cell is text")
    if distinct < 1.0:
        reasons.append("has repeated values, which headers usually do not")

    # A header is followed by data. Rows below being more numeric than this one
    # is the strongest single signal, and it is what separates a header from a
    # title: a title also has text, but so does everything under it.
    below = [grid[i] for i in range(index + 1, min(index + 4, len(grid)))]
    below_filled = [_filled(r) for r in below]
    below_filled = [r for r in below_filled if r]
    if below_filled:
        below_numeric = sum(
            sum(1 for c in r if looks_numeric(c)) / len(r) for r in below_filled
        ) / len(below_filled)
        if below_numeric > 0.2:
            score += 0.35
            reasons.append("rows beneath it contain numbers")
        widths = [len(r) for r in below_filled]
        if max(widths) - min(widths) <= 1 and len(filled) >= max(widths) - 1:
            score += 0.15
            reasons.append("the rows beneath are a consistent width")
    else:
        score -= 0.4
        reasons.append("nothing follows it, so it cannot be a header")

    # One or two filled cells across a wide sheet is a title, not a header.
    if len(filled) <= 2 and len(row) > 3:
        score -= 0.5
        reasons.append("too few filled cells; looks like a title")

    return score, reasons


def _merge_two(top, bottom) -> list:
    """Join a two-row header: 'Quantity' over 'Kg' becomes 'Quantity Kg'.

    Used when the upper row is sparse -- typically because it was merged across
    a group of columns -- and the lower row names each column within it.
    """
    width = max(len(top), len(bottom))
    out = []
    for i in range(width):
        upper = str(top[i]).strip() if i < len(top) and top[i] is not None else ""
        lower = str(bottom[i]).strip() if i < len(bottom) and bottom[i] is not None else ""
        if upper and lower and normalise(upper) != normalise(lower):
            out.append(f"{upper} {lower}")
        else:
            out.append(lower or upper)
    return out


def inspect(grid) -> HeaderGuess:
    """Decide where the header is, and say why."""
    if not grid:
        return HeaderGuess(header_row=0, confidence=0.0,
                           reasons=["the file is empty"])

    limit = min(len(grid), MAX_SCAN_ROWS)
    scored = [(score_row(grid, i)[0], i) for i in range(limit)]
    best_score, best = max(scored, key=lambda pair: (pair[0], -pair[1]))
    _, reasons = score_row(grid, best)

    header_rows = [best]
    raw = list(grid[best])

    # Two-row header: the row above is sparse (a merged group label) and the
    # candidate names the columns under it.
    if best > 0:
        above = grid[best - 1]
        above_filled = _filled(above)
        if above_filled and len(above_filled) < len(_filled(grid[best])):
            if all(not looks_numeric(c) for c in above_filled):
                raw = _merge_two(above, grid[best])
                header_rows = [best - 1, best]
                reasons.append("a sparse row above it was merged in as a group label")

    skipped = [i for i in range(min(header_rows)) if _filled(grid[i])]
    if skipped:
        reasons.append(f"{len(skipped)} title or blank row(s) above were skipped")

    raw_columns, columns = [], []
    for i, cell in enumerate(raw):
        text = "" if cell is None else str(cell).strip()
        raw_columns.append(text)
        name = normalise(text)
        # Unnamed trailing columns are real -- a stray formatted cell -- and
        # need a stable name so a mapping can refer to them.
        columns.append(name or f"column_{i + 1}")

    while raw_columns and raw_columns[-1] == "":
        raw_columns.pop()
        columns.pop()

    return HeaderGuess(
        header_row=best,
        header_rows=header_rows,
        columns=columns,
        raw_columns=raw_columns,
        first_data_row=max(header_rows) + 1,
        skipped_rows=skipped,
        confidence=round(min(1.0, max(0.0, best_score)), 3),
        reasons=reasons,
    )
