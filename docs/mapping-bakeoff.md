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

---

# Outcome

*Appended after running what could be run. Nothing above this line was changed.*

## The baseline, measured

`planbrain.mapping.suggest`, the existing normalised-equality matcher, on the
full corpus:

| | dev (24 cases) | **holdout (21 cases)** |
|---|---|---|
| accuracy | 74.0% (71/96) | **69.0% (58/84)** |
| hit rate | 75.0% | 65.8% |
| refusal correctness | 66.7% | 90.9% |
| **false confidence** | 33.3% (4/12) | **9.1% (1/11)** |
| latency p95 | 0.2 ms | 0.1 ms |

**So the bar a model must clear is 79.0% accuracy on the holdout**, at no more
than 15% false confidence.

Two things worth saying about that baseline before any model is compared to it.

**It is a harder target than it looks.** It already satisfies threshold 2 on the
holdout — 9.1% false confidence — because it refuses whenever its alias table
has no entry. A model must be *both* more accurate *and* no more reckless, and
the second is where a fluent model naturally does worse.

**Its failures are almost all misses, not errors.** On the holdout it picked a
wrong column **zero** times and simply had no answer 25 times. That is the
failure mode you want from a fallback: it declines rather than misleads. The
27-point gap between its dev and holdout false confidence is small-sample noise
on 11–12 refusal decisions, and is a reason to read those two figures loosely.

## The bake-off did not run

**`llama-cpp-python` cannot be installed in this environment**, so neither model
was scored. This is a blocker, not a decision:

| requirement | status here |
|---|---|
| `llama-cpp-python` wheel for Python 3.14 | **does not exist** |
| MSVC compiler to build from source | not installed |
| cmake | not installed |
| ~5 GB for two Q4_K_M models | not attempted, blocked upstream |

`pip download --only-binary=:all: llama-cpp-python` returns *No matching
distribution found*.

**What exists and is ready**, none of it executed:

* `packaging/grammars/column_mapping.gbnf` — the GBNF, which makes an invented
  field name *unrepresentable* rather than detected afterwards, and makes
  refusal a first-class production
* `packaging/grammars/column_mapping_prompt.txt` — the prompt, stating that
  null is correct and expected
* `tools/score_mapping.py::model_mapper` — the runner, CPU-only, non-thinking,
  temperature 0

To run it, on a machine with Python 3.11/3.12 and a compiler:

```bash
pip install llama-cpp-python
python -m tools.score_mapping --mapper model --split holdout \
    --model models/Qwen3-4B-Q4_K_M.gguf
```

**This is an unverified surface in the sense `docs/packaging.md` uses the word.**
The grammar has never been parsed by llama.cpp; the prompt has never been sent
to a model. Both are likely to need a pass of real work, and neither should be
described as done.

## Environment required to run it

**A prerequisite for a future session, not a task.** Nothing here can be worked
around in this environment; it needs a different machine.

| requirement | why |
|---|---|
| **Python 3.11 or 3.12** | `llama-cpp-python` publishes wheels for these. It has none for 3.14 |
| **A C++ compiler** — MSVC Build Tools on Windows, clang or gcc elsewhere | needed if no wheel matches and it must build from source |
| **cmake** | llama.cpp's build system |
| **~5 GB free disk** | two Q4_K_M weights, roughly 2.5 GB each |
| **Network, once** | to fetch the weights. This is outside the app boundary, exactly like `datasets/fetch_m5.py` — a developer downloading public files, not the application reaching out |

```bash
pip install llama-cpp-python
# fetch Qwen3-4B-Q4_K_M.gguf and Phi-4-mini-Q4_K_M.gguf into models/
python -m tools.score_mapping --mapper baseline --split holdout
python -m tools.score_mapping --mapper model --split holdout \
    --model models/Qwen3-4B-Q4_K_M.gguf
python -m tools.score_mapping --mapper model --split holdout \
    --model models/Phi-4-mini-Q4_K_M.gguf
```

Run the **dev** split first and iterate there. The holdout is for the final
number, once.

## Nothing has been decided

The kill condition stands and is untested. **No model ships on the strength of
an unrun bake-off**, and the manual mapping UI — which is built, tested and
usable alone — remains the shipping path either way.

## Scope of the model, unchanged

If one ships, it does **column mapping and importer error triage only**. Never
plan explanation, never parameter suggestion. Every mapping is confirmed by the
user before anything is written, and architecture rule 4 holds without
exception: the model never touches a plan number.
