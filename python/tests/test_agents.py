"""Trading agents: each strategy's decision rule in isolation, then whole markets."""

from __future__ import annotations

import math
from collections.abc import Sequence
from fractions import Fraction
from typing import Any, cast

import pytest

from udeshi_markets import OrderStatus, Side, TimeInForce
from udeshi_markets._engine import CancelReason, RejectReason
from udeshi_markets.agents import (
    FundamentalTrader,
    MarketMaker,
    MomentumTrader,
    PairsTrader,
    PopulationConfig,
    RandomTrader,
    TradingAgent,
    build_agents,
    market_simulation,
)
from udeshi_markets.simulation import (
    MINUTE,
    SECOND,
    DeterministicRng,
    EnvironmentConfig,
    Fill,
    News,
    OrderIntent,
    OrderReport,
    PairConfig,
    PublicTrade,
    SimulationConfig,
    SimulationContext,
    deterministic_log,
)


class FakePortfolio:
    def __init__(self) -> None:
        self.positions: dict[int, int] = {}

    def position(self, instrument: int) -> int:
        return self.positions.get(instrument, 0)


class FakeContext:
    """The parts of SimulationContext that strategies use, with every action recorded."""

    def __init__(self, marks: dict[int, float], ticks: dict[int, float] | None = None) -> None:
        self.now = 0
        self.rng = DeterministicRng(7)
        self.activity = 1.0
        self.portfolio = FakePortfolio()
        self.marks = marks
        self.ticks = ticks or dict.fromkeys(marks, 0.01)
        self.trades: dict[int, list[PublicTrade]] = {}
        self.estimates: dict[int, float] = {}
        self.orders: list[OrderIntent] = []
        self.cancels: list[tuple[int, int]] = []
        self.wakeups: list[tuple[int, str]] = []

    def mark(self, instrument: int) -> Fraction:
        return Fraction(self.marks[instrument])

    def tick_size(self, instrument: int) -> float:
        return self.ticks[instrument]

    def reference_price(self, instrument: int) -> int:
        return round(self.marks[instrument])

    def recent_trades(self, instrument: int, limit: int = 50) -> list[PublicTrade]:
        return self.trades.get(instrument, [])[-limit:]

    def fundamental_estimate(self, instrument: int) -> float:
        return self.estimates[instrument]

    def submit(self, intent: OrderIntent) -> None:
        self.orders.append(intent)

    def cancel(self, instrument: int, order_id: int) -> None:
        self.cancels.append((instrument, order_id))

    def wakeup(self, delay_ns: int, tag: str = "") -> None:
        self.wakeups.append((delay_ns, tag))

    def take(self) -> list[tuple[Side, int, int, bool]]:
        """(side, quantity, price, immediate) of the orders since the last call."""
        taken = [
            (o.side, o.quantity, o.price, o.time_in_force is TimeInForce.IMMEDIATE_OR_CANCEL)
            for o in self.orders
        ]
        self.orders.clear()
        return taken


def as_ctx(fake: FakeContext) -> SimulationContext:
    return cast(SimulationContext, fake)


def resting(agent: TradingAgent, fake: FakeContext, order_id: int, intent: OrderIntent) -> None:
    """Delivers the report of a resting order to the agent."""
    report = OrderReport(
        time=fake.now,
        intent=intent,
        order_id=order_id,
        status=OrderStatus.RESTING,
        reject_reason=RejectReason.NONE,
        cancel_reason=CancelReason.NONE,
        filled_quantity=0,
        resting_quantity=intent.quantity,
        cancelled_quantity=0,
    )
    agent.on_order_report(as_ctx(fake), report)


# ---- Market maker ----------------------------------------------------------------------------


def maker(**kwargs: Any) -> tuple[MarketMaker, FakeContext]:
    fake = FakeContext({0: 10_000.0})
    params: dict[str, Any] = {
        "half_spread": 0.0005,
        "quote_size": 20,
        "skew_at_limit": 0.001,
        "max_position": 200,
    }
    params.update(kwargs)
    agent = MarketMaker(1, [0], **params)
    agent.on_start(as_ctx(fake))
    return agent, fake


def test_a_flat_market_maker_quotes_symmetrically_around_fair_value() -> None:
    agent, fake = maker()
    agent.act(as_ctx(fake), "act")
    assert fake.take() == [(Side.BUY, 20, 9995, False), (Side.SELL, 20, 10005, False)]


@pytest.mark.parametrize(
    ("position", "expected"),
    [
        # Reservation price 10000 * (1 - 0.001 * q / 200); quotes at +-5 ticks around it.
        (100, [(Side.BUY, 20, 9990, False), (Side.SELL, 20, 10000, False)]),
        (-100, [(Side.BUY, 20, 10000, False), (Side.SELL, 20, 10010, False)]),
        (190, [(Side.BUY, 10, 9985, False), (Side.SELL, 20, 9996, False)]),
        (200, [(Side.SELL, 20, 9995, False)]),  # At the limit: no bid at all.
        (-200, [(Side.BUY, 20, 10005, False)]),
    ],
)
def test_inventory_skews_both_quotes(
    position: int, expected: list[tuple[Side, int, int, bool]]
) -> None:
    agent, fake = maker()
    fake.portfolio.positions[0] = position
    agent.act(as_ctx(fake), "act")
    assert fake.take() == expected


def test_quotes_are_replaced_and_fair_value_follows_trades() -> None:
    agent, fake = maker(fair_value_alpha=0.5)
    resting(agent, fake, 41, OrderIntent(0, Side.BUY, 20, price=9995))
    fake.trades[0] = [PublicTrade(5, 0, 0, 10_100, 3, Side.BUY)]
    agent.act(as_ctx(fake), "act")
    assert fake.cancels == [(0, 41)]
    assert agent.fair[0] == 10_050.0  # Halfway to the trade.
    agent.act(as_ctx(fake), "act")
    assert agent.fair[0] == 10_050.0  # The same trade is not counted twice.


def test_spread_earned_and_adverse_selection_are_measured_in_currency() -> None:
    agent, fake = maker(markout_seconds=5.0)
    # Passive buy 10 at 9995 when fair is 10000: earned 5 ticks * 10 * 0.01 = 0.50.
    agent.on_fill(as_ctx(fake), Fill(1, 0, 0, 77, Side.BUY, 9995, 10, is_aggressor=False))
    assert agent.spread_earned == pytest.approx(0.5)
    fake.now = 4 * SECOND
    agent.act(as_ctx(fake), "act")
    assert agent.markouts_measured == 0  # Not yet 5 seconds.
    fake.now = 5 * SECOND
    fake.marks[0] = 9_985.0  # The market fell after the market maker bought.
    agent.act(as_ctx(fake), "act")
    assert agent.adverse_selection == pytest.approx((9995 - 9985) * 10 * 0.01)
    assert agent.metrics()["markouts_measured"] == 1.0
    # Its own aggressive fills are neither spread earned nor adverse selection.
    agent.on_fill(as_ctx(fake), Fill(2, 0, 0, 78, Side.SELL, 9990, 5, is_aggressor=True))
    assert agent.spread_earned == pytest.approx(0.5)


# ---- Momentum ----------------------------------------------------------------------------------


def test_momentum_needs_a_full_window_then_follows_the_trend() -> None:
    fake = FakeContext({0: 10_000.0})
    agent = MomentumTrader(
        2, [0], short_window=2, long_window=4, entry_threshold=0.001, exit_threshold=0.0002
    )
    for price in (10_000.0, 10_000.0, 10_000.0):
        fake.marks[0] = price
        agent.act(as_ctx(fake), "act")
    assert fake.take() == []  # Fewer than long_window samples: no signal.
    assert agent.signal[0] is None
    for price in (10_050.0, 10_100.0):
        fake.marks[0] = price
        agent.act(as_ctx(fake), "act")
    # After 10100: short = 10075, long = 10037.5, signal = +0.37%: buy, IOC, 0.1% above mark.
    orders = fake.take()
    assert orders[-1] == (Side.BUY, 10, math.ceil(10_100 * 1.001), True)
    assert agent.signal[0] == pytest.approx(10_075 / 10_037.5 - 1)


def test_momentum_sells_downtrends_and_exits_when_the_signal_fades() -> None:
    fake = FakeContext({0: 10_000.0})
    agent = MomentumTrader(2, [0], short_window=2, long_window=4)
    for price in (10_000.0, 10_000.0, 9_950.0, 9_900.0):
        fake.marks[0] = price
        agent.act(as_ctx(fake), "act")
    assert fake.take()[-1] == (Side.SELL, 10, math.floor(9_900 * 0.999), True)
    fake.portfolio.positions[0] = -10
    for price in (9_900.0, 9_900.0, 9_900.0, 9_900.0):
        fake.marks[0] = price
        agent.act(as_ctx(fake), "act")
    assert fake.take()[-1] == (Side.BUY, 10, math.ceil(9_900 * 1.001), True)  # Back to flat.


# ---- Fundamental -------------------------------------------------------------------------------


def test_fundamental_trader_buys_below_value_and_sells_above() -> None:
    fake = FakeContext({0: 10_000.0})
    agent = FundamentalTrader(3, [0], threshold=0.003, trade_size=25, max_position=1_000)
    fake.estimates[0] = 10_100.0  # Market 1% below value: buy, paying at most value - 0.3%.
    agent.act(as_ctx(fake), "act")
    assert fake.take() == [(Side.BUY, 25, math.floor(10_100 * 0.997), True)]
    fake.estimates[0] = 9_900.0
    agent.act(as_ctx(fake), "act")
    assert fake.take() == [(Side.SELL, 25, math.ceil(9_900 * 1.003), True)]
    fake.estimates[0] = 10_010.0  # Within the band: nothing.
    agent.act(as_ctx(fake), "act")
    assert fake.take() == []


def test_fundamental_trader_reacts_to_news_about_its_instruments_only() -> None:
    fake = FakeContext({0: 10_000.0, 1: 5_000.0})
    agent = FundamentalTrader(3, [0])
    agent.on_news(as_ctx(fake), News(0, 1, 0.01, "other"))
    assert fake.wakeups == []
    agent.on_news(as_ctx(fake), News(0, 0, -0.02, "mine"))
    assert fake.wakeups == [(0, "news")]
    assert agent.signal_noise == 0.005  # It is granted a private signal.


# ---- Pairs -------------------------------------------------------------------------------------


def pair_trader(**kwargs: Any) -> tuple[PairsTrader, FakeContext]:
    fake = FakeContext({0: 10_000.0, 1: 5_000.0}, {0: 0.01, 1: 0.01})
    params: dict[str, Any] = {"window": 5, "min_observations": 3, "entry_z": 1.5, "exit_z": 0.5}
    params.update(kwargs)
    return PairsTrader(4, 0, 1, **params), fake


def spread_of(fake: FakeContext) -> float:
    return deterministic_log(fake.marks[0] * 0.01) - deterministic_log(fake.marks[1] * 0.01)


def test_the_z_score_uses_only_earlier_observations() -> None:
    agent, fake = pair_trader()
    history = []
    for follower in (5_000.0, 5_010.0, 4_990.0):
        fake.marks[1] = follower
        agent.act(as_ctx(fake), "act")
        history.append(spread_of(fake))
        assert agent.z is None  # Not enough history yet when each value arrives.
    fake.marks[1] = 4_900.0  # The spread jumps up.
    agent.act(as_ctx(fake), "act")
    mean = sum(history) / 3
    stdev = math.sqrt(sum((s - mean) ** 2 for s in history) / 3)
    assert agent.z == pytest.approx((spread_of(fake) - mean) / stdev)
    assert list(agent.spreads) == [*history, spread_of(fake)]  # Appended after the signal.


def test_pairs_trader_sells_a_wide_spread_with_equal_value_legs_then_unwinds() -> None:
    agent, fake = pair_trader(exit_z=1.0)
    for follower in (5_000.0, 5_010.0, 4_990.0):
        fake.marks[1] = follower
        agent.act(as_ctx(fake), "act")
    fake.marks[1] = 4_900.0
    agent.act(as_ctx(fake), "act")
    assert agent.z is not None
    assert agent.z > 1.5
    # Sell 10 of the leader, buy the follower shares worth the same: 10 * 100.00 / 49.00 = 20.
    assert fake.take() == [
        (Side.SELL, 10, math.floor(10_000 * 0.999), True),
        (Side.BUY, 20, math.ceil(4_900 * 1.001), True),
    ]
    fake.portfolio.positions.update({0: -10, 1: 20})
    fake.marks[1] = 5_000.0
    agent.act(as_ctx(fake), "act")
    assert agent.z is not None
    assert abs(agent.z) < 1.0
    assert fake.take() == [
        (Side.BUY, 10, math.ceil(10_000 * 1.001), True),
        (Side.SELL, 20, math.floor(5_000 * 0.999), True),
    ]


def test_no_signal_without_variance() -> None:
    agent, fake = pair_trader()
    for _ in range(6):
        agent.act(as_ctx(fake), "act")
    assert agent.z is None
    assert fake.take() == []


# ---- Random trader and the base class ----------------------------------------------------------


def test_random_trader_respects_its_position_limit_and_cancels_stale_orders() -> None:
    fake = FakeContext({0: 10_000.0})
    agent = RandomTrader(5, [0], max_position=10, max_quantity=10, order_lifetime_s=30.0)
    fake.portfolio.positions[0] = 10  # Full long: it may only sell.
    for _ in range(50):
        agent.act(as_ctx(fake), "act")
    assert {side for side, *_ in fake.take()} == {Side.SELL}
    stale = OrderIntent(0, Side.SELL, 3, price=10_050)
    resting(agent, fake, 99, stale)
    fake.now = 31 * SECOND
    agent.act(as_ctx(fake), "act")
    assert (0, 99) in fake.cancels


def test_room_counts_resting_orders_that_could_still_fill() -> None:
    fake = FakeContext({0: 10_000.0})
    agent = RandomTrader(5, [0], max_position=50)
    fake.portfolio.positions[0] = 20
    resting(agent, fake, 1, OrderIntent(0, Side.BUY, 15, price=9_990))
    assert agent.room(as_ctx(fake), 0, Side.BUY) == 15
    assert agent.room(as_ctx(fake), 0, Side.SELL) == 70
    agent.on_fill(as_ctx(fake), Fill(9, 0, 0, 1, Side.BUY, 9_990, 5, is_aggressor=False))
    assert agent.open_orders[1].remaining == 10
    assert agent.stats.passive_fills == 1


def test_agents_act_at_poisson_times_scaled_by_activity() -> None:
    fake = FakeContext({0: 10_000.0})
    agent = RandomTrader(5, [0], mean_interval_s=2.0)
    for activity in (1.0, 4.0):
        fake.activity = activity
        fake.wakeups.clear()
        for _ in range(4000):
            agent.schedule(as_ctx(fake))
        mean = sum(delay for delay, _ in fake.wakeups) / len(fake.wakeups)
        assert mean == pytest.approx(2.0 * SECOND / activity, rel=0.05)


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: RandomTrader(1, []), "at least one instrument"),
        (lambda: RandomTrader(1, [0], mean_interval_s=0), "mean_interval_s"),
        (lambda: MarketMaker(1, [0], fair_value_alpha=0), "fair_value_alpha"),
        (lambda: MomentumTrader(1, [0], short_window=5, long_window=5), "windows"),
        (lambda: PairsTrader(1, 0, 0), "two different"),
        (lambda: PairsTrader(1, 0, 1, entry_z=1, exit_z=1), "thresholds"),
        (lambda: FundamentalTrader(1, [0], signal_noise=-1), "signal_noise"),
    ],
)
def test_agent_parameters_are_validated(factory: Any, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        factory()


def test_room_counts_orders_still_on_their_way() -> None:
    """With order latency, an agent can act again before its last order is reported. The
    order in flight must count against the limit, or two actions size against the same room."""
    fake = FakeContext({0: 10_000.0})
    agent = MomentumTrader(2, [0], max_position=20)
    agent.limit(as_ctx(fake), 0, Side.BUY, 15, 10_010, immediate=True)
    assert agent.room(as_ctx(fake), 0, Side.BUY) == 5
    assert agent.busy(0)
    # Its report arrives (filled): the shares are now in the position, not in flight.
    fake.portfolio.positions[0] = 15
    agent.on_order_report(as_ctx(fake), _report(OrderIntent(0, Side.BUY, 15, price=10_010)))
    assert agent.room(as_ctx(fake), 0, Side.BUY) == 5
    assert not agent.busy(0)


def _report(intent: OrderIntent, status: OrderStatus = OrderStatus.FILLED) -> OrderReport:
    return OrderReport(
        time=0,
        intent=intent,
        order_id=1,
        status=status,
        reject_reason=RejectReason.NONE,
        cancel_reason=CancelReason.NONE,
        filled_quantity=intent.quantity if status is OrderStatus.FILLED else 0,
        resting_quantity=0,
        cancelled_quantity=0,
    )


def test_a_market_maker_waits_for_its_last_quotes_before_requoting() -> None:
    agent, fake = maker()
    agent.act(as_ctx(fake), "act")
    assert len(fake.take()) == 2
    agent.act(as_ctx(fake), "act")  # Its quotes have not been reported yet.
    assert fake.take() == []
    assert fake.cancels == []


def test_a_cancel_is_sent_once() -> None:
    fake = FakeContext({0: 10_000.0})
    agent = RandomTrader(5, [0])
    resting(agent, fake, 7, OrderIntent(0, Side.BUY, 3, price=9_990))
    agent.cancel_all(as_ctx(fake))
    agent.cancel_all(as_ctx(fake))
    assert fake.cancels == [(0, 7)]
    assert agent.room(as_ctx(fake), 0, Side.BUY) == 200 - 3  # It could still fill.


@pytest.mark.parametrize("latency_ms", [10, 100, 500])
def test_position_limits_hold_at_every_event_whatever_the_latency(latency_ms: int) -> None:
    config = SimulationConfig(
        duration_ns=2 * MINUTE, seed=1, order_latency_ns=latency_ms * 1_000_000
    )
    sim = market_simulation(config)
    sim.start()
    agents = [p for p in sim.participants.values() if isinstance(p, TradingAgent)]
    while sim.state.name == "RUNNING":
        sim.step(1)
        for agent in agents:
            for instrument in agent.instruments:
                position = sim.portfolio(agent.participant_id).position(instrument)
                assert abs(position) <= agent.max_position, (agent.name, sim.now, position)


def test_identical_spreads_give_no_signal_despite_rounding() -> None:
    """The float mean of 21 copies of this spread is not exactly the spread itself, which used
    to leave a variance of about 1e-32 and turn a one-tick move into |z| ~ 1e12."""
    agent, _ = pair_trader(window=21, min_observations=21)
    spread = deterministic_log(100.0) - deterministic_log(42.5)
    agent.spreads.extend([spread] * 21)
    assert agent._z_score(spread + 1e-4) is None


def test_pairs_legs_shrink_together_when_one_is_capped() -> None:
    fake = FakeContext({0: 5_000.0, 1: 1_000.0}, {0: 0.05, 1: 0.01})  # 250.00 and 10.00.
    agent = PairsTrader(4, 0, 1, max_position=100, trade_size=10)
    sizes = agent._leg_sizes(as_ctx(fake), Side.BUY, Side.SELL, 250.0, 10.0)
    # 10 leader shares would need 250 follower shares; only 100 fit, so the leader leg is 4.
    assert sizes == (4, 100)
    fake.portfolio.positions[1] = -100  # No room at all on the follower: no trade.
    assert agent._leg_sizes(as_ctx(fake), Side.BUY, Side.SELL, 250.0, 10.0) is None


def test_markouts_are_taken_exactly_at_the_horizon() -> None:
    agent, fake = maker(markout_seconds=5.0)
    agent.on_fill(as_ctx(fake), Fill(1, 0, 0, 77, Side.BUY, 9995, 10, is_aggressor=False))
    assert fake.wakeups[-1] == (5 * SECOND, "markout")
    fake.now = 5 * SECOND
    fake.marks[0] = 9_990.0
    agent.on_wakeup(as_ctx(fake), "markout")
    assert agent.markouts_measured == 1
    assert agent.adverse_selection == pytest.approx(5 * 10 * 0.01)
    assert fake.take() == []  # A measurement only: no requote.


def test_the_default_market_needs_no_configuration() -> None:
    summary = market_simulation(SimulationConfig(duration_ns=MINUTE)).run()
    assert summary.trades > 0


# ---- Whole markets ---------------------------------------------------------------------------


def market(seed: int = 3, minutes: int = 5, **population: Any) -> Any:
    config = SimulationConfig(duration_ns=minutes * MINUTE, seed=seed)
    env = EnvironmentConfig(pairs=(PairConfig("ACME", "NOVA"),))
    return market_simulation(config, PopulationConfig(**population), env)


def test_population_builds_every_strategy_round_robin() -> None:
    config = SimulationConfig()
    agents = build_agents(
        config, PopulationConfig(random_traders=7, market_makers=5), (PairConfig("ACME", "NOVA"),)
    )
    assert [a.participant_id for a in agents] == list(range(1, len(agents) + 1))
    by_strategy: dict[str, list[Sequence[int]]] = {}
    for agent in agents:
        by_strategy.setdefault(agent.strategy, []).append(agent.instruments)
    assert sorted(tuple(i) for i in by_strategy["market_maker"]) == [(0,), (1,), (2,), (3,), (4,)]
    assert len(by_strategy["random"]) == 7
    assert by_strategy["pairs"] == [(0, 1)]
    with pytest.raises(ValueError, match="need at least one pair"):
        build_agents(config, PopulationConfig(pairs_traders=1))
    with pytest.raises(ValueError, match="non-negative"):
        PopulationConfig(random_traders=-1)


def test_a_market_conserves_cash_and_shares_exactly() -> None:
    sim = market(minutes=10)
    summary = sim.run()
    assert summary.trades > 1000
    initial = Fraction(1_000_000)
    assert sum(sim.portfolio(pid).cash for pid in sim.participants) == initial * len(
        sim.participants
    )
    for instrument in range(5):
        assert sum(sim.portfolio(pid).position(instrument) for pid in sim.participants) == 0
    marks = sim.marks()
    assert sum(sim.portfolio(pid).total_pnl(marks) for pid in sim.participants) == 0


def test_every_agents_books_agree_with_its_fills() -> None:
    sim = market(minutes=5)
    sim.run()
    for pid, participant in sim.participants.items():
        if isinstance(participant, TradingAgent):
            portfolio = sim.portfolio(pid)
            trades = sum(portfolio.details(i).trades for i in range(5))
            assert trades == participant.stats.fills
            for instrument in range(5):
                assert abs(portfolio.position(instrument)) <= participant.max_position


def test_prices_track_fundamentals() -> None:
    """The informed traders keep prices close to the hidden values: the mean absolute gap
    stays well under the size of a typical news jump (1%)."""
    sim = market(minutes=30)
    sim.start()
    gaps = []
    for second in range(60, 1800, 30):
        sim.run_until(second * SECOND)
        env = sim.environment
        assert env is not None
        for i in range(5):
            tick = sim.exchange.instrument(i).tick_size
            gaps.append(abs(math.log(float(sim.mark(i)) * tick / env.fundamental(i))))
    assert sum(gaps) / len(gaps) < 0.006


def test_market_runs_are_reproducible_and_pinned() -> None:
    first, second = market(seed=9), market(seed=9)
    assert first.run() == second.run()
    summary = market(seed=9).run()
    assert (summary.trades, summary.volume, summary.digest) == (
        GOLDEN_TRADES,
        GOLDEN_VOLUME,
        GOLDEN_DIGEST,
    )
    assert market(seed=10).run().digest != summary.digest


def test_the_default_population_trades_all_five_strategies() -> None:
    sim = market(minutes=10)
    sim.run()
    traded = {
        participant.strategy
        for participant in sim.participants.values()
        if isinstance(participant, TradingAgent) and participant.stats.fills
    }
    assert traded == {"random", "market_maker", "momentum", "fundamental", "pairs"}


def test_market_orders_and_instrument_bounds_from_agents_are_valid() -> None:
    sim = market(minutes=5, random_traders=20)
    summary = sim.run()
    assert summary.orders_rejected == 0  # Agents never send malformed orders.
    assert summary.state.name == "COMPLETED"


# A five-minute, 26-agent market. Strategies use only exactly rounded arithmetic (and the
# deterministic log and exp), so these hold on every platform; CI checks Linux and macOS.
GOLDEN_TRADES = 2297
GOLDEN_VOLUME = 16929
GOLDEN_DIGEST = "b0460effe81fdcd43c2ad1a44f5f2b6d"
