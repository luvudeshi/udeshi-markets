# Development Log

What has been built, the decisions behind it, and what is next. The requirements are in
[project-brief.md](project-brief.md).

## Status

| Phase | Scope | Status |
| --- | --- | --- |
| 1 | C++ matching engine, price-time priority, cancellation, tests | **Done.** CI green; 61/61 mutants killed on Linux and macOS. |
| 2 | pybind11 bindings, exchange API, discrete-event loop, integration tests | **Done.** 68 review findings, all reproduced and fixed with tests. |
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

**Found by the adversarial review and fixed** (four lenses: bindings and build, kernel, API,
tests; 68 findings, every one reproduced by an independent verifier before it was fixed):
* **Bounded work.** A single request could make the server build tens of millions of empty bars
  on the event loop and run out of memory. There is now a bar budget (500k per simulation in
  the API, 10M in the kernel) and a 1 ms minimum interval; update payloads are built only for
  subscribers; history aggregates only the tail it returns; live tapes are bounded.
* **Stream protocol.** A reset left clients showing stale books. A slow client silently lost
  trades and bars. Close codes 4404/4400 never reached real clients, and a binary frame killed
  the stream. Now the snapshot is one message, sent again after a reset or when a client falls
  behind; the server accepts the connection before closing it with a code; frames are ignored.
* **Kernel correctness.**
  * Trades at the exact end of a run were in no bar. A run now covers [0, D), and a stop at t
    keeps the instant t.
  * A failing participant callback left a half-notified run RUNNING; it now ends in a terminal
    FAILED state.
  * The re-entrancy guard missed `on_start` and `on_stop`, and could be cleared by nesting.
  * Out-of-range ids crashed mid-dispatch after counters had changed.
* **Information leaks.** The `NOT_OWNER` cancel status let participants map other participants'
  orders; the public tape exposed the engine's global sequence number.
* **Digest and RNG.** Wakeup tags were not length-prefixed (two different runs could share a
  digest); the end of a run was not digested; RNG stream labels `3` and `"3"` collided; the
  logarithm was up to 4.6 ulp off (now within 2).
* **Bindings and build.**
  * The exception translator leaked into every other pybind11 module in the process.
  * `__eq__` broke Python's comparison protocol; the stubs allowed hashing and constructing
    classes that support neither.
  * `__version__` was not tied to the package version, and every engine symbol was exported.
  * Build dependencies were unpinned. They are now pinned and recorded in `uv.lock`.
  * The Dockerfile recompiled the engine on any backend edit.
  * CI never compiled the bindings with `-Werror` or tested the installed wheel; a new `wheel`
    job does both on Linux and macOS.
* **API details.** Infinite or NaN inputs and unusable history intervals gave 500s; concurrent
  creates could return an evicted session; 405 responses had no `Allow` header.
* **Test gaps.** About 30, including multi-instrument trading, notification order, the context's
  information boundary, end-of-run boundaries and RNG goldens for every distribution.

**Verification:**
* Python suite: bindings (with Hypothesis properties), stubs and signatures, RNG goldens,
  recorder, kernel (`test_simulation.py`, plus `test_kernel_contract.py` for the review
  fixes), the README example, the API, the WebSocket stream (including a real uvicorn server
  with a real WebSocket client), the runner and the hub. Warnings are errors.
* mypy `--strict` and ruff are clean.
* CI runs the engine on Linux and macOS in debug, release and ASan+UBSan builds, plus the
  Python suite, the installed-wheel tests and the Docker image.

**Limitations carried forward:**
* Bars are built eagerly, including empty ones. That is why there is a bar budget; storing only
  non-empty bars would remove the need for it.
* Order ids come from one global counter, as on many real venues, so the gap between a
  participant's own ids reveals how many orders others had accepted in between.
* Sessions live in memory only. Persistence arrives with PostgreSQL in Phase 4.
* The live runner falls behind rather than skipping work when a simulation cannot keep up with
  the requested speed.

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
