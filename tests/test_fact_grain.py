"""Grain invariants for the planning fact table.

The grain (sku_id, loc_id, bucket_date, measure, scenario_id) is the load-bearing
contract of the whole system: every planning number is addressed by exactly these
five columns. A duplicate row at that address means two answers to one question,
which is the failure mode CLAUDE.md calls "confidently wrong numbers".
"""

import sqlite3
from pathlib import Path

import pytest

SCHEMA = Path(__file__).resolve().parents[1] / "planbrain" / "facts" / "schema.sql"

ROW = (101, 7, "2026-03-02", "gross_req", 0, 250)


def _db():
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA.read_text())
    return con


def _insert(con, row=ROW):
    con.execute(
        "INSERT INTO fact_supply_demand"
        " (sku_id, loc_id, bucket_date, measure, scenario_id, qty)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        row,
    )


def test_duplicate_grain_is_rejected():
    con = _db()
    _insert(con)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(con)


def test_same_address_different_measure_coexist():
    """Only the full five-column address must be unique, not any prefix of it."""
    con = _db()
    _insert(con)
    _insert(con, ROW[:3] + ("net_req",) + ROW[4:])
    assert con.execute("SELECT count(*) FROM fact_supply_demand").fetchone()[0] == 2


def test_unknown_measure_is_rejected():
    """The measure vocabulary is closed — typos must not silently create a series."""
    con = _db()
    with pytest.raises(sqlite3.IntegrityError):
        _insert(con, ROW[:3] + ("gross_reqs",) + ROW[4:])


def test_unknown_scenario_is_rejected():
    con = _db()
    with pytest.raises(sqlite3.IntegrityError):
        _insert(con, ROW[:4] + (99,) + ROW[5:])


def test_qty_may_be_negative():
    """Projected on-hand goes negative on a shortage; that is a real plan number."""
    con = _db()
    _insert(con, (101, 7, "2026-03-02", "on_hand_open", 0, -40))
    assert con.execute("SELECT qty FROM fact_supply_demand").fetchone()[0] == -40
