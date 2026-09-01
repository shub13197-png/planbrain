# Service backtest — fill rate against inventory held

Build item 5, and the proof-of-value report. Everything else in this repo is
machinery for producing the table below.

> ## Read this first: these numbers assume production is unconstrained
>
> Every fill rate on this page assumes the plant makes whatever the policy
> orders. **It cannot.** `rccp` reports the same plan as **infeasible in 139 of
> 450 resource-buckets**, at 85% overall utilisation, with individual buckets
> well below the capacity their load requires.
>
> **These figures are the full 222-series portfolio.** An earlier version of
> this page quoted a 40-series sample and went stale when the demo generator
> changed — it disagreed with the README for weeks, because the
> published-figure register did not cover this file. It does now.
>
> A capacity-capped sensitivity is reported at the bottom of this page. The
> honest headline is a **range**, not the single number in the table below.
>
> This is the largest open gap in the project. The engines do not agree about
> whether the plan is real: `rccp` says it cannot be made, and every stock and
> service figure here assumes it gets made anyway.

```bash
python -m tools.service_report --sample 60
python -m tools.service_report --sample 60 --sweep    # the frontier
```

## Why this exists

Item 4 measured forecast accuracy and, in doing so, disqualified its own metric
for half the portfolio. MASE rated intermittent demand at 1.34 and lumpy at 1.39
— apparently worse than naive — while the naive forecast scoring well on those
series is a **forecast of zero**, which is a policy that never orders anything.

A metric that rewards not ordering cannot judge an inventory policy. So the
question is restated as the one a finance manager actually asks:

> **What service did I get, and what did it cost me in stock?**

## Results at seed 7, 40 of 242 series, 90-day holdout, 7 days safety stock

Re-run at item 7 after **demand drift** was added to the generator. The earlier
numbers, on a stationary history, are kept below for comparison.

**All 222 series, 205 scored**, 90-day holdout, 7 days safety stock:

| policy | fill rate | avg on-hand | units short |
|---|---|---|---|
| fitted forecast | **97.2%** | 1,301 | 26,034 |
| reorder point, **tuned** | 95.7% | 880 | 109,154 |
| reorder point, **stale** | 93.4% | 870 | 192,687 |
| naive zero forecast | 76.1% | 272 | 237,101 |

By demand pattern, fill rate / average on-hand:

| policy | erratic | **intermittent** | lumpy | smooth |
|---|---|---|---|---|
| fitted forecast | 98.2% / 1,236 | **97.3% / 948** | 92.8% / 898 | 99.6% / 1,986 |
| reorder point, tuned | 95.9% / 796 | 95.6% / 849 | **93.7% / 939** | 96.9% / 915 |
| reorder point, stale | 95.4% / 834 | 91.1% / 828 | 89.3% / 846 | 97.1% / 952 |
| naive zero | 89.7% / 274 | **63.0% / 144** | **55.1% / 79** | 94.1% / 541 |

**On lumpy demand a tuned reorder point beats this tool** — 93.7% against 92.8%.
That is the retraction recorded in the README: the opposite claim came from a
40-series sample and did not survive the full run.

### Two reorder-point rows, on purpose

**Tuned** refits its parameters on all available history. **Stale** freezes them
on the first third and never revisits — which is what an SME incumbent actually
looks like. Nobody re-derives their reorder points quarterly; the numbers were
set once, possibly by someone who has since left.

The tuned row is not the honest incumbent, because "well-tuned" presupposes
ongoing tuning nobody is doing.

### Drift changed this result, and it is worth showing both

At item 5 the demo history was stationary — launches and discontinuations, but
no sustained trend. On that data the stale rule held up almost as well as the
tuned one, which weakened the "parameters go stale" argument. That was reported
at the time as under-evidenced rather than as a result, because staleness bites
hardest under drift and the dataset had none.

Item 7 added drift: 35% of series now carry a trend of −48% to +119% across the
history. The comparison moved:

| | stationary history | with drift |
|---|---|---|
| tuned reorder point | 94.1% | 95.2% |
| stale reorder point | 93.3% | 91.9% |
| **gap** | **0.8 pts** | **3.3 pts** |

Staleness now costs 3.3 points of fill rate on comparable stock, and it bites
hardest on exactly the classes this tool claims: **intermittent 87.3% against a
tuned 93.9%, lumpy 86.0% against 94.0%.**

The pre-commitment, written before the drift run, was that claim 3 would be
**dropped** if drift changed nothing. It changed something, so the claim stands —
and it now stands on evidence rather than on plausibility.

### The intermittent question, settled

**The naive-zero forecast delivers 70.6% fill on intermittent demand and 48.1%
on lumpy.** That is the forecast that wins on MASE. If accuracy were the
objective, that is what would ship, and roughly half of every lumpy customer
order would go unserved.

Accuracy was never the objective. The metric question settles itself.

## Read the frontier, not the point comparison

A single (fill rate, stock) pair per policy is close to meaningless: **any
policy buys service with stock.** The default table gives each policy a
different safety-stock term, so part of the inventory gap above is a tuning
setting rather than a property of the policy.

Sweeping safety stock, same 40 series:

| policy | 0d | 3d | 7d | 14d | 21d |
|---|---|---|---|---|---|
| fitted forecast | 91.9% / 704 | 95.1% / 916 | 95.7% / 1,208 | 95.8% / 1,723 | 95.9% / 2,239 |
| naive zero | 4.4% / 30 | 50.0% / 68 | 78.2% / 282 | 89.5% / 804 | 92.1% / 1,342 |
| reorder point, tuned | 94.1% / 824 | flat — | flat — | flat — | flat — |
| reorder point, stale | 93.3% / 824 | flat — | flat — | flat — | flat — |

Both reorder-point rows are flat by construction: their buffer comes from demand
variability, not the safety-days setting.

### The honest reading, including the uncomfortable part

**The fitted forecast is competitive with a reorder point, not dramatically
better than it — tuned or stale.** At 3 days of safety it reaches 95.1% on 916
units of stock against the tuned incumbent's 94.1% on 824 and the stale one's
93.3% on the same 824. About a point or two of service for about 11% more
inventory. At zero safety it is behind both on service.

The clear win is **lumpy demand**, where the forecast gets better service on
less stock (84.4% / 868 against 82.0% / 910). That is the hardest class and the
one a spreadsheet handles worst, which is a reasonable place for the value to
show up — but it is one class, not the portfolio.

This is reported rather than tuned away. A reorder point performing well is a
finding; making it look worse would be the kind of thing this project exists not
to do. The 4.4% naive-zero figure at zero safety stock is the result that
matters, and it does not depend on any of this tuning.

## What the simulation assumes, stated because it moves the numbers

* **Receive, then order, then serve.** Goods arriving today can serve today; an
  order placed today arrives after the lead time.
* **Unmet demand is lost, not backordered.** A customer who cannot get 20W-50
  today buys it elsewhere. Backorders would let a late delivery still count as
  served, flattering any policy that under-stocks.
* **The holdout is genuinely held out.** The forecast policy is fitted on
  training data only. Fitting on full history would be the inventory equivalent
  of scaling MASE on the test window, and nothing would flag it.
* **Series with no demand in the holdout have no fill rate** and are counted as
  unscored, never as 100%.

## Deviation: no SimPy

The brief points at `anshul-musing/multi-echelon-inventory-optimization`, whose
replay is built on SimPy. The architectural idea — replay history with the
policy injected as the thing under test — is followed exactly. The framework is
not.

At a single echelon with daily buckets and deterministic lead times, this is a
loop over days with a pipeline dict. A discrete-event framework would add a
runtime dependency and a layer of indirection over the one number the entire
positioning rests on, and this should be readable end to end without knowing a
framework.

SimPy earns its place the moment any of these arrive: multiple echelons with
concurrent replenishment, stochastic lead times, or contention for a shared
resource. None are in scope, and multi-echelon is "later if ever".

## Known gaps

* **Single echelon.** No plant-to-depot replenishment, consistent with the DRP
  gap recorded in `docs/netreq.md`.
* **Safety stock is days-of-cover, not a service-level target.** A proper
  formulation would solve for the stock that achieves a target fill rate given
  demand and lead-time variability. That is stockpyl's territory and follows the
  standing policy in `docs/decisions.md` when it is built.
* **No cost model.** The table reports units of stock, not money. Turning
  inventory into working capital needs unit costs, which come from the customer's
  system of record.
* **`netreq`'s plan and this simulation's policy are different things**, and the
  difference is now decomposed rather than mysterious — see
  `docs/reconciliation.md`. Reconciling them found a real bug in the replay.
* **Every policy here can order whatever it likes.** None of these numbers
  reflect capacity. `rccp` (item 6) shows the demo plan is infeasible even after
  balancing, so the service figures above are what the plant would achieve *if
  it could make the plan*. Reconciling the two is unstarted and is the largest
  open item in the repo.

---

# Capacity sensitivity (item 10)

## What was done

`simulate.capacity_factor()` takes the per-bucket ratio of available hours to
loaded hours from `rccp` and applies it to the holdout as a **delivery factor** —
the planner still orders what they need, and less turns up.

Across the horizon the factor averages 0.916, with 70 of 90 buckets unconstrained
and the ten worst at **0.21, 0.34, 0.36, 0.38, 0.38, 0.41, 0.42, 0.46, 0.47,
0.51**. The constraint is concentrated, not spread.

## The range

Full portfolio, 205 scored:

| policy | unconstrained | capacity-capped |
|---|---|---|
| fitted forecast | 97.2% / 1,301 | **97.0% / 1,272** |
| reorder point, tuned | 95.7% / 880 | 95.0% / 838 |
| reorder point, stale | 93.4% / 870 | 92.9% / 827 |
| naive zero | 76.1% / 272 | 75.6% / 247 |

**The cap costs about 0.2 points across the portfolio** — smaller than the
0.6 an earlier 40-series sample suggested. That gap is itself a sampling
artefact of the kind that killed the lumpy claim, and it is the reason this page
now reports the whole portfolio.

The constraint is concentrated rather than spread: the delivery factor averages
0.916, with **70 of 90 buckets entirely unconstrained** and the worst at 0.21.
A shortfall in a fifth of buckets moves a portfolio average very little and can
still be severe for the SKUs in those buckets.

## Three limitations, because a crude number presented cleanly is worse than none

**It is not a clean lower bound.** Erratic demand scores *higher* under the cap.
That is not an error: an order-up-to policy that under-receives sees a lower
position and orders more, and the larger later orders overshoot. The cap changes
the policy's behaviour, not just its supply. So the range is indicative, not a
bound in either direction.

**The horizons do not match.** `rccp` runs on the forward horizon; the service
backtest runs on a holdout inside history. The factor is applied cyclically as a
stationary approximation — the *shape* of the constraint this plant exhibits, not
the actual constraint on those buckets.

**It probably understates the impact.** The simulation's order-up-to policy
produces smoother, smaller orders than `netreq`'s lot-sized plan. The capacity
shortfall was measured against the lumpier plan and applied to the smoother one,
so the real bite is likely larger than 0.6 points.

## What would close this properly

Capacity-feasible lot sizing — the CLSP — so that the plan `rccp` checks is one
the plant can actually make, and the service backtest replays a feasible plan
rather than an aspirational one. Deliberately not built here: it is a different
and much harder problem, and a crude honest range is worth more today than a
precise number three items away.
