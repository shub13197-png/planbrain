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

The demo plant is closed one day a week and `netreq` does not know that. Twelve
buckets per resource carry work on a closed day. That is the capacity argument
in miniature: a plan that nets and lot-sizes perfectly still schedules work on a
day the plant is shut, and nothing upstream of `rccp` can see it.

## First run against the demo: the plan is not feasible

| resource | load h | avail h | util | over | no-cap |
|---|---|---|---|---|---|
| Blender A 20kL | 3,094 | 1,112 | 278% | 86 | 12 |
| Blender B 10kL | 3,615 | 1,128 | 320% | 86 | 12 |
| Blender C 5kL | 3,540 | 1,104 | 321% | 86 | 12 |
| Fill Line 1 small pack | 3,468 | 1,100 | 315% | 86 | 12 |
| Fill Line 2 drum | 2,830 | 1,108 | 255% | 78 | 12 |

Overloaded in 86 of 90 buckets, at roughly three times capacity.

**Run time 8,527 h (51.5%), changeover 8,020 h (48.5%).**

Splitting those two matters because they have different fixes. Changeover time
is attacked by lot sizing and campaign sequencing; run time can only be attacked
by more capacity or less demand. Reporting one number would hide which problem
the plant has.

Nearly half the load is changeover, and the cause is visible: `netreq` uses
lot-for-lot on intermediates, so a blend is made on every day it is needed —
a median of **26 production days per SKU across 90 buckets**, each paying a full
changeover. Lot-for-lot minimises inventory and is blind to setup cost.

## What this does and does not demonstrate

**Demonstrated:** `rccp` detects infeasibility that nothing upstream can see,
attributes it between run time and changeover, and flags work scheduled on
closed days. The capability is real and tested.

**Not demonstrated: that this tool produces capacity-feasible plans.** It
currently produces an infeasible one and correctly says so. Detecting the
problem is a genuine capability and is worth having on its own — a planner who
learns on Monday that the week is 3× over is better off than one who finds out
on Thursday — but it is not the same claim.

**And the magnitude here is not evidence about real plants.** The demo's routing
rates and setup times were generated independently of its demand volumes, so
nothing ever checked that the plant could make what it sells. Run time alone
(8,527 h) exceeds total capacity (5,552 h) by 54%, which means **the demo plant
is structurally under-capacitised** — no lot-sizing policy could rescue it. That
is a flaw in the item 2 generator, logged below rather than papered over by
retuning the numbers until the plan looks feasible.

The *mechanism* rccp shows is real. The *numbers* are a property of a synthetic
plant that was never balanced.

## Known gaps

* **The demo plant is not capacity-balanced.** Routings were generated without
  reference to demand. Until the generator produces a plant that can plausibly
  make its own demand, the demo cannot evidence "we produce feasible plans" —
  only "we detect infeasible ones".
* **No feedback loop.** `rccp` reports an overload; nothing acts on it. Closing
  the loop means lot sizing that prices changeover (Wagner-Whitin with a setup
  cost derived from routing hours — the DP already exists in `netreq`) and then
  campaign sequencing with sequence-dependent changeovers, which is PyJobShop.
* **Infinite capacity assumption upstream.** `netreq` plans as if capacity were
  unlimited, which is what makes rough-cut necessary. That is the standard MRP
  arrangement and not a defect, but it means the two engines currently disagree
  and nothing reconciles them.
* **No sequence-dependent setups.** A flush between two compatible grades costs
  less than between incompatible ones. Rough-cut charges a flat setup per bucket.
