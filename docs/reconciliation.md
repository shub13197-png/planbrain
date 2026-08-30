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

---

# Outcome

*Appended after running. Nothing above this line was changed.*

## The ladder, 40 of 221 series, 90-day holdout

Average units on hand per series:

| rung | | units | delta |
|---|---|---|---|
| 0 | pure netting | 8 | — |
| 1 | + safety stock | 461 | **+453** |
| 2 | + lot sizing — *netreq's plan* | 782 | **+322** |
| 3 | vs realised demand | 1,306 | **+524** |
| 4 | + lost sales — *comparable* | 1,519 | **+213** |

| term | units |
|---|---|
| safety stock | +453 |
| lot-sizing granularity | +322 |
| forecast error | +524 |
| stockout truncation | +213 |

## The residual of −0.0 was worthless, and here is the proof

The decomposition summing to the gap **cannot fail**. Every term is defined as a
difference between adjacent rungs, so they collapse to the gap as an algebraic
identity regardless of what the rungs contain. Feeding it five random numbers
still produces a residual of zero — there is a test that does exactly that
(`test_the_term_sum_is_an_identity_and_cannot_fail`).

No injected error can move it either. Perturbing rung 4 by X changes the
truncation term by +X and the observed gap by −X, and they cancel. It is a guard
against a coding slip in how the terms are assembled, and nothing more.

**A check that cannot fail is documentation, not verification.** Reporting that
residual as evidence overstated what was known.

### The falsifiable check, and what it survives

The reported residual is now a **cross-engine** one: rung 4 from the ladder
against rung 4 computed by `simulate.replay`, a separately written engine with
its own ordering of receive, order and serve.

| | construction check | cross-engine residual |
|---|---|---|
| fed random rungs | passes | n/a |
| receipts scaled by +2% / +10% / +40% | passes | **moves, proportionally** |
| schedule shifted one bucket | passes | **caught** |
| zero-lead-time order lost | passes | **caught it in practice** |

Both are now reported side by side and labelled, because the honest thing is to
show which one is load-bearing rather than to quietly drop the weaker number.

Value on the demo: **+0.0, or 0.0000% of plan**, well inside the 0.5% committed
above — and this time that means something.

## What the decomposition actually says

**Almost none of netreq's planned stock is an artefact.** Pure netting under
lot-for-lot with no safety stock carries 8 units. Everything above that — 453 of
safety stock and 322 of lot round-up — is a deliberate choice someone
configured. That is a reassuring answer and it was not the expected one.

**The simulation holds roughly twice the plan's stock**, and both reasons are
ordinary: realised demand came in under forecast (+524), and flooring stock at
zero raises an average that would otherwise go negative (+213).

## Per class, where the aggregate lies

| class | n | plan | replayed | safety | lots | fcst err | stockout |
|---|---|---|---|---|---|---|---|
| erratic | 4 | 878 | 1,543 | +466 | +398 | +658 | +8 |
| intermittent | 8 | 968 | 1,117 | +648 | +318 | **+72** | +76 |
| lumpy | 12 | 760 | 1,376 | +414 | +343 | +512 | +104 |
| smooth | 16 | 683 | 1,822 | +381 | +289 | **+725** | **+414** |

This is why the doc committed to reporting per class before running.

**On intermittent demand the plan is nearly right** — forecast error of +72 on a
plan of 968. Its stock is overwhelmingly safety stock, which is what an
intermittent SKU should be carrying.

**On smooth demand the plan is furthest out** — forecast error +725 and stockout
truncation +414. Smooth series are the ones carrying sustained drift, and the
forecast does not fully track it. In the aggregate those two effects sit beside
intermittent's near-zero and produce a comfortable middling number that describes
no actual series.

## A real bug, found by the cross-check

The ladder replays a fixed schedule; `simulate.replay` scores a policy. They are
separate implementations of the same physics, so the ladder's truncated rung
must reproduce what the simulation gets when fed the same schedule at zero lead
time.

It did not. **`simulate.replay` silently lost every order placed with a zero
lead time**: the order went into the pipeline at bucket `t` *after* that bucket's
arrivals had already been collected, so it was never received. Ten units ordered,
zero delivered, no error anywhere — a fill rate of zero with a full order book.

Fixed, with a regression test. **No published number was affected**: every SKU in
the demo has a lead time of at least one day, and the service backtest defaults
to seven. But it would have been waiting for the first same-day-delivery item any
customer configured.

This is the reconciliation earning its place. The bug was invisible to both
engines individually and only appeared when they were made to answer the same
question.

## What is still not reconciled

* **The simulation's own policy figure remains different**, because it is a
  different policy — order-up-to rather than netreq's schedule. That difference
  is now labelled rather than mysterious, but it is not decomposed.
* **Multi-level explosion is out of the comparison.** Both engines are run at the
  simulation's single-echelon footing, or the whole BOM structure would land in
  an unnamed residual.
* **Capacity is still ignored by both.** `rccp` says the plan is infeasible;
  both stock figures assume it gets made anyway.
