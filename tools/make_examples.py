"""Write one worked example per payload kind.

The examples are the readable half of the contract -- a schema tells you what is
legal, an example tells you what a real payload looks like. They are validated
in CI, so a schema change that nobody reflected here fails the build.

    python -m tools.make_examples
"""

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "planbrain" / "contracts" / "examples"

HORIZON = {"start": "2026-03-02", "end": "2026-03-06", "bucket_count": 5, "bucket": "day"}

EXAMPLES = {
    "netreq_input": {
        "scenario_id": 0,
        "horizon": HORIZON,
        "items": [
            {
                "sku_id": 101,
                "loc_id": 7,
                "lead_time_days": 2,
                "on_hand": 120.0,
                "safety_stock": 50.0,
                "lot_sizing": {"policy": "fixed_qty", "fixed_qty": 200.0},
                "gross_req": [80.0, 0.0, 0.0, 150.0, 0.0],
                "scheduled_receipt": [0.0, 0.0, 100.0, 0.0, 0.0],
            }
        ],
    },
    "netreq_output": {
        "meta": {
            "scenario_id": 0,
            "status": "optimal",
            "solve_seconds": 0.03,
            "engine": "planbrain.netreq 0.1",
        },
        "horizon": HORIZON,
        "items": [
            {
                "sku_id": 101,
                "loc_id": 7,
                "on_hand_open": [120.0, 40.0, 40.0, 140.0, 190.0],
                "net_req": [10.0, 0.0, 0.0, 60.0, 0.0],
                "planned_order_receipt": [200.0, 0.0, 0.0, 200.0, 0.0],
                "planned_order_release": [0.0, 200.0, 0.0, 0.0, 0.0],
            }
        ],
        "exceptions": [
            {
                "sku_id": 101,
                "loc_id": 7,
                "kind": "past_due_release",
                "bucket_index": 0,
                "qty": 200.0,
                "days_late": 2,
            }
        ],
    },
    "rccp_input": {
        "scenario_id": 0,
        "horizon": HORIZON,
        "resources": [
            {
                "resource_id": 5,
                "name": "Blender A",
                "capacity_avail_hours": [16.0, 16.0, 16.0, 16.0, 8.0],
            }
        ],
        "routings": [
            {"sku_id": 101, "resource_id": 5, "hours_per_unit": 0.05, "setup_hours": 1.5}
        ],
        "planned_order_receipt": [{"sku_id": 101, "series": [200.0, 0.0, 0.0, 200.0, 0.0]}],
    },
    "rccp_output": {
        "meta": {
            "scenario_id": 0,
            "status": "optimal",
            "solve_seconds": 0.01,
            "engine": "planbrain.rccp 0.1",
        },
        "horizon": HORIZON,
        "resources": [
            {
                "resource_id": 5,
                "capacity_load_hours": [11.5, 0.0, 0.0, 11.5, 0.0],
                "utilisation": [0.72, 0.0, 0.0, 0.72, 0.0],
                "overloaded_buckets": [],
            }
        ],
    },
    "haulplan_input": {
        "scenario_id": 0,
        "time_limit_seconds": 30.0,
        "trucks": [
            {"truck_id": 1, "ytd_long_haul_km": 41200.0, "capacity_kg": 16000.0, "available": True},
            {"truck_id": 2, "ytd_long_haul_km": 12800.0, "capacity_kg": 16000.0, "available": True},
        ],
        "trips": [
            {
                "trip_id": 900,
                "origin_id": 7,
                "dest_id": 31,
                "bucket_date": "2026-03-02",
                "load_kg": 14000.0,
                "distance_km": 640.0,
                "is_long_haul": True,
            },
            {
                "trip_id": 901,
                "origin_id": 7,
                "dest_id": 12,
                "bucket_date": "2026-03-02",
                "load_kg": 9000.0,
                "distance_km": 85.0,
                "is_long_haul": False,
            },
        ],
    },
    "haulplan_output": {
        "meta": {
            "scenario_id": 0,
            "status": "timeout",
            "solve_seconds": 30.0,
            "engine": "timefold-solver 1.x",
        },
        "assignments": [
            {"trip_id": 900, "truck_id": 2},
            {"trip_id": 901, "truck_id": 1},
        ],
        "fairness": {
            "long_haul_km_after": [
                {"truck_id": 1, "ytd_long_haul_km": 41200.0},
                {"truck_id": 2, "ytd_long_haul_km": 13440.0},
            ],
            "spread_km": 27760.0,
        },
    },
}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for kind, payload in EXAMPLES.items():
        (OUT / f"{kind}.json").write_text(json.dumps(payload, indent=2) + "\n")
        print(f"wrote {kind}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
