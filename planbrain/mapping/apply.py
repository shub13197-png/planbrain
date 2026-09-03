"""Apply a column mapping: preview it, then commit it. All or nothing.

The preview is the point. A user maps `Item Code -> sku_id`, sees twenty parsed
rows, and notices immediately that their material numbers lost a leading zero or
their dates came out as next year. Catching that before anything is written is
worth more than any validation message afterwards.

Same all-or-nothing rule as the importer: nothing is written unless the whole
mapping validates. A partial import leaves a database that looks populated and
is missing rows nobody finds until a plan comes out wrong.
"""

from dataclasses import dataclass, field

from ..importer.errors import ErrorLog
from ..importer.fields import SCHEMAS
from .detect import normalise
from .io import as_text

#: Rows shown in the preview. Enough to see a pattern, few enough to read.
PREVIEW_ROWS = 20


@dataclass
class MappingResult:
    rows: list = field(default_factory=list)
    log: ErrorLog = None
    preview: list = field(default_factory=list)
    total_rows: int = 0
    unmapped_sources: list = field(default_factory=list)
    missing_required: list = field(default_factory=list)
    #: Conversions that changed the text a person typed. Not errors -- the value
    #: parsed fine -- but the user needs to see them before committing.
    warnings: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.log and not self.missing_required


def required_fields(table: str) -> list:
    """Canonical fields that must be mapped for a table to be importable.

    Derived from the parsers themselves rather than a second list: a field whose
    parser accepts a blank is optional, and one that does not is required. Two
    lists would eventually disagree.
    """
    if table not in SCHEMAS:
        raise ValueError(f"unknown table {table!r}; expected one of {sorted(SCHEMAS)}")
    required = []
    for name, parse in SCHEMAS[table]["columns"].items():
        _value, message = parse(None)
        if message:
            required.append(name)
    return required


#: Trailing tokens that say "this is an identifier" and carry no meaning of
#: their own: ITEM_CD is the item, MATERIAL NO (TEXT) is the material. Stripped
#: only from the END, because a leading one changes the meaning -- `code_date`
#: is not `date`.
_NOISE_SUFFIXES = frozenset({"cd", "code", "no", "num", "number", "id", "text"})

#: Abbreviations that stand for a whole word rather than decorating one.
_SUFFIX_WORDS = {"dt": "date", "qty": "qty", "amt": "amount"}


def _expansions(tokens) -> list:
    """Alternative spellings of one header tail.

    Two moves, both conservative and both reversible by eye:

    * drop a trailing identifier suffix, so `ITEM_CD` also offers `item`
    * expand a trailing abbreviation, so `TXN_DT` also offers `txn_date`

    Deliberately not a stemmer. A stemmer would map `dated` and `dating` onto
    `date` and would eventually map something onto the wrong field with no line
    of code anyone could point at. These two rules are listable, and the corpus
    says which files need them.
    """
    if not tokens:
        return []
    out = []
    last = tokens[-1]
    if len(tokens) > 1 and last in _NOISE_SUFFIXES:
        out.append("_".join(tokens[:-1]))
    if last in _SUFFIX_WORDS:
        out.append("_".join(tokens[:-1] + [_SUFFIX_WORDS[last]]))
    return [o for o in out if o]


def suggest(headers, table: str) -> dict:
    """Exact and near-exact header matches. **Deliberately not clever.**

    Only normalised equality and a small alias table -- the kind of match a user
    would be annoyed to have to make by hand. Anything requiring judgement is
    left blank for the person to decide, because a plausible wrong guess that is
    accepted without reading is worse than a blank dropdown.

    This is the fallback the model will sit on top of, and it has to be
    trustworthy on its own.
    """
    if table not in SCHEMAS:
        raise ValueError(f"unknown table {table!r}")

    aliases = {
        "sku_id": ("sku", "item_code", "item", "material", "material_no",
                   "material_number", "part", "part_no", "part_number",
                   "product_code", "article", "item_no",
                   # Abbreviated and transliterated forms, from the corpus.
                   # `maal` is Hindi for goods and is what a Tally file in a
                   # north Indian workshop actually says.
                   "mtrl", "prod", "maal", "itm"),
        "loc_id": ("location", "loc", "depot", "warehouse", "godown", "plant",
                   "site", "branch", "store", "store_id",
                   # `whse` is the no-vowel abbreviation; `kidangu` is Tamil for
                   # warehouse and `naam`-suffixed forms come through the tail
                   # matching already in place.
                   "whse", "wh", "kidangu", "stores"),
        "bucket_date": ("date", "day", "posting_date", "doc_date", "txn_date",
                        "transaction_date", "period",
                        # `dinank` Hindi, `tarikh` Hindi/Urdu, `thethi` Tamil.
                        # All three appear on real ledgers and none of them
                        # resembles the English word at all, which is the whole
                        # reason a normalised-equality matcher cannot find them.
                        "dt", "dinank", "tarikh", "thethi", "txn_dt", "trn_dt"),
        "qty": ("quantity", "qty", "units", "volume", "sales", "demand",
                "issued", "consumed", "quantity_sold",
                # `matra` Hindi, `alavu` Tamil.
                "qt", "matra", "alavu", "nos"),
        "unit_cost": ("cost", "rate", "price", "unit_price", "std_cost",
                      "standard_cost", "value"),
        "lead_time_days": ("lead_time", "leadtime", "lt_days", "lead_days"),
        "capacity_kg": ("capacity", "payload", "max_load", "tonnage"),
        "name": ("description", "item_name", "material_description", "desc"),
    }

    # A two-row header merges a group label onto each column beneath it, so
    # "Item Code" under a merged "Material" arrives as "Material Item Code".
    # Each header therefore offers its full name AND its tails, because the
    # specific part of a merged heading is the end of it. Longest match wins,
    # so an exact header still beats a tail of a different one.
    candidates = {}
    for header in headers:
        tokens = normalise(header).split("_")
        for start in range(len(tokens)):
            key = "_".join(tokens[start:])
            if key and key not in candidates:
                candidates[key] = (header, len(tokens) - start)
            for expanded in _expansions(tokens[start:]):
                # Registered at a LOWER specificity than the literal tail, so an
                # exact header always beats an expansion of a different one. An
                # expansion that outranked a real match would be the matcher
                # preferring its own cleverness to what the file says.
                if expanded not in candidates:
                    candidates[expanded] = (header, len(tokens) - start - 1)

    mapping, claimed = {}, set()
    for canonical in SCHEMAS[table]["columns"]:
        options = (canonical,) + aliases.get(canonical, ())
        best, best_len = None, -1
        for option in options:
            hit = candidates.get(option)
            # One source column cannot supply two fields. Without this, a file
            # with "Date" and "Posting Date" can map both to bucket_date and
            # silently drop one of them.
            if hit and hit[0] not in claimed and hit[1] > best_len:
                best, best_len = hit[0], hit[1]
        if best is not None:
            mapping[canonical] = best
            claimed.add(best)
    return mapping


def apply_mapping(grid, mapping: dict, table: str, *, first_data_row: int,
                  headers: list, source: str = "sheet") -> MappingResult:
    """Parse the grid through ``mapping``. Returns rows, errors and a preview.

    ``mapping`` is canonical field -> source header, which is the direction the
    UI works in: a user picks, for each field the system needs, which of their
    columns supplies it. The reverse direction reads naturally in code and is
    wrong in the interface, because it asks the user to think about fields they
    do not have.
    """
    if table not in SCHEMAS:
        raise ValueError(f"unknown table {table!r}; expected one of {sorted(SCHEMAS)}")

    schema = SCHEMAS[table]
    log = ErrorLog()
    result = MappingResult(log=log)

    index_of = {}
    for canonical, header in mapping.items():
        if header in headers:
            index_of[canonical] = headers.index(header)
        else:
            log.add(source, 1, canonical, header,
                    f"is mapped to a column named '{header}', which is not in "
                    f"this file")

    result.missing_required = [
        f for f in required_fields(table)
        if f not in mapping or f not in index_of
    ]
    result.unmapped_sources = [
        h for h in headers if h and h not in set(mapping.values())
    ]
    if result.missing_required or log:
        return result

    parsed, seen, lossy = [], {}, {}
    for offset, raw in enumerate(grid[first_data_row:]):
        row_number = first_data_row + offset + 1     # 1-based, as Excel shows it
        if not any(c is not None and str(c).strip() != "" for c in raw):
            continue                                  # a blank spacer row

        record, ok = {}, True
        for canonical, parse in schema["columns"].items():
            if canonical not in index_of:
                value, message = parse(None)          # optional and absent
            else:
                column = index_of[canonical]
                cell = raw[column] if column < len(raw) else None
                text = as_text(cell)
                value, message = parse(text)
                if not message:
                    _note_lossy(lossy, canonical, mapping.get(canonical, canonical),
                                text, value, row_number)
            if message:
                log.add(source, row_number, mapping.get(canonical, canonical),
                        as_text(raw[index_of[canonical]])
                        if canonical in index_of and index_of[canonical] < len(raw)
                        else "", message)
                ok = False
            else:
                record[canonical] = value
        if not ok:
            continue

        key = tuple(record[c] for c in schema["key"])
        if key in seen:
            log.add(source, row_number, ", ".join(schema["key"]), key,
                    f"duplicates row {seen[key]}; each row must be unique")
            continue
        seen[key] = row_number
        parsed.append(record)

    result.rows = parsed
    result.total_rows = len(parsed)
    result.preview = parsed[:PREVIEW_ROWS]
    result.warnings = sorted(lossy.values(), key=lambda w: w["field"])
    return result


def _note_lossy(lossy, canonical, source_header, text, value, row_number) -> None:
    """Record a conversion that changed what the person typed.

    The one that matters is a leading zero. Material number `007821` is stored
    as the integer 7821, because `sku_id` is an integer everywhere in this
    system -- the fact tables, the BOM, the routings. That is a schema-wide
    decision and not one a column mapping can revisit.

    It is usually harmless: if a file is internally consistent, `007821` and
    `7821` are the same part throughout. It is **not** harmless if a catalogue
    contains both as distinct materials, which does happen after a migration.
    Only the user knows which, so the preview shows it and they decide.
    """
    if canonical in lossy or not text:
        return
    rendered = "" if value is None else str(value)
    if rendered == text.strip():
        return
    if text.strip().startswith("0") and text.strip().lstrip("0").isdigit():
        lossy[canonical] = {
            "field": canonical,
            "column": source_header,
            "kind": "leading_zero",
            "example": f"{text.strip()} -> {rendered}",
            "row": row_number,
            "message": (
                f"leading zeros are dropped because '{canonical}' is a whole "
                f"number here. Harmless if your file uses one style "
                f"throughout; a problem if '{text.strip()}' and "
                f"'{rendered}' are different parts in your catalogue."
            ),
        }
