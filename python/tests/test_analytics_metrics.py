"""Metrics on a scripted market where every value can be worked out by hand, then on real ones."""

from __future__ import annotations

import pytest

from udeshi_markets import OrderType, Side
from udeshi_markets.agents import market_simulation
from udeshi_markets.analytics import MarketObserver, analyze, run_observed
from udeshi_markets.simulation import (
    MINUTE,
    SECOND,
    InstrumentConfig,
    OrderIntent,
    ScriptedCancel,
    ScriptedOrder,
    ScriptedParticipant,
    Simulation,
    SimulationConfig,
)

ONE = (InstrumentConfig("AAA", 100.0, tick_size=1.0),)  # One tick = 1 currency unit.


def scripted_market() -> tuple[Simulation, MarketObserver]:
    """A maker quotes 99 / 101 (mid 100). At 1 s a taker buys 4 at 101. At 2 s the maker
    requotes 101 / 103 (mid 102), which is still the quote at the 5 s markout horizon."""
    maker = ScriptedParticipant(
        1,
        [
            ScriptedOrder(0, OrderIntent(0, Side.BUY, 10, price=99)),
            ScriptedOrder(0, OrderIntent(0, Side.SELL, 10, price=101)),
            ScriptedCancel(2 * SECOND, 0),
            ScriptedCancel(2 * SECOND, 1),
            ScriptedOrder(2 * SECOND, OrderIntent(0, Side.BUY, 10, price=101)),
            ScriptedOrder(2 * SECOND, OrderIntent(0, Side.SELL, 10, price=103)),
        ],
    )
    taker = ScriptedParticipant(
        2, [ScriptedOrder(SECOND, OrderIntent(0, Side.BUY, 4, order_type=OrderType.MARKET))]
    )
    config = SimulationConfig(instruments=ONE, duration_ns=10 * SECOND, initial_cash=1000.0)
    sim = Simulation(config, lambda: [maker, taker])
    observer = MarketObserver(sim, interval_ns=SECOND, markout_ns=5 * SECOND)
    run_observed(sim, observer)
    return sim, observer


def test_spread_decomposition_by_hand() -> None:
    _, observer = scripted_market()
    [micro] = analyze(observer).microstructure
    # s = +1, p = 101, m = 100, m' = 102 (in ticks of 1.0).
    assert micro.effective_spread_bps == pytest.approx(2 * (101 - 100) / 100 * 1e4)  # 200
    assert micro.realized_spread_bps == pytest.approx(2 * (101 - 102) / 100 * 1e4)  # -200
    assert micro.price_impact_bps == pytest.approx(2 * (102 - 100) / 100 * 1e4)  # 400
    assert micro.measured_trades == 1
    assert (micro.trades_without_quote, micro.trades_without_markout) == (0, 0)
    # Submitted: 10 + 10 + 4 + 10 + 10 = 44 shares; one trade of 4 fills two orders.
    assert micro.fill_rate == pytest.approx(2 * 4 / 44)
    assert (micro.average_trade_size, micro.buyer_initiated_fraction) == (4.0, 1.0)


def test_market_metrics_by_hand() -> None:
    _, observer = scripted_market()
    [market] = analyze(observer).market
    # Samples at 0, 1, ..., 10 s: mid 100 until the requote at 2 s, then 102.
    assert observer.series[0].mids == [100.0, 100.0] + [102.0] * 9
    assert market.mean_spread == pytest.approx(2.0)
    assert market.mean_depth == pytest.approx((20 + 16 + 9 * 20) / 11)
    assert market.pricing_error_bps is None  # No environment, no fundamental.
    assert (market.trades, market.volume, market.last_price) == (1, 4, 101.0)
    assert market.volatility_per_hour is not None
    assert market.volatility_per_hour > 0


def test_agent_metrics_by_hand() -> None:
    _, observer = scripted_market()
    agents = {a.participant_id: a for a in analyze(observer).agents}
    taker, maker = agents[2], agents[1]
    # The taker bought 4 at 101; the end mark is the mid, 102.
    assert taker.total_pnl == pytest.approx(4.0)
    assert taker.unrealized_pnl == pytest.approx(4.0)
    assert taker.realized_pnl == 0.0
    assert taker.win_rate is None  # No position was ever reduced.
    assert (taker.trades, taker.fill_rate, taker.average_price) == (1, 1.0, {"AAA": 101.0})
    assert taker.return_pct == pytest.approx(0.4)
    assert maker.total_pnl == pytest.approx(-4.0)  # A zero-sum market.
    # After its sale the maker's equity peaks at 1004 (cash 1404, short 4 at the 100 mid), then
    # falls to 996 when the mid moves to 102: a drawdown of 8.
    assert maker.max_drawdown == pytest.approx(8.0)
    assert taker.sharpe_ratio is not None


def test_metrics_of_an_empty_market_are_undefined_not_zero() -> None:
    config = SimulationConfig(instruments=ONE, duration_ns=5 * SECOND)
    sim = Simulation(config, lambda: [ScriptedParticipant(1, [])])
    observer = MarketObserver(sim)
    run_observed(sim, observer)
    analytics = analyze(observer)
    [market], [micro], [agent] = analytics.market, analytics.microstructure, analytics.agents
    assert market.mean_spread is None
    assert market.one_sided_fraction == 1.0
    assert market.mean_imbalance is None
    assert market.volatility_per_hour is None
    assert micro.effective_spread_bps is None
    assert micro.fill_rate is None
    assert agent.sharpe_ratio is None  # Flat equity: undefined.
    assert agent.max_drawdown == 0.0


def test_a_real_market_decomposes_exactly_and_is_reproducible() -> None:
    def run() -> object:
        sim = market_simulation(SimulationConfig(duration_ns=3 * MINUTE, seed=5))
        observer = MarketObserver(sim)
        run_observed(sim, observer)
        return analyze(observer)

    analytics = run()
    for micro in analytics.microstructure:  # type: ignore[attr-defined]
        assert micro.effective_spread_bps == pytest.approx(
            micro.realized_spread_bps + micro.price_impact_bps
        )
        assert micro.measured_trades > 50
    for market in analytics.market:  # type: ignore[attr-defined]
        assert market.pricing_error_bps is not None  # There is an environment.
    assert run() == analytics


def test_observation_does_not_change_the_simulation() -> None:
    def digest(observe: bool) -> str:
        sim = market_simulation(SimulationConfig(duration_ns=2 * MINUTE, seed=8))
        if observe:
            run_observed(sim, MarketObserver(sim))
        else:
            sim.run()
        return sim.digest

    assert digest(True) == digest(False)


def test_observer_parameters_are_validated() -> None:
    sim = Simulation(SimulationConfig(instruments=ONE))
    with pytest.raises(ValueError, match="must be positive"):
        MarketObserver(sim, interval_ns=0)
