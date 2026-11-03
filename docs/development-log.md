# Development Log

What has been built, the decisions behind it, and what is next. The requirements are in
[project-brief.md](project-brief.md).

## Status

| Phase | Scope | Status |
| --- | --- | --- |
| 1 | C++ matching engine, price-time priority, cancellation, tests | **Done.** CI green; 61/61 mutants killed on Linux and macOS. |
| 2 | pybind11 bindings, exchange API, discrete-event loop, integration tests | **Implemented and green in CI** (9/9 jobs). An independent adversarial review is in progress; its findings must be triaged and fixed before the phase is closed. |
| 3 | Trading agents, market dynamics, portfolio accounting | Not started |
| 4 | Analytics, experiments, Monte Carlo, PostgreSQL | Not started |
| 5 | Next.js frontend, WebSocket integration, visualisations | Not started |
| 6 | Benchmarks, profiling, optimisation, documentation | Not started |

## Phase 1: matching engine

**Delivered:** `engine/` (C++20, standard library only):
* `OrderBook` with best-first `BookSide`s;
* `PriceLevel` (intrusive FIFO) and `OrderPool` (slab allocator, ASan-poisoned when released);
* `MatchingEngine`: validation, global ids and sequence numbers, limit / market / IOC orders,
  cancel, self-trade prevention;
* `Exchange`: symbols, tick conversion, trade-derived statistics, ring buffer of recent trades.

Documentation is in [matching-engine.md](matching-engine.md).

**Verification:**
* GoogleTest suite:
  * scenario and exact-contract tests;
  * a differential test against a naive `ReferenceEngine` over about 250k random operations;
  * direct contract properties: quantity conservation, limit respect, contiguous sequencing;
  * golden cross-platform digests;
  * fault injection in a separate binary, and death tests.
* CI runs Debug, Release and ASan/UBSan on Linux (GCC) and macOS (Clang).
* Mutation testing (`tools/mutation_test.py`, plus the weekly `mutation.yml` workflow): 61/61.

**Found by two adversarial review rounds and fixed:**
* out-of-range enums silently accepted;
* non-atomic instrument registration (heap overflow after an allocation failure);
* off-grid prices rounded past the stated limit;
* FMA making VWAP differ across platforms;
* locale-dependent symbol checks;
* a tautological conservation test;
* `Order` copies keeping live links;
* the fault-injection allocator hiding ASan mismatch checks.

**Lessons recorded:**
* CI was green while running *zero* tests, because `enable_testing()` was only called in a
  subdirectory. Test presets now fail when no tests are found.
* A battery-throttled laptop made sanitizer runs look like hangs. Heavy verification belongs on
  CI runners.
* Always check tool exit codes explicitly: a `| tail` once hid a mypy failure.

## Phase 2: bindings, simulation kernel, API

**Delivered:**
* **Bindings:** `python/bindings/module.cpp`, the `udeshi_markets._engine` extension built
  through scikit-build-core.
  * Everything is passed by value, and enums are native Python enums.
  * `price_to_ticks` exposes the single tick-conversion implementation.
  * The hand-written `.pyi` stubs are kept in sync by a test.
* **Simulation kernel:** `python/udeshi_markets/simulation/`.
  * An event queue ordered by (time, sequence).
  * A lifecycle state machine.
  * Participants act through bound contexts: their own orders only, public data only.
  * Ownership-checked cancels and configurable latency.
  * An OHLCV recorder and a BLAKE2b run digest.
  * A platform-independent RNG.

  Documentation is in [simulation-engine.md](simulation-engine.md).
* **API:** `backend/udeshi_api/`, a FastAPI service.
  * Simulation lifecycle, market data and manual order entry.
  * A WebSocket stream with bounded per-client queues.
  * A live runner that paces by measured wall-clock time × speed.
  * One error format for every failure.

  Documentation is in [api.md](api.md).
* **Tooling:**
  * `backend/Dockerfile` (multi-stage, hash-pinned dependencies, non-root) and
    `docker-compose.yml`;
  * CI jobs for Python (ruff, mypy `--strict`, pytest with warnings as errors) and for the
    Docker image (build plus smoke test).

**Key decisions:**
* **Kernel in Python, matching in C++.** Strategies need flexibility, and matching is the hot
  path. Phase 6 will profile before moving anything else to C++.
* **Participants never act re-entrantly.** Their actions are scheduled at `now + latency`, so
  callbacks cannot nest inside exchange calls.
* **The manual API user is a participant** (id `1000000`), using the same kernel path, digest
  and ownership checks as any agent. Its orders are processed immediately at the current
  simulated time.
* **Concurrency by construction.** All handlers are async on one event loop (a test enforces
  this), so the single-threaded engine needs no locks.
* **Anonymity.** The public tape carries no trader or order ids, and cancelling someone else's
  order looks exactly like cancelling a missing one.
* **PostgreSQL is deferred to Phase 4**, when experiment results give it real data to persist
  ("do not introduce unnecessary infrastructure").

**Found while building it:**
* Bars closed inside `record()` never reached the live change stream.
* `Settings.from_environment` read slot descriptors instead of defaults; mypy caught it.
* The paused runner re-sent identical status messages 20 times a second.

**Verification so far:**
* 111 Python tests (bindings including Hypothesis properties, stubs, RNG golden values, recorder,
  kernel with a golden digest, API, WebSocket, runner, hub).
* mypy `--strict` and ruff are clean.
* CI is green on Linux and macOS.
* The real server was exercised end to end, including the WebSocket from a browser.

**Open items to close Phase 2:**
1. Triage the adversarial review (`phase2-adversarial-review` workflow), fix confirmed findings
   with tests, and re-run CI.
2. Update `docs/architecture.md` (phase table, repository layout) and the README (status,
   Python / API / Docker quick start, example simulation).
3. Write the Phase 2 report: what was implemented, decisions, limitations, documentation.

## Working conventions

* Commit and push after every meaningful step (the user's instruction).
* Each phase ends with:
  * all tests run and passing;
  * an explanation of what was built and why;
  * the remaining limitations;
  * updated documentation.
* After implementing, run an independent multi-agent adversarial review, in which every finding
  must be reproduced before it is fixed, and then confirm the fixes in CI.
* Local tools live in `.venv` (created by `uv venv --python 3.12`; `uv sync --all-packages`
  rebuilds the extension).
  * C++: `cmake --preset debug|release|asan`, then `ctest --preset …`.
  * Python: `.venv/bin/pytest`, `.venv/bin/mypy`, `.venv/bin/ruff`.
* Local port 8000 is taken on the development machine. `.claude/launch.json` (git-ignored) runs
  the API on 8010.
