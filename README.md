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
| fitted forecast (this tool) | **95.7%** | 1,208 | 4,410 |
| reorder point, tuned | 94.1% | 824 | 11,462 |
| reorder point, stale | 93.3% | 824 | 15,225 |
| naive zero forecast | 78.2% | 282 | 21,483 |

### Read that honestly

**At portfolio level this tool is competitive with a spreadsheet reorder point,
not dramatically better than it.** About a point or two of fill rate for roughly
11% more inventory. At zero safety stock it is behind on service. A "stale"
reorder point — parameters set once and never revisited, which is what most SMEs
actually run — loses only 0.8 points to a continuously tuned one.

If forecast accuracy across a whole portfolio were the pitch, the pitch would be
weak. It is not the pitch.

### Where it is genuinely better

Fill rate / average on-hand, by demand pattern:

| policy | smooth | erratic | intermittent | **lumpy** |
|---|---|---|---|---|
| fitted forecast | 99.9% / 1,719 | 98.7% / 1,189 | 98.7% / 1,023 | **84.4% / 868** |
| reorder point, tuned | 97.6% / 722 | 97.7% / 842 | 97.8% / 828 | 82.0% / 910 |
| naive zero | 97.7% / 535 | 91.3% / 249 | 70.6% / 286 | **48.1% / 36** |

**Lumpy demand is the one class where this tool gets better service on *less*
stock** — 84.4% on 868 units against 82.0% on 910. Rare, large, unpredictable
orders are exactly what a spreadsheet handles worst, and they are common in
industrial distribution.

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
   make it — including work scheduled on days it is closed, which nothing
   upstream can see. **It currently detects infeasibility rather than producing
   feasible plans**; see the gaps.
3. **Parameters that stay fitted rather than going stale.** Currently the
   weakest of the three; see the gaps below.

**Does not claim:** better forecast accuracy across a portfolio. The numbers
above are why.

## Gaps, stated plainly

* **Capacity checking detects, it does not yet fix.** `rccp` correctly reports
  the current demo plan as roughly 3x over capacity. Nothing acts on that:
  closing the loop needs lot sizing that prices changeover, then campaign
  sequencing. The service table above assumes unlimited capacity.
* **The demo plant is not capacity-balanced.** Its routings were generated
  without reference to its demand, so run time alone exceeds total capacity by
  54%. The demo can evidence "we detect infeasible plans" and cannot yet
  evidence "we produce feasible ones".
* **The demo history has no sustained demand drift** — lifecycle events yes,
  drift no. Staleness bites hardest under drift, so the stale comparator is
  under-tested and claim 3 is under-evidenced.
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
| [`docs/demo.md`](docs/demo.md) | The seeded dataset |

`docs/decisions.md` records rejections as prominently as decisions. Rejections
are the more useful half: they stop the same choice being relitigated in three
weeks.

## Running it

```bash
pip install -e ".[dev]"
pytest -q                                  # 317 tests
python -m tools.check_fact_access          # the CI gate
python -m tools.seed_demo                  # build the demo database
python -m tools.service_report --sample 40 # the evidence above
python -m tools.service_report --sweep     # service-vs-inventory frontier
python -m tools.capacity_report            # can the plant make the plan?
```
