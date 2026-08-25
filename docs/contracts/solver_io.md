# Solver IO contract

Source of truth: `planbrain/contracts/solver_io.schema.json` (JSON Schema
draft 2020-12), with a worked example per payload kind in
`planbrain/contracts/examples/`. Validate with
`planbrain.contracts.validate_payload(kind, payload)` on **both** sides of the
boundary.

## Why this is strict

Solvers run behind a queue (architecture rule 3). A malformed payload does not
raise at the call site — it fails in a worker minutes later, with the original
request long gone. Everything below exists to make that failure impossible or
loud, never silent.

`additionalProperties: false` throughout. A typo'd key is an error, not an
ignored field.

## Series are dense and spine-aligned

The sparse-storage rule stops at this boundary. Django resolves it with
`read_facts` before serialising; a sparse array must never cross.

Every `dense_series` has exactly `horizon.bucket_count` entries, positionally
aligned to the daily spine from `horizon.start` to `horizon.end` inclusive.

JSON Schema cannot express "this array's length equals that field's value", so
`validate_payload` walks the schema alongside the payload and checks it. This is
the most important check in the file: a series one element short does not look
broken, it shifts the entire plan by a bucket and every number it produces stays
plausible.

## Payload kinds

| Kind | Produced by | Consumed by |
|---|---|---|
| `netreq_input` / `netreq_output` | Django / `netreq` | `netreq` / Django |
| `rccp_input` / `rccp_output` | Django / `rccp` | `rccp` / Django |
| `haulplan_input` / `haulplan_output` | Django / `haulplan` | `haulplan` / Django |

## Best-so-far is a first-class result

`run_meta.status` is one of `optimal`, `feasible`, `timeout`, `infeasible`.
A `timeout` result carries a usable answer and must be shown as one. In
`haulplan_output`, `truck_id: null` means a trip went unassigned — legitimate
under a time limit, and it must surface rather than being quietly dropped.

## Decisions worth knowing

**`gross_req` arrives already exploded.** BOM explosion happens caller-side;
`netreq` nets, lot-sizes and offsets. Keeps the solver a pure function of its
payload.

**`lead_time_days` is days, not buckets.** They coincide by construction because
buckets are daily. That is the point of the daily grain.

**`netreq_output.exceptions` is the planner's action list**, not a warning log.
A `past_due_release` means the order is already late.

**`rccp_output.overloaded_buckets` is reported, not recomputed by the UI**, so
the number the planner sees and the number a report counts are the same number.

**`haulplan_input.trucks[].ytd_long_haul_km` is required.** The fairness ledger
must be carried in. Without it, fairness restarts from zero every run and the
same truck gets picked every week — which is the exact problem `haulplan`
exists to solve.

**`distance_km` is road distance from the OSRM matrix**, never straight-line.

## No LLM writes a number here

Architecture rule 4. Agent code may read these payloads, explain them, and
propose parameters. Every value in them is produced by deterministic Python.
