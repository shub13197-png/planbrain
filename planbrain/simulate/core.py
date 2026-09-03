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

**Unmet demand is lost by default; backordering is an option.** A retail
customer who cannot get 20W-50 today buys it from someone else. An OEM on a
supply contract waits and expects it next week. Both are real and they are
different businesses.

Lost sales stays the default because it is the conservative reading. Under
``unmet="backorder"`` the headline ``fill_rate`` still means **served in the
bucket it was demanded in** -- what changes is that unserved units come back
tomorrow instead of disappearing. ``eventual_fill_rate`` reports the softer
number separately and the two are never merged: a policy that serves everything
a fortnight late is not the same as one that serves everything, and a single
blended figure would hide exactly that difference.
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
    #: Units served in the bucket they were demanded in. Equal to units_served
    #: under lost sales; lower under backordering, which is why they are kept
    #: apart rather than reconciled.
    units_served_on_time: float = None
    #: Mean units owed and not yet delivered. Zero under lost sales.
    average_backlog: float = 0.0
    #: Buckets that closed still owing something.
    backlog_buckets: int = 0

    def __post_init__(self):
        # Lost sales is the case where the two are the same thing, and defaulting
        # keeps every existing construction of this object correct rather than
        # silently reporting zero on-time service.
        if self.units_served_on_time is None:
            object.__setattr__(self, "units_served_on_time", self.units_served)

    @property
    def fill_rate(self):
        """Fraction of demanded units served **in the bucket they were demanded**.

        Deliberately on-time rather than eventual, so the figure means the same
        thing under both unmet-demand rules. Were it eventual, switching to
        backorders would raise every policy's service without anyone shipping
        anything sooner.

        None when nothing was demanded -- a series with no demand in the window
        has no service level, and reporting 1.0 there would quietly lift every
        portfolio average with SKUs that were never tested.
        """
        if self.units_demanded == 0:
            return None
        return self.units_served_on_time / self.units_demanded

    @property
    def eventual_fill_rate(self):
        """Fraction served at all, however late. Equal to ``fill_rate`` under
        lost sales, and the softer number under backordering."""
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
    unmet: str = "lost",
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
    if unmet not in ("lost", "backorder"):
        raise ValueError(
            f"unmet must be 'lost' or 'backorder', got {unmet!r}. There is no "
            f"default for a business rule this consequential"
        )
    if lead_time_days < 0:
        raise ValueError(f"lead time cannot be negative, got {lead_time_days}")
    if review_every < 1:
        raise ValueError(f"review period must be at least 1 bucket, got {review_every}")

    state = _State(on_hand=float(initial_on_hand))
    demanded = served = served_on_time = ordered = 0.0
    orders = stockouts = backlog_buckets = 0
    backlog = 0.0
    on_hand_trace = []
    backlog_trace = []

    for t, quantity in enumerate(demand):
        arriving = state.pipeline.pop(t, 0.0)
        if delivery_factor is not None:
            arriving *= delivery_factor[t]
        state.on_hand += arriving

        if t % review_every == 0:
            # The policy sees net stock, not gross. Showing it on-hand while a
            # backlog is outstanding would let it decide it has enough while
            # owing a fortnight of demand, and it would never catch up.
            order = policy(t, state.on_hand - backlog, state.inbound)
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

        # Owed demand is served before today's. Anything else would leave the
        # oldest customer waiting longest, which is neither what happens nor
        # what anyone would defend.
        requirement = quantity + backlog
        fulfilled = min(state.on_hand, requirement)
        state.on_hand -= fulfilled

        to_backlog = min(fulfilled, backlog)
        backlog -= to_backlog
        today = fulfilled - to_backlog

        demanded += quantity
        served += fulfilled
        served_on_time += today
        if today < quantity:
            stockouts += 1
            if unmet == "backorder":
                backlog += quantity - today
        if backlog > 0:
            backlog_buckets += 1
        on_hand_trace.append(state.on_hand)
        backlog_trace.append(backlog)

    return Outcome(
        units_demanded=demanded,
        units_served=served,
        units_served_on_time=served_on_time,
        average_backlog=(sum(backlog_trace) / len(backlog_trace)) if backlog_trace else 0.0,
        backlog_buckets=backlog_buckets,
        average_on_hand=(sum(on_hand_trace) / len(on_hand_trace)) if on_hand_trace else 0.0,
        peak_on_hand=max(on_hand_trace) if on_hand_trace else 0.0,
        stockout_buckets=stockouts,
        orders_placed=orders,
        units_ordered=ordered,
        n_buckets=len(demand),
    )
