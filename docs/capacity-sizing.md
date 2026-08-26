# How the demo plant is sized

**Written before the sizing was run, and not revised afterwards.** The outcome
is reported in `docs/rccp.md` whatever it turned out to be.

The item 6 run found the demo plant roughly 3× over capacity, because the item 2
generator produced routing rates and setup times without ever checking that the
plant could make what it sells. This is the rule that replaces that accident.

## The rule

Capacity is sized from **demand**, never from the plan's load.

1. **Annual demand per finished SKU** — from `demand_actual`, scaled from the
   18-month history to a year.
2. **Explode through the BOM by volume** — a finished SKU's annual units become
   its components' annual units via `qty_per`. Volume only; no time phasing.
3. **Convert to run hours** — annual units × `hours_per_unit`, accumulated per
   resource from the routings.
4. **Add a changeover allowance** — a stated assumption, below.
5. **Divide by target utilisation** to get required available hours.
6. **Spread across the working calendar** to get hours per day.

## The two stated assumptions

**Campaign cycle: 14 days.** Sizing assumes each routed SKU is produced roughly
fortnightly, so 26 changeovers a year per routing.

This number is deliberately **not** matched to what `netreq` currently does.
Lot-for-lot on intermediates produces a blend on every day it is needed — a
median of 26 production days per *90 buckets*, roughly four times the assumed
rate. Sizing to the plan's actual behaviour would be sizing from the plan, which
is the thing this rule exists to avoid. The gap between the assumption and the
plan is a finding, not an error to be tuned away.

**Target utilisation: 82%** at the bottleneck resource.

Not 100%, deliberately. A perfectly balanced plant is as unrealistic as a 3×
overloaded one, and it would make capacity checking look unnecessary — a plant
that is never tight has no use for rough-cut. 80–85% is what a real small plant
runs at, and it leaves some buckets genuinely overloaded, which is the honest
case and the one worth demonstrating.

## What is forbidden

* **Tuning capacity until the plan is feasible.** The rule above is applied once
  and the result is reported.
* **Iterating on the target utilisation until the output looks good.** 82% was
  chosen before any capacity number was computed.
* **Sizing from `rccp`'s load figures.** That would fit capacity to the plan
  rather than to the business, and would manufacture the finding.

If the plan is still infeasible after balancing, that is the result and it goes
in the docs unedited.

## A resource is a work centre, not a machine

Sized available hours may exceed 24 per day. That is not an error: a rough-cut
resource is a **work centre**, which may hold parallel equipment. A work centre
showing 28 hours a day is two vessels running fourteen. Rough-cut deliberately
does not know which physical unit does which job — that is finite scheduling.

## Demand drift, added in the same pass

The item 5 stale-reorder-point comparison was under-evidenced because the demo
history carried lifecycle events but no sustained drift, and staleness bites
hardest under drift.

A share of series now carry a multiplicative trend across the history. The
stale comparator is then re-run.

**Committed in advance:** if the stale reorder point still holds up under drift,
claim 3 — "parameters that stay fitted rather than going stale" — is **dropped**
from the README, not softened.
