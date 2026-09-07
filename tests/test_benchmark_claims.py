"""`docs/benchmark.md` against the run it was written from, in CI.

**Half of this pin does not need the dataset, so half of it is gated.**

`docs/benchmark.md` originally said its figures were not CI-checked at all,
because they come from a 45 MB download CI does not have. That was true of
*recomputing* them and false of everything else. The run itself is 17 KB of
JSON, so it is committed as `docs/benchmark-run.json` and three links exist
where there was one:

| link | checked by | needs the dataset |
|---|---|---|
| document ↔ recorded run | this file, in CI | no |
| recorded run ↔ a fresh run | `tools/check_benchmark` | **yes** |
| dataset ↔ its checksum | the fetch script's `--verify-only` | **yes** |

So a number cannot drift in the prose, and cannot drift in the register, without
CI failing. What CI cannot tell you is whether the engines still *produce* that
run — that needs the download, and `docs/benchmark.md` says so in those terms
rather than implying the figures are unchecked.
"""

import copy
import json
from pathlib import Path

import pytest

from tools.check_benchmark import (
    CLAIMS,
    PATTERN_SERIES,
    SHAPE,
    check,
    check_prose,
    stock_to_match,
)

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "docs" / "benchmark-run.json"


@pytest.fixture(scope="module")
def recorded():
    payload = json.loads(RECORD.read_text(encoding="utf-8"))
    return payload["rows"], payload["meta"]


# --------------------------------------------------------------------------
# document <-> recorded run
# --------------------------------------------------------------------------

def test_every_published_benchmark_figure_matches_the_recorded_run(recorded):
    rows, _meta = recorded
    assert check(rows) == []


def test_every_published_benchmark_figure_appears_in_the_prose():
    """The other half: a document that drifts from the register fails."""
    assert check_prose() == []


def test_the_recorded_run_is_the_shape_the_document_describes(recorded):
    _rows, meta = recorded
    for key, expected in SHAPE.items():
        assert str(meta[key]) == str(expected), key


#: The document prints stock to one decimal and the ratio to whole percent, so
#: a published pair only constrains the ratio to an interval. Both bounds are
#: needed and neither is slack: at 3.6 units, half a display digit is 1.4% of
#: the value, so "4.1 against 3.6" is consistent with anything from +11% to
#: +17%. Comparing the rounded pair directly says +14% and fails a correct
#: document, which is what the first version of this test did.
DISPLAY_HALF_UNIT = 0.05
DISPLAY_HALF_PERCENT = 0.005


def test_the_register_arithmetic_is_internally_consistent():
    """Catches a transcription error in the register itself.

    Every "+30%" is `stock / our_stock - 1`, so the two numbers in a cell
    constrain each other. Without this a claim could be copied from the report
    with one figure right and the other wrong, and both links above would still
    pass, because both would be checking the same wrong pair against each other.

    Checked as an interval rather than an equality, because the published
    figures are rounded for display -- see `DISPLAY_HALF_UNIT`. The interval is
    still narrow enough to catch a digit transposed or a row copied from the
    wrong line, which is the failure this is for.
    """
    for claim in CLAIMS:
        if claim.stock is None:
            continue
        low = (claim.stock - DISPLAY_HALF_UNIT) / (claim.our_stock + DISPLAY_HALF_UNIT) - 1.0
        high = (claim.stock + DISPLAY_HALF_UNIT) / (claim.our_stock - DISPLAY_HALF_UNIT) - 1.0
        assert low - DISPLAY_HALF_PERCENT <= claim.ratio <= high + DISPLAY_HALF_PERCENT, (
            f"{claim.pattern or 'portfolio'} @ {claim.fill:.1%} {claim.policy}: "
            f"{claim.stock} against {claim.our_stock} allows "
            f"{low:+.1%} to {high:+.1%}, not the published {claim.ratio:+.0%}"
        )


def test_that_consistency_check_can_still_fail():
    """The interval must not be so wide that it accepts anything.

    A tolerance derived from display rounding is one bad edit away from being
    slack for a wrong number, so this transposes a digit and requires it to be
    rejected.
    """
    import tools.check_benchmark as cb

    claim = next(c for c in CLAIMS if c.stock is not None)
    wrong = cb.Claim(claim.pattern, claim.fill, claim.our_stock, claim.policy,
                     claim.stock, claim.ratio + 0.10, claim.status, claim.literal)
    low = (wrong.stock - DISPLAY_HALF_UNIT) / (wrong.our_stock + DISPLAY_HALF_UNIT) - 1.0
    high = (wrong.stock + DISPLAY_HALF_UNIT) / (wrong.our_stock - DISPLAY_HALF_UNIT) - 1.0
    assert not (low - DISPLAY_HALF_PERCENT <= wrong.ratio <= high + DISPLAY_HALF_PERCENT)


def test_the_rows_that_go_against_us_are_pinned_too():
    """A register holding only the wins is a brochure.

    Both losing shapes have to be represented: a setting where an incumbent is
    already ahead of us, and one where it never catches up. If either drops out
    of the register, the remaining table reads as a clean sweep.
    """
    statuses = {claim.status for claim in CLAIMS}
    assert "already above it" in statuses
    assert "never reaches it" in statuses


# --------------------------------------------------------------------------
# the check has to be able to fail
# --------------------------------------------------------------------------

def _perturbed(rows, policy, *, stock=1.0, fill=0.0):
    out = copy.deepcopy(rows)
    for row in out:
        if row["policy"] == policy:
            row["average_on_hand"] *= stock
            row["fill_rate"] = min(0.999, row["fill_rate"] + fill)
            for pattern, stats in list(row["by_pattern"].items()):
                f, s, n = stats
                row["by_pattern"][pattern] = (
                    None if f is None else min(0.999, f + fill),
                    None if s is None else s * stock,
                    n,
                )
    return out


@pytest.mark.parametrize("policy, kwargs, what", [
    ("moving_average", {"stock": 1.03}, "the spreadsheet holding 3% more"),
    ("forecast", {"stock": 1.01}, "our own inventory moving 1%"),
    ("reorder_point", {"fill": 0.10}, "the ERP rule gaining 10 points"),
])
def test_the_check_rejects_a_run_that_no_longer_matches(recorded, policy, kwargs, what):
    """Attack it. A consistency check nothing can break is documentation.

    Each of these is a change that would make the published claims wrong while
    leaving the file structurally valid — which is exactly the shape of drift a
    reader cannot see and a passing test suite would otherwise bless.
    """
    rows, _meta = recorded
    problems = check(_perturbed(rows, policy, **kwargs))
    assert problems, f"{what} went unnoticed"


def test_a_changed_denominator_is_caught(recorded):
    """A fill rate is meaningless if the series count moved under it."""
    rows, _meta = recorded
    mutated = copy.deepcopy(rows)
    for row in mutated:
        if "lumpy" in row["by_pattern"]:
            f, s, n = row["by_pattern"]["lumpy"]
            row["by_pattern"]["lumpy"] = (f, s, n - 1)
    assert check(mutated), "a changed series count went unnoticed"


def test_a_literal_absent_from_the_document_is_caught(monkeypatch):
    """The prose link, attacked the same way."""
    import tools.check_benchmark as cb

    broken = CLAIMS + (
        cb.Claim(None, 0.676, 46.3, "moving_average", 60.0, 0.30, "ok",
                 "60.0 (**+31%**)"),
    )
    monkeypatch.setattr(cb, "CLAIMS", broken)
    assert cb.check_prose(), "a literal missing from the document went unnoticed"


# --------------------------------------------------------------------------
# the metric itself
# --------------------------------------------------------------------------

def test_stock_to_match_interpolates_within_the_curve():
    curve = [(0.50, 10.0), (0.70, 20.0)]
    stock, status = stock_to_match(curve, 0.60)
    assert status == "ok"
    assert stock == pytest.approx(15.0)


def test_stock_to_match_refuses_to_extrapolate_upward():
    """A policy that never reaches the fill rate has no answer, not a guess."""
    stock, status = stock_to_match([(0.50, 10.0), (0.70, 20.0)], 0.90)
    assert stock is None and status == "never reaches it"


def test_stock_to_match_says_when_the_incumbent_is_already_ahead():
    """The two ways of being off the curve are opposite and must not merge.

    Reporting "already above it" as "never reaches it" would print an
    incumbent's win as its failure, which the first draft of the benchmark did.
    """
    stock, status = stock_to_match([(0.50, 10.0), (0.70, 20.0)], 0.30)
    assert stock is None and status == "already above it"


def test_every_pattern_the_document_names_carries_its_series_count():
    """The heading "lumpy demand — 2,274 scored series" is a claim as much as
    any number in the table below it."""
    text = (ROOT / "docs" / "benchmark.md").read_text(encoding="utf-8")
    for pattern, count in PATTERN_SERIES.items():
        assert f"{count:,} scored series" in text, pattern


# --------------------------------------------------------------------------
# the trading calendar is read off the data, not assumed
# --------------------------------------------------------------------------

def _series(counts):
    """A demand book with `counts[weekday]` events on that weekday."""
    from datetime import date, timedelta
    start = date(2024, 1, 1)          # a Monday, so weekday 0 is Monday
    days = {}
    for weekday, n in counts.items():
        for i in range(n):
            days[start + timedelta(days=weekday + 7 * i)] = 1.0
    return {"SKU": days}


def test_a_weekday_below_the_cutoff_is_not_a_trading_day():
    """The boundary, not a count of days.

    The calendar was hardcoded to the UK retailer's week -- every day except
    Saturday -- and handed to a manufacturer that works Monday to Friday it
    called Sunday a trading day on 1.15% of the rows. The seasonal period is
    derived from this, so a wrong period puts every weekly pattern out of phase.
    """
    from tools.benchmark import TRADING_DAY_SHARE, trading_days

    busy = 100
    just_under = int(busy * TRADING_DAY_SHARE) - 1
    just_over = int(busy * TRADING_DAY_SHARE) + 1

    counts = {0: busy, 1: busy, 2: busy, 3: busy, 4: busy}
    assert 6 not in trading_days(_series({**counts, 6: just_under}))
    assert 6 in trading_days(_series({**counts, 6: just_over}))


def test_a_five_day_week_and_a_six_day_week_are_told_apart():
    """The two real datasets, in miniature. Both must survive the same rule."""
    from tools.benchmark import trading_days

    weekdays = {0: 100, 1: 100, 2: 100, 3: 100, 4: 100}
    # The manufacturer: a trickle of Sunday bookings that are not a working day.
    assert trading_days(_series({**weekdays, 5: 1, 6: 6})) == frozenset({0, 1, 2, 3, 4})
    # The retailer: Sunday is a real trading day, Saturday is not.
    assert trading_days(_series({**weekdays, 5: 1, 6: 79})) == frozenset({0, 1, 2, 3, 4, 6})


def test_a_calendar_cannot_be_derived_from_nothing():
    """An empty book must say so rather than return an empty week, which would
    read downstream as a business that never trades."""
    import pytest as _pytest

    from tools.benchmark import trading_days

    with _pytest.raises(ValueError, match="no demand rows"):
        trading_days({})
