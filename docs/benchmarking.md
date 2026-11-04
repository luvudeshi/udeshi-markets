# Benchmarks

How fast the matching engine is, measured. Every number on this page comes from a run of the
benchmark harness in [`bench/`](../bench); nothing is estimated. The result files are in
[`bench/results/`](../bench/results), and the tables below are generated from them by
[`tools/bench_report.py`](../tools/bench_report.py).

## Running them

```bash
cmake --preset release && cmake --build --preset release
build/release/bench/udeshi_bench --json results.json        # every workload, about 20 s
build/release/bench/udeshi_bench --list                      # what each workload does
build/release/bench/udeshi_bench --filter cancel --repetitions 9
python3 tools/bench_report.py results.json --baseline bench/results/apple-m4-baseline.json \
    --update docs/benchmarking.md                            # regenerate the tables below
```

CI runs the full set on every push, in a Release build on a GitHub-hosted Linux runner, after
`udeshi_bench --self-check`. It fails if any replay differs from its recording, and keeps the
JSON as an artifact (see [Linux](#linux-github-hosted-runner)).

## Method

* **What is measured.** The C++ `MatchingEngine`, in process and on one thread: an *operation*
  is one `submit` or one `cancel` call. One workload replays the same flow through
  `Exchange`, which adds the market data the simulation reads (statistics and the
  recent-trades ring). Neither network nor Python is involved; the simulation's own cost is
  measured separately ([Python](#the-python-simulation)).
* **Recorded, then replayed.**
  * Each workload is generated once against a real engine, from a fixed seed. The generator
    keeps track of the resting orders, so every cancel names an order that is resting at that
    moment and every "add" really rests.
  * It is then replayed on fresh engines. The *setup* operations build the starting book,
    untimed; the *measured* operations are timed.
  * Every replay is checked against the recording, and the run fails on any difference:
    * the first replay (also the warm-up) compares every field of every result, trades
      included;
    * every later replay compares a light checksum of its results (a few instructions per
      operation, so the timing is unaffected);
    * every replay compares the final book, level by level, and the last sequence number.
  * `--self-check` shows this catches real differences. Three recorded workloads must verify;
    changed copies of them must fail: an order with one more share, an order one tick
    further out, and a cancel of an order that is not there.
* **Throughput.**
  * The measured operations run in one timed loop with no per-operation instrumentation.
  * The table shows the median of the repetitions (min–max in brackets), after one untimed
    warm-up replay.
  * Every result feeds a checksum (a few arithmetic instructions per operation), so nothing
    can be optimised away.
  * *Mean ns* is 10⁹ / median throughput: the average cost of an operation in a stream.
* **Latency.**
  * A separate replay times every operation with `std::chrono::steady_clock`.
  * Percentiles are nearest-rank: p99 is the smallest sample that at least 99% of the samples
    do not exceed. They include the cost of reading the clock.
  * Throughput and latency answer different questions. In an untimed stream the processor
    overlaps the memory accesses of consecutive operations. Timing each one separately
    prevents that, so for memory-bound work an operation's latency exceeds its share of the
    stream. Random cancels in a 100k-order book cost 17 ns each in a stream, but 28 ns each on
    average when timed one at a time.
  * On Apple silicon the clock ticks at 24 MHz, so these samples are multiples of 41.67 ns,
    and an operation shorter than one tick reads as 0 or 41 ns. That is why medians are often
    exactly 41 or 42 ns. The throughput means above are exact; on Linux the clock resolves
    single nanoseconds.
* **Allocations.** The benchmark binary replaces the global `operator new`, and a third
  replay counts allocations per operation, by kind of operation. Counting is off in the timed
  replays.
* **Memory.** A book of *N* resting orders is built from scratch, each size in a fresh
  process, so no memory freed earlier is reused.
  * *Heap bytes per order* is the growth of live heap memory, using the allocator's usable
    block sizes (`malloc_size` on macOS, `malloc_usable_size` on Linux), divided by *N*.
  * *RSS growth* is the growth of the process's resident set. It also counts the allocator's
    own metadata and whole pages, so it exceeds the heap figure for small books.
* **Scheduling.** On macOS the benchmark asks for the user-interactive quality of service, so
  the scheduler runs it on a performance core (macOS offers no pinning). The machine was on
  mains power. A web browser was open and busy on other cores, which is why the throughput
  ranges are shown.
* **The workloads.** All are around a mid price of 100,000 ticks (1,000.00 at a 0.01 tick),
  with quantities from 1 to 100.
  * *Book size* and *price levels* alternate a new resting order with the cancel of a random
    resting order, so the book keeps its size.
  * *Patterns*:
    * only new resting orders;
    * a maker joining the best price followed by an IOC taker for the same quantity;
    * market orders that empty the best 1, 10 or 50 levels, which are then refilled;
    * a mixed flow of 50% new orders near the touch, 35% cancels, 10% marketable IOC orders
      and 5% market orders, holding about 10,000 resting orders.
  * *Cancellation* empties a book in random order, oldest first or newest first. It also
    covers cancels of orders that are already gone, and a market maker that cancels its
    oldest quote and requotes.

## Hardware and build

<!-- bench:environment:start -->
| | |
| --- | --- |
| CPU | Apple M4 (10 logical CPUs) |
| Memory | 16 GiB |
| OS | macOS 15.6 (Darwin 24.6.0 arm64) |
| Compiler | Clang 17.0.0 (clang-1700.0.13.5) |
| Build | Release ( -O3 -DNDEBUG) |
| Scheduling | QoS user-interactive (macOS schedules it on a performance core) |
| Clock | `std::chrono::steady_clock`: ticks of 41.67 ns; two consecutive reads at least 41 ns apart (median 0 ns) |
| Repetitions | 7 timed replays per workload, after a warm-up |
| Object sizes | `Order` 88 B, `PriceLevel` 40 B, `Trade` 88 B |
<!-- bench:environment:end -->

## Results

<!-- bench:workloads:start -->
**Book size**

| Workload | Operations | M ops/s (min–max) | Mean ns | p50 | p95 | p99 | p99.9 | Max | Allocations per op |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `book_size/1k` | 400,000 | 15.9 (15.8–15.9) | 63 | 42 | 125 | 125 | 167 | 6,417 | 0.30 |
| `book_size/10k` | 400,000 | 31.4 (31.3–31.6) | 32 | 41 | 83 | 84 | 125 | 6,208 | 0.00 |
| `book_size/100k` | 400,000 | 28.4 (27.8–29.8) | 35 | 42 | 83 | 125 | 167 | 4,291 | 0.00 |
| `book_size/1M` | 400,000 | 8.8 (8.0–9.3) | 114 | 84 | 209 | 291 | 375 | 2,542 | 0.00 |

**Price levels**

| Workload | Operations | M ops/s (min–max) | Mean ns | p50 | p95 | p99 | p99.9 | Max | Allocations per op |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `price_levels/1` | 400,000 | 53.4 (52.3–55.1) | 19 | 0 | 42 | 42 | 84 | 1,667 | 0.00 |
| `price_levels/10` | 400,000 | 45.3 (43.8–46.2) | 22 | 41 | 42 | 83 | 125 | 1,750 | 0.00 |
| `price_levels/100` | 400,000 | 37.4 (36.3–37.9) | 27 | 41 | 42 | 83 | 125 | 1,208 | 0.00 |
| `price_levels/1k` | 400,000 | 29.2 (28.7–29.5) | 34 | 42 | 83 | 84 | 125 | 292 | 0.00 |
| `price_levels/10k` | 400,000 | 19.6 (19.0–19.7) | 51 | 42 | 125 | 167 | 209 | 917 | 0.00 |

**Submission patterns**

| Workload | Operations | M ops/s (min–max) | Mean ns | p50 | p95 | p99 | p99.9 | Max | Allocations per op |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `patterns/passive` | 400,000 | 18.3 (15.6–18.9) | 55 | 42 | 125 | 125 | 208 | 1,243,875 | 0.00 |
| `patterns/maker_taker` | 400,000 | 28.3 (28.1–28.6) | 35 | 42 | 83 | 84 | 125 | 1,292 | 0.50 |
| `patterns/sweep_1` | 22,000 | 33.6 (33.1–33.8) | 30 | 41 | 167 | 208 | 209 | 375 | 0.27 |
| `patterns/sweep_10` | 202,000 | 29.4 (29.3–29.6) | 34 | 41 | 42 | 125 | 1709 | 2,458 | 0.15 |
| `patterns/sweep_50` | 1,002,000 | 29.4 (29.3–29.4) | 34 | 41 | 42 | 42 | 7584 | 11,875 | 0.11 |
| `patterns/mixed` | 2,000,000 | 26.7 (26.6–26.8) | 37 | 41 | 84 | 167 | 250 | 8,416 | 0.22 |

**Cancellation**

| Workload | Operations | M ops/s (min–max) | Mean ns | p50 | p95 | p99 | p99.9 | Max | Allocations per op |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `cancel/random/100k` | 100,000 | 58.9 (57.0–61.4) | 17 | 0 | 84 | 125 | 250 | 1,083 | 0.00 |
| `cancel/oldest_first/100k` | 100,000 | 79.4 (77.6–81.2) | 13 | 0 | 42 | 84 | 125 | 1,000 | 0.00 |
| `cancel/newest_first/100k` | 100,000 | 80.7 (77.9–83.2) | 12 | 0 | 42 | 84 | 167 | 1,542 | 0.00 |
| `cancel/random/1M` | 1,000,000 | 14.9 (14.7–15.0) | 67 | 84 | 250 | 292 | 792 | 11,583 | 0.00 |
| `cancel/oldest_first/1M` | 1,000,000 | 30.0 (29.7–30.2) | 33 | 42 | 84 | 166 | 333 | 11,042 | 0.00 |
| `cancel/newest_first/1M` | 1,000,000 | 30.6 (30.2–30.8) | 33 | 42 | 84 | 167 | 375 | 13,625 | 0.00 |
| `cancel/missing` | 400,000 | 317.4 (314.7–333.5) | 3 | 0 | 42 | 42 | 42 | 5,917 | 0.00 |
| `cancel/requote` | 400,000 | 60.9 (60.2–61.2) | 16 | 0 | 42 | 42 | 42 | 14,667 | 0.01 |

**Through the Exchange (matching plus market data)**

| Workload | Operations | M ops/s (min–max) | Mean ns | p50 | p95 | p99 | p99.9 | Max | Allocations per op |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `exchange/mixed` | 2,000,000 | 24.6 (24.4–24.7) | 41 | 41 | 125 | 167 | 250 | 8,167 | 0.22 |
<!-- bench:workloads:end -->

**The mixed flow, by kind of operation:**

<!-- bench:mixed:start -->
| Operation | Count | Mean ns | p50 | p95 | p99 | p99.9 | Max | Allocations per op |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| add | 1,017,742 | 29.4 | 41 | 42 | 83 | 125 | 8,416 | 0.14 |
| cancel | 687,688 | 22.1 | 41 | 42 | 42 | 125 | 4,750 | 0.00 |
| take | 196,151 | 96.6 | 84 | 167 | 209 | 292 | 5,292 | 1.00 |
| market | 98,419 | 63.0 | 42 | 125 | 167 | 250 | 4,083 | 1.00 |
<!-- bench:mixed:end -->

**Memory per resting order** (1,000 price levels per side):

<!-- bench:memory:start -->
| Resting orders | Levels per side | Heap bytes per order | Allocations per order | RSS growth per order |
| ---: | ---: | ---: | ---: | ---: |
| 10,000 | 1,000 | 186.3 | 0.201 | 234.3 |
| 100,000 | 1,000 | 141.9 | 0.021 | 149.9 |
| 1,000,000 | 1,000 | 130.1 | 0.003 | 143.0 |
<!-- bench:memory:end -->

## What the measurements changed

The first full run ([`apple-m4-baseline.json`](../bench/results/apple-m4-baseline.json))
pointed at two causes:

* **A heap node per resting order.** Sampling the 1M-order add/cancel workload with macOS's
  `sample` profiler put `malloc` and `free` above everything else, followed by the hash table
  in `OrderBook::insert`. The order-id index was a `std::unordered_map`:
  * every resting order cost one allocation to add and one free to cancel;
  * every lookup followed a pointer to a node somewhere in the heap.

  `OrderIndex` replaced it ([matching-engine.md](matching-engine.md#data-structures)). It is
  open addressing over one array of `{id, Order*}` slots, with Fibonacci hashing and
  backward-shift deletion. It allocates only when it doubles.
* **A trades vector that grew one allocation at a time.** Takers in the mixed flow allocated
  1.9 times each, because their trades vector grew 1 → 2 → 4. It now reserves room for 8
  trades at the first trade.

Neither change alters behaviour through the engine:
* the golden digests, the differential tests against the reference model, and the Python suite
  are unchanged;
* `OrderBook::insert`, the low-level primitive, now also rejects order id 0, which the engine
  never assigns and the index uses to mark an empty slot;
* the index has its own tests, including the wrap-around cases of backward-shift deletion, and
  seven mutants in the mutation suite.

Same machine and workloads, before → after:

<!-- bench:comparison:start -->
| Workload | Before, M ops/s | After, M ops/s | Speed-up | Max latency before → after | Allocations per op before → after |
| --- | ---: | ---: | ---: | ---: | ---: |
| `book_size/1k` | 13.5 | 15.9 | 1.18× | 10,583 → 6,417 ns | 0.80 → 0.30 |
| `book_size/10k` | 17.4 | 31.4 | 1.81× | 6,375 → 6,208 ns | 0.50 → 0.00 |
| `book_size/100k` | 16.1 | 28.4 | 1.77× | 5,583 → 4,291 ns | 0.50 → 0.00 |
| `book_size/1M` | 4.7 | 8.8 | 1.86× | 13,625,500 → 2,542 ns | 0.50 → 0.00 |
| `price_levels/1` | 18.8 | 53.4 | 2.84× | 449,209 → 1,667 ns | 0.50 → 0.00 |
| `price_levels/10` | 11.1 | 45.3 | 4.08× | 271,834 → 1,750 ns | 0.50 → 0.00 |
| `price_levels/100` | 12.4 | 37.4 | 3.03× | 450,333 → 1,208 ns | 0.50 → 0.00 |
| `price_levels/1k` | 12.3 | 29.2 | 2.38× | 234,000 → 292 ns | 0.50 → 0.00 |
| `price_levels/10k` | 11.4 | 19.6 | 1.72× | 394,125 → 917 ns | 0.50 → 0.00 |
| `patterns/passive` | 16.3 | 18.3 | 1.12× | 534,208 → 1,243,875 ns | 1.00 → 0.00 |
| `patterns/maker_taker` | 14.8 | 28.3 | 1.91× | 8,042 → 1,292 ns | 1.46 → 0.50 |
| `patterns/sweep_1` | 14.8 | 33.6 | 2.27× | 917 → 375 ns | 1.45 → 0.27 |
| `patterns/sweep_10` | 15.2 | 29.4 | 1.94× | 4,583 → 2,458 ns | 1.17 → 0.15 |
| `patterns/sweep_50` | 14.9 | 29.4 | 1.98× | 26,500 → 11,875 ns | 1.12 → 0.11 |
| `patterns/mixed` | 14.5 | 26.7 | 1.84× | 13,250 → 8,416 ns | 0.84 → 0.22 |
| `cancel/random/100k` | 13.5 | 58.9 | 4.36× | 424,583 → 1,083 ns | 0.00 → 0.00 |
| `cancel/oldest_first/100k` | 30.1 | 79.4 | 2.64× | 100,583 → 1,000 ns | 0.00 → 0.00 |
| `cancel/newest_first/100k` | 38.0 | 80.7 | 2.13× | 7,333 → 1,542 ns | 0.00 → 0.00 |
| `cancel/random/1M` | 3.1 | 14.9 | 4.80× | 551,125 → 11,583 ns | 0.00 → 0.00 |
| `cancel/oldest_first/1M` | 19.8 | 30.0 | 1.51× | 293,541 → 11,042 ns | 0.00 → 0.00 |
| `cancel/newest_first/1M` | 29.3 | 30.6 | 1.05× | 39,708 → 13,625 ns | 0.00 → 0.00 |
| `cancel/missing` | 372.3 | 317.4 | 0.85× | 12,542 → 5,917 ns | 0.00 → 0.00 |
| `cancel/requote` | 39.7 | 60.9 | 1.53× | 32,584 → 14,667 ns | 0.51 → 0.01 |
| `exchange/mixed` | 13.6 | 24.6 | 1.81× | 35,959 → 8,167 ns | 0.84 → 0.22 |
<!-- bench:comparison:end -->

**Worst cases.** The baseline's 13.6 ms maximum in `book_size/1M` was not the index: that
phase never grows it. It was a one-off event outside the engine, of the kind the *Max* column
records. The new index still rehashes every entry when it doubles. Measured on the M4 with
orders added one at a time, a single insert takes:
* about 1.6 ms when the index passes 2^18 entries;
* 3–4 ms at 2^19;
* 6.6 ms at 2^20, and once 17.6 ms.

`patterns/passive` shows the first of these as its 1.2 ms maximum. A production engine would
pre-size the index or rehash incrementally; growth is amortised O(1) here.

**What still costs the most** is `OrderBook::insert` and `OrderBook::remove`. They include the
price levels' `std::map`: a tree walk per new order, and a node allocated or freed whenever a
level appears or empties. This is visible in the price-level sweep, where throughput falls as
levels thin out. A flat, price-indexed ladder would be the next step, but it is a deeper change
to the book's invariants, and these measurements do not yet justify it.

## Linux (GitHub-hosted runner)

<!-- bench:linux:start -->
Intel(R) Xeon(R) Platinum 8370C CPU @ 2.80GHz (2 logical CPUs), Linux 7.0.0-1017-azure x86_64, GCC 13.3.0, Release ( -O3 -DNDEBUG). Clock resolution 16 ns. Source: `bench/results/linux-ci.json`, from the CI job.

| Workload | M ops/s (min–max) | Mean ns | p50 | p99 | p99.9 | Max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `book_size/1k` | 8.5 (7.1–8.5) | 118 | 145 | 243 | 305 | 41,227 |
| `book_size/10k` | 12.4 (8.2–12.7) | 81 | 109 | 202 | 350 | 14,355 |
| `book_size/100k` | 6.1 (6.0–6.4) | 164 | 214 | 601 | 758 | 50,430 |
| `book_size/1M` | 4.9 (4.7–4.9) | 206 | 251 | 650 | 874 | 19,376 |
| `price_levels/1` | 6.4 (6.2–6.6) | 156 | 193 | 582 | 723 | 16,026 |
| `price_levels/10` | 7.4 (7.0–9.0) | 136 | 194 | 592 | 769 | 111,989 |
| `price_levels/100` | 6.0 (5.8–6.3) | 165 | 199 | 587 | 736 | 57,542 |
| `price_levels/1k` | 5.8 (5.7–6.2) | 171 | 224 | 626 | 824 | 27,364 |
| `price_levels/10k` | 4.2 (4.1–4.3) | 239 | 317 | 821 | 1119 | 49,323 |
| `patterns/passive` | 7.8 (7.7–8.0) | 128 | 141 | 473 | 575 | 3,272,778 |
| `patterns/maker_taker` | 17.4 (17.1–17.6) | 58 | 75 | 195 | 413 | 16,538 |
| `patterns/sweep_1` | 13.9 (13.8–13.9) | 72 | 63 | 416 | 455 | 7,377 |
| `patterns/sweep_10` | 11.8 (11.5–11.8) | 85 | 64 | 1674 | 3970 | 16,845 |
| `patterns/sweep_50` | 11.9 (11.8–12.0) | 84 | 64 | 155 | 16977 | 52,429 |
| `patterns/mixed` | 12.9 (12.9–13.0) | 78 | 96 | 299 | 519 | 36,118 |
| `cancel/random/100k` | 8.6 (7.7–9.5) | 116 | 237 | 658 | 871 | 17,803 |
| `cancel/oldest_first/100k` | 11.9 (11.2–12.4) | 84 | 126 | 499 | 621 | 13,567 |
| `cancel/newest_first/100k` | 12.5 (11.9–14.9) | 80 | 134 | 517 | 646 | 15,565 |
| `cancel/random/1M` | 6.4 (4.9–6.4) | 157 | 262 | 656 | 829 | 42,844 |
| `cancel/oldest_first/1M` | 9.7 (9.6–9.8) | 103 | 141 | 515 | 618 | 26,684 |
| `cancel/newest_first/1M` | 9.7 (9.7–9.9) | 103 | 141 | 516 | 624 | 29,416 |
| `cancel/missing` | 126.2 (123.7–126.7) | 8 | 30 | 128 | 427 | 16,044 |
| `cancel/requote` | 24.9 (24.7–25.0) | 40 | 65 | 137 | 370 | 61,668 |
| `exchange/mixed` | 11.8 (11.8–11.9) | 85 | 99 | 370 | 593 | 75,116 |
<!-- bench:linux:end -->

## The Python simulation

<!-- bench:python:start -->
Most of the system's time is not in the engine but in the Python simulation around it:
* the discrete-event kernel;
* the agents;
* exact `Fraction` portfolio accounting;
* the run digest;
* the analytics.

[`bench/simulation_bench.py`](../bench/simulation_bench.py) measures it on the default market
(26 agents, five instruments, seeds fixed, median of 3 runs). It reports the kernel's
throughput, and what the live API pays per request as a run grows. Results:
[`python-final.json`](../bench/results/python-final.json), and before and after the change
below, [`python-before.json`](../bench/results/python-before.json) and
[`python-after.json`](../bench/results/python-after.json).

```bash
uv run python bench/simulation_bench.py --json results.json
```

**Kernel** (Apple M4, CPython 3.12.15):

| | Events/s | Orders/s | Trades/s | Simulated seconds per second |
| --- | ---: | ---: | ---: | ---: |
| Headless | 86,835 | 32,897 | 11,248 | 1,397 |
| With the analytics observer | 71,071 | 26,925 | 9,206 | 1,144 |

Each order costs about 30 µs of Python against about 40 ns in the engine. At this level, the
engine work above changes nothing measurable: kernel throughput is the same before and after.

**Live analytics.** Profiling showed that every request recomputed the analytics over the
whole run:
* every trade, once per instrument;
* about 30,000 logarithms for volatility and pricing error;
* every participant's Sharpe ratio and drawdown.

The cost grew with the run, and the terminal polls it. `MarketObserver` now keeps running
aggregates as samples, trades and markouts arrive, and `analyze()` reads them
([quantitative-metrics.md](quantitative-metrics.md)). The results are the same:
* sums and means are bit-for-bit identical (`FloatSum` reproduces Python's `sum`);
* drawdowns and counts follow the same steps;
* variances use Welford's algorithm, within 1e-12 of the two-pass result;
* a test runs whole simulations through both and compares every metric.

Milliseconds per request, before → after (same engine build):

| Run length | Analytics endpoint | Sharpe, all 28 participants | Leaderboard |
| --- | ---: | ---: | ---: |
| 10 min (602 samples, 4,837 trades) | 11.2 → 0.56 | 2.01 → 0.010 | 2.78 → 0.68 |
| 30 min (1,802 samples, 12,526 trades) | 32.4 → 0.56 | 6.18 → 0.013 | 6.97 → 0.69 |
| 120 min (7,202 samples, 46,345 trades) | 130.6 → 0.66 | 25.7 → 0.029 | 27.0 → 0.70 |

* **The cost moved, it did not vanish.** It is now spread across the run: with the observer
  attached, kernel throughput fell about 7% (74.8k → 69.8k events/s in the same comparison).
* **A whole experiment costs about 5% more.** From the result files, an observed 10-minute
  run plus one `analyze()` took 0.498 + 0.011 = 0.509 s before and 0.534 + 0.001 = 0.535 s
  after. That is the price of a per-request cost that no longer grows with the run.
* **The live server's per-request cost no longer grows.** That makes the terminal's polling
  safe for runs of any length.
<!-- bench:python:end -->

## Reading these numbers

* **These are matching costs in process.** A production exchange's latency is dominated by
  networking, kernel bypass and serialisation, none of which exists here. The comparison that
  matters is between workloads, and between before and after.
* **Single thread, single instrument.** Books are independent, so instruments could be
  sharded across cores. That is not measured.
* **Worst cases are rare events.** The *Max* column includes the doubling of the order pool or
  the id index (see above), page faults on first touch, and interrupts. p99.9 is the better
  guide to steady-state tails.
* **Big books are memory-bound.** At a million resting orders the working set (about 130 MB)
  is far beyond the caches, and a random cancel waits on main memory: compare
  `cancel/random/1M` with `cancel/oldest_first/1M`.
