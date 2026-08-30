# rccp — rough-cut capacity planning

Build item 6. Loads the plan `netreq` produced onto resources and reports
whether the plant can actually make it.

```bash
python -m tools.capacity_report
```

This is the claim the service backtest could not make. A reorder point has no
concept of a blender being full — it cannot produce a capacity-feasible plan
because it has no representation of capacity at all. This is where the tool
differs in kind rather than in degree.

## Load is placed when work starts

A planned order released on day 8 with a two-day lead time occupies the blender
on days 8–10, so the hours land in **bucket 8**. Loading at the *receipt* bucket
would report a plant that looks free exactly when it is busiest.

This changed the contract: `rccp_input` takes `planned_order_release`, not
`planned_order_receipt`.

Rough-cut front-loads the whole order into the release bucket rather than
spreading it across the lead time. Deliberate, and it is what "rough" means: it
surfaces an overload earlier rather than later, and it is honest about its own
resolution. Exact timing within the lead time is finite scheduling — PyJobShop's
job, not this one.

## Two exceptions, because they are different problems

* **Overloaded** — more work than hours available. Normal, expected, and what a
  planner resolves by moving orders.
* **Load without capacity** — work scheduled into a bucket with *zero* hours
  available: a closed day, or a resource down for maintenance. Not a degree of
  overload but a different mistake, and **invisible in a utilisation figure**,
  because dividing by zero has no honest answer. Utilisation reads 0.0 there and
  the real signal lives in its own list.

The demo plant is closed one day a week. At item 6, `netreq` did not know that
and twelve buckets per resource carried work on a closed day — the capacity
argument in miniature: a plan that nets and lot-sizes perfectly still schedules
production on a day the plant is shut, and nothing upstream of `rccp` could see
it. Item 7 fixed the cause; see the bottom of this page.

## Results after balancing (item 7)

The item 6 run found the plan 3x over capacity, but the demo plant was never
sized against its own demand. It has since been sized **blind**, from demand,
to a target of 82% at a 14-day campaign cycle -- rule written and committed
before any number was computed, in `docs/capacity-sizing.md`.

Both runs below use the balanced plant.

### With the part master's lot sizing (lot-for-lot on intermediates)

| resource | load h | avail h | util | over |
|---|---|---|---|---|
| Blending, large batch | 3,293 | 2,423 | 136% | 68 |
| Blending, medium batch | 2,988 | 2,120 | 141% | 52 |
| Blending, small batch | 3,208 | 2,053 | 156% | 67 |
| Filling, small pack | 3,817 | 3,457 | 110% | 34 |
| Filling, drum | 3,097 | 2,868 | 108% | 30 |

**Overall 16,403 h against 12,920 h available — 127%. Not feasible.**

The plant is sized for fortnightly campaigns and the plan runs near-daily ones:
5,593 separate campaigns across 90 buckets. Changeover is 48.5% of total load.

### With cost-based lot sizing

Wagner-Whitin, setup cost taken from routing hours. The DP already existed in
`netreq`; it only ever lacked costs.

| resource | load h | avail h | util | over |
|---|---|---|---|---|
| Blending, large batch | 1,776 | 2,423 | 73% | 4 |
| Blending, medium batch | 1,626 | 2,120 | 77% | 4 |
| Blending, small batch | 1,582 | 2,053 | 77% | 3 |
| Filling, small pack | 2,550 | 3,457 | 74% | 14 |
| Filling, drum | 2,124 | 2,868 | 74% | 10 |

**Overall 9,658 h against 12,920 h available — 75%. Still not feasible.**

Load falls 41% and overloaded buckets fall from 251 to 35. Changeover drops from
48.5% of load to 2.3%.

### Why "still not feasible" at 75% utilisation

The plan now fits **on average** and not **bucket by bucket**. That is the exact
signature of cost-based rather than capacity-constrained lot sizing: Wagner-Whitin
trades setup against holding and never sees a per-bucket capacity limit, so it
cannot be steered to one. 35 of 450 resource-buckets remain over.

**Claim 2 therefore stays where it is: capacity awareness, not capacity-feasible
plans.** That was committed in advance and the result did not clear the bar.

Genuinely capacity-constrained lot sizing is the **CLSP** — capacitated lot
sizing problem — a different and much harder problem, and out of scope. This gap
is a design boundary, not an oversight.

### And the capacity win is bought with inventory

| | lot-for-lot | cost-based |
|---|---|---|
| capacity load | 16,403 h | 9,658 h |
| overloaded buckets | 251 | 35 |
| campaigns in 90 buckets | 5,593 | 216 |
| **average projected on-hand** | **136,439 units** | **1,008,134 units** |

Cost-based lot sizing buys a 41% capacity reduction with **7.4× the inventory**.
216 campaigns across 160 routed SKUs means most SKUs are made **once** in a
90-day horizon — a quarter's supply built in one run.

That is not a good plan. It is an economically consistent answer to the question
as posed, and the question is posed wrong: pricing inventory by the *capacity
hours embedded in it* badly undervalues it. A litre of finished lubricant costs
money to hold because of the material in it, not because of the machine-minutes.
With holding almost free relative to a two-hour changeover, the arithmetic
correctly concludes "make everything once".

**The fix is not to tune the carrying rate until the campaigns look sensible.**
That would be fitting the parameter to the desired answer. The fix is a real
carrying cost built on unit costs from the customer's system of record, which is
the "no cost model" gap already on the list.

**So the capacity loop closes only in a narrow sense.** It demonstrably reduces
capacity load, and it does so at an inventory cost this repo cannot currently
price. Both halves are reported because reporting only the first would be a
straightforward lie.

### The closed-day bug is fixed

Item 6 found 12 buckets per resource carrying work on a day the plant is shut.
`netreq` now pulls a planned release back to the previous **working** bucket --
backward, because starting later would make the receipt late. Every run above
reports **zero** buckets of load without capacity, and a test holds it there.

## Known gaps

* **No capacity-constrained lot sizing.** Cost-based batching cannot be steered
  to a per-bucket limit because it never sees one. CLSP is the real answer and
  is out of scope. This is a boundary, not an oversight.
* **The inventory cost of cost-based lot sizing is unpriced.** Holding is valued
  by embedded capacity hours, which undervalues it badly. A real carrying cost
  needs unit costs from the system of record.
* **`netreq` lot sizing and the service simulation's policy are different
  things.** So the 7.4x inventory figure above is not reflected in the service
  backtest, and the two engines currently disagree about how much stock the plan
  implies. Reconciling them is unstarted.
* **Infinite capacity assumption upstream.** `netreq` plans as if capacity were
  unlimited, which is what makes rough-cut necessary. That is the standard MRP
  arrangement and not a defect, but it means the two engines currently disagree
  and nothing reconciles them.
* **No sequence-dependent setups.** A flush between two compatible grades costs
  less than between incompatible ones. Rough-cut charges a flat setup per bucket.

---

# Finding: the plant is under-budgeted for changeover, by 57%

A separate result from the infeasibility above, and it needs its own section
because the two are easy to conflate — and conflating them would overstate both.

## The arithmetic

Capacity sizing (`docs/capacity-sizing.md`, committed `3e0977d`) provisioned
changeover capacity on the assumption of a **14-day campaign cycle**. Cost-based
lot sizing, priced from unit costs committed independently (`1ce6c66`), produces
campaigns roughly every **8.5 days**.

Over the 90-bucket horizon, across 160 routings:

| | provisioned | realised | ratio |
|---|---|---|---|
| campaigns | 1,029 | 1,680 | **1.63×** |
| changeover hours | 1,162 | 1,824 | **1.57×** |

**The changeover budget is short by 663 hours, or 5.1% of the plant's total
capacity.** Nothing was tuned to produce that: one number came from demand
volumes and an assumed campaign frequency, the other from material prices and a
fixed carrying rate, and neither derivation reads the other.

## What this is a capability to do

**This tool can tell a plant that it is provisioned for fewer changeovers than
its own cost structure demands.** That is a real and useful thing to learn, and
it is not something a reorder point or a spreadsheet can produce, because
neither has both a routing model and a lot-sizing economics model to compare.

The plant does not know it. Its aggregate capacity looks adequate — total load
of 11,258 hours against 12,920 available — so nothing in a utilisation report
flags it. The shortfall only appears when the changeover budget is separated
from run time and compared against what the economics call for.

## What this is NOT: the explanation for the infeasibility

It would be convenient to say the plan is infeasible because the plant is
under-budgeted. **The data does not support that**, and the counterfactual is
cheap to run.

Re-sizing with the changeover allowance matched to the realised campaign
interval — a diagnostic only; the committed demo sizing is unchanged:

| allowance | capacity | utilisation | overloaded buckets | feasible |
|---|---|---|---|---|
| 14 days (committed) | 12,920 h | 87% | 147 / 450 | no |
| 9.0 days (median) | 13,691 h | 82% | 139 / 450 | no |
| 8.5 days (mean) | 13,816 h | 81% | 138 / 450 | no |

Correcting the budget entirely moves utilisation from 87% to 81% and removes
**nine** of 147 overloaded buckets — about 6%.

So the residual infeasibility is **overwhelmingly bucket-level timing
lumpiness**, not aggregate provisioning. The plan fits on average and clumps in
time, and neither capacity sizing nor cost-based lot sizing addresses that
because neither ever sees a per-bucket limit. That is the CLSP, and it remains
out of scope exactly as stated.

**Claim 2 does not move on the back of this**, and this finding is not folded
into it. They are separate: one is "we detect that a plan cannot be made", the
other is "we detect that a changeover budget is mis-set". The second is
evidenced here; the first still stops short of producing feasible plans.
