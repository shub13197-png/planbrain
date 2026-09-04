"""Shortages: the buckets where the plan runs the stock negative.

**The second thing the plan computed and never showed.** `netreq.core.plan_item`
builds a `PlanException` for every projected shortage and every past-due
release, `netreq.run` returns only the count of rows it wrote, and the whole list
is dropped on the floor -- the only reader anywhere is `tools/make_examples.py`.

That is the screen a planner opens planning software *for*. "What is going to go
wrong, and when" outranks "here is the plan" in every conversation a small
manufacturer has, because the plan is only interesting where it fails.

`projected_on_hand` is a written measure, so the shortages are already in the
fact table and can be read back rather than plumbed through a changed return
type. Past-due releases are **not** derivable from facts -- `_offset` puts them
in bucket zero, indistinguishable from an order that legitimately starts there
-- so they are not reported here rather than guessed at.
"""

from datetime import date, timedelta

import pytest

from planbrain import alerts, netreq
from planbrain.demo import build_demo, populate
from planbrain.facts.access import Fact, write_facts

TABLE = "fact_supply_demand"


@pytest.fixture
def planned(con):
    demo = build_demo(seed=7)
    populate(con, demo)
    netreq.run(con, demo)
    return demo


def test_a_negative_projection_is_reported_as_a_shortage(con):
    """The rule, on a plan written by hand so the answer is arithmetic."""
    start = date(2026, 7, 1)
    demo = _one_part_demo(start, periods=4)
    _write(con, start, projected=[10.0, -5.0, -2.0, 3.0])

    found = alerts.shortages(con, demo)

    assert [(s.bucket_date, s.short_by) for s in found] == [
        (start + timedelta(days=1), 5.0),
        (start + timedelta(days=2), 2.0),
    ]


def test_a_shortage_is_reported_as_a_positive_quantity_short(con):
    """`projected_on_hand` is negative; "short by" is what a planner reads.

    A screen showing "-5" next to a shortage invites the reading that stock is
    minus five, which is not a thing. The sign is flipped once, here, rather
    than in every caller that displays it.
    """
    start = date(2026, 7, 1)
    demo = _one_part_demo(start, periods=2)
    _write(con, start, projected=[-12.5, 0.0])

    assert alerts.shortages(con, demo)[0].short_by == 12.5


def test_a_plan_that_never_goes_negative_reports_nothing(con):
    """And reports it as an empty list, not as an error."""
    start = date(2026, 7, 1)
    demo = _one_part_demo(start, periods=3)
    _write(con, start, projected=[4.0, 2.0, 9.0])

    assert alerts.shortages(con, demo) == []


def test_shortages_come_back_soonest_first(con):
    """A planner works the earliest date; it is the one still fixable."""
    start = date(2026, 7, 1)
    demo = _one_part_demo(start, periods=4)
    _write(con, start, projected=[2.0, -8.0, -1.0, -4.0])

    found = alerts.shortages(con, demo)
    assert [s.bucket_date for s in found] == [
        start + timedelta(days=i) for i in (1, 2, 3)
    ]


def test_every_shortage_names_the_item(con):
    """A stock code alone cannot be chased."""
    start = date(2026, 7, 1)
    demo = _one_part_demo(start, periods=2)
    _write(con, start, projected=[-3.0, 1.0])

    found = alerts.shortages(con, demo)
    assert [(s.name, s.location) for s in found] == [("Widget", "Plant")]
    assert all(s.short_by > 0 for s in found)


def test_the_demo_material_plan_projects_no_shortage_at_all(con, planned):
    """And that is the correct answer, not an empty screen.

    `netreq` plans an order for every requirement it can reach, so on the demo
    the projected balance never goes negative: the material plan is feasible.
    What does not fit is *capacity* -- 134 overloaded days -- which is a
    different question and a different screen.

    Asserted rather than left as an accident, because it is the reason the
    shortage list needs to distinguish "nothing is going to go wrong" from
    "nothing has been planned". Those look identical and mean opposite things.
    """
    assert alerts.shortages(con, planned) == []
    summary = alerts.shortage_summary(con, planned)
    assert summary["total"] == 0
    assert summary["first_date"] is None


def test_the_worst_shortage_is_reachable_without_reading_all_of_them(con):
    """`limit` bounds what a screen renders; `total` says what it did not show."""
    start = date(2026, 7, 1)
    demo = _one_part_demo(start, periods=6)
    _write(con, start, projected=[-1.0, -9.0, -3.0, -40.0, -2.0, 5.0])

    summary = alerts.shortage_summary(con, demo, limit=3)
    assert summary["total"] == 5
    assert summary["shown"] == 3
    assert len(summary["shortages"]) == 3
    assert summary["items_affected"] == 1
    assert summary["units_short"] == 55.0
    assert summary["first_date"] == start.isoformat()

    # Deepest first when ranked by size, so a truncated screen shows the ones
    # that matter rather than the ones that happen to fall earliest.
    by_size = alerts.shortage_summary(con, demo, limit=3, worst_first=True)
    assert [s["short_by"] for s in by_size["shortages"]] == [40.0, 9.0, 3.0]
    assert by_size["total"] == 5, "the totals describe the list, not the page"


# --------------------------------------------------------------------------
# past-due releases: the signal that actually fires
# --------------------------------------------------------------------------

def test_the_demo_plan_has_past_due_releases_even_though_it_has_no_shortages(
    con, planned
):
    """The reason this measure had to exist.

    `netreq` dates a planned receipt at the bucket the material is needed, so
    `projected_on_hand` does not go negative when a plan is infeasible -- the
    infeasibility comes out as a release that had to be pulled to bucket zero
    because its true release date was before the horizon opened.

    A "what is going to go wrong" screen built on negative projections alone
    therefore reports "nothing runs out" on a plan with hundreds of overdue
    orders in it, which is worse than having no screen at all. Found by running
    the planner over 2,947 real stock codes and getting zero shortages back.
    """
    assert alerts.shortages(con, planned) == []
    overdue = alerts.past_due(con, planned)
    assert overdue, "the demo plan is expected to carry past-due releases"
    assert all(o.qty > 0 and o.name for o in overdue)


def test_past_due_is_reported_soonest_needed_first(con, planned):
    """Ordered by when the material is needed, which is when it hurts."""
    overdue = alerts.past_due(con, planned)
    assert [o.needed_on for o in overdue] == sorted(o.needed_on for o in overdue)


def test_the_risk_summary_carries_both_kinds_and_says_which(con, planned):
    """One screen, two failure modes, never merged into a single count."""
    summary = alerts.risk_summary(con, planned, limit=5)

    assert summary["shortages"]["total"] == 0
    assert summary["past_due"]["total"] > 0
    assert summary["past_due"]["shown"] <= 5
    assert summary["past_due"]["units"] > 0
    # The two are different questions -- "we will run out" and "we are already
    # late" -- and a screen that added them would report a number meaning
    # neither.
    assert "total" not in summary


def test_a_plan_with_no_lead_time_has_nothing_past_due(con):
    """The boundary: past-due exists only because a release predates the horizon."""
    start = date(2026, 7, 1)
    demo = _one_part_demo(start, periods=3)
    write_facts(
        con, TABLE, scenario_id=0, measure="past_due_release",
        facts=[Fact(keys=(1, 1), bucket_date=start, qty=0.0)],
    )
    assert alerts.past_due(con, demo) == []


# --------------------------------------------------------------------------
# the method the interface calls
# --------------------------------------------------------------------------

def test_the_backend_refuses_to_report_shortages_with_no_data_loaded(con):
    """The message a user meets first should name the next action."""
    from planbrain.backend.api import Session, plan_risks

    with pytest.raises(ValueError, match="no dataset loaded"):
        plan_risks(Session(con=con, demo=None))


def test_the_backend_returns_a_shortage_page_the_screen_can_render(con):
    """Shape, including the totals a truncated screen has to quote."""
    from planbrain.backend.api import Session, plan_risks

    start = date(2026, 7, 1)
    demo = _one_part_demo(start, periods=3)
    _write(con, start, projected=[-2.0, -7.0, 1.0])

    r = plan_risks(Session(con=con, demo=demo), limit=1)["shortages"]
    assert r["total"] == 2 and r["shown"] == 1
    assert r["units_short"] == 9.0
    assert r["shortages"][0]["name"] == "Widget"
    # Dates cross the pipe as strings; a date object would not survive json.dumps
    # without the default= fallback quietly stringifying it somewhere else.
    assert isinstance(r["shortages"][0]["bucket_date"], str)


# --------------------------------------------------------------------------

def _one_part_demo(start, *, periods):
    from types import SimpleNamespace

    from planbrain.demo.generate import Location, Part

    return SimpleNamespace(
        parts=[Part(sku_id=1, name="Widget", level="finished", lead_time_days=0,
                    safety_stock=0.0, lot_policy="lot_for_lot", lot_qty=0.0)],
        locations=[Location(loc_id=1, name="Plant", kind="plant")],
        horizon_start=start,
        horizon_end=start + timedelta(days=periods - 1),
    )


def _write(con, start, *, projected):
    write_facts(
        con, TABLE, scenario_id=0, measure="projected_on_hand",
        facts=[
            Fact(keys=(1, 1), bucket_date=start + timedelta(days=i), qty=qty)
            for i, qty in enumerate(projected)
        ],
    )
