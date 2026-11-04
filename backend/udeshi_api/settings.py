"""Service configuration, read from environment variables (prefix ``UDESHI_``)."""

import os
from dataclasses import dataclass, field

DEFAULT_EXPERIMENT_WORKERS = min(4, os.cpu_count() or 1)

MAX_BOOK_DEPTH = 200
"""Deepest order book served, per side (the order-book endpoint's ``depth`` limit)."""


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
    """Messages buffered per WebSocket client. A client that falls further behind is sent a
    fresh snapshot instead of the updates it missed."""
    stream_book_depth: int = 15
    """Levels per side in streamed books (at most the order-book endpoint's maximum)."""
    max_bars_per_simulation: int = 500_000
    """Cap on the OHLCV bars a simulation can produce (instruments x duration / bar interval),
    which bounds its memory and the work of closing bars when simulated time jumps."""
    trade_history_limit: int = 10_000
    """Recent trades kept per instrument for the tape endpoint and stream snapshots."""
    cors_origins: tuple[str, ...] = field(
        default=("http://localhost:3000", "http://127.0.0.1:3000")
    )
    database_url: str | None = None
    """Where experiment results are kept (a SQLAlchemy URL, e.g. postgresql+psycopg://...).
    None: an in-memory SQLite database, lost when the process exits."""
    experiment_workers: int = DEFAULT_EXPERIMENT_WORKERS
    """Processes that run experiment simulations, in parallel with the live markets."""

    def __post_init__(self) -> None:
        if not 0.001 <= self.tick_seconds <= 5.0:
            raise ValueError("tick_seconds must lie in [0.001, 5]")
        if self.max_sessions < 1:
            raise ValueError("max_sessions must be at least 1")
        if self.max_events_per_tick < 1:
            raise ValueError("max_events_per_tick must be at least 1")
        if self.stream_queue_size < 1:
            raise ValueError("stream_queue_size must be at least 1")
        if not 1 <= self.stream_book_depth <= MAX_BOOK_DEPTH:
            # A negative depth would make every streamed book fail to build (and with it any
            # order request that publishes one, after its order had executed); 0 would stream
            # empty books.
            raise ValueError(f"stream_book_depth must lie in [1, {MAX_BOOK_DEPTH}]")
        if self.max_bars_per_simulation < 1:
            raise ValueError("max_bars_per_simulation must be at least 1")
        if self.trade_history_limit < 1:
            raise ValueError("trade_history_limit must be at least 1")
        if self.experiment_workers < 1:
            raise ValueError("experiment_workers must be at least 1")

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
            max_bars_per_simulation=_int("UDESHI_MAX_BARS", defaults.max_bars_per_simulation),
            trade_history_limit=_int("UDESHI_TRADE_HISTORY", defaults.trade_history_limit),
            database_url=os.environ.get("UDESHI_DATABASE_URL") or None,
            experiment_workers=_int("UDESHI_EXPERIMENT_WORKERS", defaults.experiment_workers),
            cors_origins=(
                tuple(o.strip() for o in origins.split(",") if o.strip())
                if origins
                else defaults.cors_origins
            ),
        )
