import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "planbrain" / "facts" / "schema.sql"


@pytest.fixture
def con():
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA foreign_keys = ON")
    c.executescript(SCHEMA.read_text())
    yield c
    c.close()


@pytest.fixture(scope="session")
def demo_offline():
    """Run the pipeline end to end with every outbound socket blocked.

    Session-scoped because it fits models and is the slowest fixture here. The
    guard is engaged before the planning imports so that anything reaching the
    network at import time trips it too -- a version check on first import is a
    real pattern and would be invisible to a guard engaged later.
    """
    import sqlite3

    from planbrain.offline import engage, release

    engage()
    try:
        from planbrain import forecast, netreq, rccp, simulate
        from planbrain.demo import build_demo, populate
        from planbrain.forecast import demand_keys

        con = sqlite3.connect(":memory:")
        con.execute("PRAGMA foreign_keys = ON")
        con.executescript(SCHEMA.read_text(encoding="utf-8"))
        demo = build_demo(seed=7)
        populate(con, demo)

        keys = demand_keys(demo)[:6]
        fitted = forecast.run(con, demo, keys=keys)
        plan = netreq.run(con, demo, lot_sizing="cost_based")
        capacity = rccp.run(con, demo)
        simulate.compare(con, demo, keys=keys, safety_days=7.0)

        result = {
            "forecast_rows": fitted["rows_written"],
            "plan_measures": set(plan),
            "capacity_buckets": capacity["buckets"],
        }
        con.close()
        return result
    finally:
        release()
