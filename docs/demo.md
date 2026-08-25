# The demo dataset

Build item 2. A fake lubricant blending plant, generated deterministically from
a seed. Items 3-5 are developed and tested against it.

```bash
python -m tools.seed_demo --out data/local/demo.sqlite3 --seed 7
```

The generator **is** the dataset. Nothing is committed as data — `data/local/`
is gitignored and the database is disposable. Same seed, same bytes, anywhere.

## Shape at seed 7

| | |
|---|---|
| Parts | 200 — 40 raw, 40 intermediate blends, 120 finished packs |
| BOM edges | 294, three levels, acyclic |
| Locations | 1 plant, 3 depots |
| Resources | 3 blenders, 2 fill lines, 1 QC lab |
| Trucks | 12 (reference data only — `fact_fleet` stays empty until item 6) |
| History | 2025-01-01 … 2026-06-30, 546 daily buckets |
| Forward horizon | 2026-07-01 … 2026-09-28, 90 buckets |
| Demand series | 242 (SKU × depot; packs are not stocked everywhere) |
| Rows stored | 66,620 demand + 62 receipts + 462 capacity |

Demand storage is **50% dense** — half the SKU-days are genuinely zero and are
therefore absent rows. That number is the sparse rule earning its keep.

Capacity is 462 rows rather than 540 because the 78 Sundays are zeros and get
dropped on write, then reappear on read. A small live check that the sparse
round trip works on a grain other than supply and demand.

## The history is messy on purpose

A backtest against clean synthetic demand proves nothing: it makes a naive mean
look excellent and hides precisely the failure modes Croston, TSB and IMAPA
exist for. So the generator injects, deliberately:

* **Five demand patterns** — smooth, erratic, seasonal, intermittent, lumpy.
* **22 mid-history launches and 11 discontinuations** at seed 7. Both break
  naive mean forecasting, and both are common in a real pack portfolio.
* **30 stockout windows** — zero demand that is censored *supply*, not absent
  demand.
* **Promotional spikes** at ~1.5% of days, 2.5-6x.
* **A closed day every Sunday**, producing structural zeros.

The lifecycle events are recorded on the dataset (`launched_mid_history`,
`discontinued_mid_history`, `stockout_windows`) so tests and the item 4 backtest
can find them, but **they are not stored as facts**.

That is the important part: in the fact table a structural zero, a censored
zero and a genuinely-zero-demand day are indistinguishable. Telling them apart
is the forecaster's problem. A dataset that quietly labelled them would make
item 4 easier than reality and the resulting MASE would be a lie.

## Reference data is not InvenTree

`locations`, `parts`, `bom`, `resources`, `routings` and `trucks` are plain
dataclasses on the returned `DemoDataset`. They stand in for what the importer
will pull from InvenTree, which is read-only (architecture rule 1). They are
deliberately *not* written to any planning table.

## It goes in through the front door

`populate()` writes exclusively through `write_facts`, so the demo exercises the
same sparsification and the same frozen-scenario guard a real importer will —
and `tests/test_demo.py` reads it back exclusively through `read_facts`, without
a single direct fact-table query.
