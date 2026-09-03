"""The policies under test. Three of them, and the comparison is the point.

Each is a closure over what it is allowed to know at decision time, and none
sees future demand.

| policy | what drives it | what it is here to show |
|---|---|---|
| `forecast_order_up_to` | the fitted per-pattern forecast | what the tool actually does |
| `naive_zero_order_up_to` | a forecast of zero | the MASE-optimal choice for intermittent demand |
| `reorder_point` | historical mean, no forecast | the rule a planner already uses on paper |

**The naive-zero policy is the experiment that settles the intermittent
question.** On an intermittent SKU, forecasting a flat zero scores beautifully
on point-error metrics -- it is right on every quiet day and wrong only on the
few days that matter. It is also a policy that never orders anything. Running it
here turns an abstract argument about metric choice into a fill rate a finance
manager can read.

All three are order-up-to or reorder-point rules over a review period plus a
lead time. That protection window is not decoration: an order placed today
arrives after the lead time, so today's decision has to cover demand until the
*next* order could possibly arrive.
"""

import math
from statistics import NormalDist


def _cover(forecast: list, start: int, length: int) -> float:
    """Expected demand over ``length`` buckets from ``start``, clipped at the end."""
    return float(sum(forecast[start : start + length]))


def forecast_order_up_to(forecast, *, lead_time_days, review_every=1, safety_stock=0.0,
                         lot_multiple=0.0):
    """Order up to expected demand over the protection window, plus safety stock.

    The protection window is lead time plus review period, because a decision
    made today must carry the position until the next order could land.

    ``lot_multiple`` rounds the order up, mirroring the fixed-quantity lot sizing
    netreq applies. Without it the comparison would flatter this policy against
    a real plan, which cannot order 37.4 litres.
    """
    window = lead_time_days + review_every

    def policy(t, on_hand, inbound):
        target = _cover(forecast, t, window) + safety_stock
        shortfall = target - (on_hand + inbound)
        if shortfall <= 0:
            return 0.0
        if lot_multiple:
            return math.ceil(shortfall / lot_multiple - 1e-9) * lot_multiple
        return shortfall

    return policy


def naive_zero_order_up_to(*, lead_time_days, review_every=1, safety_stock=0.0):
    """The policy implied by a forecast of zero.

    Orders only what safety stock demands, which with no safety stock is
    nothing at all. Included precisely because this forecast wins on MASE for
    intermittent SKUs. If accuracy were the right objective, this would be the
    policy to ship.
    """
    return forecast_order_up_to(
        [0.0] * 1, lead_time_days=lead_time_days, review_every=review_every,
        safety_stock=safety_stock,
    )


def reorder_point(*, mean_demand, lead_time_days, review_every=1, safety_factor=1.0,
                  demand_sd=0.0, order_quantity=None):
    """Classic (s, S): order when the position drops below s, bring it up to S.

    ``s`` is expected demand over the protection window plus a safety term.
    ``S`` defaults to s plus one protection window of demand, which is a plain
    periodic-review rule rather than an economic order quantity -- no cost data
    is involved here and inventing some would make the comparison unfalsifiable.

    This is the honest incumbent. A planner with a spreadsheet is running some
    version of it, and a tool that cannot beat it is not worth installing.
    """
    window = lead_time_days + review_every
    s = mean_demand * window + safety_factor * demand_sd * math.sqrt(window)
    S = s + (order_quantity if order_quantity is not None else mean_demand * window)

    def policy(t, on_hand, inbound):
        position = on_hand + inbound
        if position > s:
            return 0.0
        return max(0.0, S - position)

    return policy


def demand_statistics(history: list):
    """Mean and standard deviation of a demand history, over ALL buckets.

    Over all buckets including the zeros, not only the days with demand. For an
    intermittent SKU those are wildly different numbers -- the mean of non-zero
    sizes might be 40 where the mean rate is 2 -- and using the non-zero mean in
    a reorder point would order twenty times too much. Getting this wrong is a
    classic and it does not raise.
    """
    if not history:
        return 0.0, 0.0
    mean = sum(history) / len(history)
    variance = sum((v - mean) ** 2 for v in history) / len(history)
    return mean, math.sqrt(variance)


def safety_stock_for_service(demand_sd: float, *, lead_time_days: int,
                             service_level: float, review_every: int = 1) -> float:
    """Safety stock for a cycle service level, the textbook periodic-review form.

        SS = z(alpha) * sigma * sqrt(L + R)

    where sigma is the per-bucket demand standard deviation, L the lead time and
    R the review period. Source: Silver, Pyke & Peterson, *Inventory Management
    and Production Planning and Scheduling*, 3rd ed., ch. 7.

    z comes from `statistics.NormalDist` rather than scipy: it is the stdlib, it
    is exact, and it keeps a one-line formula from adding a dependency to the
    bundle.

    **This assumes demand over the lead time is normally distributed, and for
    much of a real portfolio it is not.** Intermittent and lumpy series are
    mostly zeros with occasional spikes; the normal approximation understates
    the tail that actually causes stockouts, so the achieved service level comes
    in below the requested one. That is measured per demand pattern in
    `docs/service-backtest.md` rather than left as a caveat -- the number the
    rule delivers is more useful than the number it asks for.

    A service level of 0.5 gives z = 0 and no safety stock, which is correct and
    is what "I will be short half the time" means.
    """
    if not 0.0 < service_level < 1.0:
        raise ValueError(
            f"service level must be a probability strictly between 0 and 1, "
            f"got {service_level!r}. 1.0 would demand infinite stock"
        )
    z = NormalDist().inv_cdf(service_level)
    return max(0.0, z * demand_sd * math.sqrt(lead_time_days + review_every))
