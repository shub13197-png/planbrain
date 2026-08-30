"""Greedy largest-deficit assignment. Pure; no database, no dates.

The baseline any solver has to beat. Deliberately simple, because the point of
item 9 is a ledger that is right and explainable, not an optimiser.

**Feasibility binds before fairness.** A fair assignment that cannot physically
happen is worse than an unfair one, so the candidate set is filtered first and
fairness only chooses among what survives. A trip with no feasible truck comes
back **unassigned** rather than being forced onto a truck that cannot carry it --
the same principle `haulplan_output` established at item 1 by allowing
``truck_id: null``.

**Why largest-deficit.** Assign each trip to the feasible truck furthest behind
the fleet leader on cumulative long-haul kilometres. It is the obvious rule, it
is explainable to a driver in one sentence -- *you got this run because you were
furthest behind* -- and explainability is worth more here than optimality. A
driver who does not believe the allocation is fair will not be persuaded by a
solver's objective value.

**Long-haul trips are processed first within a bucket.** This is not cosmetic
ordering, and the first version got it wrong. Processing by trip id meant the
short-haul runs were assigned first, consuming exactly the trucks furthest
behind, so by the time the long-haul trips came up the only feasible trucks were
the ones already ahead. Fairness is measured on long-haul kilometres, so the
long-haul trips must have first claim on the fair trucks. On the demo this took
long-haul assignment from 13 trips to 65 and the Jain index from a rounding
error to a real movement.

After that, the order is (bucket, long-haul first, then trip id) so the result
is deterministic and a rerun does not reshuffle the fleet.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Assignment:
    trip_id: int
    truck_id: int | None
    bucket: int
    distance_km: float
    is_long_haul: bool
    reason: str = ""


@dataclass
class Plan:
    assignments: list = field(default_factory=list)
    unassigned: list = field(default_factory=list)


def feasible_trucks(trip, trucks, busy) -> list:
    """Trucks that could physically take this trip.

    Three filters, all hard: available, big enough, and not already out on
    another trip in the same bucket.
    """
    return [
        truck.truck_id
        for truck in trucks
        if truck.available
        and truck.capacity_kg >= trip.load_kg
        and (truck.truck_id, trip.bucket) not in busy
    ]


def why_infeasible(trip, trucks, busy) -> str:
    """Which filter emptied the candidate set, for the unassigned report.

    Reported because "no truck available" is useless to a planner. Whether the
    fleet is too small, fully committed, or simply not big enough for the load
    are three different problems with three different fixes.
    """
    if not any(t.available for t in trucks):
        return "no truck is available"
    if not any(t.available and t.capacity_kg >= trip.load_kg for t in trucks):
        return f"no available truck carries {trip.load_kg:,.0f} kg"
    return "every capable truck is already committed in this bucket"


def assign(trips, trucks, ledger) -> Plan:
    """Assign trips to trucks, fairest-first among feasible candidates.

    Records each assignment in the ledger as it goes, so a trip assigned earlier
    in the run immediately affects who is furthest behind for the next one. That
    is the whole mechanism: without it, every trip in a bucket would go to the
    same truck.
    """
    plan = Plan()
    busy = set()

    for trip in sorted(trips, key=lambda t: (t.bucket, not t.is_long_haul, t.trip_id)):
        candidates = feasible_trucks(trip, trucks, busy)
        if not candidates:
            plan.unassigned.append(
                Assignment(
                    trip_id=trip.trip_id, truck_id=None, bucket=trip.bucket,
                    distance_km=trip.distance_km, is_long_haul=trip.is_long_haul,
                    reason=why_infeasible(trip, trucks, busy),
                )
            )
            continue

        chosen = ledger.next_by_deficit(candidates)
        ledger.record(chosen, trip)
        busy.add((chosen, trip.bucket))
        plan.assignments.append(
            Assignment(
                trip_id=trip.trip_id, truck_id=chosen, bucket=trip.bucket,
                distance_km=trip.distance_km, is_long_haul=trip.is_long_haul,
                reason="furthest behind among feasible trucks",
            )
        )

    return plan
