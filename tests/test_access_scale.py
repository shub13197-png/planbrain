"""`read_facts` against a portfolio bigger than the demo's.

**Found on real data, not here.** Running the service backtest over the UCI
Online Retail II log -- 2947 stock codes that clear an eligibility bar -- failed
inside `read_facts`:

    sqlite3.OperationalError: Expression tree is too large (maximum depth 1000)

The accessor built one `(sku_id = ? AND loc_id = ?)` clause per key and joined
them with OR, so the query's parse tree grew with the portfolio and SQLite
refused it. The ceiling is around 500 keys, and the demo asks for 222 -- which
is why two hundred passing tests never went near it.

That number is not a benchmark detail. A small manufacturer with three thousand
part numbers is entirely ordinary, and until this was fixed the application
could not read a plan for one.

The tests below assert the **rule** -- any number of keys reads correctly -- at a
size comfortably past the old ceiling, rather than pinning the ceiling itself.
"""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from planbrain.facts.access import Fact, read_facts, write_facts

SCHEMA = Path(__file__).resolve().parents[1] / "planbrain" / "facts" / "schema.sql"
TABLE = "fact_supply_demand"

#: Past the old ~500-key ceiling by enough that a partial fix fails here, and
#: small enough to stay a fast test. The rule is "any number of keys"; this is
#: the size that demonstrates it, not a limit being asserted.
MANY = 1500

START = date(2026, 1, 1)
DAYS = 3


@pytest.fixture
def con():
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA foreign_keys = ON")
    c.executescript(SCHEMA.read_text(encoding="utf-8"))
    yield c
    c.close()


@pytest.fixture
def wide(con):
    """One measure written for MANY distinct SKUs at one location."""
    facts = [
        Fact(keys=(sku, 1), bucket_date=START + timedelta(days=day), qty=float(sku + day))
        for sku in range(1, MANY + 1)
        for day in range(DAYS)
    ]
    write_facts(con, TABLE, scenario_id=0, measure="demand_actual", facts=facts)
    return con


def test_reading_a_portfolio_larger_than_the_query_limit_works(wide):
    """The regression. This raised OperationalError before the accessor chunked."""
    keys = [(sku, 1) for sku in range(1, MANY + 1)]
    rows = read_facts(
        wide, TABLE, scenario_id=0, measure="demand_actual",
        start=START, end=START + timedelta(days=DAYS - 1), keys=keys,
    )
    assert len(rows) == MANY * DAYS


def test_every_value_survives_the_chunking(wide):
    """Chunking must not drop, duplicate or misalign a row.

    Asserts the values themselves, not the count: a chunk boundary that returned
    the right number of rows with the wrong quantities against them would pass a
    count check and be worse than a crash.
    """
    keys = [(sku, 1) for sku in range(1, MANY + 1)]
    rows = read_facts(
        wide, TABLE, scenario_id=0, measure="demand_actual",
        start=START, end=START + timedelta(days=DAYS - 1), keys=keys,
    )
    for row in rows:
        expected = row.keys[0] + (row.bucket_date - START).days
        assert row.qty == float(expected), row


def test_the_order_is_keys_then_buckets_across_chunk_boundaries(wide):
    """The contract `read_facts` documents, which chunking could quietly break."""
    keys = [(sku, 1) for sku in range(1, MANY + 1)]
    rows = read_facts(
        wide, TABLE, scenario_id=0, measure="demand_actual",
        start=START, end=START + timedelta(days=DAYS - 1), keys=keys,
    )
    assert [r.keys for r in rows] == [k for k in keys for _ in range(DAYS)]
    assert [r.bucket_date for r in rows[:DAYS]] == [
        START + timedelta(days=d) for d in range(DAYS)
    ]


def test_absent_keys_still_densify_to_zero_at_scale(wide):
    """The sparse rule has to hold across chunks too: absent means zero."""
    keys = [(sku, 1) for sku in range(1, MANY + 1)] + [(999_999, 1)]
    rows = read_facts(
        wide, TABLE, scenario_id=0, measure="demand_actual",
        start=START, end=START + timedelta(days=DAYS - 1), keys=keys,
    )
    missing = [r for r in rows if r.keys == (999_999, 1)]
    assert len(missing) == DAYS
    assert all(r.qty == 0 for r in missing)
