"""The single read path for planning facts.

Fact storage is sparse: an absent row means zero. Any query that inner-joins a
fact table silently drops the zero buckets, and every statistic computed over
the survivors is then biased high -- worst on the intermittent-demand SKUs that
Croston and TSB exist for. That is a wrong number that looks right, which
CLAUDE.md names as the worst possible output of this project.

Documentation does not prevent that. This module does: it is the only place
allowed to SELECT from a fact table, enforced by tools/check_fact_access.py in
CI, and it always densifies against a generated bucket spine.
"""

from datetime import date, timedelta
from typing import NamedTuple, Sequence

from .grains import FACT_TABLES, TABLE_GRAIN


class Fact(NamedTuple):
    """One dense bucket. ``keys`` matches the table's grain columns, in order."""

    keys: tuple
    bucket_date: date
    qty: float


def bucket_spine(start: date, end: date) -> list[date]:
    """Every daily bucket from ``start`` to ``end``, both ends inclusive."""
    if end < start:
        raise ValueError(f"end {end} precedes start {start}")
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


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
    if table not in FACT_TABLES:
        raise ValueError(f"unknown fact table {table!r}; expected one of {sorted(FACT_TABLES)}")

    key_cols = FACT_TABLES[table]
    keys = [tuple(k) for k in keys]
    for k in keys:
        if len(k) != len(key_cols):
            raise ValueError(
                f"key arity {len(k)} does not match {table} grain {key_cols}"
            )

    want = TABLE_GRAIN[table]
    row = con.execute("SELECT grain FROM measure WHERE measure = ?", (measure,)).fetchone()
    if row is None:
        raise ValueError(f"unknown measure {measure!r}")
    if row[0] != want:
        raise ValueError(
            f"measure {measure!r} is of grain {row[0]!r}; {table} holds grain {want!r}"
        )

    spine = bucket_spine(start, end)
    if not keys:
        return []

    # The sparse rows for this window, addressed by (key..., bucket).
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


def _as_date(value) -> date:
    """SQLite hands back ISO text; PostgreSQL hands back a date."""
    return value if isinstance(value, date) else date.fromisoformat(value)
