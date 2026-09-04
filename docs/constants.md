# Committed constants, and which item set them

Every number in this project that was chosen rather than derived, with the build
item that set it and the document that justifies it.

## Why this exists

Two constants committed at different times, by rules with no awareness of each
other, have now collided twice:

* **Helpfully** — the 14-day campaign allowance (item 7) and the campaign
  interval the unit costs imply (item 8, median 9.0 days) landed close enough to
  be genuine internal-consistency evidence.
* **Unhelpfully** — truck capacities (item 2) and the 12,000 kg truckload
  (item 9) collided so that **six of twelve trucks cannot carry a full load**.
  Nobody noticed until a run produced 65 unassignable trips.

Both collisions were only found by running something. This page exists so the
third one can be found by **reading**, which is considerably cheaper.

When adding a constant: put it here, and scan the table for anything it has to
be consistent with.

## Demo dataset shape — item 2

| constant | value | note |
|---|---|---|
| `PLANT_ID`, `DEPOTS` | 1; Delhi 11, Jaipur 12, Ludhiana 13 | one plant, three depots |
| `HISTORY_START` / `HISTORY_END` | 2025-01-01 to 2026-06-30 | 546 daily buckets |
| `HORIZON_DAYS` | 90 | forward planning horizon |
| `N_RAW` / `N_INTERMEDIATE` / `N_FINISHED` | 40 / 40 / 120 | 200 SKUs, three BOM levels |
| `PACKS` | 1L, 5L, 20L, 26L, 210L | **collides with** `PACKAGING_COST` keys and, via load sizes, with truck capacity |
| ~~truck capacities~~ | *superseded at item 10* | now sized from the freight profile, not random |
| `PATTERNS` | smooth .22, erratic .18, seasonal .15, intermittent .27, lumpy .18 | demand mix |

## Demand messiness — items 2 and 7

| constant | value | doc |
|---|---|---|
| `DRIFT_SHARE` | 0.35 | `capacity-sizing.md` |
| `DRIFT_RANGE` | −0.55 to +1.20 | added at item 7 so the stale-reorder-point comparison meant something |

## Working calendar — item 5

| constant | value | note |
|---|---|---|
| `SIX_DAY_WEEK` | closed Sunday | seasonal period is **derived** from this, never hardcoded |
| `DAY_SHAPE` | Mon–Fri 1.0, Sat 0.5, Sun 0.0 | **must stay consistent with** the calendar above |

## Forecasting — item 4

| constant | value | source |
|---|---|---|
| `ADI_CUTOFF` | 1.32 | Syntetos–Boylan–Croston, published |
| `CV2_CUTOFF` | 0.49 | published |
| TSB `alpha_d`, `alpha_p` | 0.2, 0.2 | literature starting point, untuned |
| `min_nonzero` | 3 | below this a series is `unusable` rather than guessed |

## Capacity sizing — item 7, committed in `3e0977d`

| constant | value | doc |
|---|---|---|
| `CAMPAIGN_CYCLE_DAYS` | 14 | `capacity-sizing.md` |
| `TARGET_UTILISATION` | 0.82 | deliberately not 100% |

**Consistency note:** the realised interval under cost-based lot sizing is a
median of 9.0 days. The allowance is inside the interquartile range but on the
high side, which under-provisions changeover by 57%. See `rccp.md`.

## Unit costs — item 8, committed in `1ce6c66`

| constant | value | doc |
|---|---|---|
| `BASE_OIL_COST` | 90 / litre | `unit-costs.md` |
| `ADDITIVE_COST` | 350 / litre | |
| `CONVERSION_ADDER` | 8 / litre | |
| `PACKAGING_COST` | 12 / 22 / 45 / 52 / 180 | **keys must match `PACKS`** — now raises if not |
| `CAPACITY_COST_PER_HOUR` | 1,500 | |
| `ANNUAL_CARRYING_RATE` | 0.25 | **explicitly out of bounds for tuning** |

## Fleet sizing — item 10, committed in `53e4c0b`

| constant | value | doc |
|---|---|---|
| `TRUCK_CLASSES` | 9,000 / 16,000 / 25,000 kg | `haulplan.md` |
| `MIN_SMALL_SHARE` | 1/3 | keeps the feasibility filter biting |

## Fleet and fairness — item 9, committed in `255258d`

| constant | value | doc |
|---|---|---|
| `DEPOT_DISTANCE_KM` | Delhi 80, Jaipur 180, Ludhiana 420 | `haulplan.md` |
| `LONG_HAUL_KM` | 250 | only Ludhiana qualifies |
| `TRUCKLOAD_KG` | 12,000 | **committed input, not to be adjusted** |
| `KG_PER_LITRE` | 0.9 | |
| `REPLENISH_EVERY_DAYS` | 7 | weekly cadence |
| `YTD_BAND_KM` | 8,000 to 46,000 | deliberate skew; the ledger exists because fleets drift |
| `FAIR` / `ACCEPTABLE` | 0.95 / 0.85 | Jain thresholds |

## Reconciliation — item 8

| constant | value | doc |
|---|---|---|
| `RESIDUAL_TOLERANCE` | 0.005 | `reconciliation.md`; float noise only, not slack for unexplained difference |

## Benchmark against real demand — 2026-09-05

| constant | value | note |
|---|---|---|
| `MOVING_AVERAGE_DAYS` | 84 | twelve weeks. The window a planner without software actually uses -- "take the last three months" -- **committed before the comparison was run**, not tuned until this product won |
| `LEAD_TIME_DAYS` (benchmark) | 14 | a sales log does not record it. Applied identically to every policy, so it moves every curve together and cannot bias the comparison; it does move the absolute service level |
| `SERVICE_SWEEP` | 0.50, 0.75, 0.90, 0.95, 0.99 | the safety settings swept to trace the curve. 0.50 gives z = 0 and no safety stock, which is where the demand signal is all a policy has |
| `MIN_HISTORY_DAYS` / `MIN_DEMAND_EVENTS` | 365 / 12 | eligibility bar for a series, committed before the run. Everything clearing it is kept -- no sampling by size, which would drop the intermittent half |
| `HOLDOUT_DAYS` | 90 | never seen at fit time |

## Storage — 2026-09-05

| constant | value | note |
|---|---|---|
| `KEYS_PER_QUERY` | 200 | keys per SELECT in `read_facts`. The predicate is one OR-ed clause per key, and SQLite refuses the parse tree at roughly 500 two-column keys. **Found on real data at 2,947 stock codes**; the demo asks for 222, which is why nothing caught it. 200 is inside every engine's limit rather than tuned to SQLite's |

## Known live consistency requirements

Things that must agree, and where they are checked:

| these must agree | checked by |
|---|---|
| `PACKS` ↔ `PACKAGING_COST` keys | raises in `_cost_parts` |
| calendar ↔ `DAY_SHAPE` | `test_capacity_is_zero_on_closed_days` |
| `grains.py` ↔ `schema.sql` tables | `test_registry_matches_the_schema` |
| measure grain ↔ fact table | `read_facts` / `write_facts` |
| `TARGET_UTILISATION` ↔ sized capacity | `test_capacity_lands_on_the_stated_target_utilisation` |
| truck capacities ↔ `TRUCKLOAD_KG` | fleet sized from payload distribution; `_size_fleet` raises if the heaviest payload exceeds every class |
| `TRUCK_CLASSES` ↔ `MIN_SMALL_SHARE` | `test_a_third_of_the_fleet_cannot_take_a_full_load` |
| `KEYS_PER_QUERY` ↔ any portfolio size | `test_reading_a_portfolio_larger_than_the_query_limit_works` |
| benchmark figures ↔ the prose quoting them | `tools/published.py`, `test_published_numbers.py` |
