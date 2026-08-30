"""Single-echelon inventory replay. The policy is the thing under test.

Build item 5, and the number this project is actually sold on. MASE has just
proved it cannot substitute: it rewards a forecast of zero on an intermittent
SKU, which is a forecast that never orders anything and never serves anybody.
The question a finance manager asks is not "how accurate was the forecast" but
**"what service did I get, and what did it cost me in stock?"**

The replay is deliberately auditable. One bucket at a time:

    1. receive whatever the pipeline delivers today
    2. review, and place an order if the policy says so
    3. serve today's demand from what is on hand
    4. record the closing position

Ordering matters and is stated because it moves the numbers: goods received
today are available to serve today, and an order placed today arrives after the
lead time. Anything else is a modelling choice someone should have to argue for.

**Deviation, flagged rather than asked:** the brief points at
anshul-musing/multi-echelon-inventory-optimization, whose replay is built on
SimPy. The architectural idea there -- replay history with the policy injected --
is followed exactly. The SimPy machinery is not, because at a single echelon
with daily buckets and deterministic lead times this is a loop over days with a
pipeline dict, and a discrete-event framework would add a runtime dependency and
a layer of indirection over the one number the whole positioning rests on. It
should be readable end to end without knowing a framework.

SimPy earns its place the moment any of these arrive: multiple echelons with
concurrent replenishment, stochastic lead times, or contention for a shared
resource. None are in scope, and multi-echelon is "later if ever".

**Unmet demand is lost, not backordered.** A customer who cannot get 20W-50
today buys it from someone else; they do not wait. Lost sales is also the
conservative reading -- backorders let a late delivery still count as served,
which flatters any policy that under-stocks. If a customer genuinely backorders,
this is the assumption to revisit first.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Outcome:
    """What one replay produced. Fill rate and stock, never one without the other.

    A policy can hit any fill rate by holding enough stock, and can hold almost
    no stock by serving nobody. Either number alone is meaningless, so they do
    not travel separately.
    """

    units_demanded: float
    units_served: float
    average_on_hand: float
    peak_on_hand: float
    stockout_buckets: int
    orders_placed: int
    units_ordered: float
    n_buckets: int

    @property
    def fill_rate(self):
        """Fraction of demanded units served immediately from stock.

        None when nothing was demanded -- a series with no demand in the window
        has no service level, and reporting 1.0 there would quietly lift every
        portfolio average with SKUs that were never tested.
        """
        if self.units_demanded == 0:
            return None
        return self.units_served / self.units_demanded

    @property
    def units_short(self) -> float:
        return self.units_demanded - self.units_served


@dataclass
class _State:
    on_hand: float
    pipeline: dict = field(default_factory=dict)

    @property
    def inbound(self) -> float:
        return sum(self.pipeline.values())


def replay(
    demand: list,
    policy,
    *,
    initial_on_hand: float,
    lead_time_days: int,
    review_every: int = 1,
    delivery_factor: list = None,
) -> Outcome:
    """Replay ``demand`` under ``policy`` and report service against stock held.

    ``policy`` is ``(bucket, on_hand, inbound) -> order quantity``. It sees the
    position it would really see: what is physically here and what is already on
    its way. It does not see future demand, which is the entire point.

    ``delivery_factor`` scales what actually **arrives** in each bucket, on the
    scale 0 to 1. It exists to model a plant that cannot make everything that
    was ordered: the planner still orders what they need, and less turns up. It
    applies at delivery rather than at ordering because that is where the
    constraint bites -- a capacity shortfall does not stop anyone raising an
    order, it stops the goods appearing.
    """
    if lead_time_days < 0:
        raise ValueError(f"lead time cannot be negative, got {lead_time_days}")
    if review_every < 1:
        raise ValueError(f"review period must be at least 1 bucket, got {review_every}")

    state = _State(on_hand=float(initial_on_hand))
    demanded = served = ordered = 0.0
    orders = stockouts = 0
    on_hand_trace = []

    for t, quantity in enumerate(demand):
        arriving = state.pipeline.pop(t, 0.0)
        if delivery_factor is not None:
            arriving *= delivery_factor[t]
        state.on_hand += arriving

        if t % review_every == 0:
            order = policy(t, state.on_hand, state.inbound)
            if order and order > 0:
                arrival = t + lead_time_days
                if arrival == t:
                    if delivery_factor is not None:
                        order *= delivery_factor[t]
                    # Same-bucket delivery. This must be added to stock directly:
                    # this bucket's arrivals were popped above, so anything put
                    # into the pipeline at t is never collected and the order
                    # vanishes without a sound. Found by cross-checking against
                    # the reconciliation ladder, which replays a fixed schedule
                    # at zero lead time.
                    state.on_hand += order
                else:
                    state.pipeline[arrival] = state.pipeline.get(arrival, 0.0) + order
                orders += 1
                ordered += order

        fulfilled = min(state.on_hand, quantity)
        state.on_hand -= fulfilled
        demanded += quantity
        served += fulfilled
        if fulfilled < quantity:
            stockouts += 1
        on_hand_trace.append(state.on_hand)

    return Outcome(
        units_demanded=demanded,
        units_served=served,
        average_on_hand=(sum(on_hand_trace) / len(on_hand_trace)) if on_hand_trace else 0.0,
        peak_on_hand=max(on_hand_trace) if on_hand_trace else 0.0,
        stockout_buckets=stockouts,
        orders_placed=orders,
        units_ordered=ordered,
        n_buckets=len(demand),
    )
