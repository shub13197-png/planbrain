# Planning Brain

An open-source supply chain **planning** layer for small manufacturers who
cannot afford SAP, o9 or Kinaxis. It sits on top of the system of record they
already have — InvenTree, Tally, Zoho, a spreadsheet — and does not replace it.

We plan. We do not transact.

---

## Start here: what this project is actually offering

**The engines are ordinary. The method is not.**

Measured against a well-tuned reorder point on a 222-SKU portfolio, this tool
wins clearly on **one** demand pattern, loses on another, and is close to a tie
overall. **A well-tuned spreadsheet rule is hard to beat**, and most planning
software that claims otherwise has not checked carefully.

What this repo offers instead is a way of working that makes its own numbers
trustworthy:

* **Every threshold committed before the run**, in its own commit, so `git log`
  proves the rule was not fitted to the result.
* **Kill conditions stated in advance.** One claim was scheduled for removal
  before its evidence existed, and survived.
* **A headline check that failed, published anyway.** The reconciliation's
  residual turned out to be an algebraic identity that could not fail. Both the
  worthless number and its replacement are still reported, side by side.
* **A retracted claim, corrected in place.** "Lumpy demand is where we excel"
  died on its own full-portfolio run. The retraction is in the README, not a
  commit message.
* **Four negative results kept in**, because they are the useful ones.

If you are evaluating planning tools, the fill rates below are worth less to you
than the fact that they were produced this way. Anyone can show you a number.

The method is written up on its own, domain-independent, in
[`docs/method.md`](docs/method.md).

## And the limit on all of it: this is a synthetic world we built

Every number in this repository was measured against a demo dataset **we wrote
ourselves**. The five-seed audit tests sensitivity to sampling *within one model
of a plant*. It does not test whether that model resembles a real one.

So a claim holding on 5 of 5 seeds is evidence against a fluke. **It is not
evidence about your data.** The demand patterns, the cost structure, the BOM
depth, the lead times and the changeover economics were all chosen by us, and
choosing them differently would move the results — we know this because it
already happened twice, when a full-portfolio run killed one claim and a
five-seed run weakened another.

This is stated here, at the top, rather than in a gaps list at the bottom,
because it is the single most important qualification on everything below.

The only cure is real data. That is what the importer is for, and
`--check` will tell you what stands between your spreadsheets and a plan without
you committing to anything.

**There is now a first run against real data** — a UK retailer's transaction log,
2,947 stock codes, in [`docs/benchmark.md`](docs/benchmark.md). Against the tools
a small business actually has, the incumbents need **8–30% more stock to hold the
same service**, and the margin is widest where inventory is tightest. It also
**disagrees with the synthetic retraction below** about lumpy demand. One dataset
does not settle that, and both results stay published until a second and third
do.

---

## Does it actually work? Here is the evidence.

Every planning tool claims better forecasts. That claim is cheap, so this repo
leads with the measurement instead — including the parts where a spreadsheet rule
matches us and where it beats us.

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

**Said plainly: a well-tuned reorder point is hard to beat.** After ten build
items of engine work, the results have thinned to one demand pattern plus a
comparison against a policy nobody would ship. That is the honest finding, and
it is stated here rather than left for a reader to infer from the tables.

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

### Everything measured on a sample has been wrong

Three published results were drawn from a 40-of-222 sample. **All three changed
when re-run on the full portfolio** — one reversed outright:

| result | on 40 series | on all 222 |
|---|---|---|
| lumpy demand vs a tuned reorder point | we win, 96.2 vs 95.3 | **they win, 92.8 vs 93.7** |
| cost of the capacity cap | 0.6 points | 0.2 points |
| what staleness costs | 3.7 points | 2.3 points |

Every table on this page is now the whole portfolio. Sampling is for exploring;
it is not for publishing.

### Every claim re-run across five seeds

Sampling killed one claim, so the rest were audited the same way — full
portfolio, five seeds ([`docs/claim-audit.md`](docs/claim-audit.md)):

| claim | mean | range | verdict |
|---|---|---|---|
| Intermittent: fitted beats a tuned reorder point | +2.20 pts | 1.64 to 2.66 | holds 5/5 |
| ~~Lumpy: fitted beats a tuned reorder point~~ | −1.15 pts | −1.60 to −0.68 | **0/5, retracted** |
| Staleness costs fill rate | +2.37 pts | 1.89 to 3.44 | holds 5/5 |
| Plan is not capacity-feasible | 32.8% of buckets | 30.7 to 37.3 | holds 5/5 |
| Greedy fairness leaves a solver little room | 0.01 Jain | 0.00 to 0.03 | **3/5, weakened** |

The staleness result was the one at risk — the drift that rescued it was added
in a single pass on a single seed. It holds on all five.

The fairness one did not fare as well, and is corrected in `docs/haulplan.md`:
the "a solver could add almost nothing" figure came from seed 7, and across
seeds the headroom is up to ten times larger.

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

### Audited property, not a claim

**5. Runs fully offline. No data leaves the machine.**

This one is different in kind from the four above. They are measurements that
could come out differently on your data. This is a property of the build, and it
is verified rather than argued:

| check | what it establishes | where |
|---|---|---|
| CI runs the pipeline and the whole test suite with `--network=none` | the pipeline needs nothing from the network — no interface exists, so a leak cannot succeed | `.github/workflows/ci.yml`, job `offline` |
| In-process socket block, tested **with** a network available | a stray call fails loudly on a customer's laptop rather than succeeding in silence | `tests/test_offline.py::test_the_whole_pipeline_runs_with_sockets_blocked` |
| Dependency audit across all 29 runtime distributions | no telemetry, no version checks, no model downloads; the two conditional paths checked individually | [`docs/offline.md`](docs/offline.md) |

Both halves matter. The container proves the pipeline does not *need* the
network; the in-process guard covers the machine where the network *works*. The
gaps the guard cannot cover — native code, subprocesses — are named in the audit
rather than glossed.

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

* **Capacity checking detects and explains; it still does not fix.** Pricing
  changeover into the lot size cuts capacity load substantially, but buckets
  stay overloaded: the plan fits on average and not bucket by bucket.
  `--explain` now attributes every overloaded bucket to the SKUs that loaded it
  and shows the spare hours nearby — on the demo the worst single SKU accounts
  for **4%** of the excess, which is the answer: this is a capacity decision,
  not a scheduling one. It will not choose what to move, because moving
  production earlier needs the components earlier and capacity planning cannot
  see whether they are there. Steering to a per-bucket limit is the CLSP and
  stays out of scope. **The headline service table assumes unlimited capacity;
  a capacity-capped range is in
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
  must each depot hold*. Time-phased distribution (DRP) is deferred, not solved,
  and it is the largest remaining gap on this list.
* **Distances are straight-line.** Real road distances need a self-hosted OSRM
  with a local OSM extract, which is infrastructure rather than code, and the
  offline guarantee rules out a hosted routing API.
* **Safety stock as a service level is available and is weak.**
  `--service-level 0.95` implements the textbook form, and then the measurement
  says nineteen points of requested service move achieved fill by 1.7 — because
  a cycle service level is not a fill rate and the order-up-to level is
  dominated by the forecast. Lumpy demand is worst at every level, which is the
  normal approximation failing on the pattern this product is for. Days of cover
  stays the default. The whole table is in
  [`docs/service-backtest.md`](docs/service-backtest.md).
* **Backorders are available; lost sales stays the default.** With
  `--unmet backorder` the naive policy serves 58% on time and 99.5% eventually,
  which is why the headline fill rate remains *on-time* under both rules and the
  softer number is reported under its own name rather than blended in.
* **The desktop shell is not compiled in this environment**, and the first time
  anyone launched it, it was broken. The Tauri wrapper is checked here only by
  cross-file rules — the path the shell resolves against the path the bundle
  installs, the plugins the frontend calls against the crates that provide them.
  A CI-built Windows installer was eventually downloaded, checksum-verified,
  extracted and run: the window opened and rendered, and the status bar read
  `starting…` forever, because the backend's opening line was emitted before the
  interface had attached its listeners and was dropped on every launch. Nothing
  in either file was wrong; the *order* was. It is fixed and buffered now, and
  the fix has not itself been launched — see [`docs/decisions.md`](docs/decisions.md).
  **Cross-file rules cannot see a race, and this is the standing reason to keep
  launching the thing.**
* **A planner cannot overrule the plan.** The order list says what the
  arithmetic wants; a planner who knows a customer committed verbally, or that a
  machine is down on Tuesday, has nowhere to put that. The mechanism is designed
  — the textbook firm planned order, with provenance beside the fact rather than
  inside it — and deliberately not yet built, in
  [`docs/overrides.md`](docs/overrides.md).
* **Inventory value uses synthetic costs.** Working capital is now reported
  alongside units, but the demo's unit costs are generated, so the *ratios*
  between policies mean something and the absolute figures do not.

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
| [`docs/orders.md`](docs/orders.md) | The order list a planner acts on, and the gap it closed |
| [`docs/benchmark.md`](docs/benchmark.md) | **Real demand, real incumbents** — the service/inventory frontier against a spreadsheet and an ERP's min/max |
| [`docs/status.md`](docs/status.md) | Where the work stands and what is next — read this first |
| [`docs/overrides.md`](docs/overrides.md) | **Designed, not built** — letting a planner overrule the plan |
| [`docs/forecast.md`](docs/forecast.md) | Model selection and MASE, including its limits |
| [`docs/rccp.md`](docs/rccp.md) | Rough-cut capacity, and what it does not yet prove |
| [`docs/capacity-sizing.md`](docs/capacity-sizing.md) | How the demo plant was sized, written before it was run |
| [`docs/reconciliation.md`](docs/reconciliation.md) | Why netreq and the simulation report different stock |
| [`docs/haulplan.md`](docs/haulplan.md) | The long-haul fairness ledger, and an ordering bug it caught |
| [`docs/constants.md`](docs/constants.md) | Every committed constant, which item set it, and what it must agree with |
| [`docs/audit.md`](docs/audit.md) | Manual code audit: what was removed and what was left alone |
| [`docs/method.md`](docs/method.md) | **How this was built — written to transfer to any measurement work** |
| [`docs/offline.md`](docs/offline.md) | The offline audit: every dependency, both conditional paths, and what the guard cannot cover |
| [`docs/packaging.md`](docs/packaging.md) | Tauri shell, PyInstaller sidecar, and why IPC is stdio not localhost |
| [`docs/install.md`](docs/install.md) | Installing, including the Gatekeeper and SmartScreen warnings you will see |
| [`docs/mapping.md`](docs/mapping.md) | Column mapping: the Excel realities it handles, and why suggestions stay dumb |
| [`docs/mapping-bakeoff.md`](docs/mapping-bakeoff.md) | Whether a 4B model earns 2.5 GB — thresholds committed first, baseline measured, bake-off blocked |
| [`datasets/README.md`](datasets/README.md) | Public-data fetch scripts — outside the app boundary, and enforced to stay there |
| [`docs/claim-audit.md`](docs/claim-audit.md) | Every claim re-run across five seeds, and the one it weakened |
| [`docs/unit-costs.md`](docs/unit-costs.md) | How costs are derived, written before they were computed |
| [`docs/demo.md`](docs/demo.md) | The seeded dataset |

`docs/decisions.md` records rejections as prominently as decisions. Rejections
are the more useful half: they stop the same choice being relitigated in three
weeks.

## Installing it

For a planner rather than a developer: take an installer from the
[releases page](https://github.com/shub13197-png/planbrain/releases). Windows gets
an `.msi` (or an `-setup.exe` where policy blocks MSI), macOS gets a `.dmg` for
Apple Silicon and another for Intel. Every release carries `SHA256SUMS.txt` so
you can check the download is what CI built.

There is no account, no sign-in and no network access at any point. The
application is one program and one SQLite file; uninstalling leaves your data
alone.

**The installers are not code-signed**, so Windows and macOS will both show a
warning that sounds worse than it is — signing identifies a publisher, it does
not inspect code. [`docs/install.md`](docs/install.md) says exactly which dialog
you will see, which button is hidden behind "More info", and what to do on
Sequoia where the old right-click trick no longer works. Read it before you
start; the alternative is concluding the download is broken.

**Nobody has installed one yet.** The workflow that builds them
(`.github/workflows/release.yml`) has never run, because there is no Rust
toolchain in the environment this was developed in — see the unverified-surfaces
table in [`docs/packaging.md`](docs/packaging.md), which lists what CI-green
would and would not prove.

## Running it

```bash
docker compose up                          # the whole pipeline, ~1 minute
docker run --rm --network=none planbrain python -m tools.demo   # same, no network at all
```

or without Docker:

```bash
pip install -e ".[dev]"
python -m tools.demo                       # the same end-to-end run
pytest -q                                  # the whole suite, about 90s
python -m tools.check_fact_access          # the CI gate
python -m tools.seed_demo                  # build the demo database
python -m tools.service_report --sample 40 # the evidence above
python -m tools.service_report --sweep     # service-vs-inventory frontier
python -m tools.capacity_report            # can the plant make the plan?
python -m tools.capacity_report --lot-sizing cost_based
python -m tools.reconcile_report           # why the two engines differ
python -m tools.import_data --check data/  # validate your own spreadsheets
```

## Growth assumptions

A planner can say "we expect to grow 8% a year" and see what it does to the
plan:

```bash
python -m tools.capacity_report --source forecast --lot-sizing cost_based                                 --demand-growth 8 --capacity-growth 3
```

**Two parameters, never one control.** Demand growth scales what customers take;
capacity growth scales what the plant can make. A single knob moving both would
report a comfortable factory at every setting — which is the answer a planner is
least likely to question, and precisely the one capacity checking exists to
withhold.

**One source of trend.** `AutoETS` already fits a trend on 21 of 103 smooth and
erratic series in the demo, so a blanket overlay would grow those twice while
the Croston and TSB series grew once — two populations, different arithmetic,
nothing failing. When a growth rate is set, the fitted trend is suppressed and
the run reports how many series that cost. The rules were written down before
the code, in [`docs/forecast.md`](docs/forecast.md).

**Zero is a genuine no-op**, asserted rather than assumed: setting the rate to
zero produces byte-identical numbers, and every published figure above is
unchanged by the feature existing. A planner who has not opted into an
assumption is not silently given one.

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
