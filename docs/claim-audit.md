# Claim audit: what every published claim rests on

    python -m tools.claim_audit --seeds 7 11 23 42 99

The claim that lumpy demand was where this tool excelled died because it was
measured on a 40-series sample and asserted for a 222-series portfolio. This
page is the result of asking the same question of every other claim before a
reader does.

Two questions each: does it hold on the **full portfolio**, and does it hold
across **more than one seed**? A result from one synthetic dataset is a property
of that dataset until shown otherwise.

## Results, five seeds, full portfolio each

| claim | mean | range | verdict |
|---|---|---|---|
| Intermittent: fitted beats a tuned reorder point | +2.20 pts | 1.64 to 2.66 | **holds 5/5** |
| ~~Lumpy: fitted beats a tuned reorder point~~ | **−1.15 pts** | −1.60 to −0.68 | **holds 0/5 — retracted** |
| Naive-zero collapses on intermittent | 65.9% fill | 61.4 to 71.0 | **holds 5/5** |
| Staleness costs fill rate | +2.37 pts | 1.89 to 3.44 | **holds 5/5** |
| The plan is not capacity-feasible | 32.8% of buckets over | 30.7 to 37.3 | **holds 5/5** |
| Campaign interval is under the 14-day allowance | 8.35 days | 7.82 to 8.87 | **holds 5/5** |
| Greedy fairness leaves little headroom for a solver | 0.01 Jain | 0.00 to 0.03 | **holds 3/5** |

## What survived

**The lumpy retraction was correct and consistent.** A tuned reorder point beats
this tool on lumpy fill on **every** seed, by 0.68 to 1.60 points. Retracting it
on one full-portfolio run was right, and five runs confirm it was not a fluke in
the other direction either.

**The staleness result is not a single-seed artefact.** This was the specific
worry — the drift that rescued the claim was added in one pass on one seed. It
holds on all five, 1.89 to 3.44 points, so the claim stands on more than the
dataset it was born in.

**Capacity infeasibility and the changeover shortfall are structural**, not
seed-specific. Both hold on every seed with modest spread.

## What did not survive: the solver deferral is weaker than stated

**`haulplan` defers Timefold on the grounds that greedy assignment sits within
0.0033 Jain of an unreachable ceiling, leaving nothing for an optimiser.**

That figure is from seed 7. Across five seeds the headroom runs **0.00 to 0.03**,
and clears the stated 0.01 bar on only **three of five**.

0.03 is not nothing. On a fleet at 0.8865 it is the difference between staying
mid-band and approaching the committed "fair" threshold of 0.95. So the honest
statement is narrower than the one published:

> On the seed-7 dataset a solver has almost no room on fairness. On other
> datasets of the same shape it has up to ten times more. The deferral rests on
> a property of one dataset, not a general result.

**The deferral itself still stands**, for a reason that does not depend on this:
greedy has no answer at all when fairness must be traded against distance cost,
and that is the problem a solver is actually for. But *"a solver could add almost
nothing"* was overstated and is corrected here.

## What this audit cannot tell you

Every seed draws from the same generator, so five seeds test sensitivity to
**sampling within one model of a plant**, not to whether that model resembles a
real one. A claim holding on 5/5 seeds is evidence against a fluke and is not
evidence about a customer's data.

The only cure for that is real data, which is what the importer is for.
