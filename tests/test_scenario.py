"""Scenario model: flat peers, full copy, exactly one committed pointer.

No hierarchy. source_scenario_id is provenance and is never read to resolve a
value — a scenario's rows are all physically present in that scenario.
"""

import sqlite3
from datetime import date

import pytest

from planbrain.facts import scenario as sc

WORKING = 0


def _seed(con, scenario_id=WORKING):
    con.executemany(
        "INSERT INTO fact_supply_demand"
        " (sku_id, loc_id, bucket_date, measure, scenario_id, qty)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [
            (101, 7, "2026-03-02", "gross_req", scenario_id, 250),
            (101, 7, "2026-03-03", "gross_req", scenario_id, 100),
            (102, 7, "2026-03-02", "net_req", scenario_id, 40),
        ],
    )
    con.execute(
        "INSERT INTO fact_capacity"
        " (resource_id, bucket_date, measure, scenario_id, qty)"
        " VALUES (?, ?, ?, ?, ?)",
        (5, "2026-03-02", "capacity_load_hours", scenario_id, 16),
    )


def test_working_scenario_exists_and_is_open(con):
    row = con.execute(
        "SELECT name, status, frozen_at, source_scenario_id FROM scenario WHERE scenario_id = 0"
    ).fetchone()
    assert row == ("working", "open", None, None)


def test_copy_is_a_full_physical_copy(con):
    _seed(con)
    new_id = sc.copy_scenario(con, source_scenario_id=WORKING, name="what-if march")

    rows = con.execute(
        "SELECT count(*) FROM fact_supply_demand WHERE scenario_id = ?", (new_id,)
    ).fetchone()[0]
    assert rows == 3, "every row must be present in the copy, not inherited"

    caps = con.execute(
        "SELECT count(*) FROM fact_capacity WHERE scenario_id = ?", (new_id,)
    ).fetchone()[0]
    assert caps == 1, "copy must span every fact grain, not just supply/demand"


def test_copy_is_frozen_against_later_writes_to_source(con):
    """The whole point of full copy: reopening a what-if shows what it showed."""
    _seed(con)
    new_id = sc.copy_scenario(con, source_scenario_id=WORKING, name="what-if")

    con.execute(
        "UPDATE fact_supply_demand SET qty = 999"
        " WHERE scenario_id = ? AND sku_id = 101 AND bucket_date = '2026-03-02'"
        " AND measure = 'gross_req'",
        (WORKING,),
    )

    qty = con.execute(
        "SELECT qty FROM fact_supply_demand WHERE scenario_id = ? AND sku_id = 101"
        " AND bucket_date = '2026-03-02' AND measure = 'gross_req'",
        (new_id,),
    ).fetchone()[0]
    assert qty == 250, "new actuals in working must not leak into a copied scenario"


def test_source_scenario_id_is_recorded_as_provenance(con):
    _seed(con)
    new_id = sc.copy_scenario(con, source_scenario_id=WORKING, name="what-if")
    assert con.execute(
        "SELECT source_scenario_id FROM scenario WHERE scenario_id = ?", (new_id,)
    ).fetchone()[0] == WORKING


def test_commit_creates_a_frozen_snapshot_and_leaves_working_open(con):
    _seed(con)
    committed_id = sc.commit_scenario(con, source_scenario_id=WORKING, name="commit w09")

    assert committed_id != WORKING, "committing must not flag working itself"

    status, frozen_at = con.execute(
        "SELECT status, frozen_at FROM scenario WHERE scenario_id = ?", (committed_id,)
    ).fetchone()
    assert status == "committed"
    assert frozen_at is not None

    assert con.execute(
        "SELECT status, frozen_at FROM scenario WHERE scenario_id = 0"
    ).fetchone() == ("open", None)


def test_drift_between_working_and_commit_is_queryable(con):
    """The sellable question: how far has the plan moved since we committed?"""
    _seed(con)
    committed_id = sc.commit_scenario(con, source_scenario_id=WORKING, name="commit w09")
    con.execute(
        "UPDATE fact_supply_demand SET qty = 300"
        " WHERE scenario_id = 0 AND sku_id = 101 AND bucket_date = '2026-03-02'"
        " AND measure = 'gross_req'"
    )

    drift = con.execute(
        "SELECT w.qty - c.qty FROM fact_supply_demand w"
        " JOIN fact_supply_demand c USING (sku_id, loc_id, bucket_date, measure)"
        " WHERE w.scenario_id = 0 AND c.scenario_id = ?"
        "   AND w.sku_id = 101 AND w.bucket_date = '2026-03-02' AND w.measure = 'gross_req'",
        (committed_id,),
    ).fetchone()[0]
    assert drift == 50


def test_only_one_scenario_may_be_committed(con):
    _seed(con)
    sc.commit_scenario(con, source_scenario_id=WORKING, name="commit w09")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO scenario (scenario_id, name, created_at, status)"
            " VALUES (99, 'sneaky', '2026-03-02', 'committed')"
        )


def test_commit_supersedes_the_previous_commit(con):
    _seed(con)
    first = sc.commit_scenario(con, source_scenario_id=WORKING, name="commit w09")
    second = sc.commit_scenario(con, source_scenario_id=WORKING, name="commit w10")

    assert con.execute(
        "SELECT status FROM scenario WHERE scenario_id = ?", (first,)
    ).fetchone()[0] == "archived"
    assert con.execute(
        "SELECT count(*) FROM scenario WHERE status = 'committed'"
    ).fetchone()[0] == 1
    assert con.execute(
        "SELECT scenario_id FROM scenario WHERE status = 'committed'"
    ).fetchone()[0] == second
