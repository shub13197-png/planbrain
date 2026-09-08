"""Service-level backtest: fill rate against inventory held (build item 5).

The proof-of-value report. Replays held-out history one SKU at a time, applies
each candidate policy, and reports what service it achieved and what stock it
had to carry to achieve it.

Same layering as netreq and forecast: ``core`` and ``policies`` are pure and
know nothing about scenarios or dates; this module owns the adapters.

Why this exists is worth restating. Roughly half the demo portfolio is
intermittent or lumpy, which is where planning software earns its money, and
MASE demonstrably cannot judge that half -- it rewards a forecast of zero, which
is a policy that never orders. Fill rate against average on-hand is the number
that settles it, and the number a finance manager will actually read.
"""

from dataclasses import dataclass

from ..forecast import classify, make_forecaster
from ..forecast.aggregate import aggregated_forecaster
from statistics import NormalDist

from ..facts.access import read_facts
from ..forecast.metrics import ScoredMean, scored_mean
from .core import Outcome, replay
from .policies import (
    demand_statistics,
    achieved_fill_rate,
    forecast_order_up_to,
    moving_average_cover,
    naive_zero_order_up_to,
    reorder_point,
    level_by_simulation,
    level_for_exceedance,
    order_up_to_for_fill_rate,
    safety_stock_for_service,
)

TABLE = "fact_supply_demand"

POLICIES = ("forecast", "moving_average", "naive_zero", "reorder_point",
            "reorder_point_stale", "fill_rate_base", "forecast_fill_rate",
            "forecast_seasonal", "seasonal_fill_rate", "marginal_allocation",
            "simulated_recent")

#: Buckets the spreadsheet baseline averages over. Twelve weeks, because "take
#: the last three months" is the rule a planner without software actually
#: applies -- not a value tuned until this product won. Committed here and in
#: docs/constants.md before the comparison was run.
MOVING_AVERAGE_DAYS = 84

#: How much of the recent past `simulated_recent` fits its level on.
#:
#: A year, not a quarter. The spreadsheet policy above uses 84 days and beats
#: this product on share of demand served for a manufacturer whose demand has
#: drifted -- but 84 days cannot contain an annual season, so copying it would
#: trade one bias for another. A year is the shortest window that holds a full
#: cycle while discarding the five older years that the analytic rules were
#: still fitting to.
RECENT_DAYS = 365

#: Fraction of the training history the stale reorder point is fitted on. It is
#: then never revisited, which is what an SME incumbent actually looks like: the
#: numbers were set once, by someone who may have left, and nobody re-derives
#: them quarterly. "Well-tuned" presupposes ongoing tuning nobody is doing.
STALE_FIT_FRACTION = 1 / 3

__all__ = [
    "Outcome",
    "POLICIES",
    "PolicyResult",
    "capacity_factor",
    "compare",
    "demand_statistics",
    "achieved_fill_rate",
    "forecast_order_up_to",
    "level_by_simulation",
    "level_for_exceedance",
    "order_up_to_for_fill_rate",
    "moving_average_cover",
    "naive_zero_order_up_to",
    "reorder_point",
    "replay",
]


@dataclass(frozen=True)
class InventoryValue:
    """What a policy's inventory is worth, and what holding it costs per year.

    A **total**, not a mean, so it scales with how many series were evaluated --
    which is why ``series`` travels with it and why there is no ``__float__``.
    Quoting working capital without saying how much of the portfolio it covers
    is the same mistake as quoting a fill rate without its denominator.

    ``unpriced`` is the count of series whose part carries no unit cost. Those
    contribute nothing to the total, so a portfolio that is half unpriced
    reports half the working capital and looks better than it is -- an absent
    price must not read as a free part.
    """

    total: float
    annual_carrying: float
    carrying_rate: float
    series: int
    unpriced: int

    @property
    def complete(self) -> bool:
        """Every evaluated series had a price."""
        return self.unpriced == 0


@dataclass(frozen=True)
class PolicyResult:
    """One policy's outcome across a set of series, grouped by demand pattern.

    Fill rate and inventory are carried together and each as a ScoredMean, so
    neither can be quoted without its denominator and neither can be quoted
    without the other.
    """

    policy: str
    fill_rate: ScoredMean
    average_on_hand: ScoredMean
    units_short: float
    by_pattern: dict
    #: Served at all, however late. Equal to fill_rate under lost sales. Kept
    #: separate so switching to backorders cannot raise the headline figure
    #: without anything shipping sooner.
    eventual_fill_rate: ScoredMean = None
    #: Inventory reported in money as well as units. Units alone cannot be
    #: compared across a portfolio -- a thousand fasteners and a thousand
    #: castings are not the same decision -- and working capital is the term a
    #: planner is actually answerable for.
    inventory_value: InventoryValue = None
    #: Units served over units demanded across the whole portfolio.
    #:
    #: **A different question from `fill_rate`, and never merged with it.**
    #: `fill_rate` is a mean across parts, so a part with two units of annual
    #: demand counts as much as one with two million; it answers "how many of my
    #: part numbers were fine". This answers "how much of my demand did I
    #: actually serve", which is the one a business is paid on. Both are
    #: reported because they can disagree, and the disagreement is the
    #: information -- a policy can look good on one by being good at the parts
    #: that barely matter.
    weighted_fill_rate: float = None
    #: What the portfolio holds, not what an average part holds. The companion
    #: axis for `weighted_fill_rate`: a fraction of demand served has to be read
    #: against total stock, or the two halves are on different footings.
    total_on_hand: float = None


def capacity_factor(con, demo, *, scenario_id: int = 0, holdout_days: int = 90) -> list:
    """Per-bucket share of ordered production the plant could actually make.

    **A crude sensitivity, and labelled as one.** It takes the per-bucket ratio
    of available hours to loaded hours from `rccp` on the forward horizon and
    applies it to the holdout window as a stationary approximation. The horizons
    are different, so this is not the true constraint on those buckets -- it is
    the shape of the constraint the same plant exhibits.

    Crude is the point. The service backtest currently assumes production is
    unconstrained while `rccp` reports the same plan infeasible in 147 of 450
    resource-buckets, and one unqualified number is worse than an honest range.
    """
    from .. import rccp

    report = rccp.run(con, demo, scenario_id=scenario_id)
    buckets = report["buckets"]

    loads = [0.0] * buckets
    available = [0.0] * buckets
    for resource_id in report["resources"]:
        rows = read_facts(
            con, "fact_capacity", scenario_id=scenario_id,
            measure="capacity_load_hours",
            start=demo.horizon_start, end=demo.horizon_end, keys=[(resource_id,)],
        )
        avail = read_facts(
            con, "fact_capacity", scenario_id=scenario_id,
            measure="capacity_avail_hours",
            start=demo.horizon_start, end=demo.horizon_end, keys=[(resource_id,)],
        )
        for i, row in enumerate(rows):
            loads[i] += row.qty
        for i, row in enumerate(avail):
            available[i] += row.qty

    # A bucket with no load is unconstrained, not zero-capacity.
    horizon = [
        1.0 if load <= 0 else min(1.0, avail / load)
        for load, avail in zip(loads, available)
    ]
    return [horizon[t % len(horizon)] for t in range(holdout_days)]


def compare(
    con,
    demo,
    *,
    scenario_id: int = 0,
    keys=None,
    holdout_days: int = 90,
    safety_days: float = 0.0,
    safety_service_level: float = None,
    unmet: str = "lost",
    delivery_factor: list = None,
) -> dict:
    """Replay the holdout window under every policy. Returns a report per policy.

    The forecast policy is fitted on the training portion only. Fitting on the
    full history and then replaying it would let the policy see the demand it is
    being judged on, which is the inventory equivalent of scaling MASE on the
    test window -- and would make every number here meaningless in a way that
    nothing would flag.
    """
    from ..forecast import demand_keys, read_history

    all_keys = demand_keys(demo)
    keys = list(keys) if keys is not None else all_keys
    history = read_history(
        con, scenario_id=scenario_id, keys=keys,
        history_start=demo.history_start, history_end=demo.history_end,
    )
    season_length = demo.calendar.seasonal_period
    by_sku = {p.sku_id: p for p in demo.parts}

    if safety_service_level is not None and safety_days:
        # Two rules for one number. Silently preferring either would make the
        # reported safety stock depend on an argument order nobody can see.
        raise ValueError(
            "pass safety_days or safety_service_level, not both: they are two "
            "different rules for the same quantity"
        )
    # The reorder point takes a multiplier on sigma rather than an absolute
    # quantity, so a service level reaches it as z. Both policies then answer to
    # one rule instead of one following the service level and the other quietly
    # staying at 1.0.
    safety_factor = (
        NormalDist().inv_cdf(safety_service_level)
        if safety_service_level is not None
        else 1.0
    )

    outcomes = {policy: {} for policy in POLICIES}
    patterns = {}
    unmatched = []

    for key in keys:
        series = history.get(key, [])
        if len(series) <= holdout_days:
            continue
        train, holdout = series[:-holdout_days], series[-holdout_days:]
        profile = classify(train)
        patterns[key] = profile.pattern

        part = by_sku.get(key[0])
        if part is None:
            # A demand series whose SKU is not in the part master is a broken
            # import, not a SKU with default parameters. Defaulting a lead time
            # would produce a service figure for a product that does not exist.
            unmatched.append(key)
            continue
        lead_time = part.lead_time_days
        lot = part.lot_qty
        mean, sd = demand_statistics(train)
        stale_window = train[: max(1, int(len(train) * STALE_FIT_FRACTION))]
        stale_mean, stale_sd = demand_statistics(stale_window)
        safety = (
            safety_stock_for_service(sd, lead_time_days=lead_time, service_level=safety_service_level)
            if safety_service_level is not None
            else mean * safety_days
        )
        # Start every policy from the same position, or the comparison measures
        # the opening stock rather than the policy.
        opening = mean * (lead_time + 1)

        _, forecaster = make_forecaster(profile.pattern, season_length=season_length)
        fitted = forecaster(train, holdout_days)

        # The same sweep value read as a FILL RATE rather than a cycle service
        # level, inverted against the demand that actually occurred. See
        # `order_up_to_for_fill_rate`: the normal approximation above targets
        # the wrong quantity under the wrong distribution for 93-96% of both
        # real portfolios, and these two policies exist to measure what that
        # costs rather than to argue about it.
        window = lead_time + 1
        p2_level = (
            order_up_to_for_fill_rate(
                train, lead_time_days=lead_time, fill_rate=safety_service_level
            )
            if safety_service_level is not None
            else 0.0
        )
        mean_window = mean * window

        runs = {
            "forecast": forecast_order_up_to(
                fitted, lead_time_days=lead_time, safety_stock=safety, lot_multiple=lot
            ),
            "naive_zero": naive_zero_order_up_to(
                lead_time_days=lead_time, safety_stock=safety
            ),
            # The level read off a simulation of the policy on the recent
            # past, rather than derived from a distribution fitted to all of it.
            # Every other rule here fits the whole training window; this one
            # asks what the policy would actually have done lately.
            "simulated_recent": naive_zero_order_up_to(
                lead_time_days=lead_time,
                safety_stock=(
                    level_by_simulation(
                        lambda lvl: replay(
                            train[-RECENT_DAYS:],
                            naive_zero_order_up_to(lead_time_days=lead_time,
                                                   safety_stock=lvl),
                            initial_on_hand=mean * (lead_time + 1),
                            lead_time_days=lead_time, unmet=unmet,
                        ).fill_rate,
                        target=safety_service_level,
                        hi=max(mean * (lead_time + 1) * 6, 1.0),
                    )
                    if safety_service_level is not None and len(train) > lead_time + 2
                    else 0.0
                ),
            ),
            # Stock allocated across the portfolio rather than per part.
            # Equal *service* per part is not the same as spending the last unit
            # where it serves most; equal chance of running out is. The sweep
            # setting is read as the exceedance target's complement, so the
            # curve it traces is comparable with the others.
            "marginal_allocation": naive_zero_order_up_to(
                lead_time_days=lead_time,
                safety_stock=(
                    level_for_exceedance(train, lead_time_days=lead_time,
                                         exceedance=1.0 - safety_service_level)
                    if safety_service_level is not None else 0.0
                ),
            ),
            # A flat base stock at the fill-rate level: no forecast at all, the
            # demand distribution doing the whole job.
            "fill_rate_base": naive_zero_order_up_to(
                lead_time_days=lead_time, safety_stock=p2_level
            ),
            # The forecast, carried on top of the same level. The safety term is
            # what the level asks for beyond an average window, so when the
            # forecast is average the target is exactly the fill-rate level and
            # it flexes from there.
            # The same order-up-to rule, fed a forecast fitted where the
            # season is visible. `forecast` above is flat for 100% of the
            # manufacturing series and 98% of the retail ones, because at daily
            # grain they classify as lumpy and TSB returns a level. These two
            # aggregate first, so a yearly cycle is a period of 13 rather than
            # 365 -- see `planbrain/forecast/aggregate.py`.
            "forecast_seasonal": forecast_order_up_to(
                aggregated_forecaster(train, holdout_days),
                lead_time_days=lead_time, safety_stock=safety, lot_multiple=lot
            ),
            # The same seasonal forecast carried on the fill-rate level rather
            # than the normal-approximation one, so the two improvements can be
            # read apart as well as together.
            "seasonal_fill_rate": forecast_order_up_to(
                aggregated_forecaster(train, holdout_days),
                lead_time_days=lead_time,
                safety_stock=max(0.0, p2_level - mean_window), lot_multiple=lot
            ),
            "forecast_fill_rate": forecast_order_up_to(
                fitted, lead_time_days=lead_time,
                safety_stock=max(0.0, p2_level - mean_window), lot_multiple=lot
            ),
            # The spreadsheet. Given the SAME quantity of safety stock as every
            # other policy, expressed the way a spreadsheet expresses it -- as
            # days of cover -- so the only thing that differs between this and
            # `forecast` is the demand signal. Handing it less safety stock
            # would win the comparison by rigging it.
            "moving_average": moving_average_cover(
                train, window_days=MOVING_AVERAGE_DAYS,
                lead_time_days=lead_time, safety_stock=safety,
                order_quantity=lot or None,
            ),
            "reorder_point": reorder_point(
                mean_demand=mean, lead_time_days=lead_time, demand_sd=sd,
                safety_factor=safety_factor, order_quantity=lot or None,
            ),
            # Same rule, parameters frozen from the first third of history and
            # never revisited. A SKU launched after that window has a mean of
            # zero here and never gets ordered -- which is exactly what happens
            # in the field, and why it belongs in the comparison.
            "reorder_point_stale": reorder_point(
                mean_demand=stale_mean, lead_time_days=lead_time, demand_sd=stale_sd,
                safety_factor=safety_factor, order_quantity=lot or None,
            ),
        }

        for name, policy in runs.items():
            outcomes[name][key] = replay(
                holdout, policy, initial_on_hand=opening, lead_time_days=lead_time,
                delivery_factor=delivery_factor, unmet=unmet,
            )

    if unmatched:
        raise ValueError(
            f"{len(unmatched)} demand series have no part master entry, e.g. "
            f"{unmatched[:3]}; the reference data and the facts disagree"
        )

    unit_costs = {p.sku_id: p.unit_cost for p in demo.parts}
    return {
        "evaluated": len(patterns),
        "unmet_rule": unmet,
        "safety_rule": (
            f"{safety_service_level:.0%} cycle service level"
            if safety_service_level is not None
            else f"{safety_days:g} days of cover"
        ),
        "portfolio": len(all_keys),
        "holdout_days": holdout_days,
        "capacity_constrained": delivery_factor is not None,
        "pattern_mix": _tally(patterns.values()),
        "policies": {
            name: _summarise_policy(name, runs, patterns, unit_costs)
            for name, runs in outcomes.items()
        },
    }


def _inventory_value(runs, unit_costs) -> InventoryValue:
    """Working capital tied up by a policy, and the annual cost of holding it.

    Uses the same annual carrying rate as cost-based lot sizing
    (``planbrain.netreq.explode.ANNUAL_CARRYING_RATE``), deliberately: two
    numbers in one product describing the cost of holding stock must not
    disagree, and the rate is a committed business assumption rather than
    something fitted here.
    """
    from ..netreq.explode import ANNUAL_CARRYING_RATE

    total = 0.0
    unpriced = 0
    for (sku_id, _loc_id), outcome in runs.items():
        cost = unit_costs.get(sku_id)
        if not cost:
            unpriced += 1
            continue
        total += outcome.average_on_hand * cost
    return InventoryValue(
        total=total,
        annual_carrying=total * ANNUAL_CARRYING_RATE,
        carrying_rate=ANNUAL_CARRYING_RATE,
        series=len(runs),
        unpriced=unpriced,
    )


def _summarise_policy(name, runs, patterns, unit_costs=None) -> PolicyResult:
    fills = {key: outcome.fill_rate for key, outcome in runs.items()}
    eventual = {key: outcome.eventual_fill_rate for key, outcome in runs.items()}
    stock = {key: outcome.average_on_hand for key, outcome in runs.items()}
    by_pattern = {}
    for key, outcome in runs.items():
        bucket = by_pattern.setdefault(patterns[key], {"fill": {}, "stock": {}})
        bucket["fill"][key] = outcome.fill_rate
        bucket["stock"][key] = outcome.average_on_hand
    demanded = sum(o.units_demanded for o in runs.values())
    served = sum((o.units_served_on_time if o.units_served_on_time is not None
                  else o.units_served) for o in runs.values())
    return PolicyResult(
        policy=name,
        weighted_fill_rate=(served / demanded) if demanded > 0 else None,
        total_on_hand=sum(o.average_on_hand for o in runs.values()),
        fill_rate=scored_mean(fills),
        eventual_fill_rate=scored_mean(eventual),
        average_on_hand=scored_mean(stock),
        units_short=sum(o.units_short for o in runs.values()),
        by_pattern={
            pattern: {
                "fill_rate": scored_mean(v["fill"]),
                "average_on_hand": scored_mean(v["stock"]),
            }
            for pattern, v in sorted(by_pattern.items())
        },
        inventory_value=_inventory_value(runs, unit_costs or {}),
    )


def _tally(values) -> dict:
    counts = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))
