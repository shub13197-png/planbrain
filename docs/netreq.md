# netreq — time-phased material requirements planning

Build item 3. Turns independent demand into planned orders, level by level down
the bill of materials.

## Layering, and why it is strict

| Module | Knows about | Tested by |
|---|---|---|
| `netreq/core.py` | lists of floats | textbook fixtures, in memory |
| `netreq/explode.py` | parts, BOM edges | hand-built three-level BOMs |
| `netreq/adapters.py` | scenarios, measures, dates | `test_netreq_pipeline.py` |

`core` and `explode` never touch a database. That is what makes a fixture
failure diagnostic: if Wagner-Whitin disagrees with the textbook, the bug is in
one page of arithmetic, not somewhere between here and SQLite.

## The netting chain

Per bucket, in order:

```
gross requirement
  → net of opening balance, safety stock and scheduled receipts   → net_req
  → rounded up by the item's ordering rule                        → planned_order_receipt
  → shifted back by the lead time                                 → planned_order_release
```

Receipts are assumed available at the **start** of the bucket they land in, so
the closing balance is `opening + scheduled + planned − gross`. That closing
balance is `projected_on_hand`.

## Lot sizing

`lot_for_lot`, `fixed_qty` and `min_max` are per-bucket rules and run inside the
netting loop, because their excess is real stock that must reduce later net
requirements. Lot-sizing separately from netting would order in every bucket.

`wagner_whitin` is a horizon-wide cost trade-off, so it runs as a second pass
over the whole net requirement vector. It needs `setup_cost` and `holding_cost`;
a payload that asks for it without them is rejected rather than quietly
degenerating to lot-for-lot.

**Ties prefer the later order.** Flat demand often admits several optima —
`[5]×12` at setup 90, holding 1.5 can be ordered 4+4+4 or 6+6 for the same 405.
Later means less stock held for the same money, and less exposure if the
requirement moves.

### Wagner-Whitin is implemented here, not imported

stockpyl is the locked stack's inventory library, and its published instances
are our fixtures — but it declares **`sphinx==4.5.0`** among its install
requirements. A pinned documentation toolchain has no business in an InvenTree
plugin's runtime tree.

So the DP lives in `core.py` (~20 lines, O(n²)), and stockpyl is a **test-only**
oracle: `test_our_dp_agrees_with_stockpyls_solver` checks the two against each
other on four instances, and `test_shipped_package_never_imports_stockpyl` stops
the dependency creeping back in.

## Exceptions are the action list

A release that would fall before the horizon opens cannot be scheduled. It is
placed in bucket 0 — *release it now* — and reported as `past_due_release` with
how many days late it already is. Dropping it would understate the plan; placing
it silently would hide that the order is overdue.

`negative_on_hand` reports every bucket whose closing balance is below zero, at
full depth. Never clamped: the magnitude is the size of the problem.

## The forecast seam

`resolve_gross_req(..., source=...)` is the named boundary between netting and
whatever produces independent demand.

* `naive_replay` (item 3) shifts the trailing window of `demand_actual` forward
  by one horizon length. It is a **placeholder, not a forecast** — no model, no
  reconciliation, no error estimate. Its accuracy must never be quoted as a
  baseline.
* `forecast` (item 4) raises `GrossReqSourceError` today, deliberately. Nothing
  writes the forecast measure yet, so returning zeros would net against nothing
  and produce a confident, empty plan.

Item 4 changes the source. The netting loop never learns which it got.

## Two deliberate scope limits

**Single production location.** Explosion runs at the plant, and independent
demand is aggregated across depots before netting. Time-phased distribution
between plant and depot is DRP — a different problem, not part of item 3.

**`derived = 0` measures are never written.** `write_plans` refuses any measure
the vocabulary marks as imported. The database does not enforce that flag, so
this is where the rule that a planning run never overwrites the system of record
actually holds.
