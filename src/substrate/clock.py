"""The governance clock.

The architecture treats time as a property in its own right: a credential's
revocation has an effective time, an act's record preserves the time the act
occurred, and the bounded-latency property of the invalidation surface is a
bound on elapsed governance time. None of that is available to a substrate
that cannot read a clock.

Two things are deliberately separated here.

The *governance clock* is the logical tick the invalidation surface's bound is
measured in. It is monotone non-decreasing and it advances once per act, which
is what makes "no act executes under stale governance" a statement about a
countable sequence rather than about wall-clock scheduling. The formal
companion's bounded-latency theorem is proved over this clock.

The *wall-clock reading* is the operator's record of when an act happened. It is
what an inquiry asks for and what a latency measurement in seconds needs. It is
not monotone across processes, it is not comparable across operators without a
declared skew bound, and the architecture says so.

An act records both. The governance tick orders acts within a substrate; the
wall-clock reading dates them. Conflating the two is the error the formal
companion's clock-guard finding turns on, so they are distinct fields with
distinct guarantees.

Clocks are injectable because a demonstration that must produce a reproducible
ledger cannot read a real clock: an act's content identity covers its recorded
time, so a real clock makes every act identity new on every run. `FixedClock`
exists for that, and for tests.
"""

from __future__ import annotations

import datetime


class GovernanceClock:
    """A monotone tick counter paired with a wall-clock reading.

    `tick()` advances the governance clock and returns the new value. The
    first act on a substrate occurs at tick 1; tick 0 is the state before
    any act, which is what the formal model's initial state represents.

    `wall()` returns an ISO-8601 instant in UTC, to the second. Seconds are
    the resolution the architecture's claims need, and a finer resolution
    would invite comparisons across operators that the skew bound does not
    support.
    """

    def __init__(self) -> None:
        self._tick = 0

    def tick(self) -> int:
        self._tick += 1
        return self._tick

    def reading(self) -> int:
        """The current tick without advancing it."""
        return self._tick

    def wall(self) -> str:
        return (
            datetime.datetime.now(datetime.timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )


class FixedClock(GovernanceClock):
    """A governance clock whose wall-clock reading never moves.

    The governance tick still advances, because the ordering it provides is
    what the invalidation surface's bound is measured in and a demonstration
    that could not advance it would not exercise the mechanism. What is
    pinned is the wall-clock reading, so a demonstration's ledger is
    byte-identical across runs and its act identities are stable.
    """

    def __init__(self, wall: str = "1970-01-01T00:00:00Z") -> None:
        super().__init__()
        self._wall = wall

    def wall(self) -> str:
        return self._wall
