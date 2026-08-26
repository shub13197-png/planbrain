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
