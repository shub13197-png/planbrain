"""Rough-cut capacity load against available hours.

    python -m tools.capacity_report

Runs netreq, loads the resulting plan onto resources, and reports whether the
plant can actually make it. Splits load into run time and changeover time,
because those are two different problems with two different fixes.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from planbrain import netreq, rccp  # noqa: E402
from planbrain.demo import build_demo, populate  # noqa: E402
from planbrain.facts.access import read_facts  # noqa: E402

SCHEMA = Path(__file__).resolve().parents[1] / "planbrain" / "facts" / "schema.sql"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--source", default="naive_replay",
                        choices=["naive_replay", "forecast"])
    args = parser.parse_args(argv)

    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA.read_text(encoding="utf-8"))

    demo = build_demo(seed=args.seed)
    populate(con, demo)
    netreq.run(con, demo, source=args.source)
    report = rccp.run(con, demo)

    _print(report, demo, con)
    return 0


def _print(report, demo, con) -> None:
    names = {r.resource_id: r.name for r in demo.resources}
    verdict = "FEASIBLE" if report["feasible"] else "NOT FEASIBLE"
    print(f"Rough-cut capacity - {report['buckets']} buckets - plan is {verdict}")
    print()
    print(f"{'resource':26s} {'load h':>9s} {'avail h':>9s} {'util':>7s} "
          f"{'over':>6s} {'no-cap':>7s}")
    for rid, detail in report["resources"].items():
        util = "-" if detail["utilisation"] is None else f"{detail['utilisation'] * 100:.0f}%"
        print(f"{names.get(rid, rid):26s} {detail['load_hours']:9,.0f} "
              f"{detail['capacity_hours']:9,.0f} {util:>7s} "
              f"{len(detail['overloaded_buckets']):6d} "
              f"{len(detail['load_without_capacity']):7d}")

    setup_h, run_h = _split_load(con, demo)
    total = setup_h + run_h
    print()
    if total:
        print(f"run time    {run_h:9,.0f} h  {run_h / total * 100:5.1f}%")
        print(f"changeover  {setup_h:9,.0f} h  {setup_h / total * 100:5.1f}%")
    print()
    print("'over' counts buckets where work exceeds available hours. 'no-cap'")
    print("counts buckets carrying work where ZERO hours are available -- a closed")
    print("day or a resource down for maintenance. That is a different mistake")
    print("from an overload, and it is invisible in a utilisation figure because")
    print("dividing by zero has no honest answer.")
    print()
    print("Load is placed when work STARTS, not when goods arrive. Rough-cut")
    print("front-loads the whole order into the release bucket rather than")
    print("spreading it across the lead time: conservative, and honest about its")
    print("own resolution. Exact timing within the lead time is finite scheduling.")


def _split_load(con, demo):
    """Changeover hours against run hours, which have different fixes.

    Changeover time is attacked by lot sizing and campaign sequencing; run time
    can only be attacked by more capacity or less demand. Reporting one number
    would hide which problem the plant actually has.
    """
    plant = next(loc.loc_id for loc in demo.locations if loc.kind == "plant")
    setup_h = run_h = 0.0
    for routing in demo.routings:
        rows = read_facts(
            con, "fact_supply_demand", scenario_id=0,
            measure="planned_order_release",
            start=demo.horizon_start, end=demo.horizon_end,
            keys=[(routing.sku_id, plant)],
        )
        active = [row.qty for row in rows if row.qty > 0]
        setup_h += len(active) * routing.setup_hours
        run_h += sum(active) * routing.hours_per_unit
    return setup_h, run_h


if __name__ == "__main__":
    raise SystemExit(main())
