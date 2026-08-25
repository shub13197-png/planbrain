"""read_facts() is the chokepoint that makes absent-row-means-zero unavoidable.

Every one of these tests exists because the equivalent hand-written inner join
would return a plausible, wrong answer instead of failing.
"""

from datetime import date

import pytest

from planbrain.facts.access import bucket_spine, read_facts

MARCH2 = date(2026, 3, 2)
MARCH6 = date(2026, 3, 6)


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


def test_bucket_spine_is_inclusive_of_both_ends():
    assert bucket_spine(MARCH2, MARCH6) == [date(2026, 3, d) for d in (2, 3, 4, 5, 6)]


def test_absent_buckets_come_back_as_zero(seeded):
    rows = read_facts(
        seeded, "fact_supply_demand",
        scenario_id=0, measure="gross_req", start=MARCH2, end=MARCH6, keys=[(101, 7)],
    )
    assert [r.qty for r in rows] == [250, 0, 0, 100, 0]
    assert [r.bucket_date for r in rows] == bucket_spine(MARCH2, MARCH6)


def test_mean_over_the_window_is_not_biased_high(seeded):
    """The exact bug the sparse rule guards: 175 (sparse) vs 70 (correct)."""
    rows = read_facts(
        seeded, "fact_supply_demand",
        scenario_id=0, measure="gross_req", start=MARCH2, end=MARCH6, keys=[(101, 7)],
    )
    assert sum(r.qty for r in rows) / len(rows) == 70


def test_entity_with_no_rows_at_all_still_yields_a_full_row_of_zeros(seeded):
    rows = read_facts(
        seeded, "fact_supply_demand",
        scenario_id=0, measure="gross_req", start=MARCH2, end=MARCH6, keys=[(999, 7)],
    )
    assert len(rows) == 5
    assert all(r.qty == 0 for r in rows)


def test_reads_are_scoped_to_one_scenario(seeded):
    seeded.execute(
        "INSERT INTO scenario (scenario_id, name, created_at, status)"
        " VALUES (4, 'other', '2026-03-01', 'open')"
    )
    seeded.execute(
        "INSERT INTO fact_supply_demand"
        " (sku_id, loc_id, bucket_date, measure, scenario_id, qty)"
        " VALUES (101, 7, '2026-03-02', 'gross_req', 4, 5000)"
    )
    rows = read_facts(
        seeded, "fact_supply_demand",
        scenario_id=0, measure="gross_req", start=MARCH2, end=MARCH6, keys=[(101, 7)],
    )
    assert rows[0].qty == 250, "no cross-scenario bleed, and no parent chain to walk"


def test_capacity_grain_reads_through_the_same_accessor(con):
    con.execute(
        "INSERT INTO fact_capacity (resource_id, bucket_date, measure, scenario_id, qty)"
        " VALUES (5, '2026-03-03', 'capacity_load_hours', 0, 16)"
    )
    rows = read_facts(
        con, "fact_capacity",
        scenario_id=0, measure="capacity_load_hours", start=MARCH2, end=MARCH6, keys=[(5,)],
    )
    assert [r.qty for r in rows] == [0, 16, 0, 0, 0]
    assert rows[0].keys == (5,)


def test_measure_must_belong_to_the_table_grain(con):
    """A capacity measure in the supply/demand table is a wrong number, not an error."""
    with pytest.raises(ValueError, match="grain"):
        read_facts(
            con, "fact_supply_demand",
            scenario_id=0, measure="capacity_load_hours",
            start=MARCH2, end=MARCH6, keys=[(101, 7)],
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
