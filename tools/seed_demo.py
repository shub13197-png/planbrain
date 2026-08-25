"""Build the demo dataset into a local SQLite database.

    python -m tools.seed_demo --out data/local/demo.sqlite3

The generator is deterministic, so the database is disposable and nothing under
data/ is committed. PostgreSQL gets the same treatment once the plugin app's
migrations exist; the schema is portable across both.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from planbrain.demo import build_demo, populate  # noqa: E402

SCHEMA = Path(__file__).resolve().parents[1] / "planbrain" / "facts" / "schema.sql"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data/local/demo.sqlite3", type=Path)
    parser.add_argument("--seed", default=7, type=int)
    args = parser.parse_args(argv)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.exists():
        args.out.unlink()

    con = sqlite3.connect(args.out)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA.read_text(encoding="utf-8"))

    demo = build_demo(seed=args.seed)
    counts = populate(con, demo)
    con.commit()

    span = (demo.history_end - demo.history_start).days + 1
    series = len({f.keys for f in demo.facts[("fact_supply_demand", "demand_actual")]})
    print(f"{args.out}  (seed {args.seed})")
    print(f"  parts        {len(demo.parts)}  ({_by_level(demo)})")
    print(f"  bom edges    {len(demo.bom)}")
    print(f"  locations    {len(demo.locations)}   resources {len(demo.resources)}"
          f"   trucks {len(demo.trucks)}")
    print(f"  history      {demo.history_start} .. {demo.history_end}  ({span} days)")
    print(f"  horizon      {demo.horizon_start} .. {demo.horizon_end}")
    print(f"  demand series {series}")
    for (table, measure), n in sorted(counts.items()):
        print(f"  {table}.{measure}: {n} rows")
    dense = span * series
    stored = counts[("fact_supply_demand", "demand_actual")]
    print(f"  sparsity     {stored}/{dense} cells stored ({stored / dense:.0%})")
    print(f"  messiness    {len(demo.launched_mid_history)} launches, "
          f"{len(demo.discontinued_mid_history)} discontinuations, "
          f"{len(demo.stockout_windows)} stockout windows")
    con.close()
    return 0


def _by_level(demo) -> str:
    levels = {}
    for part in demo.parts:
        levels[part.level] = levels.get(part.level, 0) + 1
    return ", ".join(f"{k} {v}" for k, v in sorted(levels.items()))


if __name__ == "__main__":
    raise SystemExit(main())
