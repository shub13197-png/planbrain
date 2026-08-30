# haulplan — the long-haul fairness ledger

Build item 9. **Everything above the outcome line was written before any
assignment was run**, same discipline as `docs/capacity-sizing.md` and
`docs/unit-costs.md`.

## The problem

Long-haul trips are the ones drivers care about — more kilometres, more time away
from home, and in most Indian fleets more money. If assignment optimises only for
distance or cost, the same two or three trucks take every long run because they
are marginally better placed, and the rest of the fleet resents it. That is not a
hypothetical failure mode; it is the normal outcome of a distance-minimising
solver with no fairness term.

**The ledger is the product, not the solver.** A cumulative record of long-haul
kilometres per truck, carried year-to-date, is what makes fairness measurable at
all. Without it every run restarts fairness from zero and the same truck is
chosen every week, which is exactly the complaint.

## Order of work, committed

1. **Ledger first.** Define the `fact_fleet` measures, accumulate them, and make
   the distribution readable.
2. **Greedy largest-deficit assignment**, with feasibility filters. Simple,
   explainable, and it establishes the baseline any solver must beat.
3. **Timefold only after** the ledger is right and the greedy result is
   demonstrably explainable.

**Expectation, recorded in advance:** a greedy largest-deficit rule may well
capture most of the available fairness. If the solver adds little over it, that
gets reported — the same way the reorder point holding up against the forecast
was reported.

## `fact_fleet` measures, defined from what the ledger needs

The grain `(truck_id, bucket_date, measure, scenario_id)` has been reserved since
item 2 with a deliberately **empty** measure set, so that nothing would start
writing to it before `haulplan` decided what the ledger measures. That decision
is now:

| measure | derived | what it is |
|---|---|---|
| `long_haul_km` | 1 | Long-haul kilometres assigned to a truck in this bucket. The fairness ledger's own unit. |
| `total_km` | 1 | All kilometres assigned, long-haul or not. Fairness is measured on long-haul; utilisation needs the total. |
| `trips_assigned` | 1 | Count of trips. A truck can accumulate kilometres on few long runs or many short ones, and the two are different working weeks. |

All three are **flows**, not levels: they happen in a bucket and are zero
otherwise, so they store sparsely. The *cumulative* ledger is a running sum over
the flow, computed on read. It is deliberately not stored as a level — a stored
cumulative would be a second meaning for a fact row, and the one thing
`docs/contracts/facts.md` forbids permanently.

Year-to-date carry-in is a **scalar per truck**, like opening stock in `netreq`,
for the same reason: it is a position at one instant and in production it comes
from the system of record.

## Fairness metric, committed before any assignment runs

**Jain's fairness index**, on the cumulative long-haul kilometre distribution
across available trucks:

```
J(x) = (sum x_i)^2 / (n * sum x_i^2)
```

Reported alongside the raw spread (max − min) and the coefficient of variation.

**Why Jain rather than Gini:**

* It is bounded on `(0, 1]` with **1 meaning perfect equality**, and it has a
  direct reading: `J = 0.8` on ten trucks means the allocation is as fair as an
  equal split among eight of them. A fleet manager can act on that sentence.
  Gini's 0-to-1 scale runs the other way and has no equivalent plain reading.
* It is scale-invariant and population-size-independent, so a 12-truck depot and
  a 40-truck depot are directly comparable.
* Gini is more sensitive in the tail, which matters for income distributions and
  matters less here — the complaint being modelled is "the same truck always
  gets the long runs", which is a concentration effect Jain captures well.

**Committed thresholds**, so the result cannot be graded after the fact:

* `J >= 0.95` — fair. No planner would act on the difference.
* `0.85 <= J < 0.95` — acceptable, worth watching.
* `J < 0.85` — unfair, and the ledger should be visibly correcting it.

**And the honest caveat, stated now:** Jain rewards equal *cumulative*
kilometres, which is not the same as equal *desirability*. A truck with 40,000
long-haul km on good highways has had an easier year than one with 35,000 on bad
roads. Modelling that needs route quality data nobody has. The index measures
what the ledger records, and the ledger records kilometres.

## Zero-offset handling, tested first

Same-day dispatch is a real case: a trip planned and run on the same bucket, with
no lead time between assignment and departure.

The zero-lead-time bug found during the reconciliation was exactly this class — an
order placed into a pipeline at bucket `t` after that bucket had already been
processed, silently never arriving. Ten units ordered, zero delivered, no error.

**So the test is written before the assignment code**, not after. A trip with
zero offset between its planning bucket and its dispatch bucket must appear in
the ledger for that bucket, and its kilometres must count toward fairness in the
same run that assigned it.

## Feasibility filters, before any fairness consideration

A fair assignment that cannot physically happen is worse than an unfair one. A
truck is a candidate for a trip only if:

* it is **available** on that bucket;
* its **capacity in kilograms** covers the load;
* it is not already assigned another trip in that bucket.

Fairness chooses **among feasible candidates only**. A trip with no feasible
truck is reported as **unassigned**, never forced onto an infeasible one — the
same principle as `haulplan_output` already carrying `truck_id: null`, which
`docs/contracts/solver_io.md` established at item 1.

## Demo fleet data, stated before it was generated

The fairness *result* depends on what the demo's trips and opening ledger look
like, so the rule for producing them is committed here rather than chosen after
seeing a number.

**Distances** are plant-to-depot, at roughly real road distances from Bhiwadi:
Delhi 80 km, Jaipur 180 km, Ludhiana 420 km. **Long-haul threshold: 250 km**, so
Ludhiana runs are long-haul and the other two are not. One distant depot out of
three is a realistic shape for a regional blender.

**Trips** are generated from the finished-goods demand each depot actually
takes, converted to truckloads at a stated 12,000 kg per load with 1 litre
treated as 0.9 kg. Depots are replenished on a fixed weekly cadence rather than
continuously, because that is how a small fleet runs.

**Opening year-to-date ledger is deliberately skewed**, and this needs saying
plainly: the trucks start with cumulative long-haul kilometres spread across a
wide band rather than level. That is not rigging the result, it is the premise —
a fairness ledger exists *because* fleets drift out of balance, and a demo that
starts perfectly level would have nothing to correct and would make the feature
look pointless in exactly the way a perfectly balanced plant would have made
rough-cut look pointless.

**What is forbidden:** adjusting the skew, the threshold or the cadence after
seeing the Jain index. If greedy assignment barely moves fairness, that is the
finding.

## Scope limits

* **No routing.** Distances arrive from the caller, from an OSRM matrix in
  production. `haulplan` never computes a road distance.
* **No sequencing within a bucket.** A truck does one trip a bucket, or none.
  Multi-trip days and time windows are vehicle routing, which is what PyVROOM
  and Timefold are for, and neither is in this first pass.
* **No cost objective.** Fairness and feasibility only. Balancing fairness
  against distance cost is exactly the trade a solver is for, and it comes after
  the ledger is trustworthy.
