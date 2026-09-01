"""Score a column mapper against the corpus. Metrics fixed in docs/mapping-bakeoff.md.

    python -m tools.score_mapping --mapper baseline
    python -m tools.score_mapping --mapper baseline --split holdout

A mapper is any callable ``(headers, table) -> {canonical: header or None}``,
so the dumb matcher and a model are scored by identical code rather than by two
implementations that could differ.

**Refusal counts as a correct answer**, on 23 of the 180 decisions. A confident
wrong guess is worse than a blank dropdown, because nobody checks a field that
already looks filled in.
"""

import argparse

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "header_corpus.yaml"
FIELDS = ("sku_id", "loc_id", "bucket_date", "qty")


@dataclass
class Score:
    decisions: int = 0
    correct: int = 0
    answerable: int = 0
    hits: int = 0
    refusable: int = 0
    correct_refusals: int = 0
    false_confidence: int = 0
    missed_answers: int = 0
    wrong_column: int = 0
    latencies: list = field(default_factory=list)
    failures: list = field(default_factory=list)

    @property
    def accuracy(self):
        return self.correct / self.decisions if self.decisions else None

    @property
    def hit_rate(self):
        return self.hits / self.answerable if self.answerable else None

    @property
    def refusal_correctness(self):
        return self.correct_refusals / self.refusable if self.refusable else None

    @property
    def false_confidence_rate(self):
        return self.false_confidence / self.refusable if self.refusable else None

    def latency(self, pct):
        if not self.latencies:
            return None
        ordered = sorted(self.latencies)
        index = min(len(ordered) - 1, int(len(ordered) * pct / 100))
        return ordered[index]


def load_corpus(split=None) -> list:
    data = yaml.safe_load(CORPUS.read_text(encoding="utf-8"))
    cases = data["cases"]
    if not cases:
        # An empty corpus would score 100% on everything.
        raise ValueError("the corpus is empty")
    if split:
        cases = [c for c in cases if c["split"] == split]
        if not cases:
            raise ValueError(f"no cases in split {split!r}")
    return cases


def score(mapper, cases, table: str = "history") -> Score:
    result = Score()
    for case in cases:
        started = time.perf_counter()
        predicted = mapper(case["headers"], table)
        result.latencies.append(time.perf_counter() - started)

        for field_name in FIELDS:
            expected = case["expect"].get(field_name)
            got = predicted.get(field_name)
            result.decisions += 1

            if expected is None:
                result.refusable += 1
                if got is None:
                    result.correct += 1
                    result.correct_refusals += 1
                else:
                    result.false_confidence += 1
                    result.failures.append(
                        (case["id"], field_name, "guessed", got, expected))
            else:
                result.answerable += 1
                if got == expected:
                    result.correct += 1
                    result.hits += 1
                elif got is None:
                    result.missed_answers += 1
                    result.failures.append(
                        (case["id"], field_name, "missed", got, expected))
                else:
                    result.wrong_column += 1
                    result.failures.append(
                        (case["id"], field_name, "wrong", got, expected))
    return result


def baseline_mapper(headers, table):
    """The existing normalised-equality matcher, unchanged."""
    from planbrain.mapping import suggest

    return suggest(headers, table)


def model_mapper(model_path, grammar_path=None, n_threads=None):
    """A llama.cpp mapper, grammar-constrained. **NEVER RUN.**

    `llama-cpp-python` has no wheel for Python 3.14 and there is no compiler in
    the environment this was written in, so this function has never executed.
    It is here so the bake-off is one command away on a machine that can run it,
    and it is labelled unrun rather than presented as working -- see
    docs/mapping-bakeoff.md.
    """
    from llama_cpp import Llama, LlamaGrammar

    grammar_file = Path(grammar_path or ROOT / "packaging" / "grammars"
                        / "column_mapping.gbnf")
    prompt_file = ROOT / "packaging" / "grammars" / "column_mapping_prompt.txt"
    grammar = LlamaGrammar.from_string(grammar_file.read_text(encoding="utf-8"))
    template = prompt_file.read_text(encoding="utf-8")

    llm = Llama(
        model_path=str(model_path),
        n_ctx=2048,
        n_threads=n_threads,
        verbose=False,
        # CPU only, decided in docs/packaging.md before the model work started.
        n_gpu_layers=0,
    )

    def run(headers, table):
        import json

        prompt = template.format(
            headers="\n".join(f"  - {h}" for h in headers)
        )
        # Non-thinking mode, explicitly: reasoning tokens fight the grammar,
        # which forbids anything but the JSON object.
        out = llm.create_completion(
            prompt=prompt + "\n/no_think\n",
            grammar=grammar,
            max_tokens=256,
            temperature=0.0,
        )
        try:
            parsed = json.loads(out["choices"][0]["text"])
        except (json.JSONDecodeError, KeyError, IndexError):
            # The grammar should make this impossible. If it happens, refuse
            # everything rather than invent -- a parse failure is not evidence
            # about any field.
            return {}
        # A grammar cannot enforce membership of a runtime list, so a value the
        # model did not copy verbatim from the offered headings is discarded.
        offered = set(headers)
        return {k: v for k, v in parsed.items()
                if v is not None and v in offered}

    return run


MAPPERS = {"baseline": baseline_mapper}


def report(name, result, show_failures=0) -> None:
    print(f"{name}")
    print(f"  accuracy             {_pct(result.accuracy)}"
          f"   ({result.correct}/{result.decisions})")
    print(f"  hit rate             {_pct(result.hit_rate)}"
          f"   ({result.hits}/{result.answerable} answerable)")
    print(f"  refusal correctness  {_pct(result.refusal_correctness)}"
          f"   ({result.correct_refusals}/{result.refusable} refusable)")
    print(f"  FALSE CONFIDENCE     {_pct(result.false_confidence_rate)}"
          f"   ({result.false_confidence}/{result.refusable}) <- the one that decides it")
    print(f"  missed a real answer {result.missed_answers}")
    print(f"  picked wrong column  {result.wrong_column}")
    p50, p95 = result.latency(50), result.latency(95)
    if p50 is not None:
        print(f"  latency p50 {p50 * 1000:.1f} ms   p95 {p95 * 1000:.1f} ms")

    if show_failures:
        print("  failures:")
        for case_id, field_name, kind, got, expected in result.failures[:show_failures]:
            print(f"    {case_id:26s} {field_name:12s} {kind:7s} "
                  f"got={got!r} want={expected!r}")


def _pct(value):
    return "  n/a" if value is None else f"{value * 100:5.1f}%"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapper", default="baseline", choices=sorted(MAPPERS))
    parser.add_argument("--split", default=None, choices=["dev", "holdout"])
    parser.add_argument("--failures", type=int, default=0,
                        help="show N failing decisions (dev split only)")
    args = parser.parse_args(argv)

    if args.failures and args.split == "holdout":
        # Inspecting holdout failures is how a held-out set stops being held out.
        print("refusing to list holdout failures; that is what dev is for",
              file=sys.stderr)
        return 2

    mapper = MAPPERS[args.mapper]
    for split in ([args.split] if args.split else ["dev", "holdout"]):
        cases = load_corpus(split)
        print(f"--- {args.mapper} on {split} ({len(cases)} cases) ---")
        report(args.mapper, score(mapper, cases),
               show_failures=args.failures if split == "dev" else 0)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
