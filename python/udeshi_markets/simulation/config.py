"""Simulation configuration (immutable, validated at construction)."""

from dataclasses import dataclass, field

from udeshi_markets._engine import price_to_ticks
from udeshi_markets.simulation.simtime import MINUTE, SECOND

_MAX_SEED = (1 << 64) - 1
MAX_BARS = 10_000_000
"""Most OHLCV bars one simulation may record (instruments x duration / bar interval). Every bar
is built eagerly, so this bounds a run's memory (about 150 bytes per bar) and the time spent
closing bars when simulated time jumps."""


@dataclass(frozen=True, slots=True)
class InstrumentConfig:
    """One tradable asset. Prices are in currency units; they must lie on the tick grid."""

    symbol: str
    reference_price: float
    tick_size: float = 0.01

    def __post_init__(self) -> None:
        # The exchange's own conversion validates the tick size and that the price is on grid.
        if self.reference_price_ticks <= 0:
            raise ValueError(f"{self.symbol}: reference price must be positive")

    @property
    def reference_price_ticks(self) -> int:
        return price_to_ticks(self.reference_price, self.tick_size)


DEFAULT_INSTRUMENTS: tuple[InstrumentConfig, ...] = (
    InstrumentConfig("ACME", 100.00),
    InstrumentConfig("NOVA", 42.50),
    InstrumentConfig("QUANT", 250.00, tick_size=0.05),
    InstrumentConfig("ORBIT", 18.75),
    InstrumentConfig("VECTOR", 73.20),
)


@dataclass(frozen=True, slots=True)
class SimulationConfig:
    """Everything that determines a run. Two runs with equal configs (and the same participants
    and external inputs) produce identical results."""

    instruments: tuple[InstrumentConfig, ...] = field(default=DEFAULT_INSTRUMENTS)
    duration_ns: int = 30 * MINUTE
    seed: int = 1
    order_latency_ns: int = 0
    """Delay between a participant submitting an order or cancel and its arrival at the exchange."""
    bar_interval_ns: int = SECOND
    """Width of the OHLCV bars recorded for every instrument."""
    recent_trade_capacity: int = 1_000
    """Trades per instrument kept by the exchange's own recent-trade buffer."""
    trade_history_limit: int | None = None
    """Trades per instrument kept by the recorder (None keeps every trade)."""

    def __post_init__(self) -> None:
        if not self.instruments:
            raise ValueError("at least one instrument is required")
        symbols = [instrument.symbol for instrument in self.instruments]
        if len(set(symbols)) != len(symbols):
            raise ValueError("instrument symbols must be unique")
        if self.duration_ns <= 0:
            raise ValueError("duration_ns must be positive")
        if not 0 <= self.seed <= _MAX_SEED:
            raise ValueError("seed must be an integer in [0, 2**64)")
        if self.order_latency_ns < 0:
            raise ValueError("order_latency_ns must be non-negative")
        if not 0 < self.bar_interval_ns <= self.duration_ns:
            raise ValueError("bar_interval_ns must be positive and at most duration_ns")
        bars = -(-self.duration_ns // self.bar_interval_ns) * len(self.instruments)
        if bars > MAX_BARS:
            raise ValueError(
                f"{bars} bars (instruments x duration / bar interval) is more than {MAX_BARS}: "
                "use a wider bar interval or a shorter duration"
            )
        if self.recent_trade_capacity <= 0:
            raise ValueError("recent_trade_capacity must be positive")
        if self.trade_history_limit is not None and self.trade_history_limit <= 0:
            raise ValueError("trade_history_limit must be positive when given")

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(instrument.symbol for instrument in self.instruments)
