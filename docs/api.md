# API

The `udeshi_api` package ([`backend/`](../backend)) is a FastAPI service over the simulation
layer. Interactive documentation is served at `/docs` (Swagger UI) and `/redoc`, and the OpenAPI
schema at `/openapi.json`.

```bash
uv run python -m udeshi_api                 # http://127.0.0.1:8000/docs
docker compose up --build                   # same, in a container
```

Settings come from environment variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `UDESHI_HOST` / `UDESHI_PORT` (or `PORT`) | `127.0.0.1` / `8000` | Bind address. |
| `UDESHI_TICK_SECONDS` | `0.05` | Live runner tick (update rate 1/tick). |
| `UDESHI_MAX_SESSIONS` | `8` | Simulations kept in memory; the oldest finished ones are evicted beyond this. |
| `UDESHI_MAX_EVENTS_PER_TICK` | `200000` | Cap that keeps a busy simulation from starving the event loop. |
| `UDESHI_STREAM_QUEUE_SIZE` | `512` | Per-client WebSocket buffer, in messages. A client that falls further behind is sent a fresh snapshot. |
| `UDESHI_STREAM_BOOK_DEPTH` | `15` | Levels per side in streamed books. |
| `UDESHI_MAX_BARS` | `500000` | Most OHLCV bars a simulation may produce (instruments × duration ÷ bar interval). Larger requests get 422. |
| `UDESHI_TRADE_HISTORY` | `10000` | Recent trades kept per instrument for the tape endpoint and stream snapshots. |
| `UDESHI_CORS_ORIGINS` | `http://localhost:3000` | Comma-separated allowed origins. |
| `UDESHI_DATABASE_URL` | none | Where experiment results are kept: a SQLAlchemy URL such as `postgresql+psycopg://user:password@host:5432/db` (Compose sets one). Unset: an in-memory SQLite database, lost when the process exits. |
| `UDESHI_EXPERIMENT_WORKERS` | min(4, CPUs) | Worker processes that run experiment simulations. |

## Conventions

* **Prices are given twice.** `price` is in currency units, rounded to the instrument's tick
  grid; for example 0.15, never 0.15000000000000002. `price_ticks` is the exact integer the
  engine uses.
* **Times** are simulated seconds since the start of the run (`time_seconds`).
* **Market endpoints default to the active simulation**, the most recently created one. Pass
  `?simulation_id=…` to address another.
* **Errors** always use one shape, with a stable `code`:

  ```json
  {"error": {"code": "unknown_symbol", "message": "no market for symbol 'XYZ'"}}
  ```

  | Status | Codes |
  | --- | --- |
  | 404 | `unknown_simulation`, `no_active_simulation`, `unknown_symbol`, `order_not_found`, `unknown_agent`, `not_found` |
  | 409 | `invalid_state` (e.g. pausing a simulation that is not running) |
  | 405 | `http_error`, with an `Allow` header listing every method of the path |
  | 422 | `validation_error` (with per-field `details`; also for infinities, NaN and out-of-range ids), `invalid_configuration` (including a bar budget above `UDESHI_MAX_BARS`), `invalid_price`, `invalid_interval` |
  | 500 | `internal_error` (logged with traceback; no internals in the response) |

## Endpoints

### Simulations

| Method & path | Body | Description |
| --- | --- | --- |
| `GET /api/simulations` | — | All simulations held in memory. |
| `POST /api/simulations` | `SimulationCreate` | Creates a simulation (201) and makes it active. All fields are optional: `instruments` (`symbol`, `reference_price`, `tick_size`; defaults to ACME, NOVA, QUANT, ORBIT, VECTOR), `duration_seconds`, `seed`, `order_latency_ms`, `bar_interval_seconds` (at least 0.001), `speed`, `initial_cash` (every participant's starting cash; default 1,000,000), and `agents` and `market` (see [Agents and market](#agents-and-market)). Sessions beyond `UDESHI_MAX_SESSIONS` are evicted: stopped or completed ones first, then the oldest; never the active one. |
| `GET /api/simulations/{id}` | — | State (`created`, `running`, `paused`, `stopped`, `completed` or `failed`, with `error` set), simulated time, progress, speed, configuration (including the effective `initial_cash`, `agents` and `market`), counters and run digest. |
| `DELETE /api/simulations/{id}` | — | Stops the simulation and drops it (204). Stream clients receive `{"type": "closed"}`. |
| `POST /api/simulations/{id}/start` · `pause` · `resume` · `stop` · `reset` | — | Lifecycle transitions (409 when not allowed). |
| `POST /api/simulations/{id}/speed` | `{"speed": 10}` | Simulated seconds per wall-clock second. |
| `POST /api/simulations/{id}/step` | `{"events": 100}` | Processes events one batch at a time; only while paused. |

### Markets

| Method & path | Description |
| --- | --- |
| `GET /api/markets` | Summary per market: last, change, change %, OHLC, volume, VWAP, best bid/ask, mid, spread, and `fundamental_value` (see below). |
| `GET /api/markets/{symbol}` | Summary plus depth totals, resting order count, notional. |
| `GET /api/markets/{symbol}/orderbook?depth=10` | Aggregated levels (price, quantity, order count) per side, best first. |
| `GET /api/markets/{symbol}/trades?limit=50` | Public tape, oldest first. No order ids or trader identities. |
| `GET /api/markets/{symbol}/history?interval_seconds=&limit=500` | Closed OHLCV bars. The interval must be a whole multiple of the simulation's bar interval. |
| `GET /api/markets/{symbol}/orders` | Your (the API user's) resting orders. |
| `POST /api/markets/{symbol}/orders` | Submit an order as the API user: `side`, `type` (`limit` or `market`), `price` (limit only), `quantity`, `time_in_force` (`gtc` or `ioc`), `self_trade_prevention`. It is processed immediately at the current simulated time, and the response includes its status and fills (201). |
| `DELETE /api/markets/{symbol}/orders/{order_id}` | Cancel one of your resting orders. Another participant's order is reported as `order_not_found`, exactly like a missing one, so order ids cannot be probed. |

The API user is a participant of the simulation, with id `1000000`. Its orders go through the
same kernel path, digest and ownership checks as any agent's.

`fundamental_value` is the market environment's hidden value of the instrument, in currency:
the reference price before the start, `null` without an environment. It is the *observer's*
view, for charts and research. Agents never see it: fundamental traders get noisy private
signals, and everyone else sees only prices and news.

### Agents and market

`POST /api/simulations` accepts two optional objects. Without either, the API user trades
alone, exactly as in a plain simulation.

* **`agents`**: counts per strategy, `random_traders` (default 10), `market_makers` (5),
  `momentum_traders` (5), `fundamental_traders` (5) and `pairs_traders` (1), at most 500 in
  total, plus `order_frequency` (default 1.0; 2.0 makes every agent act twice as often). Agents
  get ids 1, 2, … in this order: market makers, random, momentum, fundamental, then pairs
  traders. Each is assigned an instrument round-robin; a pairs trader gets a pair.
* **`market`**: the environment that moves fundamental values. Fields: `volatility` (per
  √hour, default 0.02), `volatile_multiplier` (3), `news_per_hour` (6, per instrument),
  `news_impact` (0.01, mean log impact), `shocks_per_hour` (2; unannounced jumps),
  `shock_impact` (0.005), `liquidity_shocks_per_hour` (2; forced market orders),
  `liquidity_shock_quantity` (200), and `pairs`: a list of
  `{leader, follower, half_life_seconds, deviation_volatility}` for cointegrated pairs. If
  `pairs` is omitted, the default is ACME/NOVA when both symbols exist, and no pair otherwise;
  `[]` means no pairs.

`agents` without `market` uses the default environment. `market` without `agents` gives a market
with fundamental values and exogenous events, but only the API user trading. These requests get
422 `invalid_configuration`: pairs traders with no pair, a pair naming an unknown symbol or the
same symbol twice, and an instrument following two leaders.

| Method & path | Description |
| --- | --- |
| `GET /api/agents` | Every participant, in id order: the agents, the market environment (id `999999`, strategy `environment`; its trades are the liquidity shocks) and the API user (id `1000000`, strategy `manual`). Each has `strategy`, `symbols`, `cash`, `equity`, `total_pnl`, `realized_pnl`, `unrealized_pnl`, `return_pct`, `sharpe_ratio` (per hour, from the live analytics; `null` until defined, and for simulations without a market), `positions` (shares per symbol), `trades`, `orders_submitted`, `fills` and `filled_quantity`. |
| `GET /api/agents/{id}` | The same, plus `position_details` (quantity, average price, realized and unrealized P&L, shares bought and sold), `open_orders`, its last 50 order reports (`recent_orders`), `stats` (orders, rejections, cancels, fills, aggressive and passive fills; `null` for the user and the environment) and the strategy's own `metrics`: for a market maker `spread_earned`, `adverse_selection` and `markouts_measured`; for a pairs trader its last `z_score`; for the environment its news, shock and regime counts. |
| `GET /api/news?limit=50` | Published news, oldest first: `time_seconds`, `symbol`, `impact` (the change in the log fundamental), `sentiment` (`positive`, `negative`, or `neutral` for news that moved nothing) and `headline`. Unannounced shocks never appear. |

**Accounting.** Cash and positions come from the kernel's portfolios, which book every
execution exactly (with rational numbers) and use average-cost realized P&L. Positions are valued
at the mark: the midprice when both sides are quoted, else the last trade, else the reference
price. Across all participants, cash adds up to the total initial cash, positions to zero, and
P&L to zero: trading moves money and shares between participants but never creates them.

### Analytics

`GET /api/simulations/{id}/analytics` returns the metrics of the run so far, for simulations
with agents or a market environment (otherwise 409 `no_analytics`). The fields:
* `market`: per instrument, volatility per hour, spread, depth, imbalance and pricing error
  against the fundamental;
* `microstructure`: effective and realized spread, price impact, fill rate and trade size;
* `agents`: P&L, return, Sharpe ratio, drawdown, exposure, win rate, fill rate and average
  prices;
* `samples`, `interval_seconds` (1) and `markout_seconds` (5).

The definitions are in [quantitative-metrics.md](quantitative-metrics.md) and
[market-microstructure.md](market-microstructure.md).

The live runner stops the clock at every sampling instant and markout horizon, as the experiment
runner does, so live metrics are measured at exactly the right times. The exception is very high
speeds: past 100 stops in one runner tick, the rest of the tick runs in one go, so samples thin
out and markouts are read at the tick's end. A reset starts the analytics afresh.

### Experiments

An experiment runs one market many times, with seeds `base_seed`, `base_seed + 1`, …, optionally
for every point of a parameter sweep. Runs execute in background worker processes, and each is
stored as it finishes. Every run is a pure function of its settings and seed, so its `digest`
and metrics are exactly what the Python library gives for the same seed.

| Method & path | Description |
| --- | --- |
| `POST /api/experiments` | Queues an experiment and returns it (202). Body fields:<ul><li>`name`, `instruments`;</li><li>`duration_seconds` (default 300, at most 86400);</li><li>`runs` (seeds per point, default 10, at most 500);</li><li>`base_seed`, `order_latency_ms`, `initial_cash`;</li><li>`sample_interval_seconds` (1), `markout_seconds` (5);</li><li>`agents` and `market`, as for simulations (default: the default population and environment);</li><li>`sweep`: settings to vary, `{path: [values]}`, with paths such as `population.market_makers`, `environment.volatility` or `order_latency_ns`, and one point per combination.</li></ul>Limits: at most 50 points, 2,000 runs in total, and 60 days of simulated time across all runs. Every point is built before anything runs, so an invalid setting gives 422 `invalid_configuration` up front. |
| `GET /api/experiments` | Every experiment, oldest first. |
| `GET /api/experiments/{id}` | Status (`queued`, `running`, `completed`, `failed` with `error`, `cancelled`), progress (`runs_done` of `runs_total`), the request, and per point its `label`, `parameters` and a `summary` of every metric over the runs stored so far. Each metric summary has count, mean, stdev, stderr, a 95% Student-t confidence interval, minimum, 5th percentile, median, 95th percentile and maximum. |
| `GET /api/experiments/{id}/runs` | Every stored run: point, seed, parameters, all metrics, digest, `wall_seconds` (measured, informational). |
| `GET /api/experiments/{id}/runs.csv` · `summary.csv` | The same as CSV, in the library's format (`write_runs_csv`, `write_summary_csv`). |
| `GET /api/experiments/compare?a=&b=&point_a=0&point_b=0` | Two experiment points, metric by metric: both means, their difference, and Welch's t-test (t, degrees of freedom, two-sided p-value). Only metrics both points define are compared. `compare.csv` gives the same as CSV. |
| `POST /api/experiments/{id}/cancel` | Stops every run not yet started; a run already in a worker finishes there and is discarded. Status becomes `cancelled`. |
| `DELETE /api/experiments/{id}` | Cancels if needed, then deletes the experiment and its runs (204). |

Results are kept in the database named by `UDESHI_DATABASE_URL`. An experiment left unfinished
by a server restart is marked `failed` when the server starts again. Only scalar metrics are
stored per run, not the full time series.

## WebSocket stream

`GET /api/simulations/{id}/stream?symbols=ACME,NOVA` (WebSocket)

1. **Snapshot first.** On connect the server sends one `snapshot` message:
   `{"type": "snapshot", "simulation": {...status...}, "markets": [{"symbol", "book", "market",
   "trades", "bars"}], "agents": [...], "news": [...]}`. It carries the 100 most recent trades
   and closed bars per subscribed symbol, every participant as in `GET /api/agents`, and the 20
   most recent news items for the subscribed symbols. A client replaces everything it holds with
   a snapshot.
2. **Then incremental updates, once per runner tick, only when something changed:**

   | `type` | Payload |
   | --- | --- |
   | `simulation` | `state`, `time_seconds`, `progress`, `speed`, `error`. Sent only when one of them changes. |
   | `book` | The same fields as the order-book endpoint, for a symbol whose book changed. |
   | `market` | The market summary for that symbol. |
   | `trades` | `symbol` and the new public trades. |
   | `bars` | `symbol`, the newly closed OHLCV bars, and `omitted`. A jump in simulated time (a step to the end, a fast tick) can close many bars at once: the message carries the newest 1,000 and counts the rest in `omitted`; the history endpoint has them all. |
   | `news` | One published news item (the fields of `GET /api/news`), for subscribed symbols. It comes before the book and trade updates of the same tick. |
   | `agents` | The leaderboard: every participant, as in `GET /api/agents`. Sent at most once per second of wall time, only when something in it changed, and only for simulations with agents or a market environment. |
   | `closed` | The simulation was deleted; the server then closes the socket. |

3. **No silent gaps.** Each client has a bounded buffer, so a slow client cannot stall the
   market or grow server memory. Updates are queued one tick's batch at a time. When a client's
   buffer cannot take a whole batch, its pending updates are discarded and replaced by a fresh
   `snapshot`, which already includes everything it missed. Every client also receives a fresh
   snapshot after a reset (trade ids restart at 1).
4. **Close codes.** The server accepts the connection, then closes it with code `4404` for an
   unknown simulation or `4400` for an unknown symbol, with a reason. (Closing before accepting
   would reach clients as a bare HTTP 403.) Client messages, text or binary, are ignored.

## Concurrency model

The C++ engine and the simulation kernel are single-threaded. The service keeps them that way
without locks:
* Every route handler is `async def`. A test enforces this, because a synchronous handler would
  run in a thread pool.
* Each live simulation's runner is an asyncio task on the same event loop.
* Handlers never `await` between reading and mutating simulation state, so engine calls are
  serialised by construction.
* Experiments never touch a live simulation. Their runs execute in separate worker processes,
  and their database calls run in a thread, so neither blocks the event loop.
