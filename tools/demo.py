"""The whole pipeline end to end, in one command.

    docker compose up
    python -m tools.demo

Builds the demo plant, forecasts, plans, checks capacity, replays service, and
assigns the fleet — printing the headline number from each stage and the caveat
that goes with it.

This exists because the project was invisible. Nine build items of correctness
do not help anyone who cannot see what it does in under a minute.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from planbrain import netreq, rccp, simulate  # noqa: E402
from planbrain.demo import build_demo, populate  # noqa: E402
from planbrain.forecast import demand_keys  # noqa: E402
from planbrain.haulplan import Ledger, jain_index, verdict  # noqa: E402
from planbrain.haulplan.assign import assign  # noqa: E402
from planbrain.haulplan.fairness import ceiling  # noqa: E402

SCHEMA = Path(__file__).resolve().parents[1] / "planbrain" / "facts" / "schema.sql"


def rule(title):
    print()
    print(title)
    print("-" * len(title))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--sample", type=int, default=24,
                        help="series for the service replay; keeps the run under a minute")
    args = parser.parse_args(argv)

    print("Planning Brain - end-to-end demo")
    print("A fake lubricant blending plant. Every number below is computed now.")

    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA.read_text(encoding="utf-8"))

    rule("1. The dataset")
    demo = build_demo(seed=args.seed)
    counts = populate(con, demo)
    span = (demo.history_end - demo.history_start).days + 1
    series = len({f.keys for f in demo.facts[("fact_supply_demand", "demand_actual")]})
    dense = span * series
    stored = counts[("fact_supply_demand", "demand_actual")]
    print(f"  {len(demo.parts)} parts, {len(demo.bom)} BOM edges, {len(demo.trucks)} trucks")
    print(f"  {span} days of history, {series} demand series")
    print(f"  {stored:,} of {dense:,} cells stored ({stored / dense:.0%}) - the rest are")
    print(f"  genuinely zero and are not written. Absent means zero, never unknown.")

    rule("2. Plan")
    netreq.run(con, demo, lot_sizing="cost_based")
    print("  netreq: multi-level MRP, cost-based lot sizing, releases pulled back")
    print("  to working days. Wagner-Whitin verified against a published textbook")
    print("  instance (Snyder & Shen 3.9: lots of 210 and 150, cost 1380).")

    rule("3. Can the plant make it?")
    capacity = rccp.run(con, demo)
    load = sum(d["load_hours"] for d in capacity["resources"].values())
    available = sum(d["capacity_hours"] for d in capacity["resources"].values())
    over = sum(len(d["overloaded_buckets"]) for d in capacity["resources"].values())
    print(f"  {load:,.0f} h of work against {available:,.0f} h available "
          f"({load / available:.0%})")
    print(f"  NOT FEASIBLE: overloaded in {over} of "
          f"{len(capacity['resources']) * capacity['buckets']} resource-buckets.")
    print("  The plan fits on average and clumps in time. Fixing that is the CLSP,")
    print("  which is out of scope and said so before this was run.")

    rule("4. What service would it give?")
    keys = _sample(demand_keys(demo), args.sample)
    factor = simulate.capacity_factor(con, demo)
    plain = simulate.compare(con, demo, keys=keys, safety_days=7.0)
    capped = simulate.compare(con, demo, keys=keys, safety_days=7.0,
                              delivery_factor=factor)
    for label, report in (("unconstrained", plain), ("capacity-capped", capped)):
        block = report["policies"]["forecast"]
        rival = report["policies"]["reorder_point"]
        print(f"  {label:16s} fitted forecast {block.fill_rate.value:.1%} fill on "
              f"{block.average_on_hand.value:,.0f} units")
        print(f"  {'':16s} reorder point   {rival.fill_rate.value:.1%} fill on "
              f"{rival.average_on_hand.value:,.0f} units")
    print("  Two numbers because the first assumes a plan stage 3 just called")
    print("  infeasible. The spreadsheet rule is close - read docs/README before")
    print("  concluding anything from the gap.")

    rule("5. Who drives the long runs?")
    ledger = Ledger.opening(demo.truck_ytd_long_haul_km)
    before = jain_index(list(demo.truck_ytd_long_haul_km.values()))
    plan = assign(demo.trips, demo.trucks, ledger)
    after = jain_index(ledger.distribution())
    work = sum(t.distance_km for t in demo.trips if t.is_long_haul)
    top = ceiling(list(demo.truck_ytd_long_haul_km.values()), work)
    print(f"  fairness {before:.4f} ({verdict(before)}) -> {after:.4f} ({verdict(after)})")
    print(f"  {len(plan.assignments)} of {len(demo.trips)} trips assigned; "
          f"{len(plan.unassigned)} had no feasible truck")
    print(f"  best any optimiser could reach: {top:.4f}. Greedy is within "
          f"{top - after:.4f},")
    print("  which is why no solver is here yet.")

    rule("What to read next")
    print("  README.md                  claims, ranked by how well evidenced they are")
    print("  docs/service-backtest.md   the proof-of-value report, caveats first")
    print("  docs/decisions.md          what was decided, and what was rejected and why")
    print("  docs/reconciliation.md     the headline check that failed, and its replacement")
    return 0


def _sample(keys, n):
    if not n or n >= len(keys):
        return keys
    step = len(keys) / n
    return [keys[int(i * step)] for i in range(n)]


if __name__ == "__main__":
    raise SystemExit(main())
