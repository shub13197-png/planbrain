# Fact table contract

Source of truth: `planbrain/facts/schema.sql`. This document explains the *why*;
the SQL is the *what*. If they disagree, the SQL wins and this document is a bug.

## Three grains, one measure vocabulary

Planning facts do not share a single key. Three tables, three grains:

| Table | Grain | Used by |
|---|---|---|
| `fact_supply_demand` | `(sku_id, loc_id, bucket_date, measure, scenario_id)` | `netreq`, forecast |
| `fact_capacity` | `(resource_id, bucket_date, measure, scenario_id)` | `rccp` |
| `fact_fleet` | `(truck_id, bucket_date, measure, scenario_id)` | `haulplan` |

They share one `measure` lookup table, which carries a `grain` column naming
which fact table each measure belongs in. Adding a fourth grain means adding a
table here and a line in `planbrain/facts/grains.py` — nothing else in the
codebase hard-codes a fact table name.

This is a pattern, not a set of exceptions. A resource is not a `(sku, loc)`
pair and a truck is not a resource; shoehorning either into the supply/demand
table would mean one column carrying two meanings and every query needing to
know which regime it is in.

**Known gap:** the database enforces that `measure` exists, but not that a
capacity measure stays out of `fact_supply_demand`. That is checked in
`read_facts` and covered by a test. Enforcing it in SQL would need a redundant
`grain` column on every fact row, which is real storage cost at 3M+ rows for a
class of bug the accessor already catches.

## Bucket granularity: daily

`bucket_date` is a calendar date and *is* the bucket. There is no bucket
dimension table, no bucket_id, and no period-type column.

Weekly and monthly views are a **query-time rollup**, not stored rows. Nothing
is ever written at weekly grain.

Rationale: `netreq` offsets planned receipts backward by lead time to produce
releases. Lead times are quoted in days. At weekly grain that offset must round,
and the rounding error compounds through every BOM level — a four-level bill
turns a 3-day lead time into anything from 0 to 4 weeks of drift. Daily-native
storage makes the offset exact; display rollup costs nothing.

Cost accepted: roughly 7x the rows of weekly storage. Unremarkable for
PostgreSQL at target scale. Revisit partitioning by `bucket_date` only when a
real dataset makes it hurt — not before.

## Sparse storage, and the chokepoint that enforces it

**An absent row means zero. It never means unknown.**

This is the single easiest way to get a wrong number out of this schema. An
inner join silently drops the buckets where demand is zero, and a mean computed
over the survivors is biased high. Intermittent-demand SKUs — exactly the ones
Croston and TSB exist for — are mostly zeros, so it bites hardest where it
matters most.

A rule in a document does not prevent that. So:

* **`planbrain.facts.access.read_facts` is the only read path.** It densifies
  against a generated bucket spine and returns zeros for absent buckets. An
  entity with no rows at all still comes back as a full run of zeros.
* **`tools/check_fact_access.py` fails CI** on any `FROM fact_*` or
  `JOIN fact_*` outside a short, explicit allowlist. Writes are not gated —
  absent-means-zero is a read hazard, and the importer must still `INSERT`.

The allowlist may grow with storage-layer tests. It may never contain a module
that computes a plan number; a test asserts that.

## Scenarios are flat peers

`scenario(scenario_id, name, created_at, frozen_at, source_scenario_id, status)`.

* **Full physical copy, no copy-on-write.** A scenario contains every row it
  holds. No read ever resolves through a parent.
* **`source_scenario_id` is provenance only.** It renders "branched from working
  on 3 Mar" in the UI at zero read-time cost. Nothing reads it to resolve a
  value.
* **Exactly one committed scenario**, enforced by a partial unique index that
  works on both SQLite and PostgreSQL.
* **Committing creates a frozen snapshot.** Working stays live and open, so
  plan-vs-commit drift is a plain join between two scenarios. Committing a
  pointer *at* working would make that question unanswerable.

Copying ~3M rows is one `INSERT ... SELECT` and a few hundred MB. Copy-on-write
would save that and cost a recursive parent walk on every read forever —
compounding with the sparse rule into two independent "absent means something"
semantics in one query. That is the bug factory this project exists to avoid.

## Not foreign keys

`sku_id`, `loc_id`, `resource_id` and `truck_id` are logical references into
InvenTree. No FK constraint, because InvenTree core is read-only (architecture
rule 1) and may live in a separate database. Referential integrity against
InvenTree is the importer's job.

## `derived` measures

`derived = 0` measures are imported from the system of record and are never
written by a planning run. `derived = 1` measures are outputs and are the only
rows a planning run may overwrite. Not enforced at the database level yet;
`netreq` must respect it.

## qty may be negative

`on_hand_open` goes negative on a shortage. That is a real plan number and must
not be clamped at zero — the magnitude of the negative is the size of the
problem the planner needs to see.
