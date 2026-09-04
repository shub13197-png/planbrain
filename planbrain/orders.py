"""The order list: the planned releases as rows a planner acts on.

    from planbrain import orders
    rows = orders.order_list(con, demo)
    orders.to_xlsx(rows, "orders.xlsx")

**Why this module exists.** `netreq` computed 2001 planned order releases on the
demo portfolio and nothing in the application showed a single one of them: the
plan screen reported forecast counts and resource utilisation, and there was no
export of any kind. The arithmetic was right and the answer was thrown away at
the last step. That is the step where a spreadsheet wins -- an Excel planner
finishes with a list they can sort, print and send to a supplier, and finishing
with "utilisation 89.5%" is not a substitute.

**Nothing is computed here.** Every quantity is read back from the fact tables
exactly as `netreq` wrote it (architecture rule 4). This module joins names onto
ids, sorts, and writes a file. If a number on the list is wrong, the bug is in
`netreq`, not on this page.

**The list is releases, not receipts.** A release says *place this order on this
date*, which is the action. They are not one-to-one: `netreq.core._offset` pulls
a release back to the previous working bucket, so two receipts can merge onto
one release date -- the demo shows 2070 receipts against 2001 releases. Printing
a receipt date beside each release would assert a correspondence that does not
exist, so the list carries the lead time instead and leaves the arithmetic
visible.

**This is a suggestion, not a purchase order.** Issuing, approving and receiving
an order belong to the system of record; we plan, we do not transact. The export
is a file a human reads and acts on, which is the whole of the intended
workflow. See `docs/orders.md`.
"""

import csv
from dataclasses import asdict, dataclass, fields
from datetime import date

from .facts.access import read_facts

TABLE = "fact_supply_demand"
MEASURE = "planned_order_release"


@dataclass(frozen=True)
class Order:
    """One line a planner acts on.

    Field order is the column order in every export, because a planner reading
    the sheet and a planner reading the screen should not have to re-learn it.
    """

    release_date: date
    sku_id: int
    name: str
    action: str  # 'make' | 'buy'
    qty: float
    loc_id: int
    location: str
    lead_time_days: int


def order_list(con, demo, *, scenario_id: int = 0) -> list:
    """Read the planned releases for a scenario as sorted, named rows.

    Reference data -- part names, levels, lead times -- comes in as an argument
    rather than being read here, for the same reason `netreq.run` takes it:
    parts and lead times live in InvenTree, which is read-only, and the importer
    owns getting them out.

    Sorted by the date the order must be placed, then by item, so the top of the
    list is the next thing to do.
    """
    parts = {p.sku_id: p for p in demo.parts}
    locations = {loc.loc_id: loc.name for loc in demo.locations}

    rows = read_facts(
        con, TABLE,
        scenario_id=scenario_id, measure=MEASURE,
        start=demo.horizon_start, end=demo.horizon_end,
        keys=[(p.sku_id, loc_id) for p in demo.parts for loc_id in locations],
    )

    out = [
        Order(
            release_date=fact.bucket_date,
            sku_id=fact.keys[0],
            name=parts[fact.keys[0]].name,
            action=action_for(parts[fact.keys[0]]),
            qty=fact.qty,
            loc_id=fact.keys[1],
            location=locations[fact.keys[1]],
            lead_time_days=parts[fact.keys[0]].lead_time_days,
        )
        for fact in rows
        # read_facts densifies across the whole spine; an absent row means zero
        # and a zero-quantity order is not an instruction.
        if fact.qty
    ]
    out.sort(key=lambda o: (o.release_date, o.sku_id, o.loc_id))
    return out


def action_for(part) -> str:
    """Who acts on this line: purchasing, or the plant.

    A raw part is bought and anything with a BOM below it is made. This is the
    first thing a planner sorts by, because the two halves of the list go to two
    different people.
    """
    return "buy" if part.level == "raw" else "make"


def to_csv(rows, path) -> int:
    """Write the list as CSV. Returns rows written.

    CSV rather than only a workbook because it is what opens everywhere,
    including in the accounting package the customer already runs.
    """
    _refuse_empty(rows)
    columns = [f.name for f in fields(Order)]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
    return len(rows)


def to_xlsx(rows, path) -> int:
    """Write the list as a workbook. Returns rows written.

    openpyxl already ships for reading the customer's spreadsheets; writing one
    adds no dependency. Imported here rather than at module scope so that a
    planning run does not pay for a library it only needs on export.
    """
    _refuse_empty(rows)
    from openpyxl import Workbook

    columns = [f.name for f in fields(Order)]
    book = Workbook()
    sheet = book.active
    sheet.title = "Planned orders"
    sheet.append(columns)
    for row in rows:
        sheet.append([getattr(row, c) for c in columns])

    # Dates as dates, not as text: a planner sorts and filters this column, and
    # a text date sorts alphabetically -- which is wrong in a way that looks
    # right until December.
    for cell in sheet["A"][1:]:
        cell.number_format = "yyyy-mm-dd"
    sheet.freeze_panes = "A2"
    book.save(path)
    return len(rows)


def _refuse_empty(rows) -> None:
    """An empty export must not read as a plan with nothing to order.

    A header-only sheet tells a planner there is nothing to do. The realistic
    cause is that no plan was run, and those two states must not look alike --
    the same failure as a size gate that passes because it measured nothing.
    """
    if not rows:
        raise ValueError(
            "no planned orders to export; run a plan first, and if a plan has "
            "run then this scenario genuinely planned nothing"
        )
