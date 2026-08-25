# Service backtest — fill rate against inventory held

Build item 5, and the proof-of-value report. Everything else in this repo is
machinery for producing the table below.

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

| policy | fill rate | avg on-hand | units short |
|---|---|---|---|
| fitted forecast | **95.7%** | 1,208 | 4,410 |
| naive zero forecast | 78.2% | 282 | 21,483 |
| reorder point (s,S) | 94.1% | 824 | 11,462 |

By demand pattern, fill rate / average on-hand:

| policy | erratic | intermittent | lumpy | smooth |
|---|---|---|---|---|
| fitted forecast | 98.7% / 1,189 | 98.7% / 1,023 | **84.4% / 868** | 99.9% / 1,719 |
| naive zero | 91.3% / 249 | 70.6% / 286 | **48.1% / 36** | 97.7% / 535 |
| reorder point | 97.7% / 842 | 97.8% / 828 | 82.0% / 910 | 97.6% / 722 |

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
| reorder point | 94.1% / 824 | flat — | flat — | flat — | flat — |

The reorder point's row is flat by construction: its buffer comes from demand
variability, not the safety-days setting.

### The honest reading, including the uncomfortable part

**The fitted forecast is competitive with a well-tuned reorder point, not
dramatically better than it.** At 3 days of safety it reaches 95.1% on 916 units
of stock against the incumbent's 94.1% on 824 — about a point of service for
about 11% more inventory. At zero safety it is behind on both.

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
