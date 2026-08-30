# How the demo's unit costs are derived

**Written before the costs were computed and before any effect on the plan was
seen. Not revised afterwards.** Same discipline as `docs/capacity-sizing.md`.

## Why this is needed

Item 7 priced inventory holding by the **capacity hours embedded in a unit**.
That produced 216 campaigns across 160 SKUs — most made once a quarter — because
holding was nearly free against a two-hour changeover. The arithmetic was
correct and the answer was operationally absurd.

The diagnosis was that this is a **missing input, not a tuning problem**: a litre
of lubricant costs money to hold because of the material in it, not because of
the machine-minutes. So the demo gets material costs, derived from a stated rule.

**The 25% annual carrying rate is unchanged.** Adjusting it to fix the campaign
length would be fitting a parameter to a desired answer, which is the error this
whole discipline exists to prevent.

## The rule

A standard cost roll-up. Costs are in a synthetic currency unit; a real
deployment takes all of them from the customer's system of record.

**Raw materials** — priced by type, because base stock and additives differ by
roughly an order of magnitude in reality:

| type | cost per litre |
|---|---|
| base oil | 90 |
| additive | 350 |

**Intermediates (blends)** — rolled up from the BOM:

```
cost = sum(child cost x qty_per) + conversion adder
```

with a **conversion adder of 8 per litre** covering energy, labour and yield
loss in blending.

**Finished goods** — rolled up from the BOM plus packaging:

```
cost = sum(child cost x qty_per) + packaging cost
```

with packaging priced by pack size: **1L 12, 5L 22, 20L 45, 26L 52, 210L 180**.

**Capacity time** — a changeover consumes production time that has a cost:

```
changeover cost = setup_hours x 1,500 per hour
```

covering line crew, energy and lost throughput on a blending or filling line.

## What this changes

Lot sizing moves from hours-as-currency to money-as-currency:

* changeover costs `setup_hours x 1,500`
* holding one unit for one bucket costs `unit_cost x 0.25 / 365`

Both engines are affected. `netreq`'s Wagner-Whitin sees a real trade-off for
the first time, and the service simulation can eventually report working capital
rather than units — though that second half is not in this item.

## What is forbidden

* **Adjusting the 25% carrying rate.** Explicitly out of bounds.
* **Adjusting any cost above after seeing the campaign count.** The numbers are
  chosen for plausibility against real lubricant economics, committed here, and
  reported against whatever comes out.
* **Reporting the campaign count without the resulting inventory**, or the
  reverse. Item 7 established that publishing one half of that trade is a lie.

If campaigns remain absurd after real costs, that is the finding and it means
the diagnosis was wrong.

---

# Outcome

*Appended after running. Nothing above this line was changed, and the 25%
carrying rate was not touched.*

## The costs that came out

| level | n | min | median | max |
|---|---|---|---|---|
| raw | 40 | 90 | 350 | 350 |
| intermediate | 40 | 32 | 279 | 542 |
| finished | 120 | 46 | 455 | 1,006 |

A 20L pack of 5W-30 comes out at 655, which is the right order of magnitude for
the product it is imitating.

## The diagnosis was right

Re-running `netreq` with cost-based lot sizing, against the same balanced plant:

| | lot-for-lot | cost-based, **hours** (item 7) | cost-based, **money** |
|---|---|---|---|
| capacity load | 16,403 h (127%) | 9,658 h (75%) | **11,258 h (87%)** |
| overloaded buckets | 251 | 35 | 147 |
| campaigns in 90 buckets | 5,593 | 216 | **1,688** |
| average stock, units | 136,439 | 1,008,134 | **193,278** |
| average stock, value | 62.8 M | — | **72.8 M** |

1,688 campaigns across 160 routed SKUs is roughly **one campaign per SKU every
8.5 days**. The absurdity was a missing input, exactly as diagnosed, and not a
parameter that needed tuning.

That 8.5 days sits near the 14-day cycle the capacity sizing assumed, which is
an independent-convergence result and is written up as its own finding below.

The trade is now defensible and can be stated in one line: **a 31% reduction in
capacity load for a 16% increase in working capital.** Whether that is worth
taking depends on how tight the plant is, which is a question a planner can
actually answer.

**Still infeasible**: 87% overall with 147 of 450 resource-buckets over. Claim 2
does not move. Cost-based lot sizing was never going to reach feasibility,
because it never sees a per-bucket capacity limit — that was stated up front in
`docs/rccp.md` and it held.

---

# Finding: two independent rules landed near the same campaign length

This is the strongest internal-consistency evidence in the repo, so it gets
stated properly rather than in a parenthesis — including the part where it is
weaker than it first looks.

## What was independent about it

| | commit | date | derived from |
|---|---|---|---|
| **14-day campaign allowance** | `3e0977d` | 26 Aug 2026 | annual demand volume, routing hours, a chosen 82% utilisation target |
| **Unit costs → lot sizing** | `1ce6c66` | 27 Aug 2026 | raw material prices by type, a conversion adder, packaging by pack size, an hourly cost of capacity, a fixed 25% carrying rate |

Both were committed **before** the run that produced any campaign count, in
separate commits on separate days. Neither derivation references the other:
capacity sizing never reads a cost, and the cost rule never reads a campaign
frequency or a utilisation target. A reader can verify that by diffing the two
commits.

The realised interval — 8.5 days on average, **median 9.0** — was produced by
Wagner-Whitin trading setup against holding, with no knowledge that anything had
assumed 14.

## Why it is worth something

Nothing forced these to agree. The sizing rule could have assumed monthly
campaigns and the economics could have called for daily ones; the two would then
have been off by a factor of thirty and one of them would have been wrong. That
they land within the same order of magnitude is evidence that the demo's
economics are internally coherent — that its costs, its routing times and its
demand volumes describe a plant that could plausibly exist.

## Why it is weaker than it looks

**Near is not equal, and 8.5 against 14 is a 39% gap.** Three things are worth
saying rather than letting the headline stand:

**The aggregate hides a wide spread.** Per-SKU intervals run from 2.8 days to
90, with a median of 9.0 and an interquartile range of **6.4 to 18.0**. Only 45%
of SKUs fall between 7 and 21 days. The 14-day assumption sits inside that
interquartile range, on the high side of the median — which is a fair summary,
and a much weaker statement than "the two numbers matched".

**A single-point assumption cannot match a distribution.** Capacity sizing used
one campaign frequency for every routing. The economics produce a spread because
the setup-to-holding ratio varies enormously across the portfolio: unit costs
range from 32 to 1,006 and setup times from 0.3 to 3.5 hours. Cheap fast movers
economically want short runs; expensive slow movers want long ones. No single
number was ever going to describe both.

**The direction of the gap has a consequence.** Sizing provisioned changeover
capacity for ~26 campaigns a SKU-year; the economics call for roughly 43. So the
plant was sized for **fewer changeovers than the economics want**, and that
under-provisioning is part of why the plan remains infeasible at 87% with 147
buckets over. The convergence and the residual infeasibility are the same fact
seen from two directions.

## What would strengthen it

A per-SKU campaign allowance in the sizing rule, derived from each routing's own
setup-to-holding ratio rather than one portfolio-wide number. That would be a
genuine test of whether the two rules agree SKU by SKU rather than on average.
Not done, and it is the honest next step for anyone who wants to lean on this
finding harder than the paragraph above does.
