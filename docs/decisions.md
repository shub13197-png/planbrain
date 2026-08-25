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

**Decided.** Absent row means zero, never unknown.

**Rejected: dense materialisation.** Mostly-zero rows, no information gain. An
inner join over sparse storage biases any mean high, worst on
intermittent-demand SKUs.

## 2026-08-25 — Measure vocabulary is a lookup table, not free text

**Decided.** FK to a `measure` table. A typo fails at insert.

**Rejected: CHECK constraint with an inline enum.** Same safety, but adding a
measure becomes a column-definition migration instead of an insert.

**Rejected: free-text measure.** Silently creates parallel series nobody reads.

## 2026-08-25 — Multiple fact grains is the pattern, not the exception

**Decided.** Three fact tables from the start: `fact_supply_demand`,
`fact_capacity` (resource grain), `fact_fleet` (truck grain). One shared
`measure` lookup carrying a `grain` column. A registry in
`planbrain/facts/grains.py` is the only place table names appear, and both the
CI gate and the accessors derive from it.

**Superseded:** an earlier entry filed capacity as a one-off exception to a
single-grain design. That was wrong — `haulplan` needs a third grain, so the
exception would have been rediscovered a third time, by which point something
would already have been shoehorned in.

**Rejected: overloading `loc_id` as `resource_id`.** Two meanings in one column;
every query would need to know which regime it is in.

**Accepted gap:** the database does not enforce that a capacity measure stays
out of `fact_supply_demand`. Doing so in portable SQL needs a redundant `grain`
column on every fact row — real cost at 3M+ rows to duplicate what the composite
primary key and one accessor already enforce. Checked in the accessor, tested.

## 2026-08-25 — Scenario model: flat peers, full copy, explicit committed pointer

**Decided.** `scenario(scenario_id, name, created_at, frozen_at,
source_scenario_id, status)`. Full physical copy via `INSERT ... SELECT`.
`source_scenario_id` is provenance and is never read to resolve a value. Exactly
one committed scenario, enforced by a partial unique index. Committing creates a
frozen snapshot; working stays live so plan-vs-commit drift is a plain join.

**Rejected: hierarchical copy-on-write** (the Kinaxis shape). Four reasons, in
order of weight:

1. *Query cost is permanent.* Delta storage means every read walks a parent
   chain, forever. Combined with the sparse-storage rule that puts two
   independent "absent means something" semantics into one query — the bug class
   that produces plausible wrong numbers rather than crashes.
2. *Inheritance is a footgun for planning.* New actuals landing in working would
   silently rewrite every branched scenario. Reopen last week's what-if and it
   no longer says what it said.
3. *Storage saving is negligible at this scale.* 200 SKUs x 20 locations x 104
   buckets x 8 measures is ~3M rows: one INSERT...SELECT, seconds, a few hundred
   MB.
4. *Committed is not a tree position.* It is the plan that authorises order
   release — a distinct concept deserving an explicit flag, not an inferred
   location in a hierarchy.

**Rejected: committing a pointer at working itself.** Then "how far has the plan
drifted since we committed?" becomes unanswerable, and that is one of the more
sellable things this tool can do.

**Deferred to the Django migration task:** `_next_scenario_id` is `max+1`, which
races under concurrent PostgreSQL writers. Replaced with an identity column when
the plugin app's migration is generated. Tagged `TODO(django-migration)` in
source so it belongs to that task rather than floating.

## 2026-08-25 — The sparse rule gets a chokepoint, not a paragraph

**Decided.** `planbrain.facts.access` is the only module permitted to touch a
fact table. `read_facts` densifies against a generated bucket spine;
`write_facts` sparsifies. `tools/check_fact_access.py` fails CI on any direct
read or write elsewhere, deriving its table list from `grains.py`.

**Rejected: documenting the rule and trusting it.** A document has never once
prevented an inner join.

**Superseded: leaving writes ungated.** The original reasoning —
"absent-means-zero is a read hazard, so let importers INSERT freely" — was
incomplete. Raw writes also route around the frozen-scenario check and can
materialise zero rows that `read_facts` then hands back indistinguishably from
real ones.

## 2026-08-25 — Frozen scenarios enforced in the accessor, not a trigger

**Decided.** `write_facts` and the bulk copy both call `assert_writable`, which
refuses a frozen scenario and names it and its freeze time. No database trigger,
so SQLite and PostgreSQL behave identically. Revisit a trigger as
defence-in-depth if raw SQL ever enters the codebase.

**Decided: freeze after the copy, not before.** `commit_scenario` creates the
scenario unfrozen, copies, then sets `frozen_at`. Freezing first would have made
it immutable while still empty, forcing the copy to bypass its own guard.

## 2026-08-25 — Dense series at the solver boundary

**Decided.** The sparse-storage rule stops at the queue boundary. Every series
in a solver payload is dense and spine-aligned, length exactly
`horizon.bucket_count`, checked by `validate_payload` because JSON Schema cannot
express a cross-field length constraint.

**Why it is checked rather than trusted:** a series one element short does not
look broken. It shifts the entire plan by one bucket and every number downstream
stays plausible.

## 2026-08-25 — New dependency: jsonschema

**Decided.** `jsonschema` (MIT) validates every payload crossing the queue
boundary.

**Justification** per the definition of done: without a validator the schema is
decorative. Payloads cross a queue, so a malformed one fails in a worker minutes
later rather than at the call site, and the schema is the only thing that makes
that debuggable. It is the reference implementation for draft 2020-12.

**Rejected: hand-rolled structural checks with stdlib only.** Cheaper in
dependencies, but re-implements a spec badly and would not have caught the
nested-array cases in `rccp_input`.

## 2026-08-25 — fact_fleet is reserved with an empty measure set

**Decided.** `long_haul_km` and `total_km` removed. No measure of grain `fleet`
exists until `haulplan` (build item 6) defines the ledger; the accessors report
the table as reserved rather than as a typo.

**Rejected: keeping a provisional vocabulary.** A guessed measure invites
something to start writing to it before `haulplan` has decided what the ledger
is, and by then the guess is load-bearing. An empty reservation is honest.

## 2026-08-25 — PATTERN: assert the boundary, not the count

**The fix:** a test asserting `len(ALLOWED) <= 4` was replaced by one asserting
that no module under `planbrain/` outside `facts/` may ever be allowlisted.

**The pattern, which generalises well beyond this file:** an invariant that
asserts a *count* tests an implementation detail. It fires on legitimate growth,
so it gets renegotiated rather than obeyed — and an invariant that gets edited
every time it fires is not protecting anything. An invariant that asserts a
*boundary* tests the actual rule, stays silent while legitimate work happens,
and only fires when the rule is genuinely broken.

Applies to allowlists, dependency counts, file-size limits, API surface caps —
anywhere the temptation is to cap a number because the real rule feels harder to
express. It usually is not.

## 2026-08-25 — All repo text is UTF-8, asserted in CI

**Decided.** `tests/test_encoding.py` fails if any tracked text file is not
valid UTF-8.

**Why:** Python 3.14 on Windows still reads and writes with the locale codepage
(cp1252 here), so a scripted edit round-trips a UTF-8 document into mixed
encoding, and `str.replace` against a mis-decoded string matches nothing and
returns the original **without raising**. That silently no-opped an earlier
update to this very file. A no-op edit that reports success is exactly the
failure mode this project cannot tolerate.

**Rejected: remembering to pass `encoding="utf-8"`.** Same class of mistake as
trusting a document to prevent an inner join.
