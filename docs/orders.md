# The order list

The plan's output, as rows a planner acts on, and the file they send out.

## The gap this closes

A full run on the demo portfolio computes **2001 planned order releases**. Until
this existed, the application showed **none of them**. `plan.run` returned a
forecast summary and a resource utilisation table, and the results screen
rendered one table: utilisation by resource. There was no export of any kind —
no CSV, no workbook, no print.

So a planner's session ended with:

> utilisation 89.5%, 134 overloaded days, the plan does not fit.

That is a true statement and it is not something anyone can act on. The same
planner in Excel ends with a list they can sort, filter, print and email to a
supplier. **That is the step where a spreadsheet was beating this**, and it was
not an arithmetic problem: the arithmetic was right and the answer was discarded
one step before it reached a human.

Found by asking what a planner does after the verdict appears, and then looking
in the fact table for the answer that was already sitting there:

```
planned_order_receipt  2070
planned_order_release  2001
```

## What is on the list

One row per planned release: **place this order, this quantity, on this date.**

| column | what it is |
| --- | --- |
| `release_date` | the day the order must be placed or the job started |
| `sku_id`, `name` | the item, named — `3004` is not something you take to a supplier |
| `action` | `make` or `buy`: which half of the list this is, and whose |
| `qty` | the lot as `netreq` sized it |
| `loc_id`, `location` | where |
| `lead_time_days` | the offset already applied to get from need to release |

Sorted by release date, then item. The top of the list is the next thing to do.

### Releases, not receipts

The list reports **releases**. A release is the action; a receipt is the
consequence. They are not one-to-one — `netreq.core._offset` pulls a release
back to the previous working bucket, so two receipts can merge onto one release
date, which is exactly why the demo shows 2070 receipts against 2001 releases.

A "due date" column beside each release would therefore be asserting a pairing
that does not exist. The list carries the lead time instead and leaves the
arithmetic where the planner can see it.

### Make against buy

A raw part is bought; anything with a BOM below it is made. This is the first
thing a planner sorts by, because the two halves of the list go to two different
people — 179 buy lines and 1822 make lines on the demo.

## What it is not

**These are suggestions, not purchase orders.** Raising an order, approving it,
sending it and receiving it belong to the system of record. We plan; we do not
transact — see the scope boundaries in `CLAUDE.md`. The export is a file a human
reads, checks and acts on, and that is the whole of the intended workflow.

`planbrain/orders.py` computes nothing. Every quantity is read back from the
fact tables exactly as `netreq` wrote it; the module joins names onto ids, sorts,
and writes a file. If a number on the list is wrong, the bug is in `netreq`.

## Export

`.xlsx` or `.csv`, chosen by the extension typed into the save dialog rather
than by a separate control — a file that will not open in the application whose
name is in its extension is a support call. openpyxl already ships for reading
customer spreadsheets, so writing one adds no dependency.

Two rules the tests hold:

* **The export is the whole list, never the page on screen.** The screen shows
  100 of 2001; an export that silently carried those 100 would look complete and
  be wrong. `test_the_export_writes_the_whole_list_not_the_page_on_screen`.
* **An empty export is refused.** A header-only sheet tells a planner there is
  nothing to order, when the realistic cause is that no plan was run. Those two
  states must not look alike — the same failure as a size gate that passes
  because it measured nothing.

## Where the numbers are checked

`tests/test_orders.py` asserts the identity that matters: the row count and the
quantity total of the list equal the `planned_order_release` measure in the fact
table for the same scenario and horizon. A list that quietly drops or
double-counts rows cannot be seen by reading it — 2000 orders look the same
either way — so it is asserted rather than inspected.

The textbook fixture goes through the same path: Snyder & Shen *Fundamentals of
Supply Chain Theory* Example 3.9 — order 210 in period 1, 150 in period 3 —
written to the fact table and read back out as a list. The numbers a planner
reads are the numbers the textbook publishes.
