"""The corpus and its scorer. The corpus is a fixture, so it needs its own tests.

A corpus with a duplicate id, an expectation naming a header that is not offered,
or a split that drifts is a measurement instrument that quietly reports the wrong
thing -- which is the failure this project keeps finding, wearing a new hat.
"""

from pathlib import Path

import pytest
import yaml

from tools.score_mapping import FIELDS, baseline_mapper, load_corpus, score

CORPUS = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "header_corpus.yaml"


@pytest.fixture(scope="module")
def cases():
    return yaml.safe_load(CORPUS.read_text(encoding="utf-8"))["cases"]


def test_the_corpus_is_the_size_the_plan_committed_to(cases):
    """30-50 sets, per docs/mapping-bakeoff.md."""
    assert 30 <= len(cases) <= 50


def test_every_id_is_unique(cases):
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))


def test_every_case_decides_all_four_fields(cases):
    """A partially specified case scores as though the missing fields were
    refusals, which would silently inflate refusal correctness."""
    for case in cases:
        assert set(case["expect"]) == set(FIELDS), case["id"]


def test_every_expectation_names_a_header_that_is_offered(cases):
    """An expectation naming an absent column is unreachable, so it scores as a
    permanent miss and quietly caps the achievable accuracy."""
    for case in cases:
        for value in case["expect"].values():
            if value is not None:
                assert value in case["headers"], case["id"]


def test_refusals_are_a_meaningful_share(cases):
    """Refusal is scored as a first-class answer. Too few and the metric is
    noise; a corpus of only answerable cases cannot measure restraint."""
    decisions = [v for c in cases for v in c["expect"].values()]
    refusals = [v for v in decisions if v is None]
    assert 0.10 <= len(refusals) / len(decisions) <= 0.35


def test_both_splits_are_substantial(cases):
    dev = [c for c in cases if c["split"] == "dev"]
    holdout = [c for c in cases if c["split"] == "holdout"]
    assert len(dev) >= 15 and len(holdout) >= 15


def test_the_split_is_fixed_in_the_file_not_derived(cases):
    """A split recomputed from a hash would move as the corpus grows and the
    held-out half would quietly stop being held out."""
    for case in cases:
        assert case["split"] in ("dev", "holdout")
    text = CORPUS.read_text(encoding="utf-8")
    assert text.count("split:") == len(cases)


def test_the_corpus_covers_the_hard_categories(cases):
    """Committed in the plan: transliteration, other scripts, vowel-less
    abbreviations, and genuinely ambiguous headers."""
    ids = {c["id"] for c in cases}
    assert any("hinglish" in i for i in ids)
    assert any("devanagari" in i for i in ids)
    assert any("abbrev" in i for i in ids)
    assert any(i in ids for i in ("opaque-generic", "positional"))
    joined = " ".join(h for c in cases for h in c["headers"])
    assert any(ord(ch) > 0x0900 for ch in joined), "no non-Latin script present"


# --------------------------------------------------------------------------
# the scorer
# --------------------------------------------------------------------------

def test_an_empty_corpus_is_refused_not_scored():
    """It would score 100% on everything."""
    with pytest.raises(ValueError):
        load_corpus("nonexistent-split")


def test_a_perfect_mapper_scores_one(cases):
    perfect = lambda headers, table: {  # noqa: E731
        f: v for f, v in _lookup(cases, headers).items() if v is not None
    }
    result = score(perfect, cases)
    assert result.accuracy == 1.0
    assert result.false_confidence == 0


def test_a_mapper_that_always_refuses_scores_only_the_refusals(cases):
    """Establishes the floor: refusing everything is not a free pass."""
    result = score(lambda headers, table: {}, cases)
    assert result.hits == 0
    assert result.false_confidence == 0
    assert result.correct == result.refusable


def test_a_mapper_that_always_guesses_is_punished(cases):
    """The metric that decides the bake-off has to move when a mapper guesses."""
    greedy = lambda headers, table: {f: headers[0] for f in FIELDS}  # noqa: E731
    result = score(greedy, cases)
    assert result.false_confidence == result.refusable
    assert result.false_confidence_rate == 1.0


def test_the_baseline_scores_are_reproducible(cases):
    """The published baseline must not drift silently -- these are the numbers
    the model's threshold is set against, in docs/mapping-bakeoff.md.

    Moved once, deliberately, when the alias table gained transliterated and
    abbreviated forms: 69.0% to 75.0% on holdout. The edit is the point. A pin
    that can be nudged without anyone noticing protects nothing, and this one
    failed the moment the mapper improved, which is exactly when a published
    figure is most likely to go stale unremarked.
    """
    holdout = [c for c in cases if c["split"] == "holdout"]
    result = score(baseline_mapper, holdout)
    assert result.accuracy == pytest.approx(0.750, abs=0.005)
    assert result.false_confidence_rate == pytest.approx(0.091, abs=0.005)
    # The property that matters more than the accuracy, and the reason the
    # alias table could be widened at all: it still never picks a wrong column.
    assert result.wrong_column == 0


def test_the_dev_split_gain_is_not_mistaken_for_the_real_one(cases):
    """The aliases were written by reading dev misses, so dev is the number that
    flatters. Both are asserted, together, so nobody can quote the first without
    meeting the second: +17.7 points on dev and +6.0 on holdout."""
    dev = [c for c in cases if c["split"] == "dev"]
    holdout = [c for c in cases if c["split"] == "holdout"]
    assert score(baseline_mapper, dev).accuracy == pytest.approx(0.917, abs=0.005)
    assert score(baseline_mapper, holdout).accuracy == pytest.approx(0.750, abs=0.005)


def _lookup(cases, headers):
    for case in cases:
        if case["headers"] == headers:
            return case["expect"]
    raise AssertionError("unknown header set")


def test_the_docs_quote_the_scores_the_code_produces(cases):
    """Pins protect claims. The corpus scores appear in two documents and in the
    bake-off's derived bar; a mapper that improves silently leaves all three
    wrong, and no behaviour test would notice because the behaviour is correct.
    """
    root = Path(__file__).resolve().parents[1]
    dev = score(baseline_mapper, [c for c in cases if c["split"] == "dev"])
    holdout = score(baseline_mapper, [c for c in cases if c["split"] == "holdout"])

    bakeoff = (root / "docs" / "mapping-bakeoff.md").read_text(encoding="utf-8")
    mapping = (root / "docs" / "mapping.md").read_text(encoding="utf-8")

    assert f"{holdout.accuracy:.1%}" in mapping, (
        f"docs/mapping.md does not quote the holdout accuracy of "
        f"{holdout.accuracy:.1%}"
    )
    for figure in (f"{dev.accuracy:.1%}", f"{holdout.accuracy:.1%}"):
        assert figure in bakeoff, f"docs/mapping-bakeoff.md is missing {figure}"

    # Threshold 1 is written as baseline + 10 points, so the derived bar has to
    # follow the baseline. Improving the incumbent must raise the bar, and this
    # is what stops it quietly not doing so.
    bar = holdout.accuracy + 0.10
    assert f"{bar:.1%} accuracy on the holdout" in bakeoff, (
        f"the bake-off bar should be {bar:.1%}, ten points above the current "
        f"baseline of {holdout.accuracy:.1%}"
    )

