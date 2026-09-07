"""The part master has to survive closing the window.

**The database had six tables and none of them held a part.** `scenario`,
`measure`, `fact_supply_demand`, `plan_override`, `fact_capacity`,
`fact_fleet` -- facts and overrides, and nothing else. `populate()` wrote facts
only. Lead times, lot sizes, the BOM, locations, resources and the working
calendar lived in `Session.demo`, which is process-local and set by exactly one
method.

So a planner could import a year of history, close the window, reopen it, and be
told *no dataset loaded* while their demand rows sat in the file. The work was on
disk and unreachable, which from their side is the same screen as having lost it.
`docs/status.md` carried it as open item 1.

These tests are the falsifiable form of the fix: a second session, on a
connection that has never seen the first one's objects, must be able to plan.
"""

import sqlite3
from pathlib import Path

import pytest

from planbrain.backend import api
from planbrain.demo import build_demo, populate
from planbrain.facts.master import load_master, store_master

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def written(tmp_path):
    """A database with one demo dataset stored in it, then closed."""
    path = tmp_path / "planning.db"
    first = api.session(str(path))
    demo = build_demo(seed=7)
    with first.con:
        populate(first.con, demo)
        store_master(first.con, demo)
    first.con.close()
    return path, demo


# --------------------------------------------------------------------------
# it comes back
# --------------------------------------------------------------------------

def test_a_second_session_can_plan_without_importing_again(written):
    """The whole point, and the screen the user actually saw.

    Not "the rows are in the file" -- that was already true and did not help.
    A relaunched backend must answer `plan.run` rather than refuse it.
    """
    path, _ = written

    second = api.session(str(path))
    assert second.demo is not None, (
        "a second session opened the file and still has no dataset, so every "
        "method that gates on it will refuse -- which is the bug"
    )
    result = api.plan_run(second)
    second.con.close()

    assert result["capacity"]["resources"], "the relaunched plan produced no capacity load"


def test_the_part_master_survives_exactly(written):
    """Lead times and lot sizes decide what is ordered and when. A part that
    comes back with a different lead time is worse than one that does not come
    back at all, because nothing will say so."""
    path, demo = written

    con = sqlite3.connect(str(path))
    restored = load_master(con)
    con.close()

    assert restored is not None
    assert len(restored.parts) == len(demo.parts)
    before = {p.sku_id: p for p in demo.parts}
    for part in restored.parts:
        was = before[part.sku_id]
        assert part.name == was.name
        assert part.lead_time_days == was.lead_time_days
        assert part.lot_qty == pytest.approx(was.lot_qty)
        assert part.lot_policy == was.lot_policy
        assert part.safety_stock == pytest.approx(was.safety_stock)
        assert part.unit_cost == pytest.approx(was.unit_cost)


def test_the_bom_and_locations_survive(written):
    path, demo = written
    con = sqlite3.connect(str(path))
    restored = load_master(con)
    con.close()

    assert len(restored.bom) == len(demo.bom)
    assert {(e.parent_sku_id, e.child_sku_id) for e in restored.bom} == \
           {(e.parent_sku_id, e.child_sku_id) for e in demo.bom}
    assert {loc.loc_id for loc in restored.locations} == {loc.loc_id for loc in demo.locations}


def test_the_calendar_survives(written):
    """Everything needing a seasonal period derives it from the calendar. A
    six-day week restored as seven puts every weekly pattern out of phase."""
    path, demo = written
    con = sqlite3.connect(str(path))
    restored = load_master(con)
    con.close()
    assert restored.calendar.working_weekdays == demo.calendar.working_weekdays


def test_the_horizon_survives(written):
    """The plan runs between these dates. Restoring them wrong plans the wrong
    window and nothing downstream would notice."""
    path, demo = written
    con = sqlite3.connect(str(path))
    restored = load_master(con)
    con.close()
    assert restored.history_start == demo.history_start
    assert restored.history_end == demo.history_end
    assert restored.horizon_start == demo.horizon_start
    assert restored.horizon_end == demo.horizon_end


# --------------------------------------------------------------------------
# and it does not invent one
# --------------------------------------------------------------------------

def test_an_empty_database_restores_nothing(tmp_path):
    """A fresh install has no dataset and must say so, rather than hand back an
    empty one that every engine would then plan against."""
    path = tmp_path / "empty.db"
    state = api.session(str(path))
    assert state.demo is None
    assert load_master(state.con) is None
    state.con.close()


def test_storing_twice_replaces_rather_than_duplicates(written):
    """Importing again is a normal thing to do. Two part masters in one file
    would double every requirement."""
    path, demo = written
    con = sqlite3.connect(str(path))
    with con:
        store_master(con, demo)
    restored = load_master(con)
    con.close()
    assert len(restored.parts) == len(demo.parts)
