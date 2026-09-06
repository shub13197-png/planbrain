"""Letting the planner disagree, and making the disagreement legible.

A planner knows things the data does not: a customer committed on the phone, a
machine is down next Tuesday, a supplier's truck is late and everyone in the
plant knows it and no system does. Until this existed the application had no way
to be told any of it, and the only controls were two growth percentages.

The mechanism is the textbook **firm planned order** and the arithmetic lives in
`planbrain/netreq/core.py`. What is tested here is the part around it: that an
override is stored with a person and a reason attached, that a planning run
cannot quietly undo one, and that the two stores cannot disagree about which
quantities are the machine's and which are a human's.
"""

from datetime import date, timedelta

import pytest

from planbrain import netreq, overrides
from planbrain.demo import build_demo, populate
from planbrain.facts.access import Fact, read_facts, write_facts

TABLE = "fact_supply_demand"
MEASURE = "firm_planned_order"


@pytest.fixture
def loaded(con):
    demo = build_demo(seed=7)
    populate(con, demo)
    return demo


def _a_planned_receipt(con, demo):
    """An (sku, loc, bucket) the plan schedules a receipt in, and its quantity.

    **A receipt bucket, not a release bucket.** A firm planned order fixes the
    date the material is *needed*; the release is then derived from it by the
    lead-time offset like any other receipt. Overriding a release date instead
    puts fixed supply in a bucket the plan was not receiving in, which adds to
    the plan rather than replacing anything -- the first version of this helper
    did exactly that and the override came out as 40,001 instead of 1.
    """
    netreq.run(con, demo)
    rows = read_facts(
        con, TABLE, scenario_id=0, measure="planned_order_receipt",
        start=demo.horizon_start, end=demo.horizon_end,
        keys=[(p.sku_id, loc.loc_id) for p in demo.parts for loc in demo.locations],
    )
    for fact in rows:
        if fact.qty:
            return fact.keys[0], fact.keys[1], fact.bucket_date, fact.qty
    raise AssertionError("the demo plan schedules no receipts at all")


# --------------------------------------------------------------------------
# storing one
# --------------------------------------------------------------------------

def test_setting_an_override_records_the_quantity_and_the_person(con, loaded):
    when = loaded.horizon_start + timedelta(days=3)
    overrides.set_override(
        con, sku_id=1001, loc_id=1, bucket_date=when, qty=500.0,
        author="priya", reason="Kapoor confirmed 500 on the phone",
    )

    stored = overrides.list_overrides(con, loaded)
    assert len(stored) == 1
    entry = stored[0]
    assert (entry.sku_id, entry.qty, entry.author) == (1001, 500.0, "priya")
    assert entry.reason == "Kapoor confirmed 500 on the phone"
    assert entry.bucket_date == when


def test_the_quantity_is_stored_once_and_only_in_the_fact_table(con, loaded):
    """Two copies of a number is two numbers.

    The provenance table carries who and why; the fact table carries how much.
    If the quantity were duplicated they could disagree, and nothing would say
    which one the plan had used.
    """
    when = loaded.horizon_start + timedelta(days=3)
    overrides.set_override(con, sku_id=1001, loc_id=1, bucket_date=when,
                           qty=500.0, author="priya", reason="phone call")

    columns = {row[1] for row in con.execute("PRAGMA table_info(plan_override)")}
    assert "qty" not in columns
    assert {"author", "reason", "created_at"} <= columns


def test_an_override_without_a_reason_is_refused(con, loaded):
    """In three weeks an unexplained override is indistinguishable from a typo."""
    when = loaded.horizon_start + timedelta(days=3)
    for bad in ("", "   "):
        with pytest.raises(ValueError, match="reason"):
            overrides.set_override(con, sku_id=1001, loc_id=1, bucket_date=when,
                                   qty=500.0, author="priya", reason=bad)


def test_an_override_without_an_author_is_refused(con, loaded):
    when = loaded.horizon_start + timedelta(days=3)
    with pytest.raises(ValueError, match="author"):
        overrides.set_override(con, sku_id=1001, loc_id=1, bucket_date=when,
                               qty=500.0, author="  ", reason="phone call")


def test_a_negative_override_is_refused(con, loaded):
    """Fixing a supply at less than nothing is not a thing a planner means."""
    when = loaded.horizon_start + timedelta(days=3)
    with pytest.raises(ValueError, match="negative"):
        overrides.set_override(con, sku_id=1001, loc_id=1, bucket_date=when,
                               qty=-5.0, author="priya", reason="phone call")


def test_clearing_an_override_removes_the_quantity_and_the_reason(con, loaded):
    when = loaded.horizon_start + timedelta(days=3)
    overrides.set_override(con, sku_id=1001, loc_id=1, bucket_date=when,
                           qty=500.0, author="priya", reason="phone call")
    overrides.clear_override(con, sku_id=1001, loc_id=1, bucket_date=when)

    assert overrides.list_overrides(con, loaded) == []
    assert con.execute("SELECT count(*) FROM plan_override").fetchone()[0] == 0
    facts = read_facts(con, TABLE, scenario_id=0, measure=MEASURE,
                       start=when, end=when, keys=[(1001, 1)])
    assert [f.qty for f in facts] == [0]


def test_clearing_something_that_was_never_set_is_not_an_error(con, loaded):
    """Idempotent, because a planner clicking twice is not a fault."""
    overrides.clear_override(con, sku_id=1001, loc_id=1,
                             bucket_date=loaded.horizon_start)


# --------------------------------------------------------------------------
# the invariant the design rests on
# --------------------------------------------------------------------------

def test_every_fixed_quantity_has_a_person_attached(con, loaded):
    """The two stores must agree on which addresses exist.

    A `firm_planned_order` row with no provenance is an unattributable number in
    a plan; a provenance row with no quantity is a reason for nothing. Either
    one is a bug in whatever wrote it, and both must fail loudly rather than
    leave a plan half-explained.
    """
    when = loaded.horizon_start + timedelta(days=3)
    overrides.set_override(con, sku_id=1001, loc_id=1, bucket_date=when,
                           qty=500.0, author="priya", reason="phone call")
    assert overrides.audit(con, loaded) == []

    # Forge a quantity with nobody behind it -- through the accessor, because
    # `write_facts` can produce exactly this orphan on its own: it writes the
    # measure and knows nothing about provenance. Reaching for raw SQL here
    # would have been forging a state the application cannot reach, which is
    # testing `audit` against a bug that could not happen.
    write_facts(
        con, TABLE, scenario_id=0, measure=MEASURE,
        facts=[Fact(keys=(1002, 1), bucket_date=when, qty=42.0)],
    )
    problems = overrides.audit(con, loaded)
    assert problems and "1002" in problems[0]


def test_a_reason_with_no_quantity_is_caught_too(con, loaded):
    when = loaded.horizon_start + timedelta(days=3)
    con.execute(
        "INSERT INTO plan_override"
        " (sku_id, loc_id, bucket_date, scenario_id, author, reason, created_at)"
        " VALUES (1003, 1, ?, 0, 'priya', 'phone call', '2026-09-06')",
        (when.isoformat(),),
    )
    problems = overrides.audit(con, loaded)
    assert problems and "1003" in problems[0]


# --------------------------------------------------------------------------
# a planning run must not undo it
# --------------------------------------------------------------------------

def test_a_planning_run_cannot_overwrite_a_firm_order(con, loaded):
    """`firm_planned_order` is derived = 0, so `write_plans` already refuses it.

    This is the reason that classification was chosen rather than inventing a
    new protection: the rule that a run never overwrites human input is enforced
    in exactly one place and this feature inherits it.
    """
    when = loaded.horizon_start + timedelta(days=3)
    overrides.set_override(con, sku_id=1001, loc_id=1, bucket_date=when,
                           qty=500.0, author="priya", reason="phone call")

    netreq.run(con, loaded)

    facts = read_facts(con, TABLE, scenario_id=0, measure=MEASURE,
                       start=when, end=when, keys=[(1001, 1)])
    assert [f.qty for f in facts] == [500.0]
    assert overrides.list_overrides(con, loaded)[0].reason == "phone call"


def test_the_plan_actually_uses_the_number_the_planner_fixed(con, loaded):
    """End to end: the override reaches the order list."""
    from planbrain import orders

    sku_id, loc_id, when, was = _a_planned_receipt(con, loaded)
    assert was != 1.0

    overrides.set_override(con, sku_id=sku_id, loc_id=loc_id, bucket_date=when,
                           qty=1.0, author="priya", reason="cap this batch")
    netreq.run(con, loaded)

    after = read_facts(
        con, TABLE, scenario_id=0, measure="planned_order_receipt",
        start=when, end=when, keys=[(sku_id, loc_id)],
    )
    assert [f.qty for f in after] == [1.0], (
        "the plan should carry the planner's number, not its own"
    )


def test_the_plan_reports_which_numbers_are_the_humans(con, loaded):
    """The user's own condition on this feature.

    A plan carrying overrides has to say so, beside the verdict, for the same
    reason the growth assumptions are there: "the plan fits" means something
    different when a person moved three numbers to make it fit.
    """
    sku_id, loc_id, when, _was = _a_planned_receipt(con, loaded)
    overrides.set_override(con, sku_id=sku_id, loc_id=loc_id, bucket_date=when,
                           qty=1.0, author="priya", reason="cap this batch")
    netreq.run(con, loaded)

    summary = overrides.summary(con, loaded)
    assert summary["total"] == 1
    assert summary["authors"] == ["priya"]
    assert summary["overrides"][0]["reason"] == "cap this batch"
