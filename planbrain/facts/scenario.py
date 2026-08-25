"""Scenario lifecycle: flat peers, full copy, one explicit committed pointer.

There is no hierarchy and no copy-on-write. A scenario physically contains every
row it holds, so a read never resolves through a parent chain. That costs a few
hundred MB per copy at target scale -- 200 SKUs x 20 locations x 104 buckets x
8 measures is roughly 3M rows, one INSERT...SELECT, seconds -- and buys three
things worth far more:

* Reads stay flat. No recursive CTE compounding with the sparse-storage rule,
  which would put two independent "absent means something" semantics into one
  query.
* Copies freeze. New actuals landing in working cannot silently rewrite a
  what-if you opened last week.
* Committed is an explicit flag, not a position in a tree. It is the plan that
  authorises order release, which is its own concept.

See docs/decisions.md for what was rejected.
"""

from datetime import datetime, timezone

from .access import assert_writable
from .grains import FACT_TABLES


def copy_scenario(con, *, source_scenario_id: int, name: str, now=None) -> int:
    """Copy every fact row of ``source_scenario_id`` into a new open scenario.

    ``source_scenario_id`` is recorded on the new scenario as provenance only;
    nothing ever reads it to resolve a value.
    """
    new_id = _next_scenario_id(con)
    con.execute(
        "INSERT INTO scenario (scenario_id, name, created_at, source_scenario_id, status)"
        " VALUES (?, ?, ?, ?, 'open')",
        (new_id, name, _stamp(now), source_scenario_id),
    )
    _copy_facts(con, source_scenario_id, new_id)
    return new_id


def commit_scenario(con, *, source_scenario_id: int, name: str, now=None) -> int:
    """Freeze a snapshot of ``source_scenario_id`` as the committed plan.

    Committing never flags the source itself: working stays live and open, so
    "how far has the plan drifted since we committed?" is a plain join between
    two scenarios rather than a question nobody can answer. Any previously
    committed scenario is archived, keeping the partial unique index satisfied.
    """
    stamp = _stamp(now)
    con.execute("UPDATE scenario SET status = 'archived' WHERE status = 'committed'")
    new_id = _next_scenario_id(con)
    # Created unfrozen, then frozen once the rows have landed. Freezing first
    # would make the scenario immutable before it had any content, and the copy
    # would have to bypass the very guard that makes "frozen" mean something.
    con.execute(
        "INSERT INTO scenario"
        " (scenario_id, name, created_at, source_scenario_id, status)"
        " VALUES (?, ?, ?, ?, 'committed')",
        (new_id, name, stamp, source_scenario_id),
    )
    _copy_facts(con, source_scenario_id, new_id)
    con.execute(
        "UPDATE scenario SET frozen_at = ? WHERE scenario_id = ?", (stamp, new_id)
    )
    return new_id


def _copy_facts(con, source_scenario_id: int, target_scenario_id: int) -> None:
    """Full physical copy across every fact grain.

    Bulk INSERT...SELECT rather than a round-trip through write_facts: this
    moves millions of rows and the source is already sparse, so there is nothing
    for the accessor to sparsify. It shares the frozen-scenario guard, which is
    the part that has to hold.
    """
    assert_writable(con, target_scenario_id)
    for table, key_cols in FACT_TABLES.items():
        cols = ", ".join((*key_cols, "bucket_date", "measure", "qty"))
        con.execute(
            f"INSERT INTO {table} ({cols}, scenario_id)"
            f" SELECT {cols}, ? FROM {table} WHERE scenario_id = ?",
            (target_scenario_id, source_scenario_id),
        )


def _next_scenario_id(con) -> int:
    """Next free id.

    TODO(django-migration): max+1 races under concurrent PostgreSQL writers.
    Replace with an identity column when the plugin app's migration is
    generated -- that is the task that owns this, not a floating cleanup.
    """
    return con.execute("SELECT max(scenario_id) + 1 FROM scenario").fetchone()[0]


def _stamp(now) -> str:
    return (now or datetime.now(timezone.utc)).isoformat()
