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
8.5 days** — close to, and arrived at independently of, the 14-day cycle the
capacity sizing assumed. The absurdity was a missing input, exactly as diagnosed,
and not a parameter that needed tuning.

The trade is now defensible and can be stated in one line: **a 31% reduction in
capacity load for a 16% increase in working capital.** Whether that is worth
taking depends on how tight the plant is, which is a question a planner can
actually answer.

**Still infeasible**: 87% overall with 147 of 450 resource-buckets over. Claim 2
does not move. Cost-based lot sizing was never going to reach feasibility,
because it never sees a per-bucket capacity limit — that was stated up front in
`docs/rccp.md` and it held.
