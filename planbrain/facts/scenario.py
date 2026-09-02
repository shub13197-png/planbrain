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

from collections import namedtuple
from datetime import datetime, timezone

from .access import assert_writable
from .grains import FACT_TABLES


#: The growth assumptions a scenario carries. Two fields, never one: a single
#: rate moving demand and capacity together would report a comfortable factory
#: at every setting. See docs/forecast.md.
Growth = namedtuple("Growth", "demand_pct capacity_pct")


def growth_of(con, scenario_id: int) -> Growth:
    """The growth assumptions recorded on a scenario.

    Read from the scenario rather than passed per call, so two engines cannot
    run the same scenario under different assumptions -- which would be
    invisible in every output and irreproducible afterwards.
    """
    row = con.execute(
        "SELECT demand_growth_pct, capacity_growth_pct FROM scenario"
        " WHERE scenario_id = ?",
        (scenario_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown scenario_id {scenario_id}")
    return Growth(demand_pct=row[0], capacity_pct=row[1])


def set_growth(con, *, scenario_id: int, demand_growth_pct: float = None,
               capacity_growth_pct: float = None) -> Growth:
    """Record a growth assumption. Returns the scenario's assumptions after the
    change.

    Either parameter may be omitted to leave it alone, so setting one does not
    silently reset the other. Refused on a frozen scenario for the same reason
    fact writes are: a snapshot whose assumptions can still change is not a
    snapshot, and plan-versus-commit drift stops meaning anything.
    """
    assert_writable(con, scenario_id)
    current = growth_of(con, scenario_id)
    demand = current.demand_pct if demand_growth_pct is None else float(demand_growth_pct)
    capacity = current.capacity_pct if capacity_growth_pct is None else float(capacity_growth_pct)

    for label, value in (("demand", demand), ("capacity", capacity)):
        if value <= -100.0:
            raise ValueError(
                f"{label} growth of {value}% is at or below -100%, which is not "
                f"a rate; nothing can shrink by more than all of itself"
            )

    con.execute(
        "UPDATE scenario SET demand_growth_pct = ?, capacity_growth_pct = ?"
        " WHERE scenario_id = ?",
        (demand, capacity, scenario_id),
    )
    return Growth(demand_pct=demand, capacity_pct=capacity)


def copy_scenario(con, *, source_scenario_id: int, name: str, now=None) -> int:
    """Copy every fact row of ``source_scenario_id`` into a new open scenario.

    ``source_scenario_id`` is recorded on the new scenario as provenance only;
    nothing ever reads it to resolve a value.
    """
    new_id = _next_scenario_id(con)
    growth = growth_of(con, source_scenario_id)
    # The assumptions come with the rows. A copy that reset them to zero would
    # be a different plan wearing the same name -- and the difference would show
    # up as a quieter forecast with nothing to explain it.
    con.execute(
        "INSERT INTO scenario (scenario_id, name, created_at, source_scenario_id,"
        " status, demand_growth_pct, capacity_growth_pct)"
        " VALUES (?, ?, ?, ?, 'open', ?, ?)",
        (new_id, name, _stamp(now), source_scenario_id,
         growth.demand_pct, growth.capacity_pct),
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
    growth = growth_of(con, source_scenario_id)
    con.execute(
        "INSERT INTO scenario"
        " (scenario_id, name, created_at, source_scenario_id, status,"
        " demand_growth_pct, capacity_growth_pct)"
        " VALUES (?, ?, ?, ?, 'committed', ?, ?)",
        (new_id, name, stamp, source_scenario_id,
         growth.demand_pct, growth.capacity_pct),
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
