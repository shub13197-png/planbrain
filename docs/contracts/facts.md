# Fact table contract

Source of truth: `planbrain/facts/schema.sql`. This document explains the *why*;
the SQL is the *what*. If they disagree, the SQL wins and this document is a bug.

## Grain

    (sku_id, loc_id, bucket_date, measure, scenario_id) -> qty

Every planning number in the system is addressed by exactly these five columns.
There is no second way to store a time-phased number.

## Bucket granularity: daily

`bucket_date` is a calendar date and *is* the bucket. There is no bucket
dimension table, no bucket_id, and no period-type column.

Weekly and monthly views are a **query-time rollup** (`date_trunc`), not stored
rows. Nothing is ever written at weekly grain.

Rationale: `netreq` offsets planned receipts backward by lead time to produce
releases. Lead times are quoted in days. At weekly grain that offset must round,
and the rounding error compounds through every BOM level — a four-level bill
turns a 3-day lead time into anything from 0 to 4 weeks of drift. Daily-native
storage makes the offset exact; display rollup costs nothing.

Cost accepted: roughly 7x the rows of weekly storage. For the seeded demo
(200 SKUs, 18 months, 8 measures, single location) that is ~880k rows, which is
unremarkable for PostgreSQL. Revisit partitioning by `bucket_date` only when a
real dataset makes it hurt — not before.

## Sparse storage

**An absent row means zero. It never means unknown.**

Materialising a row per SKU per day per measure would be mostly zeros and would
multiply storage by the density factor for no information gain. Readers must
left-join against a generated date series and coalesce to 0, never inner-join.

This is the single easiest way to get a wrong number out of this schema: an
inner join silently drops the buckets where demand is zero, and a mean computed
over the surviving rows is biased high. Intermittent-demand SKUs — exactly the
ones Croston/TSB exist for — are mostly zeros, so this bites hardest where it
matters most.

## Measure vocabulary is closed

`measure` is a foreign key to a lookup table, not free text. Adding a measure is
a schema change and a deliberate decision. A typo (`gross_reqs`) fails loudly at
insert instead of quietly creating a parallel series that no reader looks at.

`derived = 0` measures are imported from the system of record and are never
written by a planning run. `derived = 1` measures are outputs and are the only
rows a planning run may overwrite. Nothing enforces this at the database level
yet; `netreq` must respect it.

## sku_id / loc_id are not foreign keys

They are logical references into InvenTree. No FK constraint, because InvenTree
core is read-only (architecture rule 1) and may live in a separate database.
Referential integrity against InvenTree is the importer's job.

## qty may be negative

`on_hand_open` goes negative on a shortage. That is a real, meaningful plan
number and must not be clamped at zero — the magnitude of the negative is the
size of the problem the planner needs to see.

## Open: capacity does not fit this grain

`rccp` produces load and available hours per **resource** per bucket. A resource
is not a `(sku_id, loc_id)` pair, so those measures are deliberately absent from
the vocabulary above rather than shoehorned in.

This needs a second table, `fact_capacity`, at grain
`(resource_id, bucket_date, measure, scenario_id)`. Not built — `rccp` is build
item 5 and nothing needs it yet. Decide before writing `rccp`, not during.
