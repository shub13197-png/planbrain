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

### Stated policy: ties prefer the later order

Flat demand often admits several optima — `[5]×12` at setup 90, holding 1.5 can
be ordered 4+4+4 or 6+6 for exactly 405 either way. **When costs tie, netreq
orders later.**

This is a policy, not an implementation detail, and it is a real choice with a
real trade:

* *For later:* less stock held for the same money, less cash committed, less
  exposure if the requirement moves. It also matches stockpyl, which is what
  lets the cross-check assert exact equality rather than merely equal cost.
* *Against later:* a customer running tight service levels may prefer **earlier**
  — the stock is already there when demand arrives sooner than planned.

Deliberately **not parameterised**. A knob nobody has asked for is a branch
nobody tests and a default nobody chose. If a customer asks for prefer-earlier,
that is the moment to add it, and the tie-break belongs in the payload contract
at that point rather than in a config file.

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

**"Action list" was aspirational until 2026-09-05.** Both exception kinds were
built here, returned to `netreq.run`, and dropped: the only reader anywhere was
`tools/make_examples.py`, so the application could report that a plan did not
fit and could not say which item, on which day. `past_due_release` is now a
written measure, dated at the bucket the material is *needed* rather than at the
bucket-zero release that covers it — dating them all at zero would merge every
overdue order into one number with no date to chase.

**Which of the two actually fires is the opposite of what you would guess.**
`projected_on_hand` does not go negative on an infeasible plan, because a
planned receipt is dated at the bucket it is needed: the projection balances
even when the order covering it should have gone out weeks ago. Running the
planner over 2,947 real stock codes returned **zero** `negative_on_hand` and a
long list of past-due releases. The demo plan carries no shortage at all and
**212 overdue orders across 159 items**. A risk screen built
on negative projections alone reports "nothing runs out" on a plan with hundreds
of overdue orders in it.

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

## KNOWN GAP: this answers one of the two distribution questions

Explosion runs at the plant. Independent demand is **reconciled bottom-up**
across depots before netting — item 4 forecasts per depot, sums to plant level,
and explosion consumes the plant total.

That answers:

> **What must the plant make, and when?**

It does **not** answer:

> **What must each depot hold, and when should it ship?**

Aggregating depot demand to the plant discards the per-depot lead-time offset.
A depot four days from the plant and a depot next door are summed into the same
bucket, so the plant total is right while the timing of each depot's replenishment
is simply absent. Answering the second question is DRP — time-phased
distribution requirements planning — and it is **deferred, not solved**.

This matters because a plant plan that looks complete invites someone to read
depot answers out of it. There are none in here. If a planner asks "when do I
ship to Ludhiana", the honest answer today is that Planning Brain does not know.

Deliberately not half-built: a partial DRP would produce plausible per-depot
numbers nobody had designed, which is worse than an absent feature.

**`derived = 0` measures are never written.** `write_plans` refuses any measure
the vocabulary marks as imported. The database does not enforce that flag, so
this is where the rule that a planning run never overwrites the system of record
actually holds.
