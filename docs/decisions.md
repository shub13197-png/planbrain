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

## 2026-08-25 — Demo dataset: a generator, not committed data

**Decided.** `planbrain/demo` generates the whole dataset deterministically from
a seed. `data/local/` is gitignored; the SQLite file is disposable.

**Rejected: committing fixture files.** A committed CSV or JSON drifts from the
schema silently and nobody notices until a test fails for an unrelated reason.
A seeded generator cannot drift — it is rebuilt from the current schema every
run, and `test_is_deterministic` pins reproducibility.

## 2026-08-25 — The demo history is messy on purpose, and unlabelled

**Decided.** Five demand patterns, mid-history launches and discontinuations,
stockout windows, promotional spikes, and structural Sunday zeros.

**Why:** a backtest against clean synthetic demand makes a naive mean look
excellent and hides exactly the failure modes Croston, TSB and IMAPA exist for.
Item 4 reports MASE; that number is only worth reading if the data can defeat a
naive forecaster.

**Decided: lifecycle events are recorded on the dataset object but never stored
as facts.** In the fact table a structural zero, a censored-supply zero and a
real zero-demand day are indistinguishable — as they are in a customer's data.

**Rejected: labelling censored zeros in the fact table.** It would make item 4
easier than reality and the resulting MASE would be a lie. Telling the two apart
is the forecaster's problem, which is the whole point of the exercise.

## 2026-08-25 — Demo covers a second grain from day one

**Decided.** The dataset populates `fact_capacity` as well as
`fact_supply_demand`, even though nothing reads capacity until item 5.

**Why:** items 2-4 would otherwise all run against a single grain, leaving the
multi-grain registry untested by real data for three build items — long enough
for something to be built assuming one grain. The Sunday zeros in the capacity
series also give a live demonstration that the sparse round trip works on a
grain other than supply and demand.

**`fact_fleet` stays empty.** The 12 trucks are reference data. Nothing writes
to the fleet grain until `haulplan` defines the ledger.

---

# Reconciliation, 2026-08-25

Walked the commit history after discovering that the `0cb538c` turn's log update
silently no-opped. **The four rebuilt entries were not the whole gap.** Entry
counts by commit: `f1e899e` 5, `0cb538c` 5 (the no-op — a large commit that
logged nothing), `045c773` 12, `6a61025` 15.

Two distinct causes, and the encoding bug was only one of them:

* **Inside the broken window** (`0cb538c`): entries lost to the cp1252/UTF-8
  silent `str.replace` no-op. Rebuilt earlier, plus the four below.
* **Predating the window** (`f1e899e`): decisions written into `facts.md` and
  never mirrored into the log at all. That was not a tooling failure, it was me
  treating the contract doc as the record. The log is the record; the contract
  doc explains the result.

No guard added for this. "Every decision is logged" is a judgement call, and a
checker for it would be the assert-a-count mistake wearing a different hat.

## 2026-08-25 — Planning keys are not foreign keys (recovered, f1e899e)

**Decided.** `sku_id`, `loc_id`, `resource_id` and `truck_id` carry no FK
constraint. They are logical references into InvenTree.

**Why:** InvenTree core is read-only (architecture rule 1) and may live in a
separate database, so a constraint could not be enforced anyway. Referential
integrity against InvenTree is the importer's job, checked at import time where
a bad row can be reported against its source line.

## 2026-08-25 — qty may be negative (recovered, f1e899e)

**Decided.** No non-negativity constraint on `qty`.

**Rejected: clamping at zero.** `projected_on_hand` goes negative on a shortage,
and the magnitude of the negative is the size of the problem the planner needs
to see. Clamping would turn a visible shortage into a silent zero.

## 2026-08-25 — `derived` measures are inputs vs outputs (recovered, f1e899e)

**Decided.** `measure.derived = 0` means imported from the system of record and
never written by a planning run; `derived = 1` means computed and is the only
thing a planning run may overwrite.

**Accepted gap:** not enforced at the database level. `netreq` must respect it,
and that is now a live obligation rather than a note, since item 3 is the first
code that writes derived measures.

## 2026-08-25 — schema.sql is the contract, not the deployment tool (recovered, 0cb538c)

**Decided.** One portable SQL subset that runs on SQLite for tests and
PostgreSQL in production. Django migrations for the plugin app are generated
*from* it.

**Rejected: Django models as the source of truth.** The schema would then only
be readable by booting Django, and the test suite could not run against
in-memory SQLite in under a second — which is what makes the storage-layer tests
cheap enough to actually write.

**Cost accepted:** PostgreSQL-specific features (partitioning, identity columns,
generate_series) stay out of this file until something needs them.

## 2026-08-25 — Best-so-far is a first-class result (recovered, 0cb538c)

**Decided.** `run_meta.status` is `optimal | feasible | timeout | infeasible`,
and `haulplan_output` can carry `truck_id: null` for an unassigned trip.

**Why:** architecture rule 3 puts solvers behind a queue under a hard time
limit. If a timed-out solve could only be represented as an error, the caller's
only options would be to discard a usable answer or to lie about its quality.
Unassigned trips must surface rather than being quietly dropped.

## 2026-08-25 — Solver payload conventions (recovered, 0cb538c)

**Decided.** `gross_req` arrives already exploded through the BOM;
`lead_time_days` is days rather than buckets; `rccp_output.overloaded_buckets`
is reported rather than recomputed by the UI.

**Why each:** explosion caller-side keeps the solver a pure function of its
payload. Days and buckets coincide by construction under the daily grain, which
is the point of that grain. Reporting overloads rather than recomputing them
means the number the planner sees and the number a report counts are the same
number.

## 2026-08-25 — Contract examples are generated and drift-checked (recovered, 0cb538c)

**Decided.** `tools/make_examples.py` generates one worked example per payload
kind; a test asserts the files on disk match the generator.

**Why:** a schema says what is legal, an example says what a real payload looks
like — and a stale example is worse than none, because it is what someone copies.

**Decided: the contract is self-contained**, no external `$ref`. Asserted by a
test so it stays readable without network access.

---

# Build item 3 — netreq

## 2026-08-25 — Opening on-hand is a scalar input, not a measure

**Decided.** `netreq_input.on_hand` is a per-item scalar. The demo generator
produces a `stock_on_hand` snapshot object alongside its lifecycle records, not
fact rows.

**Why:** opening on-hand is a stock position at a single instant, not a
time-phased series, and in production it comes from InvenTree stock.

**Rejected: an `on_hand` measure in the vocabulary.** It would build a fake
source that the importer would later have to be taught to fill, and then be
unwound when InvenTree stock became the real source.

## 2026-08-25 — `on_hand_open` renamed to `projected_on_hand`, timing pinned

**Decided.** One measure for the time-phased balance, named `projected_on_hand`,
defined as the balance at bucket **end** — the projected available balance a
planner reads off an MRP grid.

**Rejected: adding `projected_on_hand` alongside `on_hand_open`.** They mean the
same thing. Two measures for one concept is exactly the parallel-series problem
the closed vocabulary exists to prevent.

**Noted: it is a level, not a flow.** It carries across buckets, so a slow mover
stores densely where its demand stores sparsely — the inverse of the
intermittent case, and confirmed empirically by
`test_a_level_stores_denser_than_a_flow`. Do not size the table from the demo's
50%-dense demand figure. If one measure forces partitioning, it is this one.

## 2026-08-25 — The forecast seam is a named adapter

**Decided.** `resolve_gross_req(con, ..., source=...)`. Item 3 uses
`naive_replay`; item 4 passes `forecast`.

**Rejected: inlining the demand_actual substitution in the netting loop.** The
swap would then be a diff through the middle of the algorithm rather than a
parameter, and the stand-in would be easy to leave in place by accident.

**Decided: `source="forecast"` raises today rather than returning zeros.**
Nothing writes the forecast measure yet, and netting against zeros would produce
a confident, empty plan — the exact failure mode this project exists to avoid.

**Recorded loudly: `naive_replay` is not a forecast.** No model, no
reconciliation, no error estimate. Its accuracy must never be quoted as a
baseline for item 4.

## 2026-08-25 — Wagner-Whitin implemented here; stockpyl is a test-only oracle

**Decided.** The DP lives in `netreq/core.py`. `stockpyl` moves to the `dev`
extra.

**Why:** stockpyl declares `sphinx==4.5.0` — a pinned documentation toolchain —
among its *install* requirements, along with matplotlib, build and setuptools. A
pinned Sphinx inside an InvenTree plugin's runtime tree is a conflict waiting to
surface at deploy time. The licence gate is satisfied (MIT), but the dependency
shape is not.

**Kept:** stockpyl as the fixture source and cross-check.
`test_our_dp_agrees_with_stockpyls_solver` asserts exact agreement on four
instances, and `test_shipped_package_never_imports_stockpyl` is the boundary
assertion that stops the runtime dependency creeping back.

**Decided: ties prefer the later order.** `[5]x12` at setup 90, holding 1.5 has
two optima (4+4+4 and 6+6, both 405). Later holds less stock for the same money
and matches stockpyl, which is what lets the cross-check assert exact equality
rather than merely equal cost.

## 2026-08-25 — Explosion runs at one production location

**Decided.** Independent demand is aggregated across depots before netting, and
the BOM explodes at the plant.

**Rejected: time-phased plant-to-depot netting.** That is DRP, a separate
problem. Doing a half version of it inside item 3 would produce plausible
distribution numbers nobody had designed.

**Decided: dependent demand follows the parent's planned order RELEASE**, not
its receipt. Components must be present when production starts, not when it
finishes.

**Decided: low-level codes are computed and cycle-checked**, not assumed. Wrong
level order does not crash — it plans a component against an incomplete
requirement and silently understates the order.

## 2026-08-25 — netreq enforces the derived-measure rule

**Decided.** `write_plans` refuses to write any measure whose `derived` flag is
0. Previously logged as an accepted gap; item 3 is the first code that writes
derived measures, so it is now enforced where it can be.

## 2026-08-25 — Worked examples are generated by the engine

**Decided.** `netreq_output.json` is computed by running `plan_item` on
`netreq_input.json` rather than written by hand.

**Why:** the hand-written version claimed a second order of 200 and a different
balance series. It was simply wrong, and it was the thing a reader would copy.
Deriving it makes drift impossible rather than merely detected.

## 2026-08-25 — PATTERN: prefer edit tools that fail on no-match

**The problem, twice.** Scripted `str.replace` edits silently no-opped — once
from a cp1252/UTF-8 mismatch, once from backslash escaping in a heredoc. Both
times the script printed success and the change was not made.

**The pattern:** when editing files programmatically, use a tool that errors
when the target text is not found. A helper that returns the input unchanged on
no-match converts a loud failure into a silent one, and silent success is the
worst possible outcome — it is indistinguishable from work until something
downstream contradicts it.

Same shape as [assert the boundary, not the count]: the safety property is not
"did the operation run" but "did it change what it claimed to change".
