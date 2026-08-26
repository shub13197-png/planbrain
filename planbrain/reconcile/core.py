"""The reconciliation ladder: why netreq and the simulation report different stock.

Build item 8. Pure arithmetic over lists, like every other engine core.

Each rung adds exactly one effect, so each delta **is** that term and the
decomposition sums by construction rather than by luck. See
`docs/reconciliation.md` for the terms, which were committed before this ran.

The goal is not agreement. `netreq` computes deterministic net requirements
against a forecast; the simulation replays realised demand with stockouts. They
*should* differ. The credibility problem is differing for reasons nobody can
name.
"""

from dataclasses import dataclass

RUNGS = ("pure_netting", "with_safety_stock", "with_lot_sizing",
         "against_actuals", "with_truncation")

TERMS = ("safety_stock", "lot_granularity", "forecast_error", "stockout_truncation")


@dataclass(frozen=True)
class Ladder:
    """One series' rungs and the terms between them."""

    key: object
    pattern: str
    rungs: dict
    terms: dict

    @property
    def plan_on_hand(self) -> float:
        """What netreq reports: forecast demand, safety stock, real lot sizing."""
        return self.rungs["with_lot_sizing"]

    @property
    def replayed_on_hand(self) -> float:
        """The same schedule met by realised demand, with lost sales."""
        return self.rungs["with_truncation"]

    @property
    def observed_gap(self) -> float:
        return self.plan_on_hand - self.replayed_on_hand

    @property
    def explained_gap(self) -> float:
        return -(self.terms["forecast_error"] + self.terms["stockout_truncation"])


def replay_schedule(receipts: list, demand: list, *, opening: float,
                    truncate: bool) -> list:
    """Closing balance per bucket for a **fixed** receipt schedule.

    Deliberately not ``simulate.replay``. That function exists to score a
    *policy* under a lead time, and it always truncates -- as a service
    simulation must, because negative physical stock is not a thing. This one
    takes an already time-phased schedule, has no policy and no lead time, and
    needs an untruncated mode precisely so the effect of truncation can be
    isolated as its own term. A shared function would have to carry a flag that
    is nonsense in the other caller's context.
    """
    balance = float(opening)
    trace = []
    for t, quantity in enumerate(demand):
        balance += receipts[t]
        if truncate:
            balance = max(0.0, balance - quantity)
        else:
            balance -= quantity
        trace.append(balance)
    return trace


def build_ladder(*, key, pattern, forecast, actual, opening, lead_time_days,
                 safety_stock, lot_sizing, plan_item, item_factory) -> Ladder:
    """Walk the five rungs for one series.

    ``plan_item`` and ``item_factory`` are injected rather than imported so this
    module stays a pure function of its arguments and the netting engine can be
    stubbed in tests.
    """
    from .terms import lot_for_lot

    def _plan(ss, ls):
        return plan_item(item_factory(
            lead_time_days=lead_time_days, on_hand=opening, safety_stock=ss,
            lot_sizing=ls, gross_req=list(forecast),
        ))

    pure = _plan(0.0, lot_for_lot())
    with_ss = _plan(safety_stock, lot_for_lot())
    with_lots = _plan(safety_stock, lot_sizing)

    schedule = with_lots.planned_order_receipt
    untruncated = replay_schedule(schedule, actual, opening=opening, truncate=False)
    truncated = replay_schedule(schedule, actual, opening=opening, truncate=True)

    rungs = {
        "pure_netting": _mean(pure.projected_on_hand),
        "with_safety_stock": _mean(with_ss.projected_on_hand),
        "with_lot_sizing": _mean(with_lots.projected_on_hand),
        "against_actuals": _mean(untruncated),
        "with_truncation": _mean(truncated),
    }
    terms = {
        "safety_stock": rungs["with_safety_stock"] - rungs["pure_netting"],
        "lot_granularity": rungs["with_lot_sizing"] - rungs["with_safety_stock"],
        "forecast_error": rungs["against_actuals"] - rungs["with_lot_sizing"],
        "stockout_truncation": rungs["with_truncation"] - rungs["against_actuals"],
    }
    return Ladder(key=key, pattern=pattern, rungs=rungs, terms=terms)


def total_change(ladder: Ladder) -> float:
    """Rung 0 to rung 4. The terms must sum to exactly this."""
    return ladder.rungs["with_truncation"] - ladder.rungs["pure_netting"]


def _mean(series: list) -> float:
    return sum(series) / len(series) if series else 0.0
