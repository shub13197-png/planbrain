"""Field parsing and the table schemas the importer accepts.

Parsers return ``(value, error_message)``. A message means the cell was
unusable; the caller records it with the row and column and carries on, so one
bad cell does not hide the other three thousand.

Spreadsheet reality is handled here rather than left to the user: thousands
separators, currency symbols, stray whitespace, and the several ways people
write yes.
"""

import math
from datetime import date, datetime

TRUE_WORDS = {"y", "yes", "true", "t", "1", "available"}
FALSE_WORDS = {"n", "no", "false", "f", "0", "unavailable"}

#: Removed from anywhere in a number, not just its ends. Indian spreadsheets
#: carry all of these, and a thousands separator sits in the MIDDLE --
#: stripping only the ends leaves "2,000" unparseable.
NUMBER_NOISE = frozenset(" 	 ,₹$£€")


def _clean_number(value) -> str:
    if value is None:
        return ""
    return "".join(c for c in str(value) if c not in NUMBER_NOISE).strip()


def text(value, *, required=True):
    cleaned = "" if value is None else str(value).strip()
    if not cleaned and required:
        return None, "is required and was blank"
    return cleaned, None


def integer(value, *, minimum=None, required=True):
    cleaned = _clean_number(value)
    if not cleaned:
        return (None, "is required and was blank") if required else (None, None)
    try:
        # Accept 12.0 from a spreadsheet that made everything a float, but not
        # 12.5, which means the column was misunderstood.
        as_float = float(cleaned)
    except ValueError:
        return None, "should be a whole number"
    if not float(as_float).is_integer():
        return None, "should be a whole number, not a fraction"
    parsed = int(as_float)
    if minimum is not None and parsed < minimum:
        return None, f"should be at least {minimum}"
    return parsed, None


def number(value, *, minimum=None, required=True):
    cleaned = _clean_number(value)
    if not cleaned:
        return (None, "is required and was blank") if required else (None, None)
    try:
        parsed = float(cleaned)
    except ValueError:
        return None, "should be a number"
    if math.isnan(parsed) or math.isinf(parsed):
        # A NaN in qty has no constraint stopping it and poisons every
        # downstream sum in silence.
        return None, "is not a finite number"
    if minimum is not None and parsed < minimum:
        return None, f"should be at least {minimum}"
    return parsed, None


def one_of(value, allowed, *, required=True):
    cleaned = "" if value is None else str(value).strip().lower()
    if not cleaned:
        return (None, "is required and was blank") if required else (None, None)
    if cleaned not in allowed:
        return None, f"should be one of {', '.join(sorted(allowed))}"
    return cleaned, None


def boolean(value, *, default=True):
    cleaned = "" if value is None else str(value).strip().lower()
    if not cleaned:
        return default, None
    if cleaned in TRUE_WORDS:
        return True, None
    if cleaned in FALSE_WORDS:
        return False, None
    return None, "should be yes or no"


def day(value):
    """A calendar date. Ambiguous formats are refused rather than guessed.

    `03/04/2026` is 3 April in India and 4 March in America, and guessing wrong
    shifts a demand history by a month without anything failing. ISO only.
    """
    if isinstance(value, datetime):
        return value.date(), None
    if isinstance(value, date):
        return value, None
    cleaned = "" if value is None else str(value).strip()
    if not cleaned:
        return None, "is required and was blank"
    try:
        return date.fromisoformat(cleaned[:10]), None
    except ValueError:
        return None, "should be a date as YYYY-MM-DD (other formats are ambiguous)"


#: One entry per importable table: the columns, and how each is parsed.
#: `key` names the columns that together identify a row, for duplicate detection.
SCHEMAS = {
    "parts": {
        "key": ("sku_id",),
        "columns": {
            "sku_id": lambda v: integer(v, minimum=1),
            "name": text,
            "level": lambda v: one_of(v, {"raw", "intermediate", "finished"}),
            "lead_time_days": lambda v: integer(v, minimum=0),
            "safety_stock": lambda v: number(v, minimum=0, required=False),
            "lot_policy": lambda v: one_of(
                v, {"lot_for_lot", "fixed_qty", "min_max"}, required=False
            ),
            "lot_qty": lambda v: number(v, minimum=0, required=False),
            "unit_cost": lambda v: number(v, minimum=0, required=False),
        },
    },
    "bom": {
        "key": ("parent_sku_id", "child_sku_id"),
        "columns": {
            "parent_sku_id": lambda v: integer(v, minimum=1),
            "child_sku_id": lambda v: integer(v, minimum=1),
            "qty_per": lambda v: number(v, minimum=0),
        },
    },
    "routings": {
        "key": ("sku_id", "resource_id"),
        "columns": {
            "sku_id": lambda v: integer(v, minimum=1),
            "resource_id": lambda v: integer(v, minimum=1),
            "hours_per_unit": lambda v: number(v, minimum=0),
            "setup_hours": lambda v: number(v, minimum=0, required=False),
        },
    },
    "fleet": {
        "key": ("truck_id",),
        "columns": {
            "truck_id": lambda v: integer(v, minimum=1),
            "plate": lambda v: text(v, required=False),
            "capacity_kg": lambda v: number(v, minimum=1),
            "available": boolean,
            "ytd_long_haul_km": lambda v: number(v, minimum=0, required=False),
        },
    },
    "history": {
        "key": ("sku_id", "loc_id", "bucket_date"),
        "columns": {
            "sku_id": lambda v: integer(v, minimum=1),
            "loc_id": lambda v: integer(v, minimum=1),
            "bucket_date": day,
            "qty": lambda v: number(v, minimum=0),
        },
    },
}
