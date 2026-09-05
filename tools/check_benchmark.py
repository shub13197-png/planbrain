"""Hold `docs/benchmark.md` to what a fresh benchmark run actually produces.

    python -m tools.benchmark --data data/online-retail --json build/bench.json
    python -m tools.check_benchmark --json build/bench.json

**Why this is separate from `tools/published.py`.** That register is checked in
CI on every push, because everything it covers is computed from the demo
generator, which ships. These figures are not: they need a 45 MB download CI
does not have, so `docs/benchmark.md` says plainly that they are not gated.

Ungated is not the same as unchecked. This is the same two links the published
register asserts, run by hand:

1. **register ↔ code** — each value here matches a fresh run.
2. **register ↔ docs** — each literal appears verbatim in the prose.

Break either and this exits non-zero. Changing a published benchmark number then
costs a deliberate edit here, an edit to the document, and a reason — which is
the whole point of a pin, and the reason a figure nobody re-derives is worth
less than one somebody can.

**The dataset is pinned by checksum in the fetch script**, so "a fresh run"
means the same input. If that checksum ever fails, every figure below is
describing a different dataset and this check is meaningless until that is
resolved.
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.benchmark import headline, stock_to_match  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "benchmark.md"

#: Absolute tolerance on a stock figure, in units. The pipeline is
#: deterministic given the same input, so this exists for float formatting
#: rather than for run-to-run variance -- the doc quotes one decimal place.
STOCK_TOLERANCE = 0.05

#: Absolute tolerance on a fill rate, as a fraction. Same reasoning.
FILL_TOLERANCE = 0.0005


@dataclass(frozen=True)
class Claim:
    """One published cell of one published table.

    ``pattern`` is None for the portfolio table, or a demand-pattern name for
    the per-pattern tables. ``fill`` identifies which of our five settings the
    row is, by the fill rate we achieved at it.
    """

    pattern: object
    fill: float
    our_stock: float
    policy: str
    #: Stock the incumbent needs to match us, or None when it is off the curve.
    stock: object
    #: Extra stock as a fraction, or None with `status` set instead.
    ratio: object
    status: str
    literal: str


P = None  # the portfolio table

CLAIMS = (
    # --- portfolio -------------------------------------------------------
    Claim(P, 0.676, 46.3, "moving_average", 60.0, 0.30, "ok", "60.0 (**+30%**)"),
    Claim(P, 0.676, 46.3, "reorder_point_stale", 99.8, 1.16, "ok", "99.8 (+116%)"),
    Claim(P, 0.763, 73.0, "moving_average", 83.7, 0.15, "ok", "83.7 (**+15%**)"),
    Claim(P, 0.763, 73.0, "reorder_point", 85.6, 0.17, "ok", "85.6 (**+17%**)"),
    Claim(P, 0.797, 90.6, "moving_average", 98.9, 0.09, "ok", "98.9 (**+9%**)"),
    Claim(P, 0.797, 90.6, "reorder_point", 104.9, 0.16, "ok", "104.9 (**+16%**)"),
    Claim(P, 0.841, 125.0, "moving_average", 135.0, 0.08, "ok", "135.0 (**+8%**)"),
    Claim(P, 0.841, 125.0, "reorder_point", 139.5, 0.12, "ok", "139.5 (**+12%**)"),
    # The rows that go against us, pinned exactly like the ones that do not.
    Claim(P, 0.499, 25.9, "moving_average", None, None, "already above it",
          "| 49.9% | 25.9 | *already above it*"),
    Claim(P, 0.676, 46.3, "reorder_point", None, None, "already above it",
          "| 67.6% | 46.3 | 60.0 (**+30%**) | *already above it*"),
    Claim(P, 0.763, 73.0, "reorder_point_stale", None, None, "never reaches it",
          "85.6 (**+17%**) | never reaches it |"),

    # --- lumpy, 2,274 series ---------------------------------------------
    Claim("lumpy", 0.686, 47.7, "moving_average", 60.5, 0.27, "ok", "60.5 (+27%)"),
    Claim("lumpy", 0.686, 47.7, "reorder_point_stale", 106.3, 1.23, "ok", "106.3 (+123%)"),
    Claim("lumpy", 0.772, 75.5, "moving_average", 84.1, 0.11, "ok", "84.1 (+11%)"),
    Claim("lumpy", 0.772, 75.5, "reorder_point", 86.6, 0.15, "ok", "86.6 (+15%)"),
    Claim("lumpy", 0.848, 129.4, "moving_average", 137.9, 0.07, "ok", "137.9 (+7%)"),
    Claim("lumpy", 0.848, 129.4, "reorder_point", 142.3, 0.10, "ok", "142.3 (+10%)"),

    # --- intermittent, 162 series ----------------------------------------
    Claim("intermittent", 0.510, 3.6, "moving_average", 4.1, 0.16, "ok", "4.1 (+16%)"),
    Claim("intermittent", 0.620, 5.0, "reorder_point", 5.5, 0.10, "ok", "5.5 (+10%)"),
    Claim("intermittent", 0.727, 7.8, "moving_average", 8.1, 0.03, "ok", "8.1 (+3%)"),
    Claim("intermittent", 0.727, 7.8, "reorder_point", 8.4, 0.07, "ok", "8.4 (+7%)"),
)

#: Shape of the run itself. A figure is meaningless if the denominator moved.
SHAPE = {
    "evaluated": 2947,
    "stock_codes_in_file": 4873,
    "history": "2009-12-01 to 2011-12-09",
}

#: Series counts per pattern, quoted in the document's headings.
PATTERN_SERIES = {"lumpy": 2274, "intermittent": 162}


def curves(rows, pattern):
    """(fill, stock) points per policy, for the portfolio or one pattern."""
    out = {}
    for row in rows:
        if pattern is None:
            out.setdefault(row["policy"], []).append(
                (row["fill_rate"], row["average_on_hand"])
            )
        else:
            stats = row["by_pattern"].get(pattern)
            if stats and stats[0] is not None:
                out.setdefault(row["policy"], []).append((stats[0], stats[1]))
    return out


def our_points(rows, pattern, ours="forecast"):
    """Our own (fill, stock) points, which every claim is anchored to."""
    return sorted(curves(rows, pattern).get(ours, []))


def check(rows) -> list:
    """Every claim against a fresh run. Returns a message per failure."""
    problems = []

    for pattern, count in PATTERN_SERIES.items():
        seen = next((r["by_pattern"][pattern][2] for r in rows
                     if pattern in r["by_pattern"]), None)
        if seen != count:
            problems.append(
                f"{pattern}: docs say {count} series, a fresh run scores {seen}"
            )

    for claim in CLAIMS:
        by_policy = curves(rows, claim.pattern)
        where = claim.pattern or "portfolio"

        ours = [p for p in our_points(rows, claim.pattern)
                if abs(p[0] - claim.fill) <= FILL_TOLERANCE]
        if len(ours) != 1:
            problems.append(
                f"{where}: no single setting of ours fills {claim.fill:.1%} any "
                f"more; found {len(ours)}"
            )
            continue
        fill, stock = ours[0]
        if abs(stock - claim.our_stock) > STOCK_TOLERANCE:
            problems.append(
                f"{where} @ {claim.fill:.1%}: docs say we hold {claim.our_stock}, "
                f"a fresh run holds {stock:.1f}"
            )

        needed, status = stock_to_match(by_policy.get(claim.policy, []), fill)
        if status != claim.status:
            problems.append(
                f"{where} @ {claim.fill:.1%} {claim.policy}: docs say "
                f"{claim.status!r}, a fresh run says {status!r}"
            )
            continue
        if claim.stock is None:
            continue
        if abs(needed - claim.stock) > STOCK_TOLERANCE:
            problems.append(
                f"{where} @ {claim.fill:.1%} {claim.policy}: docs say "
                f"{claim.stock}, a fresh run says {needed:.1f}"
            )
        ratio = needed / stock - 1.0
        if abs(ratio - claim.ratio) > 0.005:
            problems.append(
                f"{where} @ {claim.fill:.1%} {claim.policy}: docs say "
                f"{claim.ratio:+.0%}, a fresh run says {ratio:+.0%}"
            )
    return problems


def check_prose() -> list:
    """Every literal must appear verbatim in the document."""
    text = DOC.read_text(encoding="utf-8")
    return [
        f"{claim.literal!r} is not in {DOC.name}"
        for claim in CLAIMS if claim.literal not in text
    ]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", required=True,
                        help="the --json dump from a tools.benchmark run")
    args = parser.parse_args(argv)

    payload = json.loads(Path(args.json).read_text(encoding="utf-8"))
    rows, meta = payload["rows"], payload["meta"]

    problems = []
    for key, expected in SHAPE.items():
        if str(meta.get(key)) != str(expected):
            problems.append(
                f"run shape: docs say {key}={expected}, this run has "
                f"{meta.get(key)}"
            )
    problems += check(rows)
    problems += check_prose()

    if problems:
        print(f"{len(problems)} disagreement(s) between docs/benchmark.md and "
              f"this run:\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(f"{len(CLAIMS)} published claims reproduce, and every literal appears "
          f"in {DOC.name}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
