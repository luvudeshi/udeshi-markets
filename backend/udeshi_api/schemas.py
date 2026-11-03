"""Request and response models.

Prices are exposed twice: in currency units (``price``) for display and as integer ticks
(``price_ticks``) for exact arithmetic. Simulated times are seconds since the start of the run.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from udeshi_markets import MAX_ORDER_QUANTITY

SYMBOL_PATTERN = r"^[A-Z0-9][A-Z0-9.\-]{0,15}$"

SideName = Literal["buy", "sell"]
StateName = Literal["created", "running", "paused", "stopped", "completed"]
StatusName = Literal["rejected", "resting", "filled", "cancelled"]


MAX_DURATION_SECONDS = 7 * 24 * 3600


class _Model(BaseModel):
    # JSON has no infinities or NaN, and Python's parser would otherwise accept them.
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


# ---- Requests ----------------------------------------------------------------------------------


class InstrumentIn(_Model):
    symbol: str = Field(pattern=SYMBOL_PATTERN)
    reference_price: float = Field(gt=0)
    tick_size: float = Field(default=0.01, gt=0)


class SimulationCreate(_Model):
    instruments: list[InstrumentIn] | None = Field(
        default=None, min_length=1, max_length=50, description="Defaults to the five demo assets."
    )
    duration_seconds: float = Field(default=1800.0, gt=0, le=MAX_DURATION_SECONDS)
    seed: int = Field(default=1, ge=0, lt=1 << 64)
    order_latency_ms: float = Field(default=0.0, ge=0, le=60_000)
    bar_interval_seconds: float = Field(default=1.0, ge=0.001, le=MAX_DURATION_SECONDS)
    speed: float = Field(default=1.0, gt=0, le=10_000, description="Simulated seconds per second.")


class SpeedIn(_Model):
    speed: float = Field(gt=0, le=10_000)


class StepIn(_Model):
    events: int = Field(default=1, ge=1, le=1_000_000)


class OrderIn(_Model):
    side: SideName
    type: Literal["limit", "market"] = "limit"
    price: float | None = Field(default=None, gt=0, description="Required for limit orders.")
    quantity: int = Field(ge=1, le=MAX_ORDER_QUANTITY)
    time_in_force: Literal["gtc", "ioc"] = "gtc"
    self_trade_prevention: Literal["allow", "cancel_resting", "cancel_incoming"] = "allow"

    @model_validator(mode="after")
    def _price_matches_type(self) -> "OrderIn":
        if self.type == "limit" and self.price is None:
            raise ValueError("limit orders require a price")
        if self.type == "market" and self.price is not None:
            raise ValueError("market orders must not have a price")
        return self


# ---- Responses ---------------------------------------------------------------------------------


class Health(_Model):
    status: Literal["ok"] = "ok"
    version: str


class SimulationSummaryOut(_Model):
    events_processed: int
    orders_submitted: int
    orders_rejected: int
    cancels_requested: int
    trades: int
    volume: int
    digest: str


class SimulationOut(_Model):
    id: str
    state: StateName
    active: bool
    created_at: str
    time_seconds: float
    duration_seconds: float
    progress: float
    speed: float
    seed: int
    order_latency_ms: float
    bar_interval_seconds: float
    symbols: list[str]
    error: str | None = Field(default=None, description="Set if the live runner failed.")
    summary: SimulationSummaryOut


class LevelOut(_Model):
    price: float
    price_ticks: int
    quantity: int
    orders: int


class OrderBookOut(_Model):
    symbol: str
    time_seconds: float
    bids: list[LevelOut]
    asks: list[LevelOut]
    best_bid: float | None
    best_ask: float | None
    midprice: float | None
    spread: float | None


class TradeOut(_Model):
    trade_id: int
    time_seconds: float
    price: float
    price_ticks: int
    quantity: int
    aggressor_side: SideName


class MarketOut(_Model):
    symbol: str
    tick_size: float
    reference_price: float
    last_price: float | None
    change: float | None
    change_percent: float | None
    open: float | None
    high: float | None
    low: float | None
    volume: int
    trade_count: int
    vwap: float | None
    best_bid: float | None
    best_ask: float | None
    midprice: float | None
    spread: float | None


class MarketDetailOut(MarketOut):
    bid_depth: int
    ask_depth: int
    resting_orders: int
    notional: float


class BarOut(_Model):
    start_seconds: float
    end_seconds: float
    open: float
    high: float
    low: float
    close: float
    volume: int
    trade_count: int
    vwap: float | None


class FillOut(_Model):
    trade_id: int
    price: float
    price_ticks: int
    quantity: int
    is_aggressor: bool


class OrderOut(_Model):
    order_id: int | None
    symbol: str
    status: StatusName
    reject_reason: str | None
    cancel_reason: str | None
    filled_quantity: int
    resting_quantity: int
    cancelled_quantity: int
    fills: list[FillOut]


class OpenOrderOut(_Model):
    order_id: int
    symbol: str
    side: SideName
    price: float
    price_ticks: int
    original_quantity: int
    remaining_quantity: int
    time_seconds: float


class CancelOut(_Model):
    order_id: int
    symbol: str
    cancelled_quantity: int
