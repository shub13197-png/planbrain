"""The working calendar, and the seasonal period derived from it.

The seasonal period is a property of the customer's calendar, not a constant in
a metrics module. A plant closed on Sunday imposes a seven-day cycle on its
demand history whether or not anyone modelled it; a continuous operation imposes
none. Hardcoding 7 works until the first customer runs round the clock, and then
it silently mis-scales every accuracy number rather than failing.

Derivation, stated plainly because it is a judgement and not a formula:

* **Any weekly pattern with at least one non-working day → 7.** The cycle length
  is the week, regardless of how many days inside it are worked. A six-day week
  and a five-day week are both seven-day cycles.
* **All seven days worked → 1.** The calendar imposes no repeating structure, so
  the honest naive baseline is one-step.

Note what this does *not* claim: that demand has no weekly shape when the plant
runs continuously. Retail demand peaks at weekends whoever is open. This derives
the period the *calendar* forces, which is the part we can know from
configuration rather than from the data.
"""

from dataclasses import dataclass

MONDAY, SATURDAY, SUNDAY = 0, 5, 6

#: Weekday indices as `date.weekday()` returns them.
ALL_WEEKDAYS = frozenset(range(7))


@dataclass(frozen=True)
class WorkingCalendar:
    """Which weekdays the operation runs. ``date.weekday()`` indexing, 0 = Monday."""

    working_weekdays: frozenset

    def __post_init__(self):
        if not self.working_weekdays:
            raise ValueError("a calendar with no working days cannot plan anything")
        if not self.working_weekdays <= ALL_WEEKDAYS:
            raise ValueError(f"weekday indices must be 0-6, got {sorted(self.working_weekdays)}")

    @property
    def seasonal_period(self) -> int:
        """Buckets in one repeat of the calendar cycle. See module docstring."""
        return 1 if self.working_weekdays == ALL_WEEKDAYS else 7

    @property
    def is_continuous(self) -> bool:
        return self.working_weekdays == ALL_WEEKDAYS

    def is_working(self, day) -> bool:
        return day.weekday() in self.working_weekdays


#: The demo plant: six days, closed Sunday.
SIX_DAY_WEEK = WorkingCalendar(frozenset(ALL_WEEKDAYS - {SUNDAY}))

#: A five-day office or plant.
FIVE_DAY_WEEK = WorkingCalendar(frozenset(ALL_WEEKDAYS - {SATURDAY, SUNDAY}))

#: Round-the-clock operation. No calendar-imposed cycle.
CONTINUOUS = WorkingCalendar(ALL_WEEKDAYS)
