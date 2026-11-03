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
| `UDESHI_STREAM_QUEUE_SIZE` | `512` | Per-client WebSocket buffer (oldest dropped when full). |
| `UDESHI_STREAM_BOOK_DEPTH` | `15` | Levels per side in streamed books. |
| `UDESHI_CORS_ORIGINS` | `http://localhost:3000` | Comma-separated allowed origins. |

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
  | 404 | `unknown_simulation`, `no_active_simulation`, `unknown_symbol`, `order_not_found`, `not_found` |
  | 409 | `invalid_state` (e.g. pausing a simulation that is not running) |
  | 422 | `validation_error` (with per-field `details`), `invalid_configuration`, `invalid_price`, `invalid_interval` |
  | 500 | `internal_error` (logged with traceback; no internals in the response) |

## Endpoints

### Simulations

| Method & path | Body | Description |
| --- | --- | --- |
| `GET /api/simulations` | — | All simulations held in memory. |
| `POST /api/simulations` | `SimulationCreate` | Creates a simulation (201) and makes it active. All fields are optional: `instruments` (`symbol`, `reference_price`, `tick_size`; defaults to ACME, NOVA, QUANT, ORBIT, VECTOR), `duration_seconds`, `seed`, `order_latency_ms`, `bar_interval_seconds`, `speed`. |
| `GET /api/simulations/{id}` | — | State, simulated time, progress, speed, configuration, counters and run digest. |
| `DELETE /api/simulations/{id}` | — | Stops the simulation and drops it (204). Stream clients receive `{"type": "closed"}`. |
| `POST /api/simulations/{id}/start` · `pause` · `resume` · `stop` · `reset` | — | Lifecycle transitions (409 when not allowed). |
| `POST /api/simulations/{id}/speed` | `{"speed": 10}` | Simulated seconds per wall-clock second. |
| `POST /api/simulations/{id}/step` | `{"events": 100}` | Processes events one batch at a time; only while paused. |

### Markets

| Method & path | Description |
| --- | --- |
| `GET /api/markets` | Summary per market: last, change, change %, OHLC, volume, VWAP, best bid/ask, mid, spread. |
| `GET /api/markets/{symbol}` | Summary plus depth totals, resting order count, notional. |
| `GET /api/markets/{symbol}/orderbook?depth=10` | Aggregated levels (price, quantity, order count) per side, best first. |
| `GET /api/markets/{symbol}/trades?limit=50` | Public tape, oldest first. No order ids or trader identities. |
| `GET /api/markets/{symbol}/history?interval_seconds=&limit=500` | Closed OHLCV bars. The interval must be a whole multiple of the simulation's bar interval. |
| `GET /api/markets/{symbol}/orders` | Your (the API user's) resting orders. |
| `POST /api/markets/{symbol}/orders` | Submit an order as the API user: `side`, `type` (`limit` or `market`), `price` (limit only), `quantity`, `time_in_force` (`gtc` or `ioc`), `self_trade_prevention`. It is processed immediately at the current simulated time, and the response includes its status and fills (201). |
| `DELETE /api/markets/{symbol}/orders/{order_id}` | Cancel one of your resting orders. Another participant's order is reported as `order_not_found`, exactly like a missing one, so order ids cannot be probed. |

The API user is a participant of the simulation, with id `1000000`. Its orders go through the
same kernel path, digest and ownership checks as any agent's.

## WebSocket stream

`GET /api/simulations/{id}/stream?symbols=ACME,NOVA` (WebSocket)

1. **Snapshot first.** On connect the server sends a `simulation` status, then for each
   subscribed symbol a `book`, a `market` summary and the 100 most recent `trades`.
2. **Then incremental updates, once per runner tick, only when something changed:**

   | `type` | Payload |
   | --- | --- |
   | `simulation` | `state`, `time_seconds`, `progress`, `speed`, `error`. Sent only when one of them changes. |
   | `book` | The same fields as the order-book endpoint, for a symbol whose book changed. |
   | `market` | The market summary for that symbol. |
   | `trades` | `symbol` and the new public trades. |
   | `bars` | `symbol` and the newly closed OHLCV bars. |
   | `closed` | The simulation was deleted; the server then closes the socket. |

3. **Close codes.** An unknown simulation closes with code `4404`, an unknown symbol with `4400`.
   Client messages are ignored.
4. **Back-pressure.** Each client has a bounded buffer. When it is full the *oldest* message is
   dropped, so a slow client cannot stall the market or grow server memory. Books and market
   summaries are full snapshots, so the next one repairs any gap.

## Concurrency model

The C++ engine and the simulation kernel are single-threaded. The service keeps them that way
without locks:
* Every route handler is `async def`. A test enforces this, because a synchronous handler would
  run in a thread pool.
* Each live simulation's runner is an asyncio task on the same event loop.
* Handlers never `await` between reading and mutating simulation state, so engine calls are
  serialised by construction.
