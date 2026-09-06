"""Time-phased material requirements planning for a single item.

Pure functions over dense series. Nothing here touches a database, a scenario or
a fact table -- that is deliberate, and it is what lets the textbook fixtures
test the *arithmetic* rather than the plumbing. If a fixture fails, the bug is
in the math on this page.

The netting chain per bucket, in order:

    gross requirement -> net of on-hand and scheduled receipts
                      -> lot-sized into a planned receipt
                      -> offset backward by lead time into a planned release

Algorithm sources are named on each function. Lot-sizing rules are per-bucket
greedy; Wagner-Whitin is a horizon-wide dynamic program and therefore runs as a
second pass over the whole net requirement vector.
"""

import math
from dataclasses import dataclass, field

#: Float noise accumulates over 500+ buckets of multiply-and-carry. Every number
#: leaving this module is rounded, so a plan is comparable across runs.
PRECISION = 6

LOT_POLICIES = ("lot_for_lot", "fixed_qty", "min_max", "wagner_whitin")


@dataclass(frozen=True)
class LotSizing:
    policy: str
    fixed_qty: float = 0.0
    min_qty: float = 0.0
    multiple_of: float = 0.0
    setup_cost: float = 0.0
    holding_cost: float = 0.0

    def __post_init__(self):
        if self.policy not in LOT_POLICIES:
            raise ValueError(f"unknown lot policy {self.policy!r}; expected one of {LOT_POLICIES}")
        if self.policy == "fixed_qty" and self.fixed_qty <= 0:
            raise ValueError("fixed_qty policy needs a positive fixed_qty")
        if self.policy == "wagner_whitin" and (self.setup_cost <= 0 or self.holding_cost <= 0):
            # Defaulting these to zero would silently degenerate to lot-for-lot
            # and report a plan nobody chose.
            raise ValueError("wagner_whitin needs positive setup_cost and holding_cost")


@dataclass(frozen=True)
class Item:
    sku_id: int
    loc_id: int
    lead_time_days: int
    on_hand: float
    safety_stock: float
    lot_sizing: LotSizing
    gross_req: list
    scheduled_receipt: list
    #: Which buckets the plant is open, aligned to the same spine. When given,
    #: a release is pulled back to the previous working bucket. Passed as flags
    #: rather than as a calendar so this module stays free of dates.
    working_buckets: list = None
    #: Quantities a planner has FIXED, aligned to the same spine. Zero, or an
    #: absent series, means the engine decides that bucket as usual.
    #:
    #: A firm bucket is fixed **entirely**: the quantity passes through
    #: unchanged and the engine plans nothing else there. That is what makes an
    #: override worth offering -- an engine that topped a capped order back up
    #: would leave the planner arguing with a number that always won. The
    #: shortfall is not hidden either: it lowers the projected balance, which
    #: surfaces as a shortage, and ordinary netting plans the deficit in a later
    #: bucket the way it would after any other shortfall.
    firm_planned_order: list = None


@dataclass(frozen=True)
class PlanException:
    sku_id: int
    loc_id: int
    kind: str  # 'past_due_release' | 'negative_on_hand'
    bucket_index: int
    qty: float
    days_late: int = 0


@dataclass(frozen=True)
class ItemPlan:
    sku_id: int
    loc_id: int
    projected_on_hand: list
    net_req: list
    planned_order_receipt: list
    planned_order_release: list
    exceptions: list = field(default_factory=list)


def plan_item(item: Item) -> ItemPlan:
    """Net, lot-size and offset one item over its horizon.

    Standard MRP record logic (Orlicky; see Nahmias, *Production and Operations
    Analysis*, ch. 7). Receipts are assumed available at the start of the bucket
    they land in, so the closing balance is
    ``opening + scheduled + planned - gross``.
    """
    n = len(item.gross_req)
    if len(item.scheduled_receipt) != n:
        raise ValueError(
            f"scheduled_receipt has {len(item.scheduled_receipt)} buckets, "
            f"gross_req has {n}; series must be spine-aligned"
        )
    if item.firm_planned_order is not None and len(item.firm_planned_order) != n:
        raise ValueError(
            f"firm_planned_order has {len(item.firm_planned_order)} buckets, "
            f"gross_req has {n}; series must be spine-aligned"
        )

    firm = _firm(item)
    if item.lot_sizing.policy == "wagner_whitin":
        net_req = _net_requirements(item)
        # A firm bucket contributes nothing for the DP to order for: the
        # planner has already fixed it. The DP therefore cannot top one up --
        # the only way a lot could land in a firm bucket is if it were chosen
        # as the order point for LATER demand, and ordering earlier is never
        # cheaper when the holding cost is positive, which `LotSizing` already
        # requires for this policy.
        receipts = _wagner_whitin_lots(
            [0.0 if f else need for need, f in zip(net_req, firm)],
            item.lot_sizing,
        )
        receipts = [r + f for r, f in zip(receipts, firm)]
    else:
        net_req, receipts = _greedy_net_and_lot(item)

    projected = _project(item, receipts)
    releases, exceptions = _offset(item, receipts)
    exceptions += _shortages(item, projected)

    return ItemPlan(
        sku_id=item.sku_id,
        loc_id=item.loc_id,
        projected_on_hand=_round(projected),
        net_req=_round(net_req),
        planned_order_receipt=_round(receipts),
        planned_order_release=_round(releases),
        exceptions=exceptions,
    )


def _firm(item: Item) -> list:
    """The planner's fixed quantities, or a run of zeros when there are none."""
    if item.firm_planned_order is None:
        return [0.0] * len(item.gross_req)
    return list(item.firm_planned_order)


def _net_requirements(item: Item) -> list:
    """Requirement remaining after on-hand and scheduled receipts, ignoring lot sizing.

    Used as the input to Wagner-Whitin, which needs the whole vector before it
    can trade setup cost against holding cost. Assumes each net requirement is
    covered exactly, which is true by construction: the planned receipts that
    Wagner-Whitin produces cover precisely these quantities.
    """
    firm = _firm(item)
    balance = item.on_hand
    net = []
    for t in range(len(item.gross_req)):
        balance += item.scheduled_receipt[t] + firm[t] - item.gross_req[t]
        if balance < item.safety_stock:
            need = item.safety_stock - balance
            net.append(need)
            # The balance is only restored where the engine is free to cover
            # the requirement. In a firm bucket it is not, so the shortfall
            # stays on the books and the next bucket sees it -- which is how it
            # reaches the projection as a shortage.
            if not firm[t]:
                balance = item.safety_stock
        else:
            net.append(0.0)
    return net


def _greedy_net_and_lot(item: Item):
    """Net and lot-size in a single pass, carrying lot-sizing excess forward.

    A fixed-quantity or minimum-order rule usually overshoots. That excess is
    real stock and must reduce later net requirements, which is why netting and
    lot sizing cannot be separated for these policies the way they can for
    Wagner-Whitin.
    """
    firm = _firm(item)
    balance = item.on_hand
    net, receipts = [], []
    for t in range(len(item.gross_req)):
        balance += item.scheduled_receipt[t] + firm[t] - item.gross_req[t]
        shortfall = item.safety_stock - balance
        if firm[t]:
            # Fixed by the planner: pass the quantity through untouched, and do
            # not lot-size anything on top of it. Whatever it leaves uncovered
            # carries in the balance.
            net.append(max(0.0, shortfall))
            receipts.append(firm[t])
        elif shortfall > 0:
            qty = _lot_size(shortfall, item.lot_sizing)
            net.append(shortfall)
            receipts.append(qty)
            balance += qty
        else:
            net.append(0.0)
            receipts.append(0.0)
    return net, receipts


def _lot_size(need: float, ls: LotSizing) -> float:
    """One bucket's requirement rounded up by the item's ordering rule."""
    if need <= 0:
        return 0.0
    if ls.policy == "lot_for_lot":
        return need
    if ls.policy == "fixed_qty":
        return math.ceil(need / ls.fixed_qty - 1e-9) * ls.fixed_qty
    if ls.policy == "min_max":
        qty = max(ls.min_qty, need)
        if ls.multiple_of:
            qty = math.ceil(qty / ls.multiple_of - 1e-9) * ls.multiple_of
        return qty
    raise ValueError(f"{ls.policy!r} is not a per-bucket rule")


def _wagner_whitin_lots(net_req: list, ls: LotSizing) -> list:
    """Optimal lot sizes by dynamic programming (Wagner & Whitin, 1958).

    Forward DP over the net requirement vector. ``best[t]`` is the least cost of
    covering buckets 0..t-1, given that the last order was placed in bucket j:

        best[t] = min over j <= t of
                  best[j] + setup + holding * sum over k in [j, t) of (k - j) * d[k]

    O(n^2), which is nothing at horizon lengths a planner reads.

    Implemented here rather than delegating to ``stockpyl.wagner_whitin``,
    despite stockpyl being the locked stack's inventory library: stockpyl
    declares ``sphinx==4.5.0`` among its install requirements, and a pinned
    documentation toolchain inside an InvenTree plugin is a dependency conflict
    waiting to happen. stockpyl stays a test-only oracle -- ``test_netreq_math``
    checks this function against both its published instances and its solver.
    """
    n = len(net_req)
    if n == 0 or not any(net_req):
        return [0.0] * n

    setup, holding = ls.setup_cost, ls.holding_cost
    best = [0.0] + [math.inf] * n
    order_at = [0] * (n + 1)

    for t in range(1, n + 1):
        for j in range(t):
            carry = sum((k - j) * net_req[k] for k in range(j, t))
            cost = best[j] + setup + holding * carry
            # <= rather than <, so among equal-cost plans the LATEST order wins.
            # Ties are common on flat demand, and the later order holds less
            # stock for the same money -- less cash committed and less exposure
            # if the requirement moves. It also matches stockpyl's choice, which
            # is what lets the cross-check assert exact equality.
            if cost <= best[t] + 1e-9:
                best[t] = cost
                order_at[t] = j

    # Walk the choices back, ordering in bucket j everything it was chosen to cover.
    receipts = [0.0] * n
    t = n
    while t > 0:
        j = order_at[t]
        receipts[j] = float(sum(net_req[j:t]))
        t = j
    return receipts


def _project(item: Item, receipts: list) -> list:
    """Closing balance per bucket, given the receipts actually planned.

    Recomputed from the receipts rather than carried out of the netting loop, so
    both lot-sizing paths produce this series the same way.
    """
    balance = item.on_hand
    projected = []
    for t in range(len(item.gross_req)):
        # `receipts` already carries the firm quantities, so the firm series is
        # deliberately absent here: adding it would count the planner's order
        # twice and report stock that does not exist.
        balance += item.scheduled_receipt[t] + receipts[t] - item.gross_req[t]
        projected.append(balance)
    return projected


def _offset(item: Item, receipts: list):
    """Shift each receipt backward by the lead time to get its release bucket.

    A release that would fall before the horizon opens cannot be scheduled, so
    it is placed in bucket 0 -- release it now -- and reported as an exception
    carrying how many days late it already is. Dropping it would understate the
    plan; placing it silently would hide that the order is overdue.

    A release landing on a non-working bucket is pulled **backward** to the
    previous open one. Backward, not forward: starting later would make the
    receipt late, which is the thing the lead-time offset exists to prevent.
    Without this the plan schedules production on days the plant is shut, and
    nothing upstream of rccp can see it.
    """
    n = len(receipts)
    releases = [0.0] * n
    exceptions = []
    for t, qty in enumerate(receipts):
        if qty == 0:
            continue
        release_at = _previous_working(item, t - item.lead_time_days)
        if release_at < 0:
            releases[0] += qty
            exceptions.append(PlanException(
                sku_id=item.sku_id, loc_id=item.loc_id,
                kind="past_due_release", bucket_index=t,
                qty=round(qty, PRECISION), days_late=-release_at,
            ))
        else:
            releases[release_at] += qty
    return releases, exceptions


def _previous_working(item: Item, bucket: int) -> int:
    """Step back to the last open bucket at or before ``bucket``.

    Returns a negative index unchanged: that is already a past-due release and
    the caller reports it as one.
    """
    if item.working_buckets is None or bucket < 0:
        return bucket
    while bucket >= 0 and not item.working_buckets[bucket]:
        bucket -= 1
    return bucket


def _shortages(item: Item, projected: list) -> list:
    """Every bucket whose closing balance is negative.

    Not clamped: the magnitude of the negative is the size of the problem.
    """
    return [
        PlanException(
            sku_id=item.sku_id, loc_id=item.loc_id,
            kind="negative_on_hand", bucket_index=t,
            qty=round(balance, PRECISION),
        )
        for t, balance in enumerate(projected)
        if balance < 0
    ]


def _round(series: list) -> list:
    return [round(v, PRECISION) for v in series]
