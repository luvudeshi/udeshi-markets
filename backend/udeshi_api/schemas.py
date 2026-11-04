"""Request and response models.

Prices are exposed twice: in currency units (``price``) for display and as integer ticks
(``price_ticks``) for exact arithmetic. Simulated times are seconds since the start of the run.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from udeshi_markets import MAX_ORDER_QUANTITY

SYMBOL_PATTERN = r"^[A-Z0-9][A-Z0-9.\-]{0,15}$"

SideName = Literal["buy", "sell"]
StateName = Literal["created", "running", "paused", "stopped", "completed", "failed"]
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


MAX_API_AGENTS = 500
MAX_RATE_PER_HOUR = 3_600.0


class AgentsIn(_Model):
    """How many trading agents of each strategy; ``order_frequency`` scales how often they all
    act (2.0: twice as often as the defaults)."""

    random_traders: int = Field(default=10, ge=0, le=MAX_API_AGENTS)
    market_makers: int = Field(default=5, ge=0, le=MAX_API_AGENTS)
    momentum_traders: int = Field(default=5, ge=0, le=MAX_API_AGENTS)
    fundamental_traders: int = Field(default=5, ge=0, le=MAX_API_AGENTS)
    pairs_traders: int = Field(default=1, ge=0, le=MAX_API_AGENTS)
    order_frequency: float = Field(default=1.0, gt=0, le=100)

    @model_validator(mode="after")
    def _bounded_population(self) -> "AgentsIn":
        total = (
            self.random_traders
            + self.market_makers
            + self.momentum_traders
            + self.fundamental_traders
            + self.pairs_traders
        )
        if total > MAX_API_AGENTS:
            raise ValueError(f"at most {MAX_API_AGENTS} agents in one simulation, not {total}")
        return self


class PairIn(_Model):
    leader: str = Field(pattern=SYMBOL_PATTERN)
    follower: str = Field(pattern=SYMBOL_PATTERN)
    half_life_seconds: float = Field(default=120.0, gt=0, le=MAX_DURATION_SECONDS)
    deviation_volatility: float = Field(default=0.01, ge=0, le=1)


class MarketIn(_Model):
    """The market environment: how hidden fundamental values move and what disturbs them.
    Rates are per hour of simulated time; impacts are in log terms (0.01: about 1%)."""

    volatility: float = Field(
        default=0.02, ge=0, le=10, description="Log-volatility of fundamentals per sqrt(hour)."
    )
    volatile_multiplier: float = Field(default=3.0, ge=1, le=100)
    news_per_hour: float = Field(default=6.0, ge=0, le=MAX_RATE_PER_HOUR)
    news_impact: float = Field(default=0.01, ge=0, le=1)
    shocks_per_hour: float = Field(default=2.0, ge=0, le=MAX_RATE_PER_HOUR)
    shock_impact: float = Field(default=0.005, ge=0, le=1)
    liquidity_shocks_per_hour: float = Field(default=2.0, ge=0, le=MAX_RATE_PER_HOUR)
    liquidity_shock_quantity: int = Field(default=200, ge=1, le=MAX_ORDER_QUANTITY)
    pairs: list[PairIn] | None = Field(
        default=None,
        max_length=25,
        description="Cointegrated pairs. Omitted: ACME/NOVA when both exist; [] for none.",
    )


class SimulationCreate(_Model):
    instruments: list[InstrumentIn] | None = Field(
        default=None, min_length=1, max_length=50, description="Defaults to the five demo assets."
    )
    duration_seconds: float = Field(default=1800.0, gt=0, le=MAX_DURATION_SECONDS)
    seed: int = Field(default=1, ge=0, lt=1 << 64)
    order_latency_ms: float = Field(default=0.0, ge=0, le=60_000)
    bar_interval_seconds: float = Field(default=1.0, ge=0.001, le=MAX_DURATION_SECONDS)
    speed: float = Field(default=1.0, gt=0, le=10_000, description="Simulated seconds per second.")
    initial_cash: float = Field(
        default=1_000_000.0, ge=0, le=1e15, description="Starting cash of every participant."
    )
    agents: AgentsIn | None = Field(
        default=None, description="Trading agents. Omitted: none (only the API user trades)."
    )
    market: MarketIn | None = Field(
        default=None,
        description="The market environment. Omitted: the defaults if agents are requested, "
        "otherwise none (no fundamental values, nothing exogenous).",
    )


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
    initial_cash: float
    agents: AgentsIn | None = Field(description="The agent population (None: no agents).")
    market: MarketIn | None = Field(description="The market environment (None: none).")
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
    fundamental_value: float | None = Field(
        description="The hidden fundamental value, shown to API observers only: agents never "
        "see it (fundamental traders get noisy private signals). None without an environment."
    )


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


# ---- Agents and news -----------------------------------------------------------------------------

StrategyName = Literal[
    "random", "market_maker", "momentum", "fundamental", "pairs", "manual", "environment"
]


class AgentOut(_Model):
    """One participant's standing. Money is in currency, valued at the current marks (the
    midprice when both sides are quoted, else the last trade, else the reference price)."""

    id: int
    name: str
    strategy: StrategyName
    symbols: list[str] = Field(description="The instruments it trades (all, for the API user).")
    cash: float
    equity: float = Field(description="Cash plus positions at the marks.")
    total_pnl: float = Field(description="Equity minus initial cash.")
    realized_pnl: float = Field(description="Average-cost P&L of closed positions.")
    unrealized_pnl: float = Field(description="Open positions at the marks, less their cost.")
    return_pct: float | None = Field(description="total_pnl / initial_cash * 100.")
    positions: dict[str, int] = Field(description="Shares per symbol (negative: short).")
    trades: int = Field(description="Executions booked to its portfolio.")
    orders_submitted: int
    fills: int
    filled_quantity: int


class PositionOut(_Model):
    symbol: str
    quantity: int
    average_price: float | None
    realized_pnl: float
    unrealized_pnl: float
    bought: int
    sold: int
    trades: int


class OrderReportOut(_Model):
    time_seconds: float
    order_id: int | None
    symbol: str
    side: SideName
    type: Literal["limit", "market"]
    price: float | None
    quantity: int
    time_in_force: Literal["gtc", "ioc"]
    status: StatusName
    reject_reason: str | None
    cancel_reason: str | None
    filled_quantity: int
    resting_quantity: int
    cancelled_quantity: int


class AgentStatsOut(_Model):
    orders_submitted: int
    orders_rejected: int
    orders_filled: int
    cancels_sent: int
    fills: int
    filled_quantity: int
    aggressive_fills: int
    passive_fills: int


class AgentDetailOut(AgentOut):
    position_details: list[PositionOut]
    open_orders: list[OpenOrderOut]
    recent_orders: list[OrderReportOut] = Field(description="Its last 50 order reports.")
    stats: AgentStatsOut | None = Field(description="None for the API user and the environment.")
    metrics: dict[str, float] = Field(description="Strategy-specific measures.")


class NewsOut(_Model):
    time_seconds: float
    symbol: str
    impact: float = Field(description="Change in the log fundamental value.")
    sentiment: Literal["positive", "negative"]
    headline: str
