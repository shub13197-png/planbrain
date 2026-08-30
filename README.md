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
python -m tools.service_report --sample 0     # the whole portfolio
```

**All 222 series, 90-day holdout, 7 days safety stock.** Whole portfolio, not a
sample — an earlier version of this table quoted a 40-series sample and one of
its conclusions did not survive the full run. See *What changed when we stopped
sampling*, below.

| policy | fill rate | avg on-hand | units short |
|---|---|---|---|
| fitted forecast (this tool) | **97.2%** | 1,301 | 26,034 |
| reorder point, tuned | 95.7% | 880 | 109,154 |
| reorder point, stale | 93.4% | 870 | 192,687 |
| naive zero forecast | 76.1% | 272 | 237,101 |

### Read that honestly

**At portfolio level this tool buys about 1.5 points of fill rate over a
continuously tuned spreadsheet rule, for roughly 48% more inventory.** That is a
real gain and not a large one, and whether it is worth the working capital
depends on what a stockout costs the customer — which this repo cannot price.

Against a **stale** reorder point — parameters set once and never revisited,
which is what most SMEs actually run — the gap is 2.3 points.

If forecast accuracy across a whole portfolio were the pitch, the pitch would be
weak. It is not the pitch.

### Where it is genuinely better

Fill rate / average on-hand, by demand pattern:

| policy | smooth | erratic | **intermittent** | lumpy |
|---|---|---|---|---|
| fitted forecast | 99.6% / 1,986 | 98.2% / 1,236 | **97.3% / 948** | 92.8% / 898 |
| reorder point, tuned | 96.9% / 915 | 95.9% / 796 | 95.6% / 849 | **93.7% / 939** |
| reorder point, stale | 97.1% / 952 | 95.4% / 834 | 91.1% / 828 | 89.3% / 846 |
| naive zero | 94.1% / 541 | 89.7% / 274 | **63.0% / 144** | **55.1% / 79** |

**Intermittent demand is where this tool is ahead** — 97.3% fill against a tuned
reorder point's 95.6%, bought with about 12% more stock.

### What changed when we stopped sampling

An earlier version of this README, computed on a 40-series sample, claimed lumpy
demand was where the tool was clearly ahead. **On the full portfolio it is not.**
A tuned reorder point reaches 93.7% on lumpy against this tool's 92.8% — better
service, on about 5% more stock. On a service-per-unit-of-stock basis the two
are close to indistinguishable.

That correction is left in rather than quietly overwritten, because the sample
size was the difference and a reader is entitled to know a published claim did
not survive a larger run. Everything on this page is now the whole portfolio.

**Staleness is the other real gap.** A set-once reorder point loses 4.5 points on
intermittent demand and 4.4 on lumpy against a continuously tuned one. That is
the evidence for claim 3, and it only appeared once the demo carried sustained
demand drift — on a stationary history the gap was 0.8 points and the claim was
not supportable.

### The result that settles a methodological argument

The bottom row is not a straw man. **A forecast of zero is close to optimal on
MASE for intermittent demand** — it is right on every quiet day and wrong only
on the few days that matter. Standard forecast-accuracy metrics rank it well.

It is also a policy that barely orders anything: **63.0% fill on intermittent
demand and 55.1% on lumpy**, against 97.3% and 92.8% for the fitted forecast.
This is the one comparison on the page that is not close.

Accuracy was never the objective. That is why this repo measures service against
inventory instead, and why it publishes both numbers together — a policy hits
any fill rate by holding enough stock, and holds almost no stock by serving
nobody.

---

## What this claims, ranked by how well evidenced each claim is

Four claims of quite different strength. Presenting them as four equal bullets
would overstate the weak ones.

### Strong — measured on the full portfolio

**1. Intermittent demand.** 97.3% fill against a tuned reorder point's 95.6%
across 66 series, bought with about 12% more stock. The supporting result is
stronger than the gap: a forecast of zero, which is close to optimal on standard
accuracy metrics for this class, delivers **63.0%**. That is not a close call and
it settles what the objective should be.

### Solid, and smaller than it sounds

**2. Parameters that stay fitted rather than going stale.** A set-once reorder
point loses **4.5 points on intermittent demand and 4.4 on lumpy** against a
continuously tuned one. This claim was **scheduled for removal before the
evidence existed** — on a stationary demand history the gap was 0.8 points and
would not have supported it. The commitment, written down before the test ran,
was that it would be dropped rather than softened if adding drift changed
nothing. Drift changed it, so it stands.

### Scoped to detection, not correction

**3. Capacity awareness.** A reorder point has no concept of a blender being
full. `rccp` loads the plan onto resources and reports **85% overall utilisation
with 139 of 450 resource-buckets over** — the plan fits on average and clumps in
time. It **detects infeasibility and does not produce feasible plans**. Steering
to a per-bucket limit is the CLSP, out of scope and said so in advance.

### Quantified, real, and the smallest of the four

**4. Detecting a mis-set changeover budget.** The demo plant is provisioned for
changeover on a 14-day campaign cycle while its own cost structure calls for one
nearer 9 days. Invisible in a utilisation report because aggregate capacity
looks adequate, and it needs both a routing model and a lot-sizing economics
model to see. It explains **fewer than one overloaded bucket in ten**, so it is
a capability rather than an explanation.

**Not claimed:** better forecast accuracy across a portfolio. The numbers above
are why — and see the correction on lumpy demand.

## The most useful thing in this repo is a check that failed

The reconciliation between the planning engine and the service simulation
reported a residual of **exactly −0.0** across four named terms. It looked like
strong evidence. It was worthless.

Every term was defined as a difference between adjacent rungs of a ladder, so
they summed to the gap as an **algebraic identity**. Five random numbers produce
the same zero — there is a test that does exactly that. No injected error could
move it, because perturbing a rung changed both sides equally and they cancelled.

The replacement is a **cross-engine** residual: the same quantity computed by two
separately written implementations. It moves proportionally with an injected
error, catches a one-bucket schedule shift, and had already caught a real bug —
an order placed at zero lead time that was silently never delivered.

**Both residuals are still reported side by side, labelled.** Quietly dropping
the weak one would hide that an earlier result had been overstated.

That is the standard the rest of these numbers are held to. Every headline figure
in this repo had its rule and its threshold committed in a **separate commit
before the run** — verifiable in `git log` — precisely so that a rule written
after seeing a result cannot masquerade as one written before it.

## Gaps, stated plainly

* **Capacity checking detects, it does not yet fix.** Pricing changeover into
  the lot size cuts capacity load substantially, but 139 of 450 resource-buckets
  stay overloaded: the plan fits on average and not bucket by bucket. Steering
  to a per-bucket limit is the CLSP, which is out of scope. **The headline
  service table assumes unlimited capacity; a capacity-capped range is in
  [`docs/service-backtest.md`](docs/service-backtest.md).**
* **The capacity win costs working capital.** Pricing changeover into the lot
  size trades capacity load against inventory value. Whether that is worth
  taking depends on how tight the plant is. Costs are synthetic in the demo; a
  real deployment reads them from the system of record.
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
| [`docs/audit.md`](docs/audit.md) | Manual code audit: what was removed and what was left alone |
| [`docs/unit-costs.md`](docs/unit-costs.md) | How costs are derived, written before they were computed |
| [`docs/demo.md`](docs/demo.md) | The seeded dataset |

`docs/decisions.md` records rejections as prominently as decisions. Rejections
are the more useful half: they stop the same choice being relitigated in three
weeks.

## Running it

```bash
docker compose up                          # the whole pipeline, ~1 minute
```

or without Docker:

```bash
pip install -e ".[dev]"
python -m tools.demo                       # the same end-to-end run
pytest -q                                  # 459 tests
python -m tools.check_fact_access          # the CI gate
python -m tools.seed_demo                  # build the demo database
python -m tools.service_report --sample 40 # the evidence above
python -m tools.service_report --sweep     # service-vs-inventory frontier
python -m tools.capacity_report            # can the plant make the plan?
python -m tools.capacity_report --lot-sizing cost_based
python -m tools.reconcile_report           # why the two engines differ
python -m tools.import_data --check data/  # validate your own spreadsheets
```

## Bringing your own data

The importer is the on-ramp, and `--check` validates without writing anything:

```bash
python -m tools.import_data --check /path/to/spreadsheets/
```

Drop `parts`, `bom`, `routings`, `fleet` and `history` in as `.csv` or `.xlsx` —
any subset works. Every problem is reported at once, each naming the file, the
row as your spreadsheet numbers it, the column and the value:

```
parts.csv row 3, column 'lead_time_days': should be a whole number (got 'soon')
history.csv row 2, column 'bucket_date': should be a date as YYYY-MM-DD
                                         (other formats are ambiguous)
```

Thousands separators, currency symbols and integer columns that arrived as
floats are handled rather than pushed back at you. Ambiguous dates are refused
rather than guessed: `03/04/2026` is April in India and March in America, and
guessing wrong shifts a demand history by a month with nothing failing.

Nothing is written unless the whole set is clean. A partial import leaves a
database that looks populated and is missing rows nobody finds until a plan
comes out wrong.
