# Benchmark: real demand, real incumbents

```bash
python datasets/fetch_online_retail.py --out data/online-retail
python -m tools.benchmark --data data/online-retail --out docs/benchmark-run.md
```

## What this answers

The README says, at the top and before any result:

> Every number in this repository was measured against a demo dataset **we wrote
> ourselves**. […] a claim holding on 5 of 5 seeds is evidence against a fluke.
> **It is not evidence about your data.** […] The only cure is real data.

This is the first run against real data. A UK online gift retailer's transaction
log — [UCI Online Retail II][uci], 1 December 2009 to 9 December 2011, 1,035,621
invoice lines over 4,873 stock codes, of which **2,947 clear the eligibility
bar**: at least twelve demand events and at least a year of shelf life.

The archive is checksum-pinned in the fetch script, so a re-run is a re-run of
the same input.

[uci]: https://doi.org/10.24432/C5CG6D

## The comparison, and why it is a curve

**Any inventory policy reaches any fill rate if you let it hold enough stock.**
A single number — "97% fill" — is therefore not a result. Every policy here is
run at five safety settings and both axes are reported at each, so a policy is
better only where its curve sits above another's *at the same inventory*.

The incumbents are the real ones, not "no planning at all":

| policy | what it stands for |
|---|---|
| `moving_average` | the spreadsheet: average the last twelve weeks, hold cover, reorder below the line |
| `reorder_point` | the min/max fields in an ERP or accounting package, kept current |
| `reorder_point_stale` | the same fields, set once by someone who has since left |
| `naive_zero` | the policy a point-accuracy metric like MASE would choose |
| `forecast` | this product |

Every policy gets the same lead time, the same opening stock, the same lot
rounding, and — at each setting — **the same quantity of safety stock**,
`z(α)·σ·√(L+R)`. The only thing that differs is the demand signal each one acts
on, which is the only thing under test.

`moving_average` is structurally identical to `reorder_point` by construction:
same (s, S) shape, same safety term. They differ *only* in the demand estimate —
a twelve-week moving average against a full-history mean. Anything else
differing would make the result a fact about two implementations.

## The headline

At the setting where Planning Brain fills *f* of demand holding *s* units, this
is what each incumbent must hold to fill the same *f*:

| our fill rate | our stock | spreadsheet | ERP min/max | stale min/max |
|---|---|---|---|---|
| 49.9% | 25.9 | *already above it* | *already above it* | *already above it* |
| 67.6% | 46.3 | 60.0 (**+30%**) | *already above it* | 99.8 (+116%) |
| 76.3% | 73.0 | 83.7 (**+15%**) | 85.6 (**+17%**) | never reaches it |
| 79.7% | 90.6 | 98.9 (**+9%**) | 104.9 (**+16%**) | never reaches it |
| 84.1% | 125.0 | 135.0 (**+8%**) | 139.5 (**+12%**) | never reaches it |

*never reaches it* = the incumbent does not get to that fill rate at any setting
tested. *already above it* = the incumbent beats that fill rate at its cheapest
setting, which is a point **against** us and is printed as one.

**Read in the direction a small manufacturer is answerable for:** to hold the
same service, the spreadsheet carries 8–30% more stock and a current ERP min/max
carries 12–17% more. The advantage is largest where inventory is tightest, which
is where a cash-constrained business actually operates, and it narrows as you buy
your way up the curve — if you can afford to bury the problem in stock, the
demand signal matters less.

**And the row that goes against us.** At the cheapest setting — no safety stock
at all — every incumbent serves more than we do. A forecast-driven order-up-to
rule holding nothing has no buffer against its own error; the reorder point's
`s` is a floor that keeps ordering regardless. Nobody runs a portfolio at z = 0,
but the row is in the table because leaving it out is how benchmarks lie.

**The stale reorder point — what most SMEs actually run — cannot reach 76.3% at
any setting tested**, and needs +116% stock to reach 67.6%.

## By demand pattern

The pattern mix of this portfolio: **lumpy 2,740, intermittent 194, erratic 13,
smooth 0.** That is what a real gift retailer's SKU base looks like at daily
granularity, and it is the pattern this product is positioned for.

### Lumpy — 2,274 scored series

| our fill rate | our stock | spreadsheet | ERP min/max | stale min/max |
|---|---|---|---|---|
| 50.9% | 26.7 | *already above it* | *already above it* | *already above it* |
| 68.6% | 47.7 | 60.5 (+27%) | *already above it* | 106.3 (+123%) |
| 77.2% | 75.5 | 84.1 (+11%) | 86.6 (+15%) | never reaches it |
| 80.6% | 93.7 | 100.4 (+7%) | 106.4 (+14%) | never reaches it |
| 84.8% | 129.4 | 137.9 (+7%) | 142.3 (+10%) | never reaches it |

### Intermittent — 162 scored series

| our fill rate | our stock | spreadsheet | ERP min/max | stale min/max |
|---|---|---|---|---|
| 32.8% | 2.3 | *already above it* | *already above it* | *already above it* |
| 51.0% | 3.6 | 4.1 (+16%) | 4.1 (+15%) | 5.1 (+42%) |
| 62.0% | 5.0 | 5.4 (+8%) | 5.5 (+10%) | 7.1 (+42%) |
| 66.8% | 6.0 | 6.3 (+5%) | 6.5 (+9%) | 8.7 (+45%) |
| 72.7% | 7.8 | 8.1 (+3%) | 8.4 (+7%) | never reaches it |

162 series is a small denominator and the margins here are correspondingly soft.
The lumpy table is the one carrying weight.

## This disagrees with our own synthetic result, and we are not resolving it here

The README carries a **retraction**: on the generated 222-SKU portfolio, "lumpy
demand is where we excel" died on a full-portfolio run, and a tuned reorder point
beat this tool 93.7% to 92.8%.

On real data, lumpy is where the margin is largest.

**One dataset does not overturn that retraction and this document does not claim
it does.** They are different worlds: a UK gift retailer against a synthetic
lubricant blender, retail replenishment against multi-level manufacturing, an
assumed 14-day lead time against a generated one. The honest position is that
the synthetic finding and the real finding disagree, both are published, and
**neither is yet the answer** — what would settle it is a second and third real
dataset, ideally from manufacturing. The retraction stays where it is.

## What this does not show

* **Not manufacturing.** Retail replenishment, single echelon, no BOM, no
  capacity constraint, no changeover. The plan this product would produce for a
  plant is not what was tested here.
* **Lead time is assumed** at 14 days; a sales log does not record one. It is
  applied identically to every policy, so it moves every curve together rather
  than biasing the comparison — but it does move the absolute service level, and
  every fill rate here would change under a different assumption.
* **One holdout window**, the last 90 days — which for this retailer contains
  Christmas. That is a hard test and a realistic one, and it is still one window.
* **Fill rates are low in absolute terms** (50–86%). Daily granularity on lumpy
  retail demand with a two-week lead time is a hard setting. The comparison
  between policies is the result; the absolute level is a property of the setup.
* **These figures are not CI-pinned.** Every number in the README is checked on
  every run against a fresh computation by `tools/published.py`. These are not:
  the dataset is a 45 MB download that CI does not have. Reproducibility here
  rests on the pinned archive checksum and the committed constants in
  `docs/constants.md`, not on a gate. That is a weaker guarantee and it is said
  plainly rather than left to be assumed.

## What it found in the product

Running a real portfolio through the engines broke two things a 222-series demo
never reached, both now fixed:

* **`read_facts` could not read more than about 500 keys.** The accessor built
  one OR-ed clause per key and SQLite refused the parse tree at 2,947 stock
  codes. A small manufacturer with three thousand part numbers is entirely
  ordinary; until this was chunked, the application could not read a plan for
  one. `tests/test_access_scale.py`.
* **The shortage screen could never fire.** `netreq` dates a planned receipt at
  the bucket the material is needed, so `projected_on_hand` balances even when
  the covering order should have gone out weeks ago — the real signal is a
  past-due release, and it was being discarded. On the demo the plan carries
  **212 overdue orders across 159 items** and the application reported "nothing
  runs out". `planbrain/alerts.py`.

Both are the same lesson as launching the desktop app for the first time: the
synthetic world was too kind, in ways no test written against it could show.

## Performance, on the same real portfolio

| step | 2,947 SKUs |
|---|---|
| write demand history (460,811 fact rows) | 2.5 s |
| net requirements, explode, lot-size, offset | 9.5 s |
| read the order list (59,625 planned releases) | 3.4 s |
| read the risk list | 0.2 s |

Single-threaded CPython on a laptop, SQLite on disk. A planner's portfolio plans
in about ten seconds, which is the number that decides whether the tool gets used
twice.
