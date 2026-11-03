"""Simulated time.

Every timestamp inside a simulation is an integer number of nanoseconds since the start of the
run. Simulated time advances only by processing events; it never reads the wall clock.
"""

from typing import Final

NANOSECOND: Final = 1
MICROSECOND: Final = 1_000
MILLISECOND: Final = 1_000_000
SECOND: Final = 1_000_000_000
MINUTE: Final = 60 * SECOND
HOUR: Final = 60 * MINUTE


def seconds(value: float) -> int:
    """Converts seconds to integer nanoseconds (rounded to the nearest nanosecond)."""
    return round(value * SECOND)


def to_seconds(nanoseconds: int) -> float:
    """Converts integer nanoseconds to (floating-point) seconds."""
    return nanoseconds / SECOND
