"""Re-run every published claim across several seeds, on the full portfolio.

    python -m tools.claim_audit --seeds 7 11 23 42 99

The claim that lumpy demand was where this tool excelled died because it was
measured on a 40-series sample and asserted for the whole portfolio. This exists
so the rest of the claims get tested the same way before a reader does it.

Two questions per claim: does it hold on the **full portfolio**, and does it hold
across **more than one seed**? A result from a single synthetic dataset is a
property of that dataset until shown otherwise.
"""

import argparse
import sqlite3
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from planbrain import netreq, rccp, simulate  # noqa: E402
from planbrain.demo import build_demo, populate  # noqa: E402
from planbrain.demo.generate import CAMPAIGN_CYCLE_DAYS  # noqa: E402
from planbrain.facts.access import read_facts  # noqa: E402
from planbrain.forecast import demand_keys  # noqa: E402
from planbrain.haulplan import Ledger, jain_index  # noqa: E402
from planbrain.haulplan.assign import assign  # noqa: E402
from planbrain.haulplan.fairness import ceiling  # noqa: E402

SCHEMA = Path(__file__).resolve().parents[1] / "planbrain" / "facts" / "schema.sql"


def run_seed(seed: int) -> dict:
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    demo = build_demo(seed=seed)
    populate(con, demo)
    netreq.run(con, demo, lot_sizing="cost_based")
    capacity = rccp.run(con, demo)

    # Full portfolio. Sampling is what killed the lumpy claim.
    report = simulate.compare(con, demo, keys=demand_keys(demo), safety_days=7.0)
    policies = report["policies"]

    def by_pattern(policy, pattern):
        block = policies[policy].by_pattern.get(pattern)
        return block["fill_rate"].value if block else None

    campaigns = 0
    for routing in demo.routings:
        rows = read_facts(
            con, "fact_supply_demand", scenario_id=0,
            measure="planned_order_release",
            start=demo.horizon_start, end=demo.horizon_end,
            keys=[(routing.sku_id, 1)],
        )
        campaigns += sum(1 for r in rows if r.qty > 0)
    buckets = capacity["buckets"]
    interval = (buckets * len(demo.routings) / campaigns) if campaigns else None

    ledger = Ledger.opening(demo.truck_ytd_long_haul_km)
    opening = list(demo.truck_ytd_long_haul_km.values())
    assign(demo.trips, demo.trucks, ledger)
    work = sum(t.distance_km for t in demo.trips if t.is_long_haul)

    load = sum(d["load_hours"] for d in capacity["resources"].values())
    available = sum(d["capacity_hours"] for d in capacity["resources"].values())

    con.close()
    return {
        "seed": seed,
        "series": len(demand_keys(demo)),
        "intermittent_fitted": by_pattern("forecast", "intermittent"),
        "intermittent_tuned": by_pattern("reorder_point", "intermittent"),
        "lumpy_fitted": by_pattern("forecast", "lumpy"),
        "lumpy_tuned": by_pattern("reorder_point", "lumpy"),
        "naive_intermittent": by_pattern("naive_zero", "intermittent"),
        "tuned_overall": policies["reorder_point"].fill_rate.value,
        "stale_overall": policies["reorder_point_stale"].fill_rate.value,
        "fitted_overall": policies["forecast"].fill_rate.value,
        "utilisation": load / available if available else None,
        "overloaded": sum(
            len(d["overloaded_buckets"]) for d in capacity["resources"].values()
        ),
        "resource_buckets": len(capacity["resources"]) * buckets,
        "campaign_interval": interval,
        "fairness_gain": jain_index(ledger.distribution()) - jain_index(opening),
        "fairness_headroom": ceiling(opening, work) - jain_index(ledger.distribution()),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, nargs="+", default=[7, 11, 23, 42, 99])
    args = parser.parse_args(argv)

    results = []
    for seed in args.seeds:
        print(f"running seed {seed} on the full portfolio ...", file=sys.stderr)
        results.append(run_seed(seed))

    print(f"Claim audit - {len(results)} seeds, full portfolio each")
    print()

    _claim(
        "1  Intermittent: fitted beats a tuned reorder point",
        results, lambda r: (r["intermittent_fitted"] - r["intermittent_tuned"]) * 100,
        "points of fill", holds=lambda v: v > 0,
    )
    _claim(
        "   Lumpy: fitted beats a tuned reorder point  (RETRACTED)",
        results, lambda r: (r["lumpy_fitted"] - r["lumpy_tuned"]) * 100,
        "points of fill", holds=lambda v: v > 0,
    )
    _claim(
        "   Naive-zero collapses on intermittent",
        results, lambda r: r["naive_intermittent"] * 100,
        "% fill", holds=lambda v: v < 80,
    )
    _claim(
        "2  Staleness costs fill rate",
        results, lambda r: (r["tuned_overall"] - r["stale_overall"]) * 100,
        "points", holds=lambda v: v > 1.0,
    )
    _claim(
        "3  The plan is not capacity-feasible",
        results, lambda r: r["overloaded"] / r["resource_buckets"] * 100,
        "% of buckets over", holds=lambda v: v > 0,
    )
    _claim(
        "4  Campaign interval is shorter than the 14-day allowance",
        results, lambda r: r["campaign_interval"],
        "days", holds=lambda v: v < CAMPAIGN_CYCLE_DAYS,
    )
    _claim(
        "   Greedy fairness leaves little headroom for a solver",
        results, lambda r: r["fairness_headroom"],
        "Jain", holds=lambda v: v < 0.01,
    )

    print()
    print("A claim that holds on every seed is a property of the method.")
    print("One that holds on some is a property of the dataset, and saying so is")
    print("cheaper than having a reader find out.")
    return 0


def _claim(label, results, extract, unit, holds):
    values = [extract(r) for r in results if extract(r) is not None]
    if not values:
        print(f"{label:52s} unmeasured")
        return
    passes = sum(1 for v in values if holds(v))
    verdict = "HOLDS" if passes == len(values) else f"holds {passes}/{len(values)}"
    spread = f"{min(values):.2f} to {max(values):.2f}"
    print(f"{label:52s} {statistics.mean(values):7.2f} {unit:18s} "
          f"[{spread}]  {verdict}")


if __name__ == "__main__":
    raise SystemExit(main())
