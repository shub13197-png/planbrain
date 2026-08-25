"""The single read and write path for planning facts.

Fact storage is sparse: an absent row means zero. Any query that inner-joins a
fact table silently drops the zero buckets, and every statistic computed over
the survivors is then biased high -- worst on the intermittent-demand SKUs that
Croston and TSB exist for. That is a wrong number that looks right, which
CLAUDE.md names as the worst possible output of this project.

Documentation does not prevent that. This module does. It is the only place
allowed to touch a fact table, enforced by tools/check_fact_access.py in CI:

* ``read_facts`` always densifies against a generated bucket spine.
* ``write_facts`` always sparsifies, and refuses to write to a frozen scenario.

The frozen check lives here rather than in a database trigger so that SQLite and
PostgreSQL behave identically. Revisit a trigger as defence-in-depth if raw SQL
ever enters the codebase.
"""

from datetime import date, timedelta
from typing import Iterable, NamedTuple, Sequence

from .grains import FACT_TABLES, TABLE_GRAIN


class FrozenScenarioError(ValueError):
    """A write was attempted against a scenario that has been frozen."""


class Fact(NamedTuple):
    """One bucket. ``keys`` matches the table's grain columns, in order."""

    keys: tuple
    bucket_date: date
    qty: float


def bucket_spine(start: date, end: date) -> list[date]:
    """Every daily bucket from ``start`` to ``end``, both ends inclusive."""
    if end < start:
        raise ValueError(f"end {end} precedes start {start}")
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def assert_writable(con, scenario_id: int) -> None:
    """Raise unless ``scenario_id`` exists and is unfrozen.

    A frozen scenario is a snapshot. If it can still be edited it is not a
    snapshot, and plan-versus-commit drift silently stops meaning anything.
    """
    row = con.execute(
        "SELECT name, frozen_at FROM scenario WHERE scenario_id = ?", (scenario_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown scenario_id {scenario_id}")
    name, frozen_at = row
    if frozen_at is not None:
        raise FrozenScenarioError(
            f"scenario {scenario_id} ({name!r}) was frozen at {frozen_at} and is immutable"
        )


def read_facts(
    con,
    table: str,
    *,
    scenario_id: int,
    measure: str,
    start: date,
    end: date,
    keys: Sequence[tuple],
) -> list[Fact]:
    """Read one measure as a dense series, zero-filled across the bucket spine.

    Returns one Fact per (key, bucket) pair, ordered by key then bucket, with
    ``qty`` of 0 wherever the sparse table holds no row. Entities absent from
    the table entirely still come back as a full run of zeros.

    Scenarios are flat peers, so this reads exactly one scenario's rows and
    never walks ``source_scenario_id``.
    """
    key_cols = _check_table(table)
    keys = [_check_key(k, key_cols, table) for k in keys]
    _check_measure(con, table, measure)

    spine = bucket_spine(start, end)
    if not keys:
        return []

    where_key = " OR ".join(
        "(" + " AND ".join(f"{c} = ?" for c in key_cols) + ")" for _ in keys
    )
    params = [scenario_id, measure, spine[0].isoformat(), spine[-1].isoformat()]
    for k in keys:
        params.extend(k)
    sql = (
        f"SELECT {', '.join(key_cols)}, bucket_date, qty FROM {table}"
        f" WHERE scenario_id = ? AND measure = ?"
        f"   AND bucket_date BETWEEN ? AND ?"
        f"   AND ({where_key})"
    )
    sparse = {
        (tuple(r[: len(key_cols)]), _as_date(r[len(key_cols)])): r[-1]
        for r in con.execute(sql, params)
    }

    # Densify: the spine is the source of truth for which buckets exist.
    return [
        Fact(keys=k, bucket_date=b, qty=sparse.get((k, b), 0))
        for k in keys
        for b in spine
    ]


def write_facts(
    con,
    table: str,
    *,
    scenario_id: int,
    measure: str,
    facts: Iterable[Fact],
) -> int:
    """Write one measure, sparsifying as it goes. Returns rows persisted.

    The exact mirror of ``read_facts``: that densifies zeros on the way out, so
    this drops them on the way in. A zero is written as the *absence* of a row,
    and an existing row at that address is deleted -- otherwise re-running a
    planning engine would leave last run's non-zero value sitting where this run
    computed zero, which is precisely a plausible wrong number.

    Idempotent: writing the same facts twice leaves the table identical.
    """
    key_cols = _check_table(table)
    _check_measure(con, table, measure)
    assert_writable(con, scenario_id)

    cols = ", ".join((*key_cols, "bucket_date", "measure", "scenario_id"))
    placeholders = ", ".join("?" for _ in range(len(key_cols) + 3))
    upsert = (
        f"INSERT INTO {table} ({cols}, qty) VALUES ({placeholders}, ?)"
        f" ON CONFLICT ({cols}) DO UPDATE SET qty = excluded.qty"
    )
    delete = (
        f"DELETE FROM {table} WHERE {' AND '.join(f'{c} = ?' for c in key_cols)}"
        f" AND bucket_date = ? AND measure = ? AND scenario_id = ?"
    )

    to_set, to_clear = [], []
    for fact in facts:
        keys, bucket_date, qty = fact
        keys = _check_key(keys, key_cols, table)
        address = (*keys, _iso(bucket_date), measure, scenario_id)
        (to_clear if qty == 0 else to_set).append(
            address if qty == 0 else (*address, qty)
        )

    if to_clear:
        con.executemany(delete, to_clear)
    if to_set:
        con.executemany(upsert, to_set)
    return len(to_set)


def _check_table(table: str) -> tuple:
    if table not in FACT_TABLES:
        raise ValueError(f"unknown fact table {table!r}; expected one of {sorted(FACT_TABLES)}")
    return FACT_TABLES[table]


def _check_key(key, key_cols: tuple, table: str) -> tuple:
    key = tuple(key)
    if len(key) != len(key_cols):
        raise ValueError(f"key arity {len(key)} does not match {table} grain {key_cols}")
    return key


def _check_measure(con, table: str, measure: str) -> None:
    """A measure may only be used with the fact table matching its grain."""
    want = TABLE_GRAIN[table]
    row = con.execute("SELECT grain FROM measure WHERE measure = ?", (measure,)).fetchone()
    if row is None:
        # A grain with no measures at all is a reserved table, not a typo.
        reserved = con.execute(
            "SELECT count(*) FROM measure WHERE grain = ?", (want,)
        ).fetchone()[0] == 0
        if reserved:
            raise ValueError(
                f"{table} is reserved: no measure of grain {want!r} is defined yet"
            )
        raise ValueError(f"unknown measure {measure!r}")
    if row[0] != want:
        raise ValueError(
            f"measure {measure!r} is of grain {row[0]!r}; {table} holds grain {want!r}"
        )


def _as_date(value) -> date:
    """SQLite hands back ISO text; PostgreSQL hands back a date."""
    return value if isinstance(value, date) else date.fromisoformat(value)


def _iso(value) -> str:
    return value.isoformat() if isinstance(value, date) else value
