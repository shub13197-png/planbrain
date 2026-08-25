# Decisions

Stand-in for Graphiti until the MCP server is configured. Same intent: record
what was decided AND what was rejected, so neither gets relitigated later.

Rejections matter more than decisions.

---

## 2026-08-25 — Bucket granularity: daily-native

**Decided.** `bucket_date` is a calendar date and is itself the bucket. Weekly
and monthly are query-time rollups, never stored.

**Rejected: weekly-native storage.** ~7x cheaper in rows, but lead-time offset
in `netreq` would have to round to week boundaries, and that error compounds
through every BOM level. Row count is not the scarce resource here; correctness
is.

**Rejected: a bucket dimension table with a period-type column.** Buys the
ability to mix granularities in one table, which is precisely the ambiguity we
do not want — two rows at "the same" bucket with different period types are two
answers to one question.

## 2026-08-25 — Sparse fact storage

**Decided.** Absent row means zero, never unknown. Readers left-join a date
series and coalesce.

**Rejected: dense materialisation.** Mostly-zero rows, no information gain. Noted
in the contract that inner-joining this table biases any mean high, worst on
intermittent-demand SKUs.

## 2026-08-25 — Measure vocabulary is a lookup table, not free text

**Decided.** FK to a `measure` table. A typo fails at insert.

**Rejected: CHECK constraint with an inline enum.** Same safety, but adding a
measure becomes a column-definition migration instead of an insert.

**Rejected: free-text measure.** Silently creates parallel series nobody reads.

## 2026-08-25 — Capacity measures excluded from `fact_supply_demand`

**Decided.** `rccp` load/available hours are keyed by resource, not by
`(sku_id, loc_id)`, so they are not in the measure vocabulary. A second table
`fact_capacity` at grain `(resource_id, bucket_date, measure, scenario_id)` is
required before build item 5.

**Rejected: overloading `loc_id` as `resource_id`.** Two meanings in one column;
every query would need to know which regime it is in.

**Not built yet** — nothing consumes it, and the ponytail ladder says a table
that nothing reads should not exist.

## OPEN — Scenario model

`scenario_id = 0` ('working') exists. Whether other scenarios are branches of it
or peers with a separate committed pointer is **undecided**. No
`parent_scenario_id` and no committed flag added until it is settled, because
the choice determines whether `netreq` writes copy-on-write.

Blocks: nothing yet. Must be settled before `netreq` writes its first row.
