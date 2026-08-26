"""Small helpers the ladder needs, kept out of core so core stays arithmetic."""

from ..netreq.core import Item, LotSizing


def lot_for_lot() -> LotSizing:
    """The zero-granularity baseline: order exactly what is needed, when needed."""
    return LotSizing(policy="lot_for_lot")


def item_factory(sku_id: int, loc_id: int):
    """Build an Item for the ladder, holding identity fixed across rungs.

    The rungs differ only in safety stock and lot sizing. Anything else varying
    between them would put an unnamed effect into a named term.
    """

    def make(*, lead_time_days, on_hand, safety_stock, lot_sizing, gross_req):
        return Item(
            sku_id=sku_id,
            loc_id=loc_id,
            lead_time_days=lead_time_days,
            on_hand=on_hand,
            safety_stock=safety_stock,
            lot_sizing=lot_sizing,
            gross_req=gross_req,
            scheduled_receipt=[0.0] * len(gross_req),
        )

    return make
