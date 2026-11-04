# Udeshi Markets

A high-performance simulated electronic stock exchange: a deterministic C++20 limit-order-book
matching engine, autonomous trading agents competing inside it, and a quantitative research
environment for studying the resulting market behaviour.

> Everything is simulated. No real money, brokerage accounts, or real-world order routing.

**Status:** all six phases are complete:
- the matching engine;
- the Python bindings, the discrete-event simulation kernel and the exchange API;
- autonomous trading agents in a market environment with hidden fundamentals and news;
- quantitative analytics, and an experiment framework with Monte Carlo runs and PostgreSQL
  persistence;
- a real-time trading terminal and experiment dashboard (Next.js);
- benchmarks, with profiling-driven optimisation.

## Matching engine (C++20)

- **Price–time priority continuous auction.** Limit (GTC / IOC) and market orders, partial fills,
  multi-level sweeps, O(1) cancellation, and self-trade prevention.
- **Exact arithmetic.** Prices are integer ticks; floating point never enters matching.
- **Purpose-built data structures:**
  - best-first `std::map` price ladders (best price is always `begin()`);
  - intrusive doubly linked FIFO queues per level;
  - a slab allocator for orders;
  - a flat open-addressing id index for cancels (no allocation per order).
- **Deterministic.**
  - A global sequence number on every event.
  - Simulated (not wall-clock) timestamps.
  - Integer-only matching and `-ffp-contract=off`.
  - Identical input gives bit-identical output on every platform.
- **Tested adversarially:**
  - scenario and exact-contract tests;
  - a structural invariant checker;
  - differential testing against an independent naive model over about 250k randomized
    operations (multi-instrument, malformed input, books of 10k+ orders), comparing every
    result and the full book;
  - quantity-conservation and limit-price properties;
  - golden cross-platform determinism digests;
  - allocation-failure injection;
  - ASan/UBSan with pool poisoning;
  - mutation testing: 68/68 injected bugs caught on Linux and macOS, re-checked weekly in CI.

```
Sell 10 @ 101, Sell 5 @ 102, then Buy 12 @ 102
→ trades: 10 @ 101, 2 @ 102      → book: 3 @ 102 remains on the ask side
```

## Simulation kernel and exchange API (Python)

- **pybind11 bindings** expose the engine to Python by value, with native enums and
  hand-written type stubs that a test keeps in sync with the module.
- **Discrete-event kernel.**
  - Simulated nanosecond clock; events ordered by (time, sequence).
  - Participants act only through a context bound to their identity: they can cancel only
    their own orders and see only public data plus reports about their own orders.
  - Configurable order latency; callbacks never run re-entrantly inside an exchange call.
- **Reproducible by construction.**
  - A platform-independent RNG (SplitMix64 with its own logarithm), one stream per participant.
  - A BLAKE2b digest of every event and outcome. Golden digests are checked on Linux and macOS.
- **FastAPI service.**
  - REST endpoints for the simulation lifecycle, market data (book, tape, OHLCV bars) and
    manual order entry.
  - A WebSocket stream with bounded per-client queues.
  - A live runner that paces simulated time by measured wall-clock time × speed.
  - One error format for every failure.

## Trading agents and market dynamics (Python)

- **Five strategies** compete in the exchange:
  - zero-intelligence random traders;
  - inventory-aware market makers that track spread earned and adverse selection;
  - moving-average momentum traders;
  - fundamental traders acting on a noisy private signal;
  - pairs traders using a rolling z-score computed from past observations only.
- **A market environment** with:
  - hidden fundamental values that follow a random walk with calm and volatile regimes;
  - public news and unannounced shocks;
  - liquidity shocks that sweep the book;
  - trading-activity levels;
  - cointegrated pairs.
- **Exact portfolio accounting.** The kernel keeps every participant's cash and positions as
  exact fractions, with average-cost realized P&L. Cash and shares are conserved exactly, and
  the tests check it after every market run.

The details are in [docs/trading-agents.md](docs/trading-agents.md).

## Analytics and experiments

- **Metrics** with documented definitions, causal measurement, and undefined values reported as
  undefined rather than NaN:
  - market: volatility, spreads, depth, imbalance, and pricing error against fundamentals;
  - microstructure: effective and realized spread, price impact, fill rates;
  - agents: P&L, Sharpe ratio, drawdown, exposure, win rate.
- **Experiments:**
  - independent seeded runs, Monte Carlo distributions and parameter sweeps;
  - summaries with Student-t confidence intervals, and Welch comparisons between experiments;
  - CSV export;
  - runs in parallel worker processes, with results identical to a sequential run.

Details: [docs/quantitative-metrics.md](docs/quantitative-metrics.md) and
[docs/market-microstructure.md](docs/market-microstructure.md).

## Trading terminal (Next.js)

![The trading terminal: markets, chart, order book, leaderboard and trade tape](docs/images/terminal.png)

- **Live market views**, updated over one WebSocket per simulation:
  - market overview;
  - order book ladder with depth chart;
  - candlestick chart with volume, trade prints, news markers and the hidden fundamental value;
  - trade tape naming buyer and seller;
  - sortable agent leaderboard with per-agent detail;
  - order entry as the API user.
- **Simulation controls:** start, pause, resume, step, stop, reset and speed, plus a
  configuration dialog for the agent population and the market environment.
- **Experiment dashboard:** create sweeps and Monte Carlo runs, watch them progress, see each
  metric's distribution per sweep point, and compare points with Welch's t-test.
- **Display only.** Every number on screen is computed by the backend (even the cumulative
  depth). The TypeScript types are generated from the API's OpenAPI schema.
- **Tested:**
  - unit tests of the stream reducer and connection;
  - Playwright tests that drive the real backend and check the values shown against the REST
    API.

Details: [docs/frontend.md](docs/frontend.md).

## Performance

Measured, not estimated: one core of an Apple M4, Release build, single instrument. The method
(workloads recorded against a real engine and replayed exactly), every workload, the Linux
results from CI, and the profiles behind the optimisations are in
[docs/benchmarking.md](docs/benchmarking.md).

<!-- bench:readme:start -->
| Engine workload | Throughput | Mean | p99 | p99.9 |
| --- | ---: | ---: | ---: | ---: |
| Mixed flow: adds, cancels, IOC and market orders, about 10k resting orders | 26.7 M ops/s | 37 ns | 167 ns | 250 ns |
| Same flow through the `Exchange` (plus market data) | 24.6 M ops/s | 41 ns | 167 ns | 250 ns |
| Add and cancel on a 100k-order book | 28.4 M ops/s | 35 ns | 125 ns | 167 ns |
| Random cancels on a 1M-order book | 14.9 M ops/s | 67 ns | 292 ns | 792 ns |
| A market order sweeping 10 levels (100 fills), timed alone | — | 1.6 µs | 1.8 µs | 2.2 µs |
<!-- bench:readme:end -->

* **Reading the columns.** Means are per operation in a stream. Percentiles time each
  operation alone, including the clock reads; the Apple clock ticks every 41.7 ns.
* **Memory.** A resting order occupies 130 bytes of heap (one million orders), with no
  allocation per order.
* **Optimisation.** Profiling showed malloc and free first, from a hash-table node per
  resting order. A flat open-addressing index replaced it, and the trades vector now reserves
  room once:
  * mixed flow 14.5 → 26.7 M ops/s;
  * random cancels on a 1M-order book 3.1 → 14.9 M ops/s;
  * allocations per resting order 1.0 → 0.003.
* **The Python simulation.** The default 26-agent market runs at about 87,000 events/s,
  roughly 1,400 simulated seconds per second. The live analytics the terminal polls now cost
  0.6 ms per request at any run length; at 2 simulated hours they cost 130 ms before.

## Quick start

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), CMake ≥ 3.24, Ninja, and a C++20
compiler (Clang 15+ or GCC 12+).

```bash
uv sync --all-packages              # builds the C++ extension, installs both packages
uv run python -m udeshi_api         # API on http://127.0.0.1:8000 (docs at /docs)
cd frontend && npm ci && npm run dev    # terminal on http://localhost:3000 (Node 22.12+)
```

Or run everything in containers (terminal, API, and PostgreSQL for experiment results):
`docker compose up --build`, then open http://localhost:3000. Without `UDESHI_DATABASE_URL`,
the API keeps experiments in an in-memory SQLite database.

```bash
B=http://127.0.0.1:8000
ID=$(curl -s -X POST $B/api/simulations -H 'content-type: application/json' -d '{"seed": 7}' \
     | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')
curl -s -X POST $B/api/simulations/$ID/start
curl -s -X POST $B/api/markets/ACME/orders -H 'content-type: application/json' \
     -d '{"side": "sell", "price": 100.05, "quantity": 10}'
curl -s -X POST $B/api/markets/ACME/orders -H 'content-type: application/json' \
     -d '{"side": "buy", "type": "market", "quantity": 4}'      # fills 4 @ 100.05
curl -s "$B/api/markets/ACME/orderbook?depth=5"                   # 6 @ 100.05 left on the ask
```

The endpoints, the WebSocket stream and the error format are described in
[docs/api.md](docs/api.md).

## A simulation in Python

```python
from udeshi_markets import Side
from udeshi_markets.simulation import (
    MILLISECOND, MINUTE, OrderIntent, Participant, Simulation, SimulationConfig,
    SimulationContext,
)


class NoiseTrader(Participant):
    """Every ~50 ms, posts a random limit order within 5 ticks of the midprice."""

    def on_start(self, ctx: SimulationContext) -> None:
        self.acme = ctx.instrument_id("ACME")
        ctx.wakeup(ctx.rng.randint(1, 50) * MILLISECOND)

    def on_wakeup(self, ctx: SimulationContext, tag: str) -> None:
        mid = ctx.midprice(self.acme) or 10_000          # ticks; ACME starts at 100.00
        side = Side.BUY if ctx.rng.bernoulli(0.5) else Side.SELL
        price = round(mid) + ctx.rng.randint(-5, 5)
        ctx.submit(OrderIntent(self.acme, side, ctx.rng.randint(1, 10), price=price))
        ctx.wakeup(round(ctx.rng.exponential(1 / 50) * MILLISECOND) + 1)


config = SimulationConfig(duration_ns=MINUTE, seed=7)
summary = Simulation(config, lambda: [NoiseTrader(i) for i in range(1, 11)]).run()
print(summary.trades, summary.volume, summary.digest)   # identical on every run and platform
```

The kernel's design, including its event ordering, lifecycle and determinism guarantees, is
described in [docs/simulation-engine.md](docs/simulation-engine.md).

A whole market of agents, run headless, with P&L summed per strategy:

```python
from udeshi_markets.agents import PopulationConfig, TradingAgent, market_simulation
from udeshi_markets.simulation import MINUTE, EnvironmentConfig, PairConfig, SimulationConfig

simulation = market_simulation(
    SimulationConfig(duration_ns=10 * MINUTE, seed=7),
    PopulationConfig(),  # 10 random, 5 market makers, 5 momentum, 5 fundamental, 1 pairs
    EnvironmentConfig(pairs=(PairConfig("ACME", "NOVA"),)),
)
summary = simulation.run()
marks = simulation.marks()
pnl: dict[str, float] = {}
for pid, agent in simulation.participants.items():
    if isinstance(agent, TradingAgent):
        total = float(simulation.portfolio(pid).total_pnl(marks))
        pnl[agent.strategy] = pnl.get(agent.strategy, 0.0) + total
print(summary.trades, {strategy: round(value) for strategy, value in sorted(pnl.items())})
```

An experiment: do more market makers tighten the spread? Five seeds each, compared with Welch's
t-test:

```python
from udeshi_markets.agents import PopulationConfig
from udeshi_markets.experiments import ExperimentSpec, compare, run_experiment
from udeshi_markets.simulation import MINUTE

few = ExperimentSpec(
    name="few makers", duration_ns=2 * MINUTE, runs=5, population=PopulationConfig(market_makers=2)
)
many = ExperimentSpec(
    name="many makers", duration_ns=2 * MINUTE, runs=5, population=PopulationConfig(market_makers=10)
)
[spread] = compare(run_experiment(few), run_experiment(many), ["spread_bps"])
print(f"spread {spread.mean_a:.2f} -> {spread.mean_b:.2f} bps, Welch p = {spread.p_value:.4f}")
```

## Testing

```bash
cmake --preset release && cmake --build --preset release && ctest --preset release
cmake --preset asan && cmake --build --preset asan && ctest --preset asan   # ASan + UBSan

uv run pytest                                   # bindings, kernel, API (warnings are errors)
uv run mypy                                     # --strict
uv run ruff check python backend && uv run ruff format --check python backend

python3 tools/mutation_test.py --jobs 4         # the suite must catch all 68 injected bugs

cd frontend
npm run lint && npm run typecheck && npm test   # ESLint, tsc (strict), Vitest
npx playwright test                             # end to end, starts the API and the terminal

build/release/bench/udeshi_bench --json results.json    # engine benchmarks (Release build)
uv run python bench/simulation_bench.py                 # simulation and live-API benchmarks
```

CI runs all of these on Linux (GCC) and macOS (Clang), and builds and smoke-tests the Docker
images.

## Architecture

```mermaid
flowchart LR
    FE["Next.js terminal"] <-->|REST / WS| API["FastAPI"]
    API --> SIM["Discrete-event simulation<br/>agents · market environment"]
    SIM --> BIND["pybind11"]
    BIND --> EX["C++ Exchange"]
    EX --> ME["MatchingEngine"] --> OB["OrderBook"]
    SIM --> AN["Analytics · experiments"] --> PG[("PostgreSQL")]
```

Details: [architecture](docs/architecture.md) · [matching engine](docs/matching-engine.md) ·
[simulation kernel](docs/simulation-engine.md) · [trading agents](docs/trading-agents.md) ·
[metrics](docs/quantitative-metrics.md) · [microstructure](docs/market-microstructure.md) ·
[API](docs/api.md) · [frontend](docs/frontend.md) · [benchmarks](docs/benchmarking.md) ·
[development log](docs/development-log.md)

## Repository layout

```
engine/          C++20 matching engine: public headers, implementation, GoogleTest suite
python/          pybind11 bindings and the udeshi_markets package (simulation kernel, market
                 environment, trading agents, analytics, experiments), tests
backend/         FastAPI service (udeshi_api), tests, Dockerfile
frontend/        Next.js trading terminal and experiment dashboard, Vitest and Playwright tests
bench/           engine benchmarks (C++), simulation benchmarks (Python), recorded results
tools/           mutation testing, benchmark report generator
docs/            design documentation
```

## Technology

C++20 · CMake · GoogleTest · Python 3.12 · pybind11 · scikit-build-core · uv · FastAPI ·
Pydantic · SQLAlchemy · PostgreSQL · pytest · Hypothesis · mypy · Ruff · Docker · Next.js ·
React · TypeScript · Tailwind CSS · lightweight-charts · Zustand · Vitest · Playwright.
