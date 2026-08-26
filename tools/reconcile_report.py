"""Why netreq and the service simulation report different stock.

    python -m tools.reconcile_report --sample 40

Walks the five-rung ladder from docs/reconciliation.md and reports every named
term, per demand class. The point is not that the two engines agree -- they
should not -- but that nothing in the difference is unexplained.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from planbrain import reconcile  # noqa: E402
from planbrain.demo import build_demo, populate  # noqa: E402
from planbrain.forecast import demand_keys  # noqa: E402

SCHEMA = Path(__file__).resolve().parents[1] / "planbrain" / "facts" / "schema.sql"

RUNG_LABELS = {
    "pure_netting": "0  pure netting",
    "with_safety_stock": "1  + safety stock",
    "with_lot_sizing": "2  + lot sizing  (netreq's plan)",
    "against_actuals": "3  vs realised demand",
    "with_truncation": "4  + lost sales  (comparable)",
}

TERM_LABELS = {
    "safety_stock": "safety stock",
    "lot_granularity": "lot-sizing granularity",
    "forecast_error": "forecast error",
    "stockout_truncation": "stockout truncation",
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--sample", type=int, default=40)
    parser.add_argument("--holdout", type=int, default=90)
    args = parser.parse_args(argv)

    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA.read_text(encoding="utf-8"))

    demo = build_demo(seed=args.seed)
    populate(con, demo)

    all_keys = demand_keys(demo)
    if not all_keys:
        print("no demand series; nothing to reconcile", file=sys.stderr)
        return 1
    if args.sample and args.sample < len(all_keys):
        step = len(all_keys) / args.sample
        keys = [all_keys[int(i * step)] for i in range(args.sample)]
    else:
        keys = all_keys

    report = reconcile.reconcile(con, demo, keys=keys, holdout_days=args.holdout)
    _print(report)
    return 0 if report.within_tolerance else 1


def _print(report) -> None:
    print(f"Reconciliation - {report.evaluated} of {report.portfolio} series, "
          f"{report.holdout_days}-day holdout")
    print("average units on hand per series")
    print()

    rungs = {
        name: sum(l.rungs[name] for l in report.ladders) / report.evaluated
        for name in reconcile.RUNGS
    }
    previous = None
    for name in reconcile.RUNGS:
        delta = "" if previous is None else f"{rungs[name] - previous:+10,.0f}"
        print(f"  {RUNG_LABELS[name]:36s} {rungs[name]:10,.0f}  {delta}")
        previous = rungs[name]

    print()
    print("the difference, decomposed:")
    for term, value in report.terms.items():
        print(f"  {TERM_LABELS[term]:26s} {value:+12,.0f}")
    print(f"  {'residual':26s} {report.residual:+12,.1f}"
          f"   ({report.residual_share:.4%} of plan)")

    print()
    print(f"{'demand class':14s} {'n':>3s} {'plan':>9s} {'replayed':>9s} "
          f"{'safety':>9s} {'lots':>9s} {'fcst err':>9s} {'stockout':>9s}")
    for pattern, block in report.by_pattern.items():
        print(f"{pattern:14s} {block['n']:3d} "
              f"{block['plan_on_hand'].value:9,.0f} "
              f"{block['replayed_on_hand'].value:9,.0f} "
              f"{block['safety_stock']:+9,.0f} "
              f"{block['lot_granularity']:+9,.0f} "
              f"{block['forecast_error']:+9,.0f} "
              f"{block['stockout_truncation']:+9,.0f}")

    print()
    print("Rungs 0-2 explain netreq's OWN stock: what is deliberate safety stock")
    print("and what is lot-sizing round-up. Rungs 2-4 explain the gap to a replay")
    print("against realised demand.")
    print()
    print("The two engines are NOT expected to agree. netreq computes")
    print("deterministic net requirements against a forecast; the simulation")
    print("replays realised demand with lost sales. The test is that every part")
    print("of the difference has a name and the names add up.")
    if not report.within_tolerance:
        print()
        print("RESIDUAL EXCEEDS TOLERANCE. Something in the difference is")
        print("unexplained, which is a bug rather than a rounding artefact.")


if __name__ == "__main__":
    raise SystemExit(main())
