"""Service configuration, read from environment variables (prefix ``UDESHI_``)."""

import os
from dataclasses import dataclass, field


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return default if raw is None else float(raw)


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return default if raw is None else int(raw)


@dataclass(frozen=True, slots=True)
class Settings:
    tick_seconds: float = 0.05
    """Wall-clock interval at which live simulations advance and publish updates."""
    max_sessions: int = 8
    """Simulations kept in memory; the oldest finished ones are evicted beyond this."""
    max_events_per_tick: int = 200_000
    """Cap on events processed per tick, so a fast simulation cannot starve the event loop."""
    stream_queue_size: int = 512
    """Messages buffered per WebSocket client before the oldest are dropped."""
    stream_book_depth: int = 15
    cors_origins: tuple[str, ...] = field(default=("http://localhost:3000",))

    def __post_init__(self) -> None:
        if not 0.001 <= self.tick_seconds <= 5.0:
            raise ValueError("tick_seconds must lie in [0.001, 5]")
        if self.max_sessions < 1:
            raise ValueError("max_sessions must be at least 1")
        if self.max_events_per_tick < 1:
            raise ValueError("max_events_per_tick must be at least 1")
        if self.stream_queue_size < 1:
            raise ValueError("stream_queue_size must be at least 1")

    @classmethod
    def from_environment(cls) -> "Settings":
        # Defaults come from an instance: with slots, class attributes are slot descriptors.
        defaults = cls()
        origins = os.environ.get("UDESHI_CORS_ORIGINS")
        return cls(
            tick_seconds=_float("UDESHI_TICK_SECONDS", defaults.tick_seconds),
            max_sessions=_int("UDESHI_MAX_SESSIONS", defaults.max_sessions),
            max_events_per_tick=_int("UDESHI_MAX_EVENTS_PER_TICK", defaults.max_events_per_tick),
            stream_queue_size=_int("UDESHI_STREAM_QUEUE_SIZE", defaults.stream_queue_size),
            stream_book_depth=_int("UDESHI_STREAM_BOOK_DEPTH", defaults.stream_book_depth),
            cors_origins=(
                tuple(o.strip() for o in origins.split(",") if o.strip())
                if origins
                else defaults.cors_origins
            ),
        )
