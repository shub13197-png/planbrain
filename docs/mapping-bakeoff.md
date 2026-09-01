# Column-mapping bake-off: what would make a model worth shipping

**Written and committed before the corpus was scored against anything —
including the existing baseline.** Results are appended below the line and
nothing above it was revised afterwards.

## What is being decided

Whether to put a 4B language model into the installer for column mapping, at
roughly 2.5 GB, when a fifteen-line normalised-equality matcher already exists
and works.

**The manual mapping UI ships either way.** It is built, tested and usable
alone. This decides only whether a model sits on top of it.

## The corpus

45 hand-authored header sets, 180 field decisions, in
`tests/fixtures/header_corpus.yaml`. No customer data; every case imitates the
shape of a real export.

**Split fixed in the file, never derived at runtime.** A split recomputed from a
hash would move as the corpus grows and the held-out half would quietly stop
being held out.

| split | cases | may be looked at |
|---|---|---|
| `dev` | 24 | yes — tune, iterate, inspect failures |
| `holdout` | 21 | **no**, until the final run |

**Both are reported.** A dev-only number is a number someone fitted to.

## Refusal is a first-class answer

23 of the 180 decisions have `null` as the correct answer — the field genuinely
cannot be mapped from the headings alone. `Ordered Qty` and `Delivered Qty` are
both quantities; `Document Date` and `Posting Date` are both dates; a batch
number is not a SKU; money is not units.

**A confident wrong guess is worse than a blank dropdown**, because nobody
checks a field that already looks filled in. So a mapper is scored on knowing
when to decline, not only on hits.

## The metrics

| metric | definition |
|---|---|
| **Accuracy** | correct decisions ÷ 180. A correct refusal counts as correct |
| **Hit rate** | correct ÷ the 157 decisions that have a real answer |
| **Refusal correctness** | correct ÷ the 23 that should be declined |
| **False confidence** | guessed a column where the answer was "ask the user" ÷ 23 |
| **Latency** | wall-clock per header set, CPU only, p50 and p95 |
| **Bundle cost** | added installer megabytes |

**False confidence is the metric that decides this.** The others describe
usefulness; that one describes harm.

## Committed thresholds

A model ships **only if it clears all four** on the **holdout**:

1. **Accuracy ≥ baseline + 10 percentage points.** Below that, 2.5 GB and a
   second inference path buy a rounding error.
2. **False confidence ≤ 15%** — at most 3 of the 23 refusal cases guessed. A
   model that guesses confidently on ambiguous headers is worse than the dumb
   matcher, whatever its hit rate.
3. **p95 latency ≤ 5 seconds** per header set on CPU. Longer and a user will
   map four columns by hand while it thinks.
4. **Bundle cost ≤ 3.0 GB.**

## Kill condition, stated in advance

**If neither model clears threshold 1, no model ships.** The manual UI stands on
its own and 2.5 GB is a real cost to a user on a rural connection — the same
user this product exists for.

That is not a hedge. It is the expected outcome if the baseline turns out to be
strong, and a bake-off whose only possible result is "ship a model" would not be
worth running.

## The contest

| | |
|---|---|
| Candidates | Qwen3-4B, Phi-4-mini |
| Quantisation | Q4_K_M |
| Runtime | `llama-cpp-python`, **CPU only** — decided in `docs/packaging.md` |
| Thinking mode | **off**, explicitly. Reasoning tokens fight grammar constraints |
| Output | constrained by GBNF to the canonical field names plus an explicit refusal token, so an invented field name is not representable |
| Baseline | `planbrain.mapping.suggest`, the existing normalised-equality matcher |

Identical grammar, identical prompt, identical corpus for both.

## Scope of the model, unchanged

If one ships, it does **column mapping and importer error triage only**. Never
plan explanation, never parameter suggestion. Every mapping is confirmed by the
user before anything is written, and architecture rule 4 holds without
exception: the model never touches a plan number.
