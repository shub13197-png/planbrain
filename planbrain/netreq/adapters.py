"""Where netreq meets the fact tables.

Everything in ``core`` and ``explode`` is pure arithmetic on lists. This module
is the only part that knows scenarios, measures and dates exist, which keeps the
textbook fixtures pointed at the math.
"""

from datetime import timedelta

from ..facts.access import Fact, bucket_spine, read_facts, write_facts

TABLE = "fact_supply_demand"

#: Measures netreq writes. All are derived=1 outputs; writing a derived=0 measure
#: would overwrite what the importer pulled from the system of record.
OUTPUT_MEASURES = (
    "gross_req",
    "net_req",
    "projected_on_hand",
    "planned_order_receipt",
    "planned_order_release",
    "past_due_release",
)


#: Where independent demand comes from. The netting loop never learns which it
#: got -- swapping the source is a parameter, not a diff through the algorithm.
SOURCES = {
    #: Item 3 placeholder: the trailing window of actuals shifted forward. No
    #: model, no reconciliation, no error estimate. Never quote its accuracy.
    "naive_replay",
    #: Item 4: fitted per depot by planbrain.forecast, reconciled bottom-up here.
    "forecast",
}


class GrossReqSourceError(ValueError):
    """The requested independent-demand source is unusable."""


def resolve_gross_req(
    con,
    *,
    scenario_id: int,
    sku_ids,
    loc_ids,
    horizon_start,
    horizon_end,
    source: str = "naive_replay",
) -> dict:
    """Independent demand for the netting horizon, keyed by sku_id.

    **This is the seam.** Item 3 nets against a naive replay of recent actuals
    so the netting loop has something realistic to consume. Item 4 swaps in
    ``source="forecast"`` and the netting loop never learns which it got.

    ``naive_replay`` shifts the trailing window of ``demand_actual`` forward by
    exactly one horizon length. It is a placeholder, **not a forecast**: it has
    no model, no reconciliation and no error estimate, and its accuracy must
    never be quoted as a baseline. It exists so that item 3 can be tested end to
    end before item 4 exists.

    Demand is aggregated across locations, because explosion runs at a single
    production location -- see explode() for why.
    """
    if source not in SOURCES:
        raise GrossReqSourceError(
            f"unknown gross requirement source {source!r}; expected one of {sorted(SOURCES)}"
        )

    spine = bucket_spine(horizon_start, horizon_end)
    keys = [(sku, loc) for sku in sku_ids for loc in loc_ids]

    if source == "forecast":
        # Read at the grain the model was fitted at, then reconcile bottom-up.
        measure, window_start, window_end = "forecast", horizon_start, horizon_end
    else:
        # Shift the trailing window of actuals forward by one horizon length.
        measure = "demand_actual"
        window_end = horizon_start - timedelta(days=1)
        window_start = window_end - timedelta(days=len(spine) - 1)

    rows = read_facts(
        con, TABLE,
        scenario_id=scenario_id, measure=measure,
        start=window_start, end=window_end, keys=keys,
    )

    demand = {}
    for row in rows:
        sku_id = row.keys[0]
        series = demand.setdefault(sku_id, [0.0] * len(spine))
        index = (row.bucket_date - window_start).days
        series[index] += row.qty

    if source == "forecast" and not any(any(s) for s in demand.values()):
        # Every series zero means the forecast measure was never written, not
        # that demand is genuinely nil. Netting against that produces a
        # confident, empty plan -- the failure this seam exists to prevent.
        raise GrossReqSourceError(
            "the forecast measure holds nothing for this horizon and scenario; "
            "run planbrain.forecast.run() before netting against it"
        )
    return demand


def read_scheduled_receipts(
    con, *, scenario_id: int, sku_ids, loc_id: int, horizon_start, horizon_end
) -> dict:
    """Confirmed open orders landing in the horizon, keyed by sku_id."""
    rows = read_facts(
        con, TABLE,
        scenario_id=scenario_id, measure="scheduled_receipt",
        start=horizon_start, end=horizon_end,
        keys=[(sku, loc_id) for sku in sku_ids],
    )
    spine_start = horizon_start
    receipts = {}
    for row in rows:
        series = receipts.setdefault(row.keys[0], [0.0] * ((horizon_end - spine_start).days + 1))
        series[(row.bucket_date - spine_start).days] += row.qty
    return receipts


def write_plans(con, plans, *, scenario_id: int, horizon_start, horizon_end, gross_by_sku) -> dict:
    """Persist a netreq run. Returns rows written per measure.

    Refuses to write any measure that is not a derived output. The database does
    not enforce the derived flag, so this is where the rule that a planning run
    never overwrites imported data actually holds.
    """
    _assert_all_derived(con, OUTPUT_MEASURES)
    spine = bucket_spine(horizon_start, horizon_end)

    series_by_measure = {m: [] for m in OUTPUT_MEASURES}
    for plan in plans:
        key = (plan.sku_id, plan.loc_id)
        for measure, values in (
            ("gross_req", gross_by_sku[plan.sku_id]),
            ("net_req", plan.net_req),
            ("projected_on_hand", plan.projected_on_hand),
            ("planned_order_receipt", plan.planned_order_receipt),
            ("planned_order_release", plan.planned_order_release),
        ):
            series_by_measure[measure].extend(
                Fact(key, bucket, qty) for bucket, qty in zip(spine, values)
            )
        # Past-due releases are a sparse list of exceptions rather than a dense
        # series, and they are dated at the bucket the material is needed. The
        # engine already computed them; until this they were returned to a
        # caller that dropped them.
        for exception in plan.exceptions:
            if exception.kind == "past_due_release":
                series_by_measure["past_due_release"].append(
                    Fact(key, spine[exception.bucket_index], exception.qty)
                )

    return {
        measure: write_facts(
            con, TABLE, scenario_id=scenario_id, measure=measure, facts=facts
        )
        for measure, facts in series_by_measure.items()
    }


def _assert_all_derived(con, measures) -> None:
    rows = dict(con.execute(
        "SELECT measure, derived FROM measure WHERE measure IN "
        f"({', '.join('?' for _ in measures)})",
        tuple(measures),
    ))
    for measure in measures:
        if measure not in rows:
            raise ValueError(f"unknown measure {measure!r}")
        if rows[measure] != 1:
            raise ValueError(
                f"{measure!r} is an imported measure (derived=0); a planning run "
                f"must never overwrite what the importer pulled from the system of record"
            )
