"""read_facts() and write_facts(): the chokepoint that makes the sparse rule
and the frozen-scenario rule unavoidable.

Every one of these tests exists because the equivalent hand-written SQL would
succeed and return a plausible, wrong answer instead of failing.
"""

from datetime import date

import pytest

from planbrain.facts import scenario as sc
from planbrain.facts.access import (
    Fact,
    FrozenScenarioError,
    bucket_spine,
    read_facts,
    write_facts,
)

MARCH2 = date(2026, 3, 2)
MARCH6 = date(2026, 3, 6)
SPINE = bucket_spine(MARCH2, MARCH6)


@pytest.fixture
def seeded(con):
    con.executemany(
        "INSERT INTO fact_supply_demand"
        " (sku_id, loc_id, bucket_date, measure, scenario_id, qty)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [
            (101, 7, "2026-03-02", "gross_req", 0, 250),
            (101, 7, "2026-03-05", "gross_req", 0, 100),
        ],
    )
    return con


def _series(con, keys=(101, 7), measure="gross_req", scenario_id=0):
    return [
        r.qty
        for r in read_facts(
            con, "fact_supply_demand",
            scenario_id=scenario_id, measure=measure,
            start=MARCH2, end=MARCH6, keys=[keys],
        )
    ]


def _stored(con, table="fact_supply_demand"):
    return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


# --------------------------------------------------------------------------
# read: densify
# --------------------------------------------------------------------------

def test_bucket_spine_is_inclusive_of_both_ends():
    assert SPINE == [date(2026, 3, d) for d in (2, 3, 4, 5, 6)]


def test_absent_buckets_come_back_as_zero(seeded):
    rows = read_facts(
        seeded, "fact_supply_demand",
        scenario_id=0, measure="gross_req", start=MARCH2, end=MARCH6, keys=[(101, 7)],
    )
    assert [r.qty for r in rows] == [250, 0, 0, 100, 0]
    assert [r.bucket_date for r in rows] == SPINE


def test_mean_over_the_window_is_not_biased_high(seeded):
    """The exact bug the sparse rule guards: 175 (sparse) vs 70 (correct)."""
    assert sum(_series(seeded)) / 5 == 70


def test_entity_with_no_rows_at_all_still_yields_a_full_row_of_zeros(seeded):
    assert _series(seeded, keys=(999, 7)) == [0, 0, 0, 0, 0]


def test_reads_are_scoped_to_one_scenario(seeded):
    other = sc.copy_scenario(seeded, source_scenario_id=0, name="other")
    write_facts(
        seeded, "fact_supply_demand", scenario_id=other, measure="gross_req",
        facts=[Fact((101, 7), MARCH2, 5000)],
    )
    assert _series(seeded)[0] == 250, "no cross-scenario bleed, and no parent chain"


def test_capacity_grain_reads_through_the_same_accessor(con):
    write_facts(
        con, "fact_capacity", scenario_id=0, measure="capacity_load_hours",
        facts=[Fact((5,), date(2026, 3, 3), 16)],
    )
    rows = read_facts(
        con, "fact_capacity",
        scenario_id=0, measure="capacity_load_hours", start=MARCH2, end=MARCH6, keys=[(5,)],
    )
    assert [r.qty for r in rows] == [0, 16, 0, 0, 0]
    assert rows[0].keys == (5,)


# --------------------------------------------------------------------------
# write: sparsify
# --------------------------------------------------------------------------

def test_write_read_round_trip(con):
    written = write_facts(
        con, "fact_supply_demand", scenario_id=0, measure="gross_req",
        facts=[Fact((101, 7), b, q) for b, q in zip(SPINE, [250, 0, 0, 100, 0])],
    )
    assert written == 2
    assert _series(con) == [250, 0, 0, 100, 0]


def test_zeros_are_not_materialised(con):
    """The mirror of densify-on-read. A dense series in must not store zeros."""
    write_facts(
        con, "fact_supply_demand", scenario_id=0, measure="gross_req",
        facts=[Fact((101, 7), b, q) for b, q in zip(SPINE, [250, 0, 0, 100, 0])],
    )
    assert _stored(con) == 2


def test_writing_zero_clears_a_previous_non_zero(con):
    """Re-running an engine must not leave last run's value where this run says zero.

    Without the delete, an upsert-only write would leave 250 sitting in a bucket
    the new plan computed as empty -- and read_facts would hand it back with a
    straight face.
    """
    write_facts(
        con, "fact_supply_demand", scenario_id=0, measure="gross_req",
        facts=[Fact((101, 7), MARCH2, 250)],
    )
    write_facts(
        con, "fact_supply_demand", scenario_id=0, measure="gross_req",
        facts=[Fact((101, 7), MARCH2, 0)],
    )
    assert _series(con) == [0, 0, 0, 0, 0]
    assert _stored(con) == 0


def test_write_is_idempotent(con):
    facts = [Fact((101, 7), b, q) for b, q in zip(SPINE, [250, 0, 0, 100, 0])]
    write_facts(con, "fact_supply_demand", scenario_id=0, measure="gross_req", facts=facts)
    before = con.execute("SELECT * FROM fact_supply_demand ORDER BY bucket_date").fetchall()
    write_facts(con, "fact_supply_demand", scenario_id=0, measure="gross_req", facts=facts)
    after = con.execute("SELECT * FROM fact_supply_demand ORDER BY bucket_date").fetchall()
    assert before == after


def test_write_overwrites_rather_than_duplicating(con):
    write_facts(
        con, "fact_supply_demand", scenario_id=0, measure="gross_req",
        facts=[Fact((101, 7), MARCH2, 250)],
    )
    write_facts(
        con, "fact_supply_demand", scenario_id=0, measure="gross_req",
        facts=[Fact((101, 7), MARCH2, 300)],
    )
    assert _stored(con) == 1
    assert _series(con)[0] == 300


def test_write_accepts_iso_date_strings(con):
    """Importers hand over strings; the accessor should not force a conversion dance."""
    write_facts(
        con, "fact_supply_demand", scenario_id=0, measure="gross_req",
        facts=[Fact((101, 7), "2026-03-02", 250)],
    )
    assert _series(con)[0] == 250


# --------------------------------------------------------------------------
# write: frozen scenarios
# --------------------------------------------------------------------------

def test_write_to_a_frozen_scenario_is_refused(con):
    """A snapshot you can still edit is not a snapshot, and drift stops meaning anything."""
    committed = sc.commit_scenario(con, source_scenario_id=0, name="commit w09")
    with pytest.raises(FrozenScenarioError) as exc:
        write_facts(
            con, "fact_supply_demand", scenario_id=committed, measure="gross_req",
            facts=[Fact((101, 7), MARCH2, 999)],
        )
    message = str(exc.value)
    assert str(committed) in message
    assert "commit w09" in message
    assert "frozen at" in message


def test_committing_still_lands_its_own_rows(con):
    """The snapshot is frozen after the copy, not before, or it would be empty."""
    write_facts(
        con, "fact_supply_demand", scenario_id=0, measure="gross_req",
        facts=[Fact((101, 7), MARCH2, 250)],
    )
    committed = sc.commit_scenario(con, source_scenario_id=0, name="commit w09")
    assert _series(con, scenario_id=committed)[0] == 250
    assert con.execute(
        "SELECT frozen_at FROM scenario WHERE scenario_id = ?", (committed,)
    ).fetchone()[0] is not None


def test_working_stays_writable_after_a_commit(con):
    sc.commit_scenario(con, source_scenario_id=0, name="commit w09")
    write_facts(
        con, "fact_supply_demand", scenario_id=0, measure="gross_req",
        facts=[Fact((101, 7), MARCH2, 300)],
    )
    assert _series(con)[0] == 300


def test_copying_into_a_frozen_scenario_is_refused(con):
    """The bulk INSERT...SELECT path shares the guard rather than routing around it."""
    from planbrain.facts.scenario import _copy_facts

    committed = sc.commit_scenario(con, source_scenario_id=0, name="commit w09")
    with pytest.raises(FrozenScenarioError):
        _copy_facts(con, 0, committed)


def test_write_to_unknown_scenario_is_refused(con):
    with pytest.raises(ValueError, match="unknown scenario_id"):
        write_facts(
            con, "fact_supply_demand", scenario_id=42, measure="gross_req",
            facts=[Fact((101, 7), MARCH2, 1)],
        )


# --------------------------------------------------------------------------
# guards shared by both directions
# --------------------------------------------------------------------------

def test_measure_must_belong_to_the_table_grain(con):
    """A capacity measure in the supply/demand table is a wrong number, not an error."""
    with pytest.raises(ValueError, match="grain"):
        read_facts(
            con, "fact_supply_demand",
            scenario_id=0, measure="capacity_load_hours",
            start=MARCH2, end=MARCH6, keys=[(101, 7)],
        )


def test_write_also_checks_the_grain(con):
    with pytest.raises(ValueError, match="grain"):
        write_facts(
            con, "fact_capacity", scenario_id=0, measure="gross_req",
            facts=[Fact((5,), MARCH2, 1)],
        )


def test_the_fleet_grain_now_has_its_measures(con):
    """Reserved with an empty measure set from item 2 until item 9 defined the
    ledger. They are flows: the cumulative is summed on read, never stored."""
    grains = dict(con.execute(
        "SELECT measure, grain FROM measure WHERE grain = 'fleet'"
    ))
    assert set(grains) == {"long_haul_km", "total_km", "trips_assigned"}

    write_facts(
        con, "fact_fleet", scenario_id=0, measure="long_haul_km",
        facts=[Fact((1,), MARCH2, 640)],
    )
    rows = read_facts(
        con, "fact_fleet", scenario_id=0, measure="long_haul_km",
        start=MARCH2, end=MARCH6, keys=[(1,)],
    )
    assert [r.qty for r in rows] == [640, 0, 0, 0, 0]


def test_a_grain_with_no_measures_still_reports_itself_as_reserved(con):
    """The mechanism that protected fact_fleet for seven build items.

    Exercised by emptying the grain, because every grain now has measures. If
    this ever stopped working, the next reserved table would silently accept
    writes under a guessed vocabulary.
    """
    con.execute("DELETE FROM measure WHERE grain = 'fleet'")
    with pytest.raises(ValueError, match="reserved"):
        write_facts(
            con, "fact_fleet", scenario_id=0, measure="long_haul_km",
            facts=[Fact((1,), MARCH2, 640)],
        )


def test_unknown_table_is_rejected(con):
    with pytest.raises(ValueError, match="fact table"):
        read_facts(
            con, "fact_supply_demand; DROP TABLE scenario",
            scenario_id=0, measure="gross_req", start=MARCH2, end=MARCH6, keys=[(1, 1)],
        )


def test_key_arity_must_match_the_grain(con):
    with pytest.raises(ValueError, match="arity"):
        read_facts(
            con, "fact_capacity",
            scenario_id=0, measure="capacity_load_hours",
            start=MARCH2, end=MARCH6, keys=[(5, 7)],
        )


def test_write_key_arity_must_match_the_grain(con):
    with pytest.raises(ValueError, match="arity"):
        write_facts(
            con, "fact_capacity", scenario_id=0, measure="capacity_load_hours",
            facts=[Fact((5, 7), MARCH2, 1)],
        )
