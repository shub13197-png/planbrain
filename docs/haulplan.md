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

## Fleet sizing rule — item 10, committed before the numbers

Item 9 found six of twelve trucks below the 12,000 kg truckload, so half the
fleet could not take a full load and 65 trips had no feasible truck. This is the
rule that replaces the arbitrary capacities generated at item 2.

**Sized from the freight profile**, never from what makes the fairness index
look good.

1. Build the **payload distribution** across all generated trips — how many
   loads at what weight.
2. Every payload must be carriable by **some** truck class, or the fleet cannot
   move its own freight.
3. Size the count of each class so that the **number of trips needing a truck in
   any bucket** can be met, allowing for one trip per truck per bucket.

**`TRUCKLOAD_KG` at 12,000 is a committed input and is not touched.** The fleet
adapts to the freight, not the other way round.

### The committed mix

Three classes, and the rationale is that a real depot runs a mixed fleet because
freight is mixed:

| class | capacity | intended for |
|---|---|---|
| rigid | 9,000 kg | remainder and part loads only |
| standard | 16,000 kg | the 12,000 kg full truckload |
| large | 25,000 kg | full loads and any future consolidation |

**Some trucks stay unable to take the largest loads, deliberately.** A fleet
where every truck can do every trip removes the feasibility filtering entirely,
and the ledger becomes a round-robin. No real depot looks like that, and a demo
that did would make the feasibility filters untestable against real data.

**Committed proportions: at least one third of the fleet must be unable to take
a full 12,000 kg load.** That keeps the filter biting. The remainder is split so
that full-load demand in the busiest bucket can be met.

### What is forbidden

* Changing `TRUCKLOAD_KG`, `LONG_HAUL_KM`, `REPLENISH_EVERY_DAYS` or
  `YTD_BAND_KM` — all committed at item 9.
* Adjusting the mix after seeing the Jain index.
* Sizing the fleet so that every trip is assignable. Some unassigned trips in a
  peak bucket are realistic; a fleet with zero slack pressure is not.

**Committed expectation:** unassigned trips should fall substantially from 65,
and the Jain index may move in **either** direction. A fleet with more capable
trucks spreads long-haul work across more of them, which could improve fairness
— or dilute it, if the extra trucks start from the low end of the ledger band.
**Whatever comes out is reported, including if it is worse than 0.9550.**

## Scope limits

* **No routing.** Distances arrive from the caller, from an OSRM matrix in
  production. `haulplan` never computes a road distance.
* **No sequencing within a bucket.** A truck does one trip a bucket, or none.
  Multi-trip days and time windows are vehicle routing, which is what PyVROOM
  and Timefold are for, and neither is in this first pass.
* **No cost objective.** Fairness and feasibility only. Balancing fairness
  against distance cost is exactly the trade a solver is for, and it comes after
  the ledger is trustworthy.

---

# Outcome

*Appended after running. Nothing above this line was changed.*

## Greedy assignment on the demo fleet

169 trips over a 90-bucket horizon, 65 of them long-haul, across 12 trucks.

| | Jain index | verdict | spread |
|---|---|---|---|
| opening ledger | 0.9169 | acceptable | 27,409 km |
| after greedy | **0.9550** | **fair** | 21,949 km |

The ledger does what it exists to do: an acceptable-but-drifting fleet is pulled
back inside the committed fair threshold, and the gap between the busiest and
quietest truck narrows by 5,460 km.

## The first version was wrong, and the demo caught it

The obvious greedy rule — process trips in order, give each to the truck
furthest behind — produced **J = 0.9194**, a movement of 0.0025 that is
indistinguishable from noise. Every individual assignment was correct by the
stated rule. The fault was in the **order**.

Trips were processed by id, which grouped them by depot: four short Delhi runs,
four medium Jaipur runs, then five long Ludhiana runs. The short runs were
assigned first and consumed exactly the trucks furthest behind, so by the time
the long-haul trips came up, the only feasible trucks were the ones already
ahead. **13 of 65 long-haul trips were assigned, and they went to the wrong
trucks.**

Fixed by giving long-haul trips first claim within each bucket, since long-haul
is what fairness is measured on. Long-haul assignment went from 13 to **65 of
65**, and the index from noise to a real movement.

Worth naming the shape of this: nothing crashed, no assignment violated the
rule, and the report would have read as a successful fairness run. It was
visible only because the index was compared against a committed threshold rather
than reported on its own.

## A second finding: half the fleet cannot carry a full load

104 of 169 trips were assigned. All 65 unassigned trips give the same reason —
*every capable truck is already committed in this bucket* — and the cause is
fleet composition, not the assignment rule:

| truck capacity | count |
|---|---|
| 9,000 kg | **6** |
| 16,000 kg | 4 |
| 25,000 kg | 2 |

The standard truckload is 12,000 kg, so **six of twelve trucks are structurally
excluded from every full load** and can only take remainder trips. The effective
fleet for full-load work is six trucks, not twelve.

Two independently committed rules collided to produce this: truck capacities
were generated at item 2, the 12,000 kg truckload at item 9, and neither knew
about the other. Unlike the campaign-length convergence, this collision is
unhelpful — but it is exactly the class of thing a real fleet suffers from, and
the tool surfaced it rather than hiding it in an average.

**Not fixed by adjusting the truckload size.** That would be tuning a committed
input to make an output look better, which is the error this whole discipline
exists to prevent. It is reported as a finding.

## Timefold: not started, and the ledger says why

The committed order of work was ledger, then greedy, then a solver only once the
ledger is right and demonstrably explainable.

The ledger is now right and the greedy result is explainable in one sentence a
driver would accept. But the demo's binding constraint is **fleet composition
and bucket capacity**, not assignment quality — 65 trips have no feasible truck
at all, and no optimiser can assign a trip to a truck that cannot carry it.

Running Timefold against this dataset would optimise the 104 assignable trips
and report an improvement over a baseline that was never the limiting factor.
**The honest next step is a demo fleet that can actually carry its own freight**,
the same way capacity sizing had to precede any conclusion about `rccp`.

Recorded in advance and worth restating: the expectation was that greedy might
capture most of the available fairness. On this data it reaches 0.9550 against a
committed fair threshold of 0.95, so there is very little headroom left for a
solver to claim on fairness alone. Where a solver would earn its place is
trading fairness against distance cost, which this pass does not attempt.

---

# Outcome, item 10: the resized fleet

*Appended after running. Nothing above the fleet sizing rule was changed, and
`TRUCKLOAD_KG` was not touched.*

## The fleet the freight profile produced

15 trucks: **5 rigid at 9,000 kg (33%), 7 standard at 16,000 kg, 3 large at
25,000 kg.** The rigid share lands exactly on the committed minimum of one
third, so the feasibility filter keeps biting.

## Unassigned trips fell as expected

| | item 9 fleet | resized fleet |
|---|---|---|
| trucks | 12 | 15 |
| trips assigned | 104 / 169 | **156 / 169** |
| unassigned | 65 | **13** |
| long-haul assigned | 65 / 65 | 65 / 65 |

The remaining 13 are all *every capable truck is already committed in this
bucket* — peak-bucket pressure, which the committed rule deliberately did not
size away. A fleet with zero slack pressure is not realistic.

## The Jain index went DOWN, and that was allowed for

| | opening | after greedy | verdict |
|---|---|---|---|
| item 9, 12 trucks | 0.9169 | 0.9550 | fair |
| resized, 15 trucks | 0.8288 | **0.8865** | **acceptable** |

The committed expectation said the index might move either way and that whatever
came out would be reported. It came out worse, and here is why — it is not a
regression in the assignment.

**The comparison is not like-for-like.** The opening ledger is drawn per truck
from the same 8,000–46,000 km band. Fifteen draws from that band are more
dispersed than twelve, so the fleet *starts* less fair: opening J fell from
0.9169 to 0.8288.

**Greedy's improvement actually grew**, from +0.0381 to **+0.0577**. It is doing
more work on a harder fleet and finishing lower.

## The structural limit, now measured

The opening spread is **36,956 km**. All the long-haul work in the horizon is
65 trips × 420 km = **27,300 km**.

**There is less work available than the gap to be closed.** Even handing every
single long-haul trip to the one truck furthest behind could not level the
fleet. A 90-day horizon cannot undo a year of drift, and no assignment rule
changes that — fairness correction is a multi-quarter process, and any claim
that one plan run fixes it would be false.

## How much room a solver has: 0.0033

`fairness.ceiling()` water-fills the available kilometres into the trucks
furthest behind, ignoring the 420 km trip granularity and the
one-trip-per-truck-per-bucket limit. That makes it an **unreachable** upper
bound rather than an optimum.

| | Jain |
|---|---|
| greedy achieved | 0.8865 |
| water-fill ceiling | 0.8898 |
| **headroom for any optimiser** | **0.0033** |

Greedy is within a third of a percentage point of a bound no real solver could
reach, because the bound ignores two constraints a solver would have to respect.

**This is what makes the Timefold deferral a measurement rather than an
opinion.** A solver run here would report an improvement over a baseline that
was never the binding constraint, and the most it could possibly claim on
fairness is 0.0033. The constraint is the work available and the ledger's
opening spread, neither of which an optimiser can change.

Timefold earns entry when fairness must be traded against **distance cost** —
a genuinely multi-objective problem where greedy has no answer at all. That
needs a cost model on trips, which this pass does not have.
