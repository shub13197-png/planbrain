# Letting the planner disagree

**Status: built, 2026-09-06.** `planbrain/overrides.py`,
`planbrain/netreq/core.py`, `tests/test_overrides.py` and
`tests/test_netreq_firm.py`. Everything above the last section was written as a
design and put up for review *before* any of it existed, because it adds to the
measure vocabulary — which is a contract, not an implementation detail. It is
left as written so the design and the result can be compared.

## Why this is not a feature request

A planner knows things the data does not. A customer committed to 500 units on
the phone. A machine is down next Tuesday. A supplier's truck is late and
everyone in the plant knows it and no system does.

Today the application has no way to be told any of that. The plan is what the
arithmetic says, and the only controls are two growth percentages. **That is the
second place Excel wins**, after the missing order list: a spreadsheet lets you
type over a number.

It is also a trust question rather than a convenience one. A planner who cannot
disagree with a system stops using it the first time it is wrong about something
they know. Being able to overrule it — and having the system carry the overrule
forward instead of quietly recomputing it away — is what makes the rest of the
output believable.

## The mechanism: a firm planned order

This is not a new invention and should not be named as one. In standard MRP a
planner **firms** a planned order: the order is fixed in quantity and date, and
subsequent planning runs net around it rather than resizing or rescheduling it.
That is exactly the semantics wanted here, it has published behaviour, and it
already fits the netting chain in `planbrain/netreq/core.py`.

    ('firm_planned_order', 'supply_demand', 'qty', 0,
     'Planner-fixed supply: quantity and date set by a human, not resized or
      rescheduled by a planning run')

**`derived = 0`, deliberately.** A firm order is an *input* to a run, in the
same class as `scheduled_receipt` — which is why `write_plans`'s existing
refusal to write anything non-derived protects it for free. A planning run
physically cannot overwrite a human's number, and that rule is already enforced
in one place rather than needing a new one.

### How it flows through the engine

`Item` grows one field beside `scheduled_receipt`:

```python
firm_planned_order: list          # aligned to the same spine
```

and `plan_item` treats a firm order as fixed supply in its bucket: it counts
toward the projected balance and toward netting, and the lot-sizing pass — the
greedy rules and the Wagner-Whitin DP alike — may not resize it or move it. A
firm order in a bucket suppresses a planned order the engine would otherwise
have created there; a shortfall the firm order does not cover still generates
one.

The consequence a planner must be told about, and which the screen has to show:
**firming an order too small does not make the requirement go away.** It creates
a shortage, which is the honest answer and is what the exception list is for.

### Provenance lives beside the fact, not inside it

The fact tables are keyed `(entity, bucket_date, measure, scenario_id)` and hold
one column, `qty`. There is nowhere to put an author or a reason, and widening
the fact schema to carry them would put the two most-written tables in the
system through a migration for a column almost every row leaves null.

So a second table holds only the story:

```sql
CREATE TABLE plan_override (
    sku_id       INTEGER NOT NULL,
    loc_id       INTEGER NOT NULL,
    bucket_date  DATE    NOT NULL,
    scenario_id  INTEGER NOT NULL REFERENCES scenario (scenario_id),
    author       TEXT    NOT NULL,
    reason       TEXT    NOT NULL,
    created_at   TEXT    NOT NULL,
    PRIMARY KEY (sku_id, loc_id, bucket_date, scenario_id)
);
```

**The quantity is not duplicated here.** It lives in the fact row and only
there, because two copies of a number is two numbers. This table answers "who
and why", the fact table answers "how much", and a test asserts the two agree on
which addresses exist — a firm order with no provenance row, or a provenance row
with no firm order, is a bug in the writer and must fail loudly.

`reason` is `NOT NULL` and must be non-empty. An override with no reason is
indistinguishable in three weeks from a typo, and the person who has to work
that out is usually the person who typed it.

## What the screen must show

The user's own condition on this work: **the plan reports which numbers are the
machine's and which are the human's.** So every row on the order list carries
its source, and a firmed row shows the author and reason on hover or in a
column. A plan containing overrides says so at the top, beside the verdict —
next to the growth assumptions, for the same reason those are there: a
feasibility answer is the number most likely to be repeated out of context, and
"the plan fits" means something different when a human moved three numbers to
make it fit.

## Rejected

**Overriding the forecast instead of the order.** Cheaper — it is just another
input series, and everything downstream stays internally consistent with no
change to the netting chain. Rejected because it does not answer the question
that was asked. "I know this customer will take 500" and "make 500, not 860" are
different statements, and only the second is a planner overruling the plan. The
forecast override is a good second feature and a bad substitute for the first.

**Editing the number in place, spreadsheet-style, with no separate concept.**
This is what Excel does and it is why an Excel plan cannot be re-run: once a
human has typed over a cell there is no way to recompute anything without
destroying their work, so in practice nobody recomputes. The whole value of
doing this in a planning system rather than a sheet is that the override
survives the next run *as an override*, and stays visible as one.

**A free-form "notes" field with no effect on the plan.** It would be the
cheapest thing to build and it would be worse than nothing: the planner writes
down what they know, the plan ignores it, and the numbers are now wrong *and*
annotated.


## What building it settled

**An override fixes the RECEIPT bucket — the day the material is needed — not
the release date the order list shows.** The design above said "quantity and
date" without saying which date, and the two are not interchangeable. The first
end-to-end test fixed a quantity of 1 against a release date and the plan came
back with **40,001**: that release bucket was not one the plan was receiving in,
so the firm supply landed beside the existing order instead of replacing it.

Receipts are where netting happens and where "replace this quantity" means
something; a release is derived from a receipt by the lead-time offset like any
other. So the interface asks for *needed on*, says so on the form, and
`override.set` takes that date. It is the same distinction the order list
already refuses to blur — it reports releases and will not print a receipt date
beside them, because the two are not one-to-one.

**The engine does not top a firm order up, and the shortfall is not hidden
either.** A firm bucket is fixed entirely: `plan_item` passes the quantity
through and lot-sizes nothing on top of it. What the planner did not supply
lowers the projected balance, which surfaces as a shortage on the risk screen,
and ordinary netting plans the deficit in a *later* bucket exactly as it would
after any other shortfall. Capping a batch says "not this much, this week" — it
does not delete the requirement.

**Wagner-Whitin needed no special case.** A firm bucket contributes zero to the
requirement vector the DP sees, so the only way a lot could land in one is if
the DP chose it as the order point for later demand — and that is never cheaper,
because ordering earlier holds the same units for more periods and `LotSizing`
already refuses a Wagner-Whitin policy with a non-positive holding cost. The
published Snyder & Shen instance is untouched, which is the check that says the
DP was not disturbed.

**The `derived = 0` classification did the work it was chosen for.** A planning
run cannot overwrite a firm order because `write_plans` already refuses to write
a non-derived measure — a rule that existed, in one place, before this feature
did. No new protection was added, which was the argument for the classification
and is now the evidence for it.

**The two stores are audited rather than trusted.** `overrides.audit()` reports
any address where a fixed quantity has no author and reason, or a reason has no
quantity behind it, and the interface shows those instead of the override list.
A plan that is half-explained is worse than an unexplained one, because a reader
cannot tell which they are looking at.
