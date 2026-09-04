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

---

# Standing policies

## 2026-08-25 — POLICY: inventory math is implemented here; stockpyl is the oracle

**Standing decision, not to be relitigated per algorithm.** Any inventory
mathematics we would otherwise take from stockpyl -- safety stock, EOQ,
base-stock levels, multi-echelon inventory optimisation -- follows the same
pattern established for Wagner-Whitin:

1. Implement it in `planbrain`, with the algorithm and its source named in the
   docstring.
2. Keep `stockpyl` in the `dev` extra only.
3. Cross-check against stockpyl in tests and **assert exact agreement**, not
   merely equal cost. Where ties are possible, pick the tie-break that matches
   stockpyl so exact equality stays assertable, and document the choice as a
   policy.
4. Keep a boundary test that the shipped package never imports it.

**Why it is standing:** stockpyl declares `sphinx==4.5.0` -- a pinned
documentation toolchain -- among its *install* requirements, along with
matplotlib, build and setuptools. That is a property of the package, not of any
one algorithm, so the answer is the same every time and re-deciding it per
function is wasted argument.

**This does not generalise to the rest of the stack.** statsforecast, Timefold
and PyVROOM are judged on their own dependency shape. Re-implementing an
AutoARIMA would be absurd where re-implementing a twenty-line dynamic program is
not.

## 2026-08-25 — POLICY: Wagner-Whitin ties prefer the later order

**Decided.** Documented in `docs/netreq.md` as a stated policy with its
rationale, not as an implementation detail.

Later holds less stock for the same money and matches stockpyl, which is what
lets the cross-check assert exact equality. A customer running tight service
levels may eventually want prefer-earlier.

**Rejected: parameterising it now.** A knob nobody has asked for is a branch
nobody tests and a default nobody chose. When someone asks, it belongs in the
payload contract rather than a config file.

## 2026-08-25 — projected_on_hand stays dense. Compression is forbidden, not deferred.

**Decided.** Store every bucket. No optimisation now: ~110k rows on a 546-day
horizon at 200 SKUs is nothing.

**Forbidden permanently: store-on-change with carry-forward on read.** This is
the natural compression for a level and it must never be adopted. It would make
an absent row mean *carry forward* for this measure and *zero* for every other
measure in the same table. Two meanings for absence in one table is exactly the
failure the sparse rule exists to prevent, and the resulting bug is the worst
shape available: a reader using zero-fill semantics gets a complete, plausible,
wrong series.

**When density does hurt: partition by `(scenario_id, bucket_date)`.** That
changes storage layout without changing what a row means. Dense or not stored;
no middle.

## 2026-08-25 — Depot demand is reconciled bottom-up before netting

**Decided.** Item 4 forecasts per depot and sums to plant level; explosion
consumes the plant total.

**KNOWN GAP, written into `docs/netreq.md` so it is not mistaken for solved:**
aggregating depot demand to the plant discards the per-depot lead-time offset. A
depot four days out and a depot next door land in the same bucket. So the
current plan answers *"what must the plant make, and when"* and does **not**
answer *"what must each depot hold, and when should it ship"*. The second
question needs DRP and is deferred.

**Rejected: a partial DRP inside item 3 or 4.** It would produce plausible
per-depot numbers nobody had designed, which is worse than an absent feature. A
plant plan that looks complete invites someone to read depot answers out of it;
the documentation says plainly that there are none.

## 2026-08-25 — PATTERN: an empty result must not read as success

Generalised from two silent `str.replace` no-ops. The class is broader than
editing: **any operation whose no-op outcome is indistinguishable from its
success outcome.**

Swept the repo for the whole class and guarded each instance:

| Site | Empty outcome | Guard |
|---|---|---|
| `validate_payload` horizon lookup | `.get()` returned None, **skipping the series-length check entirely** | required-field check, then indexed access |
| `check_fact_access.main` | glob finds no files, gate prints clean | floor on files examined |
| `test_encoding` parametrize | zero files, zero tests, green | assert the collected list is non-empty |
| stockpyl boundary test | zero modules scanned | assert modules scanned |
| `test_registry_matches_the_schema` | regex matches nothing | assert parsed result non-empty |
| `make_examples` | wrote the literal `null` | refuse to write an empty payload |
| `payload_kinds()` | zero kinds, whole contract suite collects nothing | assert kind count |

The first one is the worst and was found by the sweep rather than by a failure:
a guard that silently disables itself is worse than no guard, because the
payload then passes validation while carrying the one error the contract exists
to catch.

**Not guarded, deliberately:** `dict.get(key, default)` in `explode` and
`read_facts`. Those defaults encode a real absence -- a SKU with no independent
demand, a bucket with no row -- rather than masking a missing key. The
distinction is whether absence is *meaningful* or *unexpected*.

---

# Build item 4 — forecast

## 2026-08-26 — New dependency: statsforecast (runtime)

**Decided.** `statsforecast` 2.x is a real runtime dependency, unlike stockpyl.

**Justification** against the standing policy: the policy says stockpyl's
dependency *shape* is the problem, and that other libraries are judged on their
own. statsforecast is Apache-2.0 and requires ordinary scientific Python --
numpy, pandas, scipy, statsmodels, coreforecast -- with no pinned toolchain.
And an AutoARIMA or AutoETS is genuinely not reimplementable in twenty lines the
way a Wagner-Whitin DP is. Both halves of the policy point the same way here.

**Kept pure anyway:** `metrics`, `classify` and `backtest` do not import it, so
the parts where a silent wrong answer would hide run in 0.1s without it.

## 2026-08-26 — MASE seasonal period is 7, not 1

**Decided.** The naive baseline is seasonal at m=7.

**Why:** the plant is closed on Sunday, so daily demand has structural weekly
periodicity. A one-step naive baseline is wrong every Saturday-to-Sunday and
Sunday-to-Monday step, which inflates the MASE denominator and flatters every
model that beats it. Demonstrated by
`test_the_same_series_looks_scorable_at_the_wrong_period`.

**Decided: unscorable series are counted, not dropped.** A perfectly periodic
training window has a zero scale factor and MASE has no meaning. Those series
are reported as unscored. Excluding the hard ones silently is how a portfolio
average gets improved, and a mean quoted without its unscored count is an
advertisement rather than a measurement.

## 2026-08-26 — MASE is the wrong metric for intermittent demand, and we say so

**Measured at seed 7 over 40 series:** chosen model mean MASE 1.066, median
0.908, against seasonal naive 1.205 / 1.060. By pattern, erratic 0.765 and
smooth 0.826 beat the baseline; intermittent 1.338 and lumpy 1.389 do not.

**Decided: report it, caveat it, do not hide it or tune around it.** Croston and
TSB emit a flat rate against an actual that is mostly zero, so point-error
metrics punish them while a naive zero scores well by being right on the quiet
days. A MASE above 1.0 there does not mean the method is worse for planning; it
means MASE is measuring the wrong thing. Croston-type methods optimise expected
inventory position over a lead time.

**Rejected: switching intermittent SKUs to a model that scores better on MASE.**
That would optimise the metric rather than the plan, and the metric is known to
be wrong for this class.

**Consequence, logged as an open gap:** the honest proof-of-value for the
intermittent half of the portfolio is fill rate against inventory held, which
needs the multi-echelon simulation backtest named in the setup brief. **Not
built.** Until it is, no claim should be made either way about intermittent
performance.

## 2026-08-26 — Forecasts are stored at the grain they were fitted at

**Decided.** `forecast` facts are written per (sku, depot). Bottom-up
reconciliation to plant level happens in `netreq.resolve_gross_req` at the
moment of netting.

**Rejected: storing the reconciled plant-level series.** Aggregation would then
be baked into storage and invisible at the point of use, and the depot-level
forecast -- the thing a future DRP needs -- would be gone.

## 2026-08-26 — An empty forecast measure raises rather than netting to zero

**Decided.** `resolve_gross_req(source="forecast")` raises when every series is
zero for the horizon.

**Why:** all-zero means nobody ran the forecast, not that demand is nil. The
distinction is invisible downstream, and netting against it yields a confident,
empty plan. This is the same class as the sweep entry above -- an empty result
that would otherwise read as a valid answer.

## 2026-08-26 — Model output is clamped and NaN-guarded

**Decided.** Every forecast is clamped at zero, and non-finite output falls back
to the naive baseline with the substitution counted in the run report.

**Why clamping:** ETS extrapolates freely and predicts negative demand on a
declining series. A negative gross requirement nets backwards through MRP and
manufactures supply out of nothing.

**Why the NaN guard:** `qty` has no NOT-NaN constraint and never will. One NaN
poisons every downstream sum silently.

**Why counted rather than swallowed:** a run where a third of the portfolio fell
back to naive is a different result from one where none did, and neither the row
count nor the MASE would say which happened.

---

# Build item 5 — service backtest

## 2026-08-26 — Build order changed: service backtest promoted ahead of rccp

**Decided.** The simulation backtest becomes item 5; `rccp` moves to 6,
`haulplan` to 7. Recorded in `docs/build-order.md`, which is now the live plan;
`docs/AGENT_SETUP.md` stays unedited as the historical brief.

**Why:** it was the blocker on any honest claim about intermittent SKUs, which
is roughly half the portfolio and the half where planning software earns its
money. Item 4 had just demonstrated that MASE cannot substitute. And
service-level against inventory is the number that sells this to a finance
manager -- the positioning rests on it.

## 2026-08-26 — Deviation: no SimPy in the replay

**Decided and flagged rather than asked**, per standing instruction. The brief
points at anshul-musing/multi-echelon-inventory-optimization, whose replay uses
SimPy. The architectural idea -- replay history with the policy injected as the
thing under test -- is followed exactly; the framework is not.

**Why:** at a single echelon with daily buckets and deterministic lead times the
replay is a loop over days with a pipeline dict. A discrete-event framework
would add a runtime dependency and indirection over the one number the entire
positioning rests on, and this has to be readable end to end without knowing a
framework.

**When SimPy earns its place:** multiple echelons with concurrent replenishment,
stochastic lead times, or contention for a shared resource. None are in scope,
and multi-echelon is "later if ever".

## 2026-08-26 — Unmet demand is lost, not backordered

**Decided.** A customer who cannot get the grade today buys it elsewhere.

**Why:** it is also the conservative reading. Backorders let a late delivery
still count as served, which flatters any policy that under-stocks. If a
customer genuinely backorders, this is the first assumption to revisit.

## 2026-08-26 — The naive-zero policy is kept as a permanent comparator

**Decided.** It stays in the comparison set even though it is obviously bad.

**Why:** it is the experiment, not a filler row. A forecast of zero is close to
MASE-optimal for intermittent demand and delivers 70.6% fill on intermittent and
48.1% on lumpy -- 4.4% at zero safety stock. That single number converts an
abstract argument about metric choice into something a finance manager reads in
one line. Removing it would remove the evidence.

## 2026-08-26 — The frontier is the comparison, not the point

**Decided.** `--sweep` traces fill rate against inventory across safety levels,
and `docs/service-backtest.md` leads with the caveat that a single (fill, stock)
pair is close to meaningless because any policy buys service with stock.

**Reported honestly, including the uncomfortable part:** the fitted forecast is
*competitive with* a well-tuned reorder point, not dramatically better. At 3
days of safety it reaches 95.1% on 916 units against the incumbent's 94.1% on
824. The clear win is lumpy demand, where it gets better service on less stock.

**Rejected: tuning the reorder point down to make the forecast look better.**
A strong incumbent is a finding to report. Making it look worse is precisely
what this project exists not to do.

## 2026-08-26 — POLICY: the seasonal period comes from the working calendar

**Decided.** `planbrain/working_calendar.py` derives it: any weekly pattern with
a closed day gives 7, a continuous operation gives 1. Nothing hardcodes a
constant, and the tests assert the derivation by contrast rather than the
literal.

**Why:** the period is a property of the customer's calendar, not of a metrics
module. A hardcoded 7 works until the first round-the-clock customer, and then
it silently mis-scales every accuracy number rather than failing.

**Noted:** this derives the periodicity the *calendar* forces, which is what
configuration can know. It does not claim demand has no weekly shape when the
plant runs continuously -- retail peaks at weekends whoever is open.

## 2026-08-26 — POLICY: a mean never travels without its denominators

**Decided.** `ScoredMean(value, n_scored, n_unscored)` is returned by anything
that computes a mean. It has no `__float__`, so it cannot silently become a
number, and `Summary` wraps it with median and extremes. `SeriesResult.mase`
returns one instead of a bare float; so do fill rate and average on-hand in the
service backtest.

**Why structural rather than conventional:** a caller who has to *remember* to
report the denominator will eventually forget, and a mean quoted without it is
unfalsifiable. This is the same class as the empty-result sweep -- make the
unsafe thing impossible rather than documented.

**Extended to the service backtest:** fill rate and average on-hand are carried
together in one `PolicyResult`, because a policy hits any fill rate by holding
enough stock and holds almost no stock by serving nobody. Either alone is
meaningless.

## 2026-08-26 — A stale reorder point joins the comparison permanently

**Decided.** Two reorder-point comparators, always reported separately and
clearly labelled: **tuned** (refit on all history) and **stale** (parameters
frozen on the first third, never revisited).

**Why:** "well-tuned" presupposes ongoing tuning nobody is doing. The stale rule
is what an SME incumbent actually looks like -- numbers set once, possibly by
someone who has left. The tuned row was not the honest incumbent.

**Not a way to weaken the baseline.** Both are kept. The tuned row stays in
permanently.

**And the finding went against us, recorded unedited:** the stale rule holds up
almost as well -- 93.3% fill against 94.1%, on identical stock, with the gap
mainly on intermittent demand (95.0% against 97.8%). That weakens the
"parameters go stale" argument on this dataset.

**Caveat that cuts against us in the other direction:** the demo history is
largely stationary -- lifecycle events but no sustained demand drift. Staleness
bites hardest under drift, so this dataset **under-tests** the stale comparator.
Logged as a generator gap. Until it is fixed, the narrow tuned-versus-stale gap
is under-evidenced rather than a result.

## 2026-08-26 — POSITIONING: the claim is not "better forecasts"

**Decided, and it supersedes any earlier framing.** Planning Brain claims
exactly three things:

1. **Lumpy and intermittent demand**, where the evidence is strong and
   spreadsheets fail worst.
2. **Capacity-feasible plans.** A reorder point structurally cannot produce one.
   This is where `rccp` (item 6) lands, and it is a difference in kind rather
   than degree.
3. **Parameters that stay fitted rather than going stale.**

**Portfolio-level forecast accuracy is NOT a selling point and must not be
presented as one anywhere** -- README, docs, or demo.

**Why:** item 5 measured the fitted forecast at 95.7% fill against a tuned
reorder point's 94.1% and a stale one's 93.3% on comparable stock. At portfolio
level this is competitive with a spreadsheet rule, not dramatically better. The
clear win is lumpy demand: 84.4% on 868 units against 82.0% on 910, better
service on less stock.

**Claim 3 is currently the weakest** and is flagged as such in
`docs/service-backtest.md` rather than leaned on.

**Revisit after item 6**, when capacity feasibility exists and there may be a
stronger honest story.

## 2026-08-26 — The README publishes the uncomfortable comparison

**Decided.** The service table is the README centrepiece, including the row
where a spreadsheet rule matches us, and with all gaps stated.

**Rejected: a finance-manager pitch version.** A project that publishes a result
showing it is only competitive on part of the portfolio is far more credible
than one that publishes only its wins, and credibility is what this repo is for.

**Rejected: leading with the 95.7% headline.** It is true and it is not the
point; leading with it would be exactly the "better forecasts" framing that has
just been retired.

---

# Build item 6 — rccp

## 2026-08-26 — Rough-cut loads planned RELEASES, not receipts

**Decided.** `rccp_input` takes `planned_order_release`. The contract changed.

**Why:** work happens between release and receipt. An order released on day 8
with a two-day lead time occupies the blender on days 8-10. Loading at the
receipt bucket would report a plant that looks free exactly when it is busiest --
a wrong number that reads as good news.

**Decided: front-load the whole order into the release bucket** rather than
spreading it across the lead time. That is what "rough" in rough-cut means: it
surfaces an overload earlier rather than later, and it is honest about its own
resolution. Exact timing within the lead time is finite scheduling, which is
PyJobShop's job.

## 2026-08-26 — Work with zero available capacity is its own exception

**Decided.** `load_without_capacity` is reported separately from
`overloaded_buckets`, and utilisation reads 0.0 in those buckets.

**Why:** a bucket with zero available hours is a closed day or a resource down
for maintenance. That is a different mistake from an overload, not a worse
degree of one, and it is **invisible in a utilisation figure** because dividing
by zero has no honest answer. The demo plant is shut one day a week and `netreq`
does not know that -- twelve buckets per resource carry work on a closed day,
which is the capacity argument in miniature.

**Rejected: reporting infinity or a sentinel utilisation.** Both are numbers
somebody would then average.

## 2026-08-26 — Load is split into run time and changeover

**Decided.** The report separates them.

**Why:** they have different fixes. Changeover is attacked by lot sizing and
campaign sequencing; run time can only be attacked by more capacity or less
demand. One combined number would hide which problem the plant has.

**First result:** 8,527 h run time (51.5%) against 8,020 h changeover (48.5%).
Nearly half the load is changeover because `netreq` uses lot-for-lot on
intermediates, so a blend is made on every day it is needed -- a median of 26
production days per SKU over 90 buckets, each paying a full setup.

## 2026-08-26 — The demo plant is not capacity-balanced, and that is logged not fixed

**Found.** The first rccp run reports the plan at roughly 3x capacity,
overloaded in 86 of 90 buckets. Run time *alone* exceeds total capacity by 54%,
so no lot-sizing policy could rescue it.

**Cause:** the item 2 generator produced routing rates and setup times
independently of demand volumes. Nothing ever checked that the plant could make
what it sells.

**Rejected: retuning the generator until the plan looks feasible.** That is
tuning around an uncomfortable result, which this project does not do. Logged as
a gap in `docs/rccp.md` and the README instead.

**Consequence for positioning, stated explicitly:** the demo can evidence *"we
detect infeasible plans"* and cannot yet evidence *"we produce feasible ones"*.
Claim 2 in the README was rewritten from "capacity-feasible plans" to "capacity
awareness" to match what is actually demonstrated. Detecting the problem is a
real capability worth having -- a planner who learns on Monday that the week is
3x over is better off than one who finds out on Thursday -- but it is not the
same claim, and the two must not be blurred.

## 2026-08-26 — Loading without a plan is refused

**Decided.** `rccp.run` raises `NoPlanError` when every planned release is zero.

**Why:** all-zero means `netreq` has not run for this horizon, not that the
plant is idle. Reporting a comfortably empty factory is the most reassuring
possible wrong answer. Same class as the empty-forecast guard at item 4.

---

# Build item 7 — balance, drift, and the capacity loop

## 2026-08-26 — The sizing rule was committed before it was run

**Decided.** `docs/capacity-sizing.md` was written and committed in its own
commit (`3e0977d`) before any capacity number existed. Target utilisation 82%,
campaign allowance 14 days, both chosen in advance.

**Why the commit order matters:** a sizing rule written after seeing the result
is indistinguishable from a rule fitted to it. The separate commit is the
evidence that it was not.

**Rejected: sizing to 100%.** A perfectly balanced demo plant is as unrealistic
as a 3x overloaded one, and it would make capacity checking look unnecessary --
a plant that is never tight has no use for rough-cut.

**Rejected: matching the campaign allowance to what netreq actually does.**
Lot-for-lot runs near-daily campaigns; sizing to that would be sizing from the
plan, which is the thing the rule exists to prevent. The gap between the
assumption and the plan is the finding.

**Guarded structurally:** `test_capacity_lands_on_the_stated_target_utilisation`
reconstructs the rule independently and asserts capacity lands on the stated
target, so it cannot drift into "whatever made the plan feasible".

## 2026-08-26 — A resource is a work centre, not a machine

**Decided.** Sized hours may exceed 24 a day. Resources renamed accordingly
("Blending, large batch" rather than "Blender A 20kL").

**Why:** a rough-cut resource is a work centre which may hold parallel
equipment. 35 hours a day is two vessels running seventeen. Which physical unit
does which job is finite scheduling, which rough-cut deliberately does not know.

## 2026-08-26 — Demand drift added, and claim 3 survives on evidence

**Decided.** 35% of series carry a sustained trend, -48% to +119% across the
history.

**The pre-commitment**, written before the run: if the stale reorder point still
held up under drift, claim 3 would be **dropped**, not softened.

**Result: it did not hold up.** The tuned-versus-stale gap went from 0.8 points
to 3.3, and bites hardest on exactly the classes this tool claims -- intermittent
87.3% against 93.9%, lumpy 86.0% against 94.0%. Claim 3 stands, now on evidence
rather than on plausibility.

## 2026-08-26 — Releases are pulled back to the previous working bucket

**Decided.** `netreq` offsets a planned release backward past any non-working
bucket. `Item.working_buckets` carries the calendar as flags rather than dates,
so `core` stays free of dates.

**Backward, not forward:** starting later would make the receipt late, which is
what the lead-time offset exists to prevent.

**This was a real bug, not a reporting question.** At item 6 the plan scheduled
production on twelve closed days per resource and no engine except `rccp` could
see it. Now zero, with a test holding it there.

## 2026-08-26 — Cost-based lot sizing: hours are the currency

**Decided.** `cost_lot_sizing()` builds Wagner-Whitin parameters from routing
data: a changeover costs its setup hours, a unit-bucket of holding costs the
capacity embedded in the unit times a stated annual carrying rate of 25%.

**Why hours:** it keeps the units self-consistent without inventing money the
customer has not given us. Reuses the DP already in `netreq`, which is the
ponytail-ladder answer -- it only ever lacked costs.

**Stated limit, up front:** this is *cost-based* lot sizing, not a capacity
constraint. It never sees a per-bucket limit and cannot be steered to one.

## 2026-08-26 — Claim 2 stays where it is: the loop did not reach feasibility

**Result.** Cost-based lot sizing takes the balanced plant from 127% overall
utilisation to 75%, and overloaded buckets from 251 to 35. **Still infeasible.**

The plan fits on average and not bucket by bucket, which is the exact signature
of cost-based rather than capacity-constrained lot sizing.

**Per the pre-commitment, claim 2 remains "capacity awareness" and is not
upgraded to "capacity-feasible plans".** The bar was set in advance and the
result did not clear it.

**Out of scope, stated so the gap is not mistaken for an oversight:** genuinely
capacity-constrained lot sizing is the CLSP, a different and much harder problem.

**Guarded by a test that asserts the failure**
(`test_cost_based_lot_sizing_does_not_reach_feasibility`), with a docstring
saying that if it ever fails because the plan became feasible, that is a real
result and the claim can be upgraded -- but it must not be made to pass by
tuning.

## 2026-08-26 — The capacity win is bought with 7.4x the inventory, and we say so

**Found.** Cost-based lot sizing cuts capacity load 41% and raises average
projected on-hand from 136,439 to 1,008,134 units. 216 campaigns across 160
routed SKUs means most SKUs are made **once** in a 90-day horizon.

**Diagnosis:** valuing inventory by the capacity hours embedded in it badly
undervalues it. A litre of lubricant costs money to hold because of the material,
not the machine-minutes. With holding nearly free against a two-hour changeover,
the arithmetic correctly concludes "make everything once". The answer is
economically consistent and operationally absurd.

**Rejected: tuning the carrying rate until the campaigns look sensible.** That
is fitting a parameter to a desired answer, which is the same error as sizing
capacity to the plan. The real fix is unit costs from the system of record --
already on the gap list as "no cost model".

**Reported in full.** Publishing the 41% load reduction without the 7.4x
inventory would be a straightforward lie.

---

# Build item 8 — reconciliation and unit costs

## 2026-08-27 — Scoped as reconciliation, not unification

**Decided.** The goal is a decomposition where every term is named and the terms
add up. **Not** that the two engines produce the same number.

**Why:** `netreq` computes deterministic net requirements against a forecast; the
simulation replays realised demand with lost sales. They *should* differ. The
credibility problem is differing for reasons nobody can name, directly under the
table the positioning rests on.

**Rejected: a test asserting the two figures match.** Passing it would mean one
engine had been bent to fit the other.

**Found while scoping, and worth stating plainly:** the service backtest never
replayed `netreq`'s plan at all — it applies its own order-up-to policy. The two
engines were never answering the same question, so comparing them directly had
never been meaningful.

## 2026-08-27 — The ladder makes the decomposition sum by construction

**Decided.** Five rungs, each adding exactly one effect, so each delta *is* that
term.

**Result:** residual −0.0, or 0.0000% of plan on-hand, against a committed
tolerance of 0.5%.

**The reassuring finding:** pure netting with no safety stock and lot-for-lot
carries 8 units against a plan of 782. Essentially **all** of netreq's planned
inventory is a deliberate configured choice — 453 safety stock, 322 lot round-up
— rather than an artefact of the algorithm.

**The per-class finding, which the aggregate hides:** on intermittent demand the
plan is nearly right (forecast error +72 on a plan of 968) and its stock is
overwhelmingly safety stock. On smooth demand the plan is furthest out (+725
forecast error, +414 truncation) because smooth series carry the drift. Reporting
per class was committed before the run for exactly this reason.

## 2026-08-27 — A real bug, found by cross-checking two implementations

**Found.** `simulate.replay` silently lost every order placed with a **zero lead
time**. The order entered the pipeline at bucket `t` after that bucket's arrivals
had already been collected, so it was never received — ten units ordered, zero
delivered, fill rate zero, no error anywhere.

**Found how:** the ladder's fixed-schedule replay and `simulate.replay` are
separate implementations of the same physics, so feeding the simulation the same
schedule at zero lead time must reproduce the ladder's rung. It did not.

**Blast radius: none published.** Every demo SKU has a lead time of at least one
day and the service backtest defaults to seven. Verified rather than assumed.

**Why it matters anyway:** it was waiting for the first same-day-delivery item a
customer configured, and it was invisible to both engines individually.

## 2026-08-27 — Unit costs derived from a rule committed in advance

**Decided.** A standard cost roll-up: raws priced by type, intermediates from
their BOM plus a conversion adder, finished goods plus packaging by pack size.
Changeover priced at an hourly cost of capacity time. All committed in
`docs/unit-costs.md` in its own commit before any cost was computed.

**The 25% carrying rate was not touched.** Explicitly out of bounds. Adjusting it
to fix campaign length would be fitting a parameter to a desired answer.

**Result: the item 7 diagnosis was right.** Campaigns went from 216 (a quarter's
supply each, absurd) to 1,688 — about one per SKU every 8.5 days, arrived at
independently and close to the 14-day cycle capacity sizing had assumed. Stock
fell from 1,008,134 units to 193,278.

**The trade is now stateable:** a 31% reduction in capacity load for a 16%
increase in working capital.

**Claim 2 still does not move.** 87% overall utilisation with 147 of 450
resource-buckets over. Cost-based lot sizing never sees a per-bucket capacity
limit and was never going to reach feasibility — stated up front in
`docs/rccp.md`, and it held.

## 2026-08-27 — Claim 3's kill condition is recorded, not just its result

**Decided.** The README states that claim 3 was scheduled for removal before the
evidence existed, and what changed.

**Why:** a claim that survived a stated kill condition is worth more than one
that was never at risk. Recording only the surviving result would discard the
part that makes it credible.

---

# Item 8 follow-up — falsification and the second no-op sweep

## 2026-08-30 — PATTERN: a check that cannot fail is documentation, not verification

**Found by self-audit.** The reconciliation's headline residual of −0.0 was an
algebraic identity. Each term is a difference between adjacent rungs, so they
sum to the gap whatever the rungs contain; five random numbers produce the same
zero. No injected error could move it, because both sides derived from the same
rungs.

**Decided.** The reported residual is now the **cross-engine** one — ladder rung
4 against the same rung from `simulate.replay`, a separately written
implementation. It moves proportionally with an injected error, catches an
off-by-one schedule shift, and caught a real bug in practice.

**Both are reported, side by side and labelled.** Quietly dropping the weaker
number would hide that the earlier result had been overstated.

**The pattern, which generalises:** when presenting a set of checks as evidence,
identify which one can actually fail. Ask what change would break it; if nothing
plausible would, say so. Prefer two independent paths to the same number. Try to
break the headline result once, deliberately, before publishing it.

## 2026-08-30 — Second sweep for results that fail upward

Four instances of this class had been caught one at a time across items 4-8. The
rest were hunted in one pass. Six more masking defaults found and made loud:

| site | what the default hid |
|---|---|
| `PACKAGING_COST.get(pack, 0.0)` | packaging free for **every** finished good if the naming convention shifted |
| `unit_cost.get(sku, 0.0)` | a SKU silently dropped back to lot-for-lot |
| `MODEL_FOR_PATTERN.get(pattern, "SeasonalNaive")` | every forecast degraded to naive while the model mix still looked populated |
| `lead_times.get(sku, 7)` in `simulate` | a service figure for a product not in the part master |
| `by_sku.get(sku)` then `continue` in `reconcile` | series dropped silently, shrinking the sample without saying so |
| `(plan.value or 0.0)` | an unscored portfolio reading as perfect agreement |

**Deliberately left alone**, and this is the judgement that makes the sweep more
than a rule: `read_facts`' sparse lookup, `explode`'s missing independent demand
or component stock, `rccp`'s resource with no routed work. There absence is
*meaningful* and the default encodes it. Hardening those would turn ordinary
sparsity into an error.

Covered by `tests/test_no_silent_defaults.py`, including a test asserting the
legitimate sparse lookups still behave sparsely.

## 2026-08-30 — FINDING: two independent rules landed near the same campaign length

Promoted out of a parenthesis into `docs/unit-costs.md` with both commit hashes,
`3e0977d` (14-day campaign allowance, 26 Aug) and `1ce6c66` (unit costs, 27 Aug),
so a reader can verify the two derivations never reference each other.

**And the caveat, stated at the same weight as the finding.** 8.5 against 14 is
a 39% gap, the aggregate hides a per-SKU spread of 2.8 to 90 days with a median
of 9.0 and an interquartile range of 6.4 to 18.0, and only 45% of SKUs fall
between 7 and 21. A single-point assumption cannot match a distribution produced
by setup-to-holding ratios that vary across the portfolio.

**The gap has a consequence worth naming:** sizing provisioned for ~26 campaigns
a SKU-year where the economics want ~43, so the plant was sized for fewer
changeovers than it needs. The convergence and the residual infeasibility at 87%
are the same fact from two directions.


## 2026-09-02 — The shell shipped a shape the backend does not have

**Found by asking whether the thing is installable, not by a failing test.**
The desktop shell declared the backend as a Tauri `externalBin` — one file, with
the host target triple appended to its name. The PyInstaller build is a one-dir
tree: an executable plus an `_internal` directory of 889 files. The CI step
renamed the executable and left the tree behind.

Neither file was wrong on its own. There was no Rust toolchain here, so nothing
had ever compiled the shell, and no test of the config alone could have failed.

**Decided:** the backend ships under `resources` as a whole tree, and is spawned
with `std::process::Command` from `BaseDirectory::Resource`.

**Rejected — `tauri-plugin-shell`**, the conventional way to launch a sidecar.
We are not running a user-supplied command; we run one binary we shipped at a
path we computed. The plugin adds a scope to configure, a permission to grant and
a dependency to audit, and buys nothing. `capabilities/default.json` therefore
grants `core:default` and `dialog:allow-open` and nothing else, asserted as an
exact set.

**Rejected — deduplicating the three copies of the backend path.** It appears in
the Rust constant, the config glob and the workflow's staging step. The config
cannot read the Rust and the workflow cannot read either, so the copies stay and
a test asserts they agree. This became practice 12 in `docs/method.md`.

**Still unverified, and labelled so:** that a `resources` glob installs to a path
preserved relative to `tauri.conf.json` is read from Tauri's documentation, not
observed. The three copies are asserted to agree with each other; nothing here
proves they agree with the bundler.

## 2026-09-02 — Test counts removed from prose rather than corrected

The README published two different suite sizes, 604 in one line and 625 in
another, against a suite that had grown past both; `docs/offline.md` carried
the 604. The guard below now rejects this paragraph if it quotes either the
old way round, which is the check declining to make an exception for the
commit that introduced it.

**Rejected: pinning the count to the register.** It would be accurate and it
would need updating on every commit that adds a test — a pin that is edited
whenever it fires protects nothing, which is practice 10 applied to prose.

**Decided:** prose does not quote a suite size at all. The number is not evidence
about the product; the claims that carry weight are the fill rates, which are
pinned to a fresh run. `test_no_document_publishes_a_test_count` enforces it
across the README and every file in `docs/`, and was verified by breaking it.

## 2026-09-02 — Distribution: draft releases, checksums, no signature

`.github/workflows/release.yml` builds three targets on their own runners —
Windows MSI and NSIS, macOS DMG for Apple Silicon and for Intel — and attaches
them to a **draft** GitHub Release with `SHA256SUMS.txt`.

**NSIS as well as MSI** because some corporate policies block MSI outright, and
a planner who cannot install the thing is not a user.

**Intel macOS added.** `docs/install.md` had said Intel builds were not produced;
that sentence would have quietly become false when the runner was added, so a
test now ties the release matrix to the platforms the guide names.

**The checksums exist because the install guide already told people to compare
one** and nothing produced the file. Instructions pointing at an artifact that
does not exist are worse than no instructions: a user who follows them concludes
the download is wrong.

**Stated in the release notes rather than implied:** a checksum is not a
signature. Anyone able to replace the installer could replace the checksum file
beside it. It catches a truncated download or a bad mirror, and that is all it
claims to do until a certificate exists.

**Draft, not published.** A human looks before it is public.


## 2026-09-02 — FINDING: the project could not be installed from a clean clone

The first push to GitHub failed on the first line of every CI job:

    pip install -e ".[dev]"
    error: Multiple top-level packages discovered in a flat-layout:
           ['desktop', 'datasets', 'packaging', 'planbrain']

setuptools' auto-discovery refuses to build when a flat layout offers more than
one candidate top-level directory. `pyproject.toml` declared no packages at all,
so this had been true since `packaging/` was added.

**Why nothing caught it.** The development environment held an editable install
from when the tree had one top-level directory, and pip never re-resolves an
install that is already in place. Every local run used it. The command in the
README, in `docs/install.md` and in all three workflows was one nobody could
have run.

**Same shape as the packaged offline audit**, which was verified in a checkout
and turned out to differ in the environment that ships, and the same shape as
the `externalBin` mismatch earlier today: correct locally, wrong in the
environment that matters, and no local test able to tell.

**Fixed** with an explicit `[tool.setuptools.packages.find]` naming
`planbrain*`. `tools` stays a repo-root module on the pytest pythonpath,
`packaging` holds build scripts, `desktop` is Rust and HTML; none of them ship.

**Two guards**, both verified by removing the fix and watching them fail. One
invokes `get_requires_for_build_editable` directly -- the call that raised --
which costs a second and needs no network, so the real check is not CI-only. The
other asserts the setting is still *load-bearing*, by confirming more than one
top-level package directory remains: a config line that has quietly stopped
doing anything is a shape this project keeps finding.

**The uncomfortable part:** the previous commit's message says the release
pipeline runs every backend gate before bundling. It does, and none of them
would ever have run, because the job could not get past its install step. The
gates were real and the path to them was not.


## 2026-09-02 — FINDING: the offline job could only ever run part of the suite

With the install fixed, the `test` job went green and `offline` did not. It runs
the suite inside a container, and the Dockerfile copied `planbrain`, `tools`,
`tests` and `docs` only. Two test modules read directories that were never
copied:

* `test_bundle_manifest.py` puts `packaging/` on `sys.path` -- broken in the
  container since the day it was written
* `test_dataset_boundary.py` asserts `datasets/` exists, and `.dockerignore`
  excluded it. **The test that asserts the boundary was excluded by the
  boundary.**

Plus `test_desktop_shell.py`, added this morning, which reads `desktop/`.

README and `docs/offline.md` both said CI runs *the whole test suite* with no
network interface. It never has. There was no remote until today, so the job
had never run at all.

**Rejected: skipping the tests whose inputs are absent.** That makes the claim
true only of the tests that happened to be copied -- the empty-result-reads-as-
success shape with a green tick on it. The image now carries them instead;
`datasets/` is two small scripts and a README, and the large download it fetches
lives in `data/m5`, which is ignored separately and was never the reason.

**Guard:** `test_the_image_contains_everything_the_suite_reads` cross-reads
every repository path the suite opens against the Dockerfile's COPY lines. It
found two more on its first run, one of them inside the guard itself -- the
Dockerfile, which the guard reads and the image did not contain.

**Third instance today of one failure mode:** correct locally, wrong in the
environment that matters, invisible to every local test. The `externalBin`
mismatch, the flat-layout install, and this. The common cause is not
carelessness in any of the three; it is that this environment has no Rust, no
Docker run in the loop, and a stale editable install, so the local pass was
never evidence about the shipping environment.


## 2026-09-02 — Growth assumptions: two parameters, one source of trend

`demand_growth_pct` and `capacity_growth_pct` on the scenario, annual, compounded
daily from `history_end`. Rules committed in `docs/forecast.md` in their own
commit before any code existed.

**Rejected: one growth control.** Moving demand and capacity together reports a
comfortable factory at every setting -- the answer a planner is least likely to
question, and exactly the one `rccp` exists to withhold.

**Rejected: applying the overlay to everything and reporting the overlap.**
Measured first, not assumed: of 103 smooth and erratic series on the seed-7
demo, 21 already select a trend term (`ETS(A,A,A)` x11, `ETS(A,Ad,A)` x10).
A blanket overlay grows those twice while Croston and TSB series grow once --
two populations, different arithmetic, nothing failing. So AutoETS is refitted
with the trend forced off, but only for series that actually chose one, so four
fifths pay nothing for the check and the whole branch is dead at zero growth.

**Rejected: reporting "series eligible for suppression"** instead of the real
count. It would have been free and would have meant nothing. The report says how
many fitted trends the assumption displaced, which is a cost the planner is
paying.

**Rejected: anchoring at `horizon_start`.** It drops the gap between the last
actual and the first planned bucket -- an error that is small, always in the same
direction, and invisible in any output.

**Rejected: a second measure holding the un-grown forecast.** Comparing growth
cases is comparing peer scenarios, which is what the flat scenario model is for.
`copy_scenario` and `commit_scenario` carry the rates with the rows; a copy that
reset them would be a different plan wearing the same name.

**Kill condition met.** Zero growth is byte-identical: no refit, no suppression,
no overlay, and `test_every_published_figure_still_matches_a_fresh_run`
recomputes the portfolio with every headline number unmoved.

**One defect found in the writing, worth keeping.** The capacity report's
assumptions line was guarded on a key `rccp`'s report did not carry, so it
printed nothing when only demand growth was set -- the branch was unreachable
for one of the two parameters. Caught by running it, not by a test. Now
parametrised over each parameter and the combination.

**Cost, stated rather than absorbed:** the suite went from roughly three minutes
to eight. The capacity tests each run the full portfolio through forecast,
netreq and rccp twice, because a capacity verdict on a handful of SKUs is not a
capacity verdict. Left as-is; if it becomes intolerable the fix is a
session-scoped planned scenario, not thinner assertions.

**Not surfaced in the desktop app.** That window has the import and
column-mapping flow only -- there is no planning screen to put a field on. The
backend method `scenario.growth` exists and works; a control that nothing
reaches would be worse than the honest gap.


## 2026-09-03 — The identity gate had only ever seen one platform

The first Linux and macOS builds failed the bundle gate on twenty entries:
`libgfortran`, `libquadmath`, `libstdc++`, `libz`, `libcrypto.so.3` and the
rest. Every allowlist key carried Windows spelling -- `libcrypto-3`,
`libffi-8`, `python314` -- because the list was written from one local Windows
build and had never been run against anything else.

**Rejected: per-platform sections in the allowlist.** That would let a package
be permitted on Linux and forbidden on Windows with nobody seeing the
asymmetry, which is this gate's own failure mode moved one level up.

**Decided: canonical names.** `canonical()` strips the extension chain, the
auditwheel content hash and a trailing soversion, so `libcrypto-3.dll` and
`libcrypto.so.3` are one entry. Version digits are stripped only after a
separator, or `libbz2` becomes `libbz` and `VCRUNTIME140` becomes `VCRUNTIME`
-- two libraries silently merged into one exemption nobody wrote.

**The worse bug was in FORBIDDEN, not ALLOWED.** `audit()` resolved a name
against the allowlist only and kept the raw filename otherwise, so
`libssl.so.3` never reached the forbidden check. The build still failed, so it
was never a silent pass -- but the one finding this gate exists to produce would
have arrived as one line among twenty. Now resolved against both lists, and
tested under all three platform spellings.

**Four packages excluded rather than allowlisted**, each proven unnecessary by
blocking the import and running the whole pipeline: `formulaic`,
`interface_meta` and `patsy` (statsmodels' formula API, which we never touch --
`patsy` had been allowlisted for months on the assumption it was needed), and
`readline`, which is terminal line editing in a process whose only input is a
pipe. Excluding a module does not drop the shared library beside it, so
`strip_orphaned` removes libreadline, libtinfo and libncurses.

**Verified locally before spending CI minutes:** a full Windows build passes
both gates at 159.9 MB, and the packaged binary answers `ping`, `demo.build`
and `scenario.growth`. That is the check that would have caught the
`openpyxl.chart` exclusion the day it was made.

**A third duplicate allowlist key** (`pytz`) was found by the new guard against
repeated keys in a dict literal -- where the later entry silently wins and the
earlier reason is discarded.

## 2026-09-03 — Linux installers, and a planning screen

**Linux.** `appimage` and `deb` targets, built on `ubuntu-22.04` rather than the
newest image: a binary linked against a newer glibc refuses to start on an older
distribution and the failure reaches the user as "not a valid executable". The
one thing not bundled is the system WebKit that renders the interface, which is
documented as the single external dependency and is a rendering library rather
than a network one.

**Intel macOS moved from `macos-13` to `macos-15-intel`.** The `macos-13` job
sat unscheduled for over an hour across two runs while its Apple Silicon sibling
ran and failed honestly. Three jobs got runners; that one never did.

**The window had the import flow and nothing else**, so every plan ran through a
terminal and the audience was "a manufacturer who is comfortable with a command
line" -- close to nobody. There is now a Plan tab: load data, set the two growth
rates, choose the demand source and lot-sizing rule, run, and read the verdict
with its assumptions beside it.

**The protocol gained progress lines.** A plan run is the better part of a
minute and the pipe is one request at a time, so without them the window is
simply frozen, which is indistinguishable from crashed. They carry the request
id and are distinguished by `progress`, never by `ok`, so a client that ignores
them still sees exactly one response per request.

**The verdict states what it does not do.** When the plan does not fit, the
interface says so and then says that it will not choose what to move --
steering to a per-bucket limit is a different algorithm and is out of scope.
Naming the lever that exists (capacity growth, horizon, lot sizing) is honest;
implying the tool will solve it is not.

**Tested by the only rule that spans the halves:** every backend method the
frontend names must exist in `METHODS`. Nothing else joins them -- the Rust
never inspects the string and there is no browser test. It caught a real
mismatch on its first run.


## 2026-09-03 — Inventory in money, and a service level that does not mean what it says

**Working capital.** `simulate.compare` now reports what each policy's stock is
worth and what holding it costs per year, using the same `ANNUAL_CARRYING_RATE`
that cost-based lot sizing prices changeover against -- two numbers in one
product describing the cost of holding stock must not disagree.

A **total**, not a mean, so the series count travels with it and there is no
`__float__`: quoting working capital without saying how much of the portfolio it
covers is the same mistake as quoting a fill rate without its denominator. An
unpriced part is **counted, not treated as free** -- a half-priced portfolio
would otherwise report half the capital and look better than it is, and a zero
cost is what a missing price looks like after a spreadsheet.

The comparison it enables: on 24 series the fitted forecast buys 97.9% fill for
12.7M of stock; the tuned reorder point gets 94.8% for 8.4M. That trade was
previously only expressible in units, which cannot be compared across a
portfolio -- a thousand fasteners and a thousand castings are not the same
decision.

**Safety stock as a service target, and the finding that matters more than the
feature.** `--service-level 0.95` implements the textbook periodic-review form
with `z` from `statistics.NormalDist`. Then it was measured, and the measurement
is uncomfortable:

| requested cycle service | achieved fill | lumpy |
|---|---|---|
| 80% | 95.9% | 92.4% |
| 99% | 97.6% | 94.8% |

Nineteen points of requested service move achieved fill by 1.7. Two reasons, and
both are worth a user knowing: a cycle service level is not a fill rate -- it is
the probability of surviving a cycle, not the fraction of demand met -- and the
order-up-to level is dominated by the forecast of lead-time demand, with safety
stock a modest addition on top. **This dial is not how service is bought here.**

And lumpy demand is worst at every level, which is the normal approximation
failing exactly where it was always going to. The rule is least reliable for the
demand pattern this product claims to be for.

**Rejected: solving for a fill-rate target instead.** It is the number planners
mean, and it is obtainable through the standardised loss function -- but on the
same normality assumption the lumpy column shows breaking. That trades a number
which is honestly the wrong measure for one that is confidently the wrong value.

**Rejected: making it the default.** `--safety-days` stays, because "7 days of
cover" does not tell anyone a probability that does not hold.

**The honest route is recorded rather than done:** the backtest already replays
every series, so safety stock could be calibrated per pattern against achieved
fill instead of assumed from a distribution.

**One pin, not twenty.** The four rows move together and each costs a full
portfolio replay, so the register pins the 95% row and recomputes it.


## 2026-09-03 — Backorders, and the number that would have flattered everything

`--unmet backorder` keeps unserved demand and fills it when stock arrives. Right
for an OEM on a supply contract, wrong for a retail counter, so lost sales stays
the default.

**The risk was the reporting, not the arithmetic.** A single "fill rate" would
have risen across the board the moment the flag was set, with nothing shipping
sooner. So `fill_rate` means *served in the bucket it was demanded in* under both
rules, and `eventual_fill_rate` sits beside it under its own name.

The measurement that justifies the split, on sixteen series: the naive-zero
policy serves **58.0% on time and 99.5% eventually** -- a 41-point difference
produced entirely by which question is asked. Anyone comparing a backordered run
against a lost-sales one on one column would conclude the naive forecast had
become competitive. It is late on 42% of demand.

**The policy is shown net stock**, on-hand minus what is owed. Shown gross it
would decide it has enough while owing a fortnight of demand and would never
catch up.

**Owed demand is served before today's.** Anything else leaves the oldest
customer waiting longest, which is neither what happens nor anything anyone
would defend.

## 2026-09-03 — The allowlist was checked on one machine, and one is not the matrix

Round two of the cross-platform gate, and the more useful half.

The Windows CI build carries `ucrtbase` and thirteen `api-ms-win-*` API-set
forwarders. **A local Windows build on Python 3.14 does not.** Same OS, same
spec, different interpreter build, fourteen different files. The local build that
passed both gates at 159.9 MB was a real check, and it was not the check CI runs.

The forwarders collapse to one allowlist entry: thirteen lines saying the same
sentence is thirteen lines nobody reads.

## 2026-09-03 — FINDING: the Linux artifact is not statically linked

The first Linux bundle to get past the identity gate could not start:
`error while loading shared libraries: libz.so.1`, in
`gcr.io/distroless/base-debian12`.

PyInstaller's bootloader is an ordinary ELF executable whose own `DT_NEEDED`
entries resolve from the system at exec time, before anything in `_internal` is
reachable. The artifact needs ordinary system libraries and always did -- on
Windows and macOS too, where they ship with the OS and nobody notices.

**Decided: `debian:12-slim` as the base.** The claim under test is *no network*,
and `--network=none` is what tests it. Distroless was testing a claim nobody had
made, and testing the wrong thing convincingly is worse than not testing it.

**Recorded in `docs/install.md`, not just in a workflow comment**, because a
Linux user on a minimal install can hit it and needs to be told `zlib1g` and
`libwebkit2gtk-4.1-0`. "Self-contained" stays true in the sense that matters --
no runtime, no packages, no network. "Statically linked" was never true and is
not claimed.


## 2026-09-03 — Capacity explains itself, and refuses to solve itself

`rccp.relief` attributes every overloaded bucket: which SKUs put the hours
there, split into run and setup, and how many spare hours sit nearby on the same
resource. `--explain` on the capacity report prints it.

**It does not say what to move**, and that line is the point. "Move 3,200 units
of SKU-104 to Tuesday" claims two things: that Tuesday has the hours, and that
the material will be there. This can see the first and cannot see the second --
component availability is `netreq`'s question, and it depends on lead times,
on-hand stock and the BOM. Presenting the first as though it settled both is a
plan number that looks right.

**Two headline numbers, and both needed care.**

`concentration` is the share of excess attributable to the single worst SKU. On
the demo it is **4%**, which is the answer: no single product dominates, so this
is a capacity decision and not a scheduling one. Printed in words as well as a
percentage, because a planner reading "4%" may not read it as "rescheduling one
product cannot fix this".

`relievable_by_moving_earlier` was renamed to
`relievable_by_moving_earlier_upper_bound` after the first run reported 131 of
139. Each bucket is tested against the spare hours before it, and neighbouring
overloaded buckets are tested against **the same spare hours**. They cannot all
use them. A reader seeing "131 of 139 relievable" would reasonably conclude the
plant is fine. The honest lower bound needs an allocation across buckets, which
is the solver this module exists in order not to be.

## 2026-09-03 — FINDING: the job that guards against glibc drift had the drift

The Linux artifact would not start in `debian:12-slim`:

    libpython3.12.so.1.0: version `GLIBC_2.38' not found

`release.yml` pinned `ubuntu-22.04` for Linux from the day the target was added,
with a comment explaining that glibc is backward compatible and not forward
compatible. `package.yml` -- the workflow whose entire purpose is to catch
packaging faults before a release -- was still on `ubuntu-latest`.

So the artifact being tested was not the artifact being shipped, and the check
that existed to catch exactly this class of problem was itself built wrong. Two
workflows disagreeing about a build image is invisible in either file, so the
rule is now asserted across them, including that neither may use a `latest`
image: the day it advances, every Linux artifact silently stops running on older
distributions.

**Round three of the cross-platform gate**, and each round found something the
previous could not have: spelling, then contents, then the build environment
itself. Windows now passes the package workflow end to end -- the first platform
to do so.


## 2026-09-03 — The mapping baseline got better, so the model's bar went up

The alias table learned what the corpus actually contains: transliterated forms
(`dinank`, `tarikh`, `thethi`, `maal`, `matra`, `alavu`, `kidangu`) and two
suffix rules -- drop a trailing identifier token so `ITEM_CD` offers `item`, and
expand a trailing abbreviation so `TXN_DT` offers `txn_date`.

| | before | after | gain |
|---|---|---|---|
| dev | 74.0% | 91.7% | +17.7 |
| **holdout** | **69.0%** | **75.0%** | **+6.0** |

**The dev gain is not the result.** Those aliases were written by reading the
dev misses, so dev measures how well a list covers the cases it was copied from.
The holdout gain is a third of it. Both are asserted together in one test so
neither can be quoted alone.

**Rejected: a stemmer.** It would map `dated` and `dating` onto `date` and would
eventually map something onto the wrong field with no line anyone could point
at. Two listable rules, and the corpus says which files need them.

**Rejected: `Item Name` to sku_id.** It is the right answer in the Tally case
and the wrong one for the `parts` table, where `name` is a real column and
`sku_id` is declared first -- so the alias would silently steal it. Left as a
miss rather than fixed with a conditional nobody could reason about.

**The bake-off bar moved from 79.0% to 85.0%, against us.** Threshold 1 is
written as *baseline + 10 points*, so a stronger incumbent raises the bar the
model must clear. The rule did not change; the incumbent did. Recorded because
moving a target after the fact is what a reader should be suspicious of, and the
direction is the tell -- this makes the model's case harder, not easier. A test
recomputes the bar from the baseline so it cannot quietly fail to follow.

**Unchanged, and this is why the widening was allowed at all:** zero wrong
columns on the holdout, false confidence still 9.1%. Every point came from
converting silence into correct answers.

**The pin failed, deliberately.** `test_the_baseline_scores_are_reproducible`
broke the moment the mapper improved -- which is precisely when a published
figure goes stale unremarked -- and moving it cost an explicit edit.

## 2026-09-03 — The package workflow is green on every platform

Four jobs, four platforms: Linux offline-artifact in a container with no network
interface, Windows, macOS ARM and macOS Intel. First time any of them have all
passed together, after three rounds of cross-platform findings -- spelling, then
contents, then the build environment.

The backend halves of the installers are now verified everywhere they ship. The
Tauri compile remains unverified: there is no Rust toolchain in this
environment, and `release.yml` is what will settle it.


## 2026-09-04 — The application was launched for the first time, and it was broken

Nobody had ever run it. The Tauri shell compiled in CI and everything known
about it came from cross-file assertions; `docs/packaging.md` said as much and
CI was named as the only thing that could settle it. There is no Rust toolchain
in this environment, so the shell stayed unverified.

It did not have to. The last successful release run still had its Windows
artifact, so: download it, verify it against the published `SHA256SUMS.txt`
(**both matched — the checksum mechanism worked end to end, exercised by a
consumer for the first time**), extract the MSI with `msiexec /a` into a short
path, and launch it.

**The MSI is sound.** An administrative extract to the scratchpad first failed
with `Error 1304`, which is a `MAX_PATH` overflow from the deep temp directory
and not a defect in the package — the same extract into `C:\pbx` succeeded. The
packaged backend then ran on this machine, offline guard engaged, and built the
demo. The window opened, titled and rendered, with the tabs and all three
sections drawn.

**And the status bar read `starting…` forever.** The backend process was alive
and answering the whole time.

The shell spawns the backend in `setup()` and starts a reader thread that emits
each stdout line as a Tauri event. The backend prints its `ready` frame within
milliseconds. The webview then loads `index.html`, which only then runs the
module script that calls `listen("backend", ...)`. A Tauri event reaches the
listeners that exist when it is emitted, so **the first line was emitted into an
empty room and dropped, on every launch, on every platform.**

That line carries the offline state. The status bar showing whether the guard is
engaged is the product's central claim, and it never resolved. `loadProfiles()`
is called on the same frame, so the saved-mapping list never loaded either.

**Nothing in either file is wrong.** It is wrong in the order two things happen,
which is why every cross-file rule we have passed it, and why it took launching
the application to see. Fixed by buffering output until the interface asks for
it: `drain_backend` hands over the backlog and flips the shell to emitting live,
with the flag read under the same lock in both directions so a line is never
delivered twice and never lost.

**Buffering rather than re-sending the ready frame**, which would have been
smaller. The general fault is worse than its symptom: a backend that dies during
startup emits its error early too, and that line disappearing leaves the user
staring at `starting…` with the one message that would have explained it already
discarded.

**Second finding, in the same place.** `backend-exit` has been emitted since the
reader thread was written and the frontend never listened for it — a backend
that died mid-session left the window waiting for a reply that was never coming,
silently. Now asserted across the two files: every event the shell emits must
have a handler in the interface.

## 2026-09-04 — The plan computed 2001 orders and showed none of them

`plan.run` returned a forecast summary and a capacity verdict, and the results
screen rendered one table: utilisation by resource. A planner's session ended at
*"utilisation 89.5%, 134 overloaded days"*. Meanwhile, in the fact table:

    planned_order_receipt  2070
    planned_order_release  2001

**The arithmetic was right and the answer was discarded one step before it
reached a human.** This is the specific place a spreadsheet was beating the
product — not on forecasting, not on capacity, but on ending the session with a
list somebody can send to a supplier. `planbrain/orders.py` and
`docs/orders.md`.

`orders.py` computes nothing: quantities are read back from the fact tables
exactly as `netreq` wrote them, and the module joins names onto ids, sorts, and
writes a file. Architecture rule 4 holds trivially because there is no
arithmetic on the page.

**The list is releases, not receipts.** `_offset` pulls a release back to the
previous working bucket, so two receipts can merge onto one release date — which
is exactly the 2070/2001 gap. A due-date column beside each release would assert
a pairing that does not exist, so the list carries the lead time and leaves the
arithmetic visible.

**Rejected: pairing the k-th receipt with the k-th release.** It looks exact and
is wrong in precisely the 69 cases where the offset merged two, which is the
worst possible distribution of a defect: right everywhere a reader would check
by hand.

**Rejected: a value column, for now.** Parts carry a rolled-up `unit_cost` and
a total would be the number a finance manager asks for first. Left out because
the demo's costs are synthetic, and a rupee figure on a printed order list is
the number most likely to be quoted without its caveat.

**The empty export is refused rather than written.** A header-only sheet tells a
planner there is nothing to order; the realistic cause is that no plan was run.
Same shape as a size gate that passes because it measured nothing.

## 2026-09-04 — `dialog:allow-save` granted, and what the old test was protecting

`test_nothing_is_permitted_to_write_outside_the_application_directory` asserted
that `dialog:allow-save` was absent, on the stated reasoning that the import flow
reads a file the user chose and never writes one back. Exporting the order list
made that reasoning obsolete, so the permission is granted and the assertion
changed.

**Recorded because changing a test to admit your own work is the move a reader
should be most suspicious of.** What was actually protected is still protected
and is still asserted: no `fs:` permission, so the interface can neither read
nor write a file itself. A save dialog returns a *path*; the backend writes the
file. Granting `fs:` would let the frontend write anywhere on its own and is
still refused. The test kept its teeth and lost a clause that had stopped being
true — which is a different act from lowering a threshold, and the difference is
whether the property or the number moved.

## 2026-09-04 — Artifacts upload before the gate that rejects them

`check_installer_size.py` ran before `upload-artifact`, so a build it failed
uploaded nothing. That is how the 330 MB MSI came to be diagnosed twice from a
build *log*: WebView2 was ruled out because a grep of the log found only Rust
crate names, and the offline runtime installer was inside the MSI the whole
time.

Absence in a log is not absence in an artifact — and you cannot search an
artifact that was never uploaded. The upload now runs first. The gate still
fails the build; this only changes whether the thing it rejected can be opened.

## 2026-09-04 — Overrides: designed, deliberately not built yet

Asked for explicitly, and designed in `docs/overrides.md` before any of it is
written, because it adds to the measure vocabulary and that is a contract.

The mechanism is the textbook **firm planned order**, not a new invention: the
planner fixes quantity and date, and subsequent runs net around it rather than
resizing or rescheduling it. `derived = 0`, so it is an input in the same class
as `scheduled_receipt` — which means `write_plans`'s existing refusal to write a
non-derived measure already stops a planning run from overwriting a human's
number, with no new rule.

Provenance goes in a separate `plan_override` table holding author, reason and
timestamp and **not** the quantity, which stays in the fact row only. Two copies
of a number is two numbers.

**Rejected: overriding the forecast instead of the order.** Cheaper and
internally consistent, but it answers a different question. "This customer will
take 500" and "make 500, not 860" are not the same statement, and only the
second is a planner overruling the plan.

**Rejected: editing in place with no separate concept**, which is what Excel
does and is why an Excel plan cannot be re-run. **Rejected: a notes field with
no effect on the plan** — the planner writes down what they know, the plan
ignores it, and the numbers are now wrong *and* annotated.

## 2026-09-05 — The application saved nothing, and the install guide said it did

`planbrain/backend/__main__.py` called `session()`. The default is `":memory:"`.

So the packaged application held everything in RAM and discarded it when the
window closed: the imported history, the column mapping, the plan. A planner
could spend an afternoon mapping a Tally export and lose all of it by closing a
window, with no warning and no prompt.

Meanwhile `docs/install.md` told them, in three separate places:

* **One application and one SQLite file.**
* **Your data stays in a file you can see.** Delete it and it is gone; copy it
  and you have a backup.
* [uninstalling] *Neither removes your planning database* — `%LOCALAPPDATA%\PlanningBrain\`

**That directory was never created by anything.** The uninstall instructions
told people to delete a folder that did not exist, to remove data that was never
written, in the one document a user reads before trusting the application with a
year of their history.

Found by asking what happens when the window closes — which is a question only
worth asking once you have a window, and nobody had launched one until today.

`default_database()` now resolves the documented path per platform, and the
schema is applied only to a database that does not already have one:
`schema.sql` both creates tables and seeds scenario 0, so re-running it on the
second launch would either crash on `table scenario already exists` or, worse,
succeed and leave two scenario-0 rows for every read keyed on it to find.

**The paths are asserted against the prose**, not merely written to match it. If
the two drift, the uninstall instruction deletes the wrong folder and the user
believes their data is gone when it is not — a worse failure than the one being
fixed.

**`--db :memory:` remains**, as a flag. A throwaway session is a legitimate
thing to want; it is not a legitimate thing for everybody to get silently.

**Rejected: asking the user where to put the file on first run.** It is a
question with one sensible answer, asked of someone who has not yet seen the
application do anything, and the platform conventions already exist for exactly
this. The path is displayed instead — the ready frame now carries it and the
status bar holds it — so it is discoverable rather than chosen.

## 2026-09-05 — The frontend is 500 lines nothing had ever parsed

`tauri build` copies `dist/` verbatim. No test, linter or build step read
`index.html`, so a syntax error in the module script would ship and arrive as a
blank window, with the cause only in a devtools console the user does not have
open.

`node --check` on the extracted module, skipped where node is absent — the
offline container has none, the GitHub runners have it preinstalled. Cheap, and
it is the closest thing to a runtime check this suite has for the half of the
application written in JavaScript.
