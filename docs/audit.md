# Manual code audit

The `ponytail` plugin never landed through the marketplace across ten build
items, so the audit it would have done was done by hand instead. Three
categories: code nothing calls, abstractions with one implementation, and tests
that assert construction rather than behaviour.

## Removed

| what | why |
|---|---|
| `Ladder.observed_gap`, `Ladder.explained_gap` | superseded when the reported residual became the cross-engine one; nothing called them |
| `WorkingCalendar.describe` and its `_NAMES` table | written for a report that was never built |
| `Plan.assigned_count` | `len(plan.assignments)` already says it |
| `build_ladder(plan_item=..., item_factory=...)` | injected "so the netting engine can be stubbed in tests" — **nothing ever stubbed it**. Injection no caller uses is a seam that has to be read and understood for no benefit. Now imported directly. |
| `reconcile/terms.item_factory` | dead once the injection went |

A scan of 199 defined symbols found 4 unreferenced. That is a low rate, and the
one that mattered was not dead code but the unused seam — over-engineering hides
better than dead code because it looks like good practice.

## Tests strengthened

Two assertions checked a type where they should have checked a behaviour:

* `assert isinstance(report["feasible"], bool)` → now asserts the verdict agrees
  with the overload lists it summarises, so the headline and the detail cannot
  disagree.
* `assert isinstance(report.residual, float)` → now asserts a residual outside
  tolerance actually flips `within_tolerance`, so a breach cannot pass unnoticed.

## Deliberately left alone

**`isinstance(x, ScoredMean)` assertions.** These look like construction tests
and are not: the *type* is the mechanism that stops a mean being quoted without
its denominators. They sit next to behavioural tests asserting `float(x)` raises.

**`.get(key, default)` in `read_facts`, `explode` and `rccp`.** A SKU with no
independent demand, a bucket with no fact row, a resource with no routed work —
absence there is meaningful and the default encodes it. Hardening those would
turn ordinary sparsity into an error. The masking defaults where absence was
*unexpected* were fixed in a separate sweep; see `docs/decisions.md`.

**The three-module layering in each engine** (`core` / `explode` / `adapters`).
One implementation each, so it looks like unnecessary indirection. It is not:
the split is what lets the textbook fixtures test arithmetic without a database,
and it has paid for itself every time a fixture failure pointed at one page of
code instead of the whole pipeline.

## What the audit could not check

Whether the abstractions that exist are the *right* ones. A tool that finds dead
code cannot tell you that a live module models the wrong thing. The DRP gap and
the CLSP gap are both of that kind, and both are documented rather than detected.
