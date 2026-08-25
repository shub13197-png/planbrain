"""Forecast backtest over the demo portfolio — the proof-of-value report.

    python -m tools.backtest_report --sample 40

Replays held-out history, scores the chosen model per series against a seasonal
naive baseline, and reports MASE by demand pattern.

The report is built to be unable to flatter itself:

* Sample size is printed against portfolio size. A MASE over an unnamed subset
  is unfalsifiable.
* Series that could not be scored are counted, never dropped.
* Fallbacks to the naive baseline are counted, because a run where a third of
  the portfolio quietly fell back is a different result and the MASE alone will
  not say so.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from planbrain import forecast  # noqa: E402
from planbrain.demo import build_demo, populate  # noqa: E402

SCHEMA = Path(__file__).resolve().parents[1] / "planbrain" / "facts" / "schema.sql"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--sample", type=int, default=40,
                        help="series to evaluate; 0 means the whole portfolio")
    parser.add_argument("--horizon", type=int, default=28)
    parser.add_argument("--windows", type=int, default=3)
    args = parser.parse_args(argv)

    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA.read_text(encoding="utf-8"))

    demo = build_demo(seed=args.seed)
    populate(con, demo)

    all_keys = forecast.demand_keys(demo)
    if not all_keys:
        print("no demand series in the dataset; nothing to score", file=sys.stderr)
        return 1

    # Evenly spaced rather than the first N, so a sample is not all one grade.
    if args.sample and args.sample < len(all_keys):
        step = len(all_keys) / args.sample
        keys = [all_keys[int(i * step)] for i in range(args.sample)]
    else:
        keys = all_keys

    report = forecast.backtest(
        con, demo, keys=keys, horizon=args.horizon, n_windows=args.windows,
    )
    _print(report)
    return 0


def _print(report) -> None:
    # ASCII only: this prints to a Windows console under cp1252, where an
    # em-dash arrives as a replacement character.
    print(f"Backtest - {report['evaluated']} of {report['portfolio']} series, "
          f"{report['n_windows']} folds of {report['horizon']} days, "
          f"seasonal period {report['seasonal_period']}")
    print()
    print(f"{'':22s} {'scored':>7s} {'unscored':>9s} {'mean':>7s} {'median':>7s} {'>1.0':>6s}")
    for label, block in (("chosen model", report["model"]), ("seasonal naive", report["baseline"])):
        print(f"{label:22s} {block['n_scored']:7d} {block['n_unscored']:9d} "
              f"{_fmt(block['mean_mase']):>7s} {_fmt(block['median_mase']):>7s} "
              f"{block['worse_than_naive']:6d}")

    print()
    print("by demand pattern (chosen model):")
    print(f"  {'':16s} {'scored':>7s} {'unscored':>9s} {'mean':>7s} {'median':>7s}")
    for pattern, block in report["by_pattern"].items():
        print(f"  {pattern:16s} {block['n_scored']:7d} {block['n_unscored']:9d} "
              f"{_fmt(block['mean_mase']):>7s} {_fmt(block['median_mase']):>7s}")

    fallbacks = report["fallbacks"]
    print()
    print(f"fallbacks to naive: {sum(fallbacks.values())} "
          f"(non-finite {fallbacks['non_finite']}, model error {fallbacks['model_error']})")
    print()
    print("MASE below 1.0 beats the seasonal naive baseline. Unscored series are")
    print("perfectly periodic in training or have too little history; they are")
    print("counted here rather than excluded, because dropping the hard ones is")
    print("what makes a portfolio average look good.")
    print()
    print("READ THE INTERMITTENT AND LUMPY ROWS WITH CARE. Croston and TSB emit a")
    print("flat rate -- say 1.75 a day -- against an actual that is mostly zero")
    print("with occasional spikes. Point-error metrics punish that heavily, while")
    print("a naive forecast of zero scores well by being right on the quiet days")
    print("and wrong only where it matters. A MASE above 1.0 here does NOT mean")
    print("the method is worse for planning; it means MASE is measuring the wrong")
    print("thing. Judge intermittent SKUs on fill rate and inventory held, which")
    print("is what the multi-echelon simulation backtest is for.")


def _fmt(value) -> str:
    return "-" if value is None else f"{value:.3f}"


if __name__ == "__main__":
    raise SystemExit(main())
