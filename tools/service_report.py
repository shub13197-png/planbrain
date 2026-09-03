"""Service level against inventory held — the proof-of-value report.

    python -m tools.service_report --sample 60

Replays a held-out window of history under three policies and reports the fill
rate each achieved and the average stock it had to carry to achieve it.

This is a deliverable, not a test artifact. It is the table that answers the
question a finance manager asks -- what service did I get, and what did it cost
me in stock -- and it is the only honest evidence about the intermittent half of
the portfolio, which MASE cannot judge.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from planbrain import simulate  # noqa: E402
from planbrain.demo import build_demo, populate  # noqa: E402
from planbrain.forecast import demand_keys  # noqa: E402

SCHEMA = Path(__file__).resolve().parents[1] / "planbrain" / "facts" / "schema.sql"

LABELS = {
    "forecast": "fitted forecast",
    "naive_zero": "naive zero forecast",
    "reorder_point": "reorder point, tuned",
    "reorder_point_stale": "reorder point, stale",
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--sample", type=int, default=60,
                        help="series to replay; 0 means the whole portfolio")
    parser.add_argument("--holdout", type=int, default=90)
    parser.add_argument("--service-level", type=float, default=None, metavar="P",
                        help="cycle service level as a probability, e.g. 0.95. "
                             "Replaces --safety-days. NOT a fill-rate target: "
                             "see docs/service-backtest.md for what it delivers")
    parser.add_argument("--safety-days", type=float, default=7.0)
    parser.add_argument("--sweep", action="store_true",
                        help="trace the service-vs-inventory frontier across safety levels")
    args = parser.parse_args(argv)

    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA.read_text(encoding="utf-8"))

    demo = build_demo(seed=args.seed)
    populate(con, demo)

    all_keys = demand_keys(demo)
    if not all_keys:
        print("no demand series in the dataset; nothing to replay", file=sys.stderr)
        return 1
    if args.sample and args.sample < len(all_keys):
        step = len(all_keys) / args.sample
        keys = [all_keys[int(i * step)] for i in range(args.sample)]
    else:
        keys = all_keys

    if args.sweep:
        _print_frontier(con, demo, keys, args)
        return 0

    report = simulate.compare(
        con, demo, keys=keys, holdout_days=args.holdout,
        **({"safety_service_level": args.service_level} if args.service_level
           else {"safety_days": args.safety_days}),
    )
    _print(report, args)
    return 0


SWEEP_SAFETY_DAYS = (0.0, 3.0, 7.0, 14.0, 21.0)


def _print_frontier(con, demo, keys, args) -> None:
    """Service against inventory across safety levels, which is the honest comparison.

    A single (fill rate, stock) pair per policy is close to meaningless: any
    policy can buy service with stock. What matters is which curve sits higher
    -- more service for the same inventory. Comparing points, as the default
    table does, partly compares safety-stock settings rather than policies.
    """
    print(f"Service-vs-inventory frontier - {len(keys)} series, "
          f"{args.holdout}-day holdout")
    print("Each cell is fill rate / average on-hand at a given safety level.")
    print()
    header = " ".join(f"{d:g}d".rjust(18) for d in SWEEP_SAFETY_DAYS)
    print(f"{'policy':22s} {header}")

    rows = {name: [] for name in simulate.POLICIES}
    for safety in SWEEP_SAFETY_DAYS:
        report = simulate.compare(
            con, demo, keys=keys, holdout_days=args.holdout, safety_days=safety,
        )
        for name, result in report["policies"].items():
            rows[name].append(
                f"{_pct(result.fill_rate.value)} / {_num(result.average_on_hand.value)}".rjust(18)
            )

    for name, cells in rows.items():
        print(f"{LABELS[name]:22s} " + " ".join(cells))

    print()
    print("The reorder-point rule ignores the safety-days setting by construction:")
    print("its buffer comes from demand variability, so its row is flat. That is")
    print("the honest incumbent to beat, and the comparison to read is whether")
    print("the fitted-forecast row reaches a given fill rate on less stock.")


def _print(report, args) -> None:
    # ASCII only: this prints to a Windows console under cp1252.
    print(f"Service backtest - {report['evaluated']} of {report['portfolio']} series, "
          f"{report['holdout_days']}-day holdout, {report['safety_rule']}")
    print(f"demand mix: " + ", ".join(f"{k} {v}" for k, v in report["pattern_mix"].items()))
    print()
    print(f"{'policy':22s} {'fill rate':>10s} {'avg on-hand':>12s} "
          f"{'stock value':>13s} {'carrying/yr':>13s} {'units short':>12s} {'scored':>7s}")
    for name, result in report["policies"].items():
        value = result.inventory_value
        print(f"{LABELS[name]:22s} {_pct(result.fill_rate.value):>10s} "
              f"{_num(result.average_on_hand.value):>12s} "
              f"{value.total:13,.0f} {value.annual_carrying:13,.0f} "
              f"{result.units_short:12,.0f} {result.fill_rate.n_scored:7d}")

    # Units cannot be compared across a portfolio -- a thousand fasteners and a
    # thousand castings are not the same decision -- so the money column is the
    # one a planner is answerable for. It is a total across the evaluated
    # series, which is why the count is printed with it.
    first = next(iter(report["policies"].values())).inventory_value
    print(f"  stock value totals {first.series} series at "
          f"{first.carrying_rate * 100:.0f}% annual carrying; costs are synthetic "
          f"in the demo")
    if not first.complete:
        print(f"  WARNING: {first.unpriced} series have no unit cost and "
              f"contribute nothing to the value columns")

    print()
    print("by demand pattern - fill rate / average on-hand:")
    patterns = sorted(report["pattern_mix"])
    print(f"  {'policy':22s} " + " ".join(f"{p:>22s}" for p in patterns))
    for name, result in report["policies"].items():
        cells = []
        for pattern in patterns:
            block = result.by_pattern.get(pattern)
            if not block:
                cells.append(f"{'-':>22s}")
                continue
            cells.append(
                f"{_pct(block['fill_rate'].value)} / {_num(block['average_on_hand'].value)}".rjust(22)
            )
        print(f"  {LABELS[name]:22s} " + " ".join(cells))

    print()
    print("Fill rate is units served immediately from stock, over units demanded.")
    print("Unmet demand is LOST, not backordered. Series with no demand in the")
    print("holdout have no fill rate and are counted as unscored, not as 100%.")
    print()
    print("Two reorder-point rows on purpose. TUNED refits its parameters on all")
    print("available history; STALE freezes them on the first third and never")
    print("revisits, which is what an SME incumbent actually looks like. The")
    print("tuned row presupposes ongoing tuning nobody is doing.")
    print()
    print("The naive-zero row is the experiment. That forecast scores well on")
    print("MASE for intermittent demand -- right on every quiet day, wrong only")
    print("where it matters -- and it is also a policy that barely orders. Read")
    print("its fill rate next to its inventory and the metric question settles")
    print("itself: accuracy was never the objective.")


def _pct(value) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _num(value) -> str:
    return "-" if value is None else f"{value:,.0f}"


if __name__ == "__main__":
    raise SystemExit(main())
