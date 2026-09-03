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

from planbrain import forecast, netreq, rccp  # noqa: E402
from planbrain.demo import build_demo, populate  # noqa: E402
from planbrain.facts.access import read_facts  # noqa: E402
from planbrain.facts.scenario import set_growth  # noqa: E402

SCHEMA = Path(__file__).resolve().parents[1] / "planbrain" / "facts" / "schema.sql"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--source", default="naive_replay",
                        choices=["naive_replay", "forecast"])
    parser.add_argument("--lot-sizing", default="as_master",
                        choices=["as_master", "cost_based"],
                        help="cost_based prices changeover into the lot size")
    parser.add_argument("--explain", action="store_true",
                        help="attribute each overloaded bucket to the SKUs that "
                             "loaded it, and show the nearest spare hours")
    parser.add_argument("--demand-growth", type=float, default=0.0, metavar="PCT",
                        help="annual %% growth in demand, compounded daily from "
                             "the last actual. Needs --source forecast")
    parser.add_argument("--capacity-growth", type=float, default=0.0, metavar="PCT",
                        help="annual %% growth in available hours: a shift or a "
                             "machine phasing in")
    args = parser.parse_args(argv)

    if args.demand_growth and args.source != "forecast":
        # Refused rather than ignored. A growth rate that silently does nothing
        # is worse than one that is rejected: the run succeeds, the numbers look
        # considered, and nobody finds out the assumption never applied.
        parser.error(
            "--demand-growth only affects a forecast-sourced plan; the naive "
            "replay reads actuals, which no assumption about the future can "
            "change. Add --source forecast, or use --capacity-growth."
        )

    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA.read_text(encoding="utf-8"))

    demo = build_demo(seed=args.seed)
    populate(con, demo)
    set_growth(con, scenario_id=0,
               demand_growth_pct=args.demand_growth,
               capacity_growth_pct=args.capacity_growth)
    if args.source == "forecast":
        forecast.run(con, demo)
    netreq.run(con, demo, source=args.source, lot_sizing=args.lot_sizing)
    report = rccp.run(con, demo)

    _print(report, demo, con)
    if args.explain:
        _explain(rccp.relief(con, demo), demo)
    return 0


def _explain(explained, demo, top: int = 8) -> None:
    """What is in the overloaded buckets. A diagnosis, not an instruction."""
    summary = explained["summary"]
    if not summary["buckets"]:
        print()
        print("Nothing to explain: every bucket fits.")
        return

    names = {p.sku_id: getattr(p, "name", p.sku_id) for p in demo.parts}
    print()
    print(f"Overload detail - {summary['buckets']} buckets across "
          f"{summary['resources']} resources, {summary['over_hours']:,.0f} h over")
    print(f"  worst single SKU: {names.get(summary['worst_sku'], summary['worst_sku'])} "
          f"at {summary['worst_sku_hours']:,.0f} h "
          f"({summary['concentration']:.0%} of the excess)")
    if summary["concentration"] < 0.15:
        # The number that decides which conversation to have. Said in words,
        # because a planner reading "4%" may not read it as "rescheduling one
        # product cannot fix this".
        print("  no single product dominates, so this is a capacity decision "
              "rather than a scheduling one")
    print(f"  at most {summary['relievable_by_moving_earlier_upper_bound']} of "
          f"{summary['buckets']} buckets have enough spare hours in the previous "
          f"{explained['window_days']} days -- an UPPER bound: neighbouring")
    print("  overloaded buckets are measured against the same spare hours and "
          "cannot all use them")

    worst = sorted(explained["overloads"], key=lambda o: -o.over_hours)[:top]
    print()
    print(f"  {'resource':>8s} {'bucket':>12s} {'over h':>8s} {'slack -':>8s} "
          f"{'slack +':>8s}  top contributors")
    for over in worst:
        top_skus = ", ".join(
            f"{names.get(c.sku_id, c.sku_id)} {c.hours:.0f}h"
            for c in over.contributors[:3]
        )
        print(f"  {over.resource_id:8d} {str(over.bucket_date):>12s} "
              f"{over.over_hours:8.0f} {over.slack_before:8.0f} "
              f"{over.slack_after:8.0f}  {top_skus}")
    print()
    print("  This says where the hours are, not what to move: production can only")
    print("  move earlier if the components are there earlier, which capacity")
    print("  planning cannot see. Choosing is the capacitated lot-sizing problem.")


def _print(report, demo, con) -> None:
    names = {r.resource_id: r.name for r in demo.resources}
    verdict = "FEASIBLE" if report["feasible"] else "NOT FEASIBLE"
    total_load = sum(d["load_hours"] for d in report["resources"].values())
    total_cap = sum(d["capacity_hours"] for d in report["resources"].values())
    overall = f"{total_load / total_cap * 100:.0f}%" if total_cap else "-"
    print(f"Rough-cut capacity - {report['buckets']} buckets - plan is {verdict}")
    if report["capacity_growth_pct"] or report["demand_growth_pct"]:
        # Printed with the verdict, not in a footer. A feasibility answer read
        # without its assumptions is the one most likely to be quoted onwards.
        print(f"assumptions: demand {report['demand_growth_pct']:+.1f}%/yr, "
              f"capacity {report['capacity_growth_pct']:+.1f}%/yr, "
              f"compounded from {report['growth_anchor']}")
    print(f"overall load {total_load:,.0f} h against {total_cap:,.0f} h available "
          f"({overall})")
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
