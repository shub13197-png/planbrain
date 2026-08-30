"""The long-haul fairness ledger. Pure; no database, no dates.

The ledger is the product, not the solver. Without a cumulative record carried
year-to-date, every planning run restarts fairness from zero and the
best-placed truck takes every long run — which is the complaint being modelled,
not a hypothetical.

**Flows are recorded; the cumulative is derived.** A stored cumulative would make
a fact row mean "running total" for these measures and "quantity in this bucket"
for every other, which `docs/contracts/facts.md` forbids permanently. So
``record`` accumulates sparse per-bucket flows and ``long_haul_km`` sums them
over the opening carry-in.
"""

from dataclasses import dataclass, field

#: The fact_fleet measures, defined at item 9 from what the ledger needs. The
#: grain was reserved with an empty measure set at item 2 precisely so nothing
#: would write to it before this decision was made.
MEASURES = ("long_haul_km", "total_km", "trips_assigned")


@dataclass(frozen=True)
class Truck:
    truck_id: int
    capacity_kg: float
    available: bool = True


@dataclass(frozen=True)
class Trip:
    trip_id: int
    bucket: int
    distance_km: float
    load_kg: float
    is_long_haul: bool


@dataclass
class Ledger:
    """Cumulative long-haul kilometres per truck, plus this run's sparse flows.

    ``opening`` is a scalar per truck — a position at one instant, like opening
    stock in netreq, and in production it comes from the system of record.
    """

    opening_long_haul_km: dict
    flows: dict = field(default_factory=dict)

    @classmethod
    def opening(cls, carry_in: dict) -> "Ledger":
        return cls(opening_long_haul_km=dict(carry_in))

    def _check(self, truck_id: int) -> None:
        if truck_id not in self.opening_long_haul_km:
            # A truck absent from the opening ledger is a broken import, not a
            # truck with zero kilometres. Defaulting to zero would silently make
            # it the fairest choice in the fleet, and it would take every long
            # run until someone noticed.
            raise KeyError(
                f"truck {truck_id} is not in the opening ledger; the fleet "
                f"reference data and the ledger disagree"
            )

    def record(self, truck_id: int, trip: Trip) -> None:
        """Add one trip's kilometres to the flows for its own bucket.

        The bucket is the trip's, so a same-day dispatch lands in the bucket it
        runs in and is immediately visible to the next fairness decision in the
        same pass.
        """
        self._check(truck_id)
        bucket = self.flows.setdefault(
            (truck_id, trip.bucket), {m: 0.0 for m in MEASURES}
        )
        bucket["total_km"] += trip.distance_km
        bucket["trips_assigned"] += 1
        if trip.is_long_haul:
            bucket["long_haul_km"] += trip.distance_km

    def long_haul_km(self, truck_id: int) -> float:
        """Cumulative long-haul kilometres: carry-in plus this run's flows."""
        self._check(truck_id)
        return self.opening_long_haul_km[truck_id] + sum(
            measures["long_haul_km"]
            for (tid, _bucket), measures in self.flows.items()
            if tid == truck_id
        )

    def total_km(self, truck_id: int) -> float:
        self._check(truck_id)
        return sum(
            measures["total_km"]
            for (tid, _bucket), measures in self.flows.items()
            if tid == truck_id
        )

    def trips(self, truck_id: int) -> int:
        self._check(truck_id)
        return int(sum(
            measures["trips_assigned"]
            for (tid, _bucket), measures in self.flows.items()
            if tid == truck_id
        ))

    def distribution(self, truck_ids=None) -> list:
        """Cumulative long-haul kilometres across the fleet, for the metric."""
        ids = sorted(truck_ids if truck_ids is not None else self.opening_long_haul_km)
        return [self.long_haul_km(t) for t in ids]

    def deficit(self, truck_id: int, truck_ids=None) -> float:
        """How far behind the fleet leader this truck is.

        The quantity a largest-deficit rule maximises. Zero for the leader.
        """
        return max(self.distribution(truck_ids)) - self.long_haul_km(truck_id)

    def next_by_deficit(self, truck_ids):
        """The truck furthest behind. Ties break on truck_id, so the assignment
        is deterministic and a rerun does not reshuffle the fleet."""
        candidates = list(truck_ids)
        if not candidates:
            return None
        return min(candidates, key=lambda t: (self.long_haul_km(t), t))
