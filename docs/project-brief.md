# Project Brief (original prompt)

Verbatim copy of the instructions that started this project (2026-11-03). Progress against it is
tracked in [development-log.md](development-log.md).

---

Udeshi Markets — High-Performance Financial Market Simulation Engine
You are acting as a senior quantitative developer, systems engineer, and technical architect. I want you to help me build a sophisticated financial market simulation platform called Udeshi Markets.
This is intended to be a serious portfolio project demonstrating quantitative finance, algorithms, probability, statistics, systems programming, and software engineering. It should be impressive to engineers and quantitative researchers at firms such as Jane Street, Citadel Securities, and HRT.
Do not build a generic stock trading application or a simple paper-trading dashboard. The core of this project is a realistic simulated financial exchange, with a high-performance matching engine and autonomous trading agents interacting within it.
I want a polished, technically sophisticated, fully functional application.

1. Product vision
Build a simulated electronic stock exchange where:

* Multiple fictional financial assets can be traded.
* Autonomous trading agents submit buy and sell orders.
* A central matching engine processes orders.
* Prices change based on supply and demand.
* The exchange maintains a live limit order book.
* Trades execute according to price-time priority.
* Different market-making, momentum, arbitrage, and random trading strategies compete against each other.
* Users can observe market activity in real time.
* Users can run experiments and analyze the resulting market behavior.

Everything must be simulated. No real money, brokerage accounts, or real-world trade execution.
The application should allow users to both observe the market and experiment with its behavior.
2. Technology stack
Use the following architecture:
Backend / Exchange Engine

* C++20
* CMake
* Python 3.12
* pybind11 for Python bindings
* FastAPI for the backend API
* WebSockets for real-time market updates

Frontend

* Next.js
* TypeScript
* React
* Tailwind CSS
* Recharts or lightweight-charts for visualizations

Data

* PostgreSQL for persistent simulation results and historical data.
* Redis only if needed for background processing or event distribution.

Testing

* GoogleTest for C++.
* pytest for Python.
* Playwright for frontend end-to-end testing.

Use Docker Compose to make the entire application easy to run locally.
Do not introduce unnecessary infrastructure. Prioritize correctness, clean architecture, and performance.
3. Exchange matching engine (most important component)
Implement a real limit order book in C++.
Each asset should maintain independent bid and ask books.
Orders must support:

* Buy and sell sides.
* Limit orders.
* Market orders.
* Order quantities.
* Limit prices.
* Unique order IDs.
* Order timestamps or deterministic sequence numbers.
* Order cancellation.
* Partial fills.
* Full fills.
* Remaining order quantities.

Implement price-time priority:

1. The best available price gets priority.
2. When multiple orders have the same price, the earliest order gets priority.
3. Market orders execute against the best available opposing orders.
4. Any remaining quantity of a market order is canceled.
5. Limit orders that cannot immediately execute remain in the order book.

For example:
A seller submits:

* Sell 10 shares at $101.

Another seller submits:

* Sell 5 shares at $102.

A buyer submits:

* Buy 12 shares at $102.

The engine should execute:

* 10 shares at $101.
* 2 shares at $102.

The remaining 3 shares at $102 stay in the ask book.
Ensure the implementation handles all edge cases correctly.
Use appropriate data structures. Consider ordered maps for price levels and queues or linked structures for FIFO order priority.
Avoid inefficient implementations that require sorting the entire order book after every order.
Implement:

* OrderBook
* Order
* Trade
* MatchingEngine
* PriceLevel
* Exchange

Keep the matching engine independent of the web application and frontend.
The matching engine should be deterministic given the same sequence of submitted orders.
4. Exchange functionality
Create a simulated exchange containing multiple assets.
Initially include:

* ACME
* NOVA
* QUANT
* ORBIT
* VECTOR

Each asset should have:

* A starting reference price.
* A fundamental value.
* Current market price.
* Outstanding bids and asks.
* Recent transactions.
* Trading volume.
* Historical price information.

Implement the following exchange operations:

* Submit order.
* Cancel order.
* Retrieve order book.
* Retrieve recent trades.
* Retrieve best bid and ask.
* Retrieve midprice.
* Retrieve spread.
* Retrieve market statistics.

Ensure that market prices are derived from actual executed trades, rather than being arbitrarily changed by the frontend.
5. Autonomous trading agents
Implement an agent framework that allows different trading strategies to interact with the exchange.
Every agent should have:

* Unique ID.
* Strategy type.
* Available cash.
* Inventory for each asset.
* Order submission history.
* Profit and loss.
* Trading statistics.

Implement these strategies:
A. Random Trader
Submits randomly generated buy and sell orders.
Randomize:

* Order direction.
* Quantity.
* Limit price.
* Submission frequency.

Use a configurable random seed so experiments are reproducible.
B. Market Maker
Continuously posts bid and ask quotes around the estimated fair value.
Implement inventory-aware quoting.
For example:

* If the agent holds too many shares, make its quotes more attractive to sellers and less attractive to buyers.
* If the agent holds too few shares, adjust quotes in the opposite direction.

Track:

* Bid-ask spread earned.
* Inventory exposure.
* Mark-to-market P&L.
* Realized P&L.
* Adverse selection.

C. Momentum Trader
Analyzes recent price movements.
Implement:

* Short-term moving averages.
* Long-term moving averages.
* Momentum signals.
* Configurable entry and exit thresholds.

The agent should submit orders when its signals meet the configured thresholds.
D. Fundamental Trader
Uses estimated fundamental value to decide whether an asset is underpriced or overpriced.
If market price is sufficiently below estimated fundamental value, it may submit buy orders.
If market price is sufficiently above estimated fundamental value, it may submit sell orders.
E. Statistical Arbitrage Trader
Implement a simplified pairs trading strategy.
Allow two assets to have correlated underlying fundamental values.
Track their price spread and calculate its rolling z-score.
Generate trading signals when the spread deviates significantly from its historical mean.
Include configurable entry and exit thresholds.
Be careful to avoid using future information when calculating signals.
6. Market dynamics
Implement a configurable market environment.
Include:

* Fundamental value changes.
* Random price shocks.
* News events.
* Changes in market volatility.
* Changes in trading activity.
* Liquidity shocks.

News events should affect the fundamental value of an asset.
For example:

* Positive news increases estimated fundamental value.
* Negative news decreases estimated fundamental value.

Agents should react to the information available to them, not future information.
Allow users to configure:

* Number of trading agents.
* Number of assets.
* Simulation duration.
* Market volatility.
* Order submission frequency.
* Initial capital.
* Random seed.
* News event frequency.

Make simulation results reproducible using a fixed random seed.
7. Simulation engine
Create a simulation engine that coordinates:

1. Market events.
2. Agent decisions.
3. Order submissions.
4. Order matching.
5. Trade execution.
6. Portfolio updates.
7. Market statistics.
8. Event recording.

Use a discrete-event simulation architecture.
Each event should have a deterministic sequence number and simulated timestamp.
Do not rely on arbitrary sleep calls to advance simulated time.
Separate simulation time from wall-clock time.
Support:

* Starting a simulation.
* Pausing a simulation.
* Resuming a simulation.
* Stopping a simulation.
* Resetting a simulation.
* Running a fixed number of simulation steps.
* Running an entire experiment without a live frontend.

The simulation engine must be usable independently through Python.
8. Quantitative analytics
Implement a research and analytics module.
Calculate:
Market statistics

* Last traded price.
* Best bid.
* Best ask.
* Midprice.
* Bid-ask spread.
* Trading volume.
* Number of transactions.
* Market volatility.
* Order book depth.

Agent statistics

* Realized P&L.
* Unrealized P&L.
* Total P&L.
* Sharpe ratio.
* Maximum drawdown.
* Inventory exposure.
* Win rate.
* Number of executed trades.
* Average execution price.

Market microstructure

* Effective spread.
* Realized spread.
* Price impact.
* Order fill rate.
* Average execution size.
* Market depth.
* Order imbalance.

Clearly document the mathematical definitions and assumptions behind every metric.
Do not calculate statistics using future information.
Handle zero variance, insufficient observations, and missing values correctly.
9. Experiment and backtesting framework
Build an experiment framework that allows users to run many simulations with different configurations.
For example:
Experiment 1:

* 5 market makers.
* 20 random traders.
* 5 momentum traders.

Experiment 2:

* 10 market makers.
* 50 random traders.
* 10 momentum traders.

Compare:

* Price volatility.
* Market liquidity.
* Bid-ask spreads.
* Trading volume.
* Agent profitability.
* Market efficiency.

Support:

* Multiple independent runs.
* Random seed control.
* Parameter sweeps.
* Results aggregation.
* CSV export.
* Comparison between experiments.

Include a Monte Carlo experiment runner.
The goal is to make this a useful quantitative research environment, not just a visual simulation.
10. Frontend
Build a polished, modern financial exchange interface.
The visual design should feel like a professional quantitative trading terminal.
Use:

* Dark theme.
* Clean typography.
* Dense but readable data.
* Subtle borders.
* Professional charts.
* Responsive layouts.

Avoid generic AI-generated dashboard aesthetics.
The main interface should include the following sections.
A. Market overview
Display:

* Available assets.
* Current prices.
* Price changes.
* Trading volume.
* Market volatility.
* Number of active agents.

B. Live order book
For the selected asset, display:

* Bid prices and quantities.
* Ask prices and quantities.
* Market depth visualization.
* Best bid.
* Best ask.
* Midprice.
* Bid-ask spread.

Update the order book in real time using WebSockets.
C. Price chart
Show:

* Historical simulated prices.
* Executed trades.
* Volume.
* Configurable time intervals.
* News events.

D. Trade tape
Display recent executions:

* Timestamp.
* Asset.
* Price.
* Quantity.
* Buyer.
* Seller.

E. Agent leaderboard
Show each agent's:

* Strategy.
* P&L.
* Return.
* Inventory.
* Number of trades.
* Sharpe ratio.

Allow sorting by performance.
F. Simulation controls
Allow users to:

* Start.
* Pause.
* Resume.
* Stop.
* Reset.
* Change simulation speed.
* Configure the number of agents.
* Configure market parameters.

G. Experiment dashboard
Allow users to:

* Create experiments.
* Configure parameters.
* Execute multiple simulations.
* Compare results.
* Visualize performance distributions.

11. API design
Create clean REST endpoints.
Examples:
GET /api/markets
GET /api/markets/{symbol}
GET /api/markets/{symbol}/orderbook
GET /api/markets/{symbol}/trades
GET /api/agents
GET /api/agents/{id}
GET /api/simulations
GET /api/simulations/{id}
POST /api/simulations
POST /api/simulations/{id}/start
POST /api/simulations/{id}/pause
POST /api/simulations/{id}/resume
POST /api/simulations/{id}/stop
Create WebSocket endpoints for:

* Order book updates.
* Trade executions.
* Price updates.
* Agent statistics.
* Simulation events.

Validate inputs and return meaningful errors.
12. Testing and correctness
This is extremely important.
Write extensive automated tests.
For the matching engine, test:

* Single-order submission.
* Multiple orders at different prices.
* FIFO priority at identical prices.
* Partial fills.
* Full fills.
* Market orders.
* Order cancellation.
* Insufficient liquidity.
* Multiple simultaneous price levels.
* Large orders crossing multiple levels.
* Invalid orders.
* Deterministic replay.

Test important invariants:

* No order should execute at a price worse than its limit.
* Executed quantity must never exceed submitted quantity.
* A canceled order must not execute afterward.
* No shares or cash should be created through trade execution.
* Every trade must have matching buyer and seller quantities.
* Remaining quantities must be represented correctly in the order book.

Use property-based testing where appropriate.
Write integration tests for the Python bindings and API.
Include frontend tests for important interactions.
13. Performance engineering
Performance is an important part of this project.
Benchmark the C++ matching engine.
Measure:

* Orders processed per second.
* Average processing latency.
* Median latency.
* P95 latency.
* P99 latency.
* Memory usage.

Create benchmarks for:

* Different order book sizes.
* Different numbers of price levels.
* Different order submission patterns.
* High-volume order cancellations.

Optimize only after measuring.
Document benchmark methodology and hardware.
Do not fabricate performance claims.
Keep correctness and determinism more important than premature optimization.
14. Documentation
Create a professional README.md containing:

* Project overview.
* Architecture diagram.
* Feature list.
* Technology stack.
* Installation instructions.
* Local development setup.
* Example simulations.
* Screenshots.
* Benchmark results.
* Testing instructions.
* Future improvements.

Also create:
docs/
architecture.md
matching-engine.md
market-microstructure.md
trading-agents.md
quantitative-metrics.md
simulation-engine.md
benchmarking.md
Explain the mathematics behind the quantitative components.
Include a Mermaid architecture diagram in the documentation.
15. Development approach
Do NOT attempt to implement everything in one massive code generation step.
Work incrementally.
First, inspect the current directory and establish the project structure.
Then:
PHASE 1:

* Set up the repository.
* Create the C++ matching engine.
* Implement limit orders and market orders.
* Implement price-time priority.
* Implement order cancellation.
* Write comprehensive C++ unit tests.

PHASE 2:

* Implement Python bindings.
* Build the exchange API.
* Implement the simulation event loop.
* Write integration tests.

PHASE 3:

* Implement autonomous trading agents.
* Add market dynamics.
* Implement portfolio accounting.

PHASE 4:

* Build quantitative analytics.
* Implement experiment execution.
* Add Monte Carlo simulations.

PHASE 5:

* Build the frontend.
* Integrate WebSockets.
* Implement interactive visualizations.

PHASE 6:

* Add benchmarks.
* Profile and optimize.
* Improve documentation.
* Run the complete test suite.

At the end of every phase:

1. Run all relevant tests.
2. Fix failing tests.
3. Explain what was implemented.
4. Explain important architectural decisions.
5. Identify remaining limitations.
6. Update the documentation.
7. Important engineering principles

* Use clean, modular architecture.
* Write maintainable, strongly typed code.
* Avoid unnecessary abstractions.
* Avoid giant files.
* Avoid duplicated business logic.
* Use meaningful variable and function names.
* Handle errors explicitly.
* Include meaningful logging.
* Do not silently swallow exceptions.
* Do not hardcode fake performance metrics.
* Do not make the frontend responsible for financial calculations.
* Keep all exchange logic in the backend.
* Ensure deterministic simulations.
* Include tests alongside implementations.

Do not use mock data as a substitute for working functionality. Seeded simulation data is acceptable for demonstrations, but it must come from the actual simulation engine.
17. First task
Start by designing the architecture and implementing PHASE 1 only.
Before writing code:

1. Explain the proposed directory structure.
2. Explain the matching engine's data structures.
3. Explain how price-time priority will work.
4. Identify important edge cases.
5. Explain how the engine will be tested.

Then implement Phase 1 completely.
Do not move to Phase 2 until Phase 1 passes its tests.
I want this to be a genuinely high-quality engineering project. Prioritize correctness, mathematical rigor, performance, and thoughtful design over simply maximizing the number of features.

---
Build this in a new private github repo and push/commit very frequently as you add things/make changes. First thing you should do is make this github repo.

---

## Follow-up instructions

The user's later messages, verbatim, in order:

1. "Start Phase 2"
2. "save the original instructions i had promtped into a file and then run / compact to free up
   my context, ensuring i dont lose any info tho"
3. "expedite this please youre going super slow for nothing" (during the Phase 2 review)
4. "estimate how long till this finishes"
5. "i toguth you had finished phase 2"
6. "speed it up, try to get it done in thirty minutes"
7. "Start Phase 3"
8. "Start Phase 4"
9. "Before we start phase 5, do the thing where you compile all the information so far and
   save it all and whatnot so that i can use / compact afterward without you losing anything."

What these add to the brief:
* **Speed matters.** The user does not want to wait on long background reviews. Fix findings as
  they arrive, run disjoint work in parallel, and keep reviews short and focused, without
  lowering the bar: tests for every fix, CI green on Linux and macOS.
* **Each phase starts only on the user's word.** End each phase with the report the brief asks
  for, then ask before starting the next.
* **Before a compaction**, save the state: this file, development-log.md, the local
  `.claude/handoff.md` and the memory notes.
