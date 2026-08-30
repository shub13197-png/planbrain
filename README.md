# Planning Brain

An open-source supply chain **planning** layer for small manufacturers who
cannot afford SAP, o9 or Kinaxis. It sits on top of the system of record they
already have — InvenTree, Tally, Zoho, a spreadsheet — and does not replace it.

We plan. We do not transact.

---

## Does it actually work? Here is the evidence.

Every planning tool claims better forecasts. That claim is cheap, so this repo
leads with the measurement instead — including the part where a spreadsheet rule
matches us.

The demo is a fake lubricant blending plant: 200 SKUs, three BOM levels, 18
months of deliberately messy daily history. The test replays a held-out 90-day
window under four policies and measures what service each achieved and what
stock it had to carry to achieve it.

```bash
python -m tools.service_report --sample 40
```

**40 of 242 series, 90-day holdout, 7 days safety stock:**

| policy | fill rate | avg on-hand | units short |
|---|---|---|---|
| fitted forecast (this tool) | **97.9%** | 1,346 | 6,674 |
| reorder point, tuned | 95.2% | 793 | 25,228 |
| reorder point, stale | 91.9% | 786 | 36,975 |
| naive zero forecast | 77.8% | 269 | 40,067 |

### Read that honestly

**At portfolio level this tool buys about 2.7 points of fill rate over a
continuously tuned spreadsheet rule, for roughly 70% more inventory.** That is a
real gain and not a large one, and whether it is worth the working capital
depends on what a stockout costs the customer — which this repo cannot price.

Against a **stale** reorder point — parameters set once and never revisited,
which is what most SMEs actually run — the gap is 6.0 points.

If forecast accuracy across a whole portfolio were the pitch, the pitch would be
weak. It is not the pitch.

### Where it is genuinely better

Fill rate / average on-hand, by demand pattern:

| policy | smooth | erratic | intermittent | **lumpy** |
|---|---|---|---|---|
| fitted forecast | 99.2% / 1,910 | 98.8% / 1,297 | 95.8% / 731 | **97.1% / 1,021** |
| reorder point, tuned | 96.3% / 725 | 96.9% / 671 | 93.9% / 621 | 94.0% / 1,040 |
| reorder point, stale | 96.7% / 783 | 97.9% / 867 | 87.3% / 583 | 86.0% / 898 |
| naive zero | 95.9% / 509 | 83.3% / 151 | 66.0% / 90 | **58.0% / 106** |

**Lumpy demand is where this tool is clearly ahead** — 97.1% fill on 1,021 units
against a tuned reorder point's 94.0% on 1,040: better service on slightly less
stock. Rare, large, unpredictable orders are what a spreadsheet handles worst,
and they are common in industrial distribution.

**Staleness is the other clear gap.** A set-once reorder point loses 6.6 points
on intermittent demand and 8.0 on lumpy against a continuously tuned one. That
is the evidence for claim 3, and it only appeared once the demo carried
sustained demand drift — on a stationary history the gap was 0.8 points and the
claim was not supportable.

### The result that settles a methodological argument

The bottom row is not a straw man. **A forecast of zero is close to optimal on
MASE for intermittent demand** — it is right on every quiet day and wrong only
on the few days that matter. Standard forecast-accuracy metrics rank it well.

It is also a policy that barely orders anything: **70.6% fill on intermittent
demand, 48.1% on lumpy, and 4.4% with no safety stock.**

Accuracy was never the objective. That is why this repo measures service against
inventory instead, and why it publishes both numbers together — a policy hits
any fill rate by holding enough stock, and holds almost no stock by serving
nobody.

---

## What this claims, and what it does not

**Claims:**

1. **Lumpy and intermittent demand.** Strong evidence, above.
2. **Capacity awareness.** A reorder point has no concept of a blender being
   full. `rccp` loads the plan onto resources and reports whether the plant can
   make it. **It detects infeasibility; it does not yet produce feasible plans** —
   pricing changeover into the lot size cuts capacity load 41% and still leaves
   35 of 450 resource-buckets over. See the gaps.
3. **Parameters that stay fitted rather than going stale.** A set-once reorder
   point loses 6.6 points of fill rate on intermittent demand and 8.0 on lumpy.
4. **Detecting a mis-set changeover budget.** The demo plant is provisioned for
   1,029 campaigns over the horizon; its own cost structure calls for 1,680 — a
   **57% shortfall in changeover hours**, invisible in any utilisation report
   because aggregate capacity looks adequate. Requires both a routing model and
   a lot-sizing economics model to compare, which a spreadsheet has neither of.
   See [`docs/rccp.md`](docs/rccp.md).

   Stated separately from claim 2 on purpose. It does **not** explain the
   infeasibility — correcting the budget entirely removes only 9 of 147
   overloaded buckets.

This third claim was **scheduled for removal before the evidence existed**. The
demo history originally had no sustained demand drift, and on that data a stale
reorder point lost only 0.8 points — not enough to support the claim. The
commitment, written down before the test was run, was that if adding drift
changed nothing the claim would be **dropped, not softened**. Drift widened the
gap to 3.3 points overall and 8.0 on lumpy demand, so it stands. A claim that
survived a stated kill condition is worth more than one that was never at risk.

**Does not claim:** better forecast accuracy across a portfolio. The numbers
above are why.

## Gaps, stated plainly

* **Capacity checking detects, it does not yet fix.** Pricing changeover into
  the lot size takes the demo plan from 127% to 75% overall utilisation, but 35
  of 450 resource-buckets stay overloaded: the plan fits on average and not
  bucket by bucket. Steering to a per-bucket limit is the CLSP, which is out of
  scope. **The service table above assumes unlimited capacity.**
* **The capacity win costs working capital.** Pricing changeover into the lot
  size trades a **31% cut in capacity load for a 16% rise in inventory value**.
  Whether that is worth taking depends on how tight the plant is. Costs are
  synthetic in the demo; a real deployment reads them from the system of record.
* **`netreq`'s plan and the service simulation's policy are different things.**
  The difference is now decomposed into named terms rather than unexplained —
  see [`docs/reconciliation.md`](docs/reconciliation.md) — but the two engines
  still answer different questions and are not expected to agree.
* **Single echelon.** The plan answers *what must the plant make* and not *what
  must each depot hold*. Time-phased distribution (DRP) is deferred, not solved.
* **No cost model.** Inventory is reported in units, not working capital.
* **Safety stock is days of cover**, not a solved service-level target.
* **Unmet demand is modelled as lost, not backordered.** The conservative
  reading; revisit first if a customer genuinely backorders.

## Scope boundaries — refused, not "not yet"

No order management or ATP. No warehouse execution. No accounting, costing, GST
or e-way bills. No transport execution. If a request implies any of those, the
answer is that we import from the system that does it.

---

## How it is built

| | |
|---|---|
| **Master data** | InvenTree (MIT), read-only, plugin apps only |
| **Forecasting** | statsforecast (Apache-2.0) — AutoETS, Croston, TSB |
| **Our code** | `netreq` (MRP), `forecast`, `simulate`, `rccp`, `haulplan`, `importer` |
| **Licences** | MIT / Apache-2.0 / BSD only. No AGPL, no GPL, deliberately |

Each engine is layered the same way: a **pure core** that knows only lists of
numbers and is tested against textbook fixtures, and a thin adapter that owns
scenarios and dates. A fixture failure points at one page of arithmetic rather
than somewhere between there and the database.

### Two rules the code enforces rather than documents

**Fact storage is sparse — an absent row means zero.** A direct SELECT that
inner-joins drops the zero buckets and biases every statistic upward, worst on
exactly the intermittent SKUs that matter most. So `read_facts` and
`write_facts` are the only paths in or out, and `tools/check_fact_access.py`
fails CI on any direct fact-table access outside a short allowlist.

**A mean never travels without its denominators.** `ScoredMean` has no
`__float__`, so a fill rate cannot silently become a number without its scored
and unscored counts. Series that cannot be scored are counted, never dropped —
quietly excluding the hard ones is how a portfolio average gets improved.

## Documentation

| | |
|---|---|
| [`docs/service-backtest.md`](docs/service-backtest.md) | The proof-of-value report in full |
| [`docs/build-order.md`](docs/build-order.md) | Live plan and what is done |
| [`docs/decisions.md`](docs/decisions.md) | What was decided, and what was rejected and why |
| [`docs/contracts/facts.md`](docs/contracts/facts.md) | The fact grain and the sparse rule |
| [`docs/netreq.md`](docs/netreq.md) | Time-phased MRP |
| [`docs/forecast.md`](docs/forecast.md) | Model selection and MASE, including its limits |
| [`docs/rccp.md`](docs/rccp.md) | Rough-cut capacity, and what it does not yet prove |
| [`docs/capacity-sizing.md`](docs/capacity-sizing.md) | How the demo plant was sized, written before it was run |
| [`docs/reconciliation.md`](docs/reconciliation.md) | Why netreq and the simulation report different stock |
| [`docs/haulplan.md`](docs/haulplan.md) | The long-haul fairness ledger, and an ordering bug it caught |
| [`docs/constants.md`](docs/constants.md) | Every committed constant, which item set it, and what it must agree with |
| [`docs/unit-costs.md`](docs/unit-costs.md) | How costs are derived, written before they were computed |
| [`docs/demo.md`](docs/demo.md) | The seeded dataset |

`docs/decisions.md` records rejections as prominently as decisions. Rejections
are the more useful half: they stop the same choice being relitigated in three
weeks.

## Running it

```bash
pip install -e ".[dev]"
pytest -q                                  # 421 tests
python -m tools.check_fact_access          # the CI gate
python -m tools.seed_demo                  # build the demo database
python -m tools.service_report --sample 40 # the evidence above
python -m tools.service_report --sweep     # service-vs-inventory frontier
python -m tools.capacity_report            # can the plant make the plan?
python -m tools.capacity_report --lot-sizing cost_based
python -m tools.reconcile_report           # why the two engines differ
```
