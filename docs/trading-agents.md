# Trading Agents and Market Dynamics

Phase 3 fills the exchange with autonomous traders and gives them something to trade on: a
market environment with hidden fundamental values, news, shocks and changing volatility. Code:
[`python/udeshi_markets/agents`](../python/udeshi_markets/agents) and
[`simulation/environment.py`](../python/udeshi_markets/simulation/environment.py).

```python
from udeshi_markets.agents import PopulationConfig, market_simulation
from udeshi_markets.simulation import MINUTE, EnvironmentConfig, PairConfig, SimulationConfig

simulation = market_simulation(
    SimulationConfig(duration_ns=30 * MINUTE, seed=7),
    PopulationConfig(random_traders=10, market_makers=5, momentum_traders=5,
                     fundamental_traders=5, pairs_traders=1),
    EnvironmentConfig(pairs=(PairConfig("ACME", "NOVA"),)),
)
summary = simulation.run()
```

## What an agent can know

Agents are kernel participants, so the Phase 2 information boundary applies to them:
* they act only through their own context;
* they see public market data (books, statistics, the anonymous tape) and reports about their
  own orders;
* public news reaches all of them at the same instant.

On top of that, a participant can be granted a **private signal** of fundamental values by
setting `signal_noise` (only fundamental traders are). `ctx.fundamental_estimate(i)` then
returns V·exp(σ·Z): the *current* value V with log-normal noise σ, where Z is drawn from the
agent's own stream. Nothing returns future values.

Look-ahead is impossible by construction rather than by discipline:
* The simulation generates the future lazily. Fundamentals at t + 1 do not exist at time t.
* Indicators are computed from the agent's own past observations (moving averages, the pairs
  z-score).
* The pairs trader even excludes the current observation from the statistics it is compared
  against.

## Accounting

The kernel books every trade into both participants' portfolios before any callback runs.
Agents read their own portfolio (`ctx.portfolio`, read-only) and cannot misreport it.
* **Exact.** Cash and cost bases are rational numbers (`Fraction`). A trade moves exactly
  q·p·tick of cash, using the tick as the decimal it was written as (0.05, not
  0.05000000000000000277). Cash and shares are conserved exactly across all participants,
  which the tests check after every market run, along with total P&L summing to zero.
* **Average cost.** Adding to a position adds to its cost basis. Reducing it realizes
  (price − average cost) × quantity × sign(position). A trade that crosses zero closes the old
  position and opens the new one at the trade price.
* **Marks.** Open positions are valued at the midprice when both sides are quoted, else the
  last trade, else the reference price. Unrealized P&L = Σ(q·mark − cost basis), so
  realized + unrealized = equity − initial cash.
* **No credit checks.** As at an exchange (not a broker), cash may go negative and positions
  may be short. Agents limit themselves with `max_position`. Their `room` check counts every
  order that could still fill: resting orders (including those being cancelled) and orders
  still on their way to the exchange. A limit therefore holds whatever the order latency; the
  tests check every agent's position after every event at 10, 100 and 500 ms latency.

## The agent framework

`TradingAgent` adds to a participant:
* a strategy name;
* its resting orders, tracked from reports, fills and cancels;
* its order history (the last 2,000 reports);
* statistics: submitted, rejected, filled, cancels, fills, filled quantity, aggressive and
  passive fills;
* a `metrics()` hook for strategy-specific measures.

**Timing.** Agents act at the times of a Poisson process. After each action the next comes after
an exponential delay with mean `mean_interval_s / (frequency × activity)`:
* `frequency` is the population's `order_frequency` knob;
* `activity` is the environment's current trading-activity level, so markets get busier in
  volatile regimes.

**Units.** Prices inside strategies are in ticks, and every threshold is relative to price (0.001
means 10 basis points), so a strategy behaves the same on any instrument. Prices computed in
floating point are rounded to whole ticks with a 1e-6 tolerance, because
10000 × 1.001 = 10009.999999999998 would otherwise round a tick too low.

## Strategies

### Random trader (zero intelligence)

Each action:
* picks a side with probability ½ and a size uniform in [1, `max_quantity`];
* with probability `market_order_probability`, sends a market order;
* otherwise sends a limit order at mark·(1 + `price_noise`·Z), with Z standard normal, so about
  half its limit orders cross the spread and trade at once;
* cancels its own orders older than `order_lifetime_s`;
* when at its position limit, trades only the other way.

This is the classic zero-intelligence liquidity of Gode and Sunder: no beliefs, just
randomness, which supplies two-sided order flow.

### Market maker (inventory-aware)

Its **fair value** f is an exponentially weighted average of trade prices,
f ← (1 − α)·f + α·p for each new trade at price p. It starts at the reference price.

**Inventory skew.** With position q and limit Q, quotes are centred on a reservation price

  r = f · (1 − κ · q / Q)

where κ is `skew_at_limit`. A long market maker lowers both quotes. Its ask becomes more
attractive to buyers, so inventory is sold off, and its bid less attractive to sellers. A short
one raises both. This is a linear form of the Avellaneda–Stoikov reservation price.

**Quotes.** Bid = ⌊r − h⌋ and ask = ⌈r + h⌉, with half-spread h = max(1 tick, `half_spread`·f).
Quote sizes are capped by the room left under Q, so the side that would take |q| past Q is
not quoted. At every action the quotes are cancelled and replaced. If earlier quotes or
cancels are still on their way (with order latency), the market maker waits for them to land
before requoting.

**Measured.** All amounts are in currency, and all are causal:
* **Spread earned:** for each passive fill at price p, (f − p)·qty on buys and (p − f)·qty on
  sells, with f the fair value when the fill happened.
* **Adverse selection** (markout): exactly τ = `markout_seconds` after each passive fill (a
  timer is set at the fill), (p − mark_{t+τ})·qty on buys and (mark_{t+τ} − p)·qty on sells.
  Positive means the market moved against the fill. It is the cost of trading with
  better-informed counterparties. The measurement happens at t + τ, so it never feeds back
  into decisions taken at t.
* **Inventory exposure** (|q|·mark) and **mark-to-market and realized P&L**, from the
  portfolio.

### Momentum trader

At each action it samples the mark. Once it has `long_window` samples:

  signal = SMA_short / SMA_long − 1

where the short and long simple moving averages cover the last `short_window` and
`long_window` samples, the current one included. Above `entry_threshold` it buys, and below
−`entry_threshold` it sells, `trade_size` at a time up to its limit. While
|signal| < `exit_threshold` it trades back towards flat. Orders are immediate-or-cancel, priced
`slippage` beyond the mark. Samples are taken at the agent's Poisson action times, so the
averages are over observations rather than fixed clock intervals.

### Fundamental trader

At each action, and immediately when news about its instrument is published, it draws an
estimate E of the current fundamental value. If mark < E·(1 − θ) it buys, paying at most
E·(1 − θ). If mark > E·(1 + θ) it sells, receiving at least E·(1 + θ). θ is `threshold`, and
orders are immediate-or-cancel. These are the informed traders: they move prices towards
value, and they are why market makers face adverse selection.

### Statistical arbitrage (pairs) trader

It trades a leader/follower pair whose fundamentals are cointegrated (see below). At each
action it computes the log spread s = ln P_leader − ln P_follower from the marks. From the
previous `window` values of s, **excluding the current one**, it computes

  z = (s − mean) / stdev

using the population standard deviation. With fewer than `min_observations` values, or with
zero variance, there is no signal; the zero-variance case is never divided by. The statistics
are computed on deviations from the window's first value. Otherwise rounding in the mean of
identical values leaves a spurious variance of about 1e-32, and a one-tick move would read as
|z| ≈ 10¹².
* **Entry, when flat:** z > `entry_z` sells the spread (sell leader, buy follower), and
  z < −`entry_z` buys it.
* **Exit:** once |z| < `exit_z`, it unwinds both legs.
* **Sizing:** the legs have equal currency value, `trade_size` leader shares against
  round(`trade_size`·P_leader/P_follower) follower shares. If either leg would exceed its
  position limit, both shrink together, so the trade stays balanced; if no balanced trade
  fits, there is none.

## Market dynamics

The environment is a participant with a reserved id (999999), so its timers are ordinary
digested events. It is rebuilt with the simulation on reset. It holds the true values and never
reveals them.

| Mechanism | Model |
| --- | --- |
| **Fundamental value** | x = ln V. Every `step_ns`: x += −σ²Δt/2 + σ√Δt·Z, which makes V a martingale. σ is `volatility` per √hour. |
| **Volatility regimes** | Calm and volatile alternate with exponential durations (means `calm_seconds`, `volatile_seconds`). In the volatile regime σ is multiplied by `volatile_multiplier`. |
| **Trading activity** | `activity` is 1 when calm and `volatile_activity` when volatile; every agent's action rate is multiplied by it. |
| **News** | A Poisson process per instrument (`news_per_hour`). Impact ±J, with J exponential (mean `news_impact`): x += impact. The item is published to every participant at the same instant; positive news raises value, negative news lowers it. |
| **Random price shocks** | The same jump process (`shocks_per_hour`, `shock_impact`), never announced. Prices only find out through informed trading. |
| **Liquidity shocks** | A market-wide Poisson process (`liquidity_shocks_per_hour`). The environment sends a market order of `liquidity_shock_quantity` shares on a random side and instrument, which sweeps the book. Its fills and P&L are booked like anyone's. |
| **Correlated pairs** | For a pair (leader L, follower F): x_F(t) = x_F(0) + (x_L(t) − x_L(0)) + D(t), with D an Ornstein–Uhlenbeck process. Its discretisation is D ← a·D + s·Z, with a = e^(−κΔt), κ = ln 2 / `half_life_seconds`, and s = `deviation_volatility`·√(1 − a²), so D has stationary standard deviation `deviation_volatility`. ln P_L − ln P_F is therefore stationary (cointegrated), which is what makes pairs trading meaningful. Followers have no news or shocks of their own. |

Every random draw comes from the environment's own seeded stream. Values are computed with the
platform-independent `deterministic_exp` and `deterministic_log`, so a seed reproduces the same
fundamentals on every machine. A golden market digest is pinned in the tests and checked on
Linux and macOS in CI.

## Configuration

* **`PopulationConfig`:** the number of agents per strategy, and `order_frequency`. Without
  arguments, `market_simulation` uses the default population and `default_environment(config)`,
  which pairs ACME with NOVA when both instruments exist (and leaves out pairs traders when
  there is no pair).
* **`EnvironmentConfig`:** volatility and regimes, news and shock rates and sizes, liquidity
  shocks, and pairs.
* **`SimulationConfig`:** instruments, duration, seed, latency, bar width, and `initial_cash`
  (the same for every participant, so returns are comparable).

`build_agents` assigns ids 1, 2, … and instruments round-robin, so every instrument gets its
share of each strategy; pairs traders take the configured pairs in turn.

## Calibration (measured, not assumed)

With the default population (10 random traders, 5 market makers, 5 momentum, 5 fundamental and
1 pairs trader) and the default environment over 30 simulated minutes, the mean absolute gap
between price and fundamental, |ln(mark / V)| sampled every 10 s, was:
* 0.33% across seeds 1, 2 and 3 (maximum 4.2%, just after large news);
* 1.14% with the fundamental traders' original 100-share limit, which is why they now have a
  1,000-share limit and 25-share trades every 2 s.

A typical news jump is 1%, so prices absorb news within a few tenths of a percent. The test
suite asserts a mean gap below 0.6%. On one development machine (Apple M4, Python 3.12), a
30-minute run of 26 agents (seeds 1 to 3: 80,000 to 101,000 events and 12,000 to 15,000 trades)
took 1.0 to 1.2 s. These are single measurements; Phase 6 will benchmark properly.

## Limitations

* **Agents trade one instrument each** (pairs traders: one pair). Cross-asset portfolios and risk
  budgets are not modelled.
* **The market maker's fair value comes from trades only.** It does not read the book or the
  news, so it is slow to react to news; that is a source of its adverse selection.
* **Samples are irregular.** Momentum and pairs indicators are computed over observations taken
  at Poisson times.
* **No fees, rebates, borrowing costs or credit limits.**
* **The environment has a single market-wide regime** rather than one per instrument.
