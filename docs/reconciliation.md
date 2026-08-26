# Reconciling netreq and the service simulation

**Decomposition terms written before the reconciliation was run.** Same
discipline as `docs/capacity-sizing.md` and `docs/unit-costs.md`. Results are
appended below the line and nothing above it was revised.

## The problem, stated precisely

`netreq` reports `projected_on_hand`. The service backtest reports
`average_on_hand`. Both describe "how much stock this plan implies" and they do
not agree.

**They should not agree.** One is a deterministic net-requirements calculation
against forecast demand; the other replays realised demand with stockouts and
lost sales. Disagreement is expected. The credibility problem is disagreement
**for reasons nobody can name**, sitting directly under the table the entire
positioning rests on.

So this is reconciliation, not unification. The goal is a decomposition where
every term is named and the terms add up.

## The deeper issue found while scoping this

**The service backtest never replays `netreq`'s plan at all.** It applies its own
order-up-to policy. So the two engines are not two answers to one question; they
are answers to two different questions, and comparing them directly was never
meaningful.

Reconciliation therefore has two parts:

1. Make them comparable — replay `netreq`'s **actual planned receipts** as a
   fixed schedule, rather than a policy.
2. Decompose what remains.

## The ladder

Each rung adds exactly one effect, so each delta *is* that term and the
decomposition sums by construction rather than by luck.

| rung | what it is | delta from previous |
|---|---|---|
| 0 | lot-for-lot, zero safety stock, forecast demand, no truncation | — (pure netting baseline) |
| 1 | rung 0 **+ safety stock** | **safety stock** |
| 2 | rung 1 **+ the part's real lot sizing** | **lot-sizing granularity** |
| 3 | rung 2's schedule replayed against **realised demand**, untruncated | **forecast error** |
| 4 | rung 3 **+ lost-sales truncation** (on-hand floored at zero) | **stockout truncation** |

Rung 2 must equal `netreq`'s reported `projected_on_hand`. It is computed by a
different path, so any gap there is a **model-consistency residual** and a real
bug rather than an explained difference.

Rung 4 is the honest comparator for the simulation replaying the same schedule.

## The test

**The four terms plus the residual sum to the observed gap, within tolerance.**

That is the assertion — *not* that the two numbers match. A test demanding they
match would be wrong, and passing it would mean one engine had been bent to fit
the other.

**A residual that nothing explains is a bug and gets found, not tolerated.**
Tolerance is set for floating-point accumulation over 90 buckets and hundreds of
SKUs, not to absorb unexplained differences: **0.5% of the plan's average
on-hand**. If the residual exceeds that, this item is not finished.

## Reported per demand class

Aggregate reconciliation can hide a term that cancels between classes — safety
stock dominating on smooth SKUs while stockout truncation dominates on lumpy
ones would net out to a comfortable total that describes nothing. Every term is
reported per class.

## What this does not do

* **It does not make the two engines agree.** The simulation's own policy figure
  stays different from `netreq`'s plan, because it is a different policy. That
  difference is now labelled rather than mysterious.
* **It does not price inventory.** Working capital needs the unit costs from
  `docs/unit-costs.md` flowing into the service report, which is not in scope
  here.
* **It does not reconcile capacity.** `rccp` says the plan is infeasible; both
  stock figures assume it gets made anyway.
