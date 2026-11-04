# Benchmarks

How fast the matching engine and the simulation are, measured. Every measurement on this page
comes from the benchmark harnesses in [`bench/`](../bench) and is recorded in
[`bench/results/`](../bench/results); nothing is estimated.
* The tables are generated from those files by
  [`tools/bench_report.py`](../tools/bench_report.py).
* The figures in the text are taken from the same files.

## Running them

```bash
cmake --preset release && cmake --build --preset release
build/release/bench/udeshi_bench --repetitions 11 --json results.json   # as published, 16 s
build/release/bench/udeshi_bench --list                    # what each workload does
build/release/bench/udeshi_bench --filter cancel --repetitions 9
build/release/bench/udeshi_bench --self-check              # verification catches changed replays
python3 tools/bench_report.py results.json --baseline bench/results/apple-m4-baseline.json \
    --intermediate bench/results/apple-m4-index-only.json --python bench/results \
    --update docs/benchmarking.md                          # regenerate the tables below
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
    stream. Random cancels in a 100k-order book cost 15 ns each in a stream, but 19 ns each on
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
| Repetitions | 11 timed replays per workload, after a warm-up |
| Object sizes | `Order` 88 B, `PriceLevel` 40 B, `Trade` 88 B |
<!-- bench:environment:end -->

## Results

<!-- bench:workloads:start -->
**Book size**

| Workload | Operations | M ops/s (min–max) | Mean ns | p50 | p95 | p99 | p99.9 | Max | Allocations per op |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `book_size/1k` | 400,000 | 15.9 (15.9–16.0) | 63 | 42 | 125 | 125 | 167 | 6,167 | 0.30 |
| `book_size/10k` | 400,000 | 31.4 (31.1–31.6) | 32 | 41 | 83 | 84 | 125 | 6,292 | 0.00 |
| `book_size/100k` | 400,000 | 29.5 (29.1–29.6) | 34 | 42 | 83 | 84 | 125 | 6,333 | 0.00 |
| `book_size/1M` | 400,000 | 8.9 (8.7–9.4) | 113 | 125 | 209 | 291 | 375 | 10,500 | 0.00 |

**Price levels**

| Workload | Operations | M ops/s (min–max) | Mean ns | p50 | p95 | p99 | p99.9 | Max | Allocations per op |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `price_levels/1` | 400,000 | 51.7 (48.2–52.3) | 19 | 0 | 42 | 83 | 125 | 6,666 | 0.00 |
| `price_levels/10` | 400,000 | 43.6 (32.2–45.1) | 23 | 41 | 42 | 83 | 125 | 8,500 | 0.00 |
| `price_levels/100` | 400,000 | 37.6 (37.0–38.0) | 27 | 41 | 42 | 83 | 84 | 4,250 | 0.00 |
| `price_levels/1k` | 400,000 | 28.9 (27.4–29.3) | 35 | 42 | 84 | 125 | 209 | 1,458 | 0.00 |
| `price_levels/10k` | 400,000 | 19.3 (18.5–19.9) | 52 | 42 | 125 | 125 | 208 | 6,583 | 0.00 |

**Submission patterns**

| Workload | Operations | M ops/s (min–max) | Mean ns | p50 | p95 | p99 | p99.9 | Max | Allocations per op |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `patterns/passive` | 400,000 | 19.1 (18.7–19.7) | 52 | 42 | 125 | 125 | 209 | 1,150,375 | 0.00 |
| `patterns/maker_taker` | 400,000 | 28.6 (28.2–28.8) | 35 | 42 | 84 | 84 | 125 | 6,125 | 0.50 |
| `patterns/sweep_1` | 22,000 | 33.8 (33.2–33.9) | 30 | 41 | 167 | 208 | 209 | 375 | 0.27 |
| `patterns/sweep_10` | 202,000 | 29.6 (29.4–29.7) | 34 | 41 | 42 | 84 | 1709 | 6,375 | 0.15 |
| `patterns/sweep_50` | 1,002,000 | 29.2 (29.1–29.3) | 34 | 41 | 42 | 42 | 7541 | 14,500 | 0.11 |
| `patterns/mixed` | 2,000,000 | 26.7 (26.7–26.8) | 37 | 41 | 84 | 167 | 209 | 6,500 | 0.22 |

**Cancellation**

| Workload | Operations | M ops/s (min–max) | Mean ns | p50 | p95 | p99 | p99.9 | Max | Allocations per op |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `cancel/random/100k` | 100,000 | 65.9 (61.8–66.8) | 15 | 0 | 42 | 125 | 167 | 3,250 | 0.00 |
| `cancel/oldest_first/100k` | 100,000 | 85.3 (83.2–86.6) | 12 | 0 | 42 | 84 | 125 | 3,084 | 0.00 |
| `cancel/newest_first/100k` | 100,000 | 88.8 (86.1–89.8) | 11 | 0 | 42 | 83 | 125 | 1,458 | 0.00 |
| `cancel/random/1M` | 1,000,000 | 14.9 (13.5–15.1) | 67 | 84 | 250 | 292 | 416 | 15,292 | 0.00 |
| `cancel/oldest_first/1M` | 1,000,000 | 30.2 (26.9–30.5) | 33 | 42 | 84 | 166 | 292 | 13,334 | 0.00 |
| `cancel/newest_first/1M` | 1,000,000 | 30.6 (30.1–30.8) | 33 | 42 | 125 | 167 | 334 | 13,375 | 0.00 |
| `cancel/missing` | 400,000 | 357.9 (348.8–358.9) | 3 | 0 | 42 | 42 | 42 | 5,750 | 0.00 |
| `cancel/requote` | 400,000 | 61.1 (60.8–61.5) | 16 | 0 | 42 | 42 | 83 | 1,375 | 0.01 |

**Through the Exchange (matching plus market data)**

| Workload | Operations | M ops/s (min–max) | Mean ns | p50 | p95 | p99 | p99.9 | Max | Allocations per op |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `exchange/mixed` | 2,000,000 | 25.2 (24.9–25.4) | 40 | 41 | 125 | 167 | 250 | 25,209 | 0.22 |
<!-- bench:workloads:end -->

**The mixed flow, by kind of operation:**

<!-- bench:mixed:start -->
| Operation | Count | Mean ns | p50 | p95 | p99 | p99.9 | Max | Allocations per op |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| add | 1,017,742 | 29.4 | 41 | 42 | 83 | 84 | 6,500 | 0.14 |
| cancel | 687,688 | 22.2 | 41 | 42 | 42 | 84 | 417 | 0.00 |
| take | 196,151 | 96.3 | 84 | 167 | 209 | 292 | 4,375 | 1.00 |
| market | 98,419 | 63.3 | 42 | 125 | 167 | 250 | 334 | 1.00 |
<!-- bench:mixed:end -->

**Memory per resting order** (1,000 price levels per side):

<!-- bench:memory:start -->
| Resting orders | Levels per side | Heap bytes per order | Allocations per order | RSS growth per order |
| ---: | ---: | ---: | ---: | ---: |
| 10,000 | 1,000 | 186.3 | 0.201 | 235.9 |
| 100,000 | 1,000 | 141.9 | 0.021 | 150.1 |
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

Same machine and workloads, before, with the new index only
([`apple-m4-index-only.json`](../bench/results/apple-m4-index-only.json), measured just before
the final run), and after both changes:

<!-- bench:comparison:start -->
| Workload | M ops/s: Before | Index only | After | Speed-up | Max latency before → after | Allocations per op, before → index only → after |
| --- | ---: | ---: | ---: | ---: |
| `book_size/1k` | 13.5 → 16.1 → 15.9 | 1.19× | 10,583 → 6,167 ns | 0.80 → 0.30 → 0.30 |
| `book_size/10k` | 17.4 → 31.6 → 31.4 | 1.81× | 6,375 → 6,292 ns | 0.50 → 0.00 → 0.00 |
| `book_size/100k` | 16.1 → 29.7 → 29.5 | 1.83× | 5,583 → 6,333 ns | 0.50 → 0.00 → 0.00 |
| `book_size/1M` | 4.7 → 9.2 → 8.9 | 1.88× | 13,625,500 → 10,500 ns | 0.50 → 0.00 → 0.00 |
| `price_levels/1` | 18.8 → 56.2 → 51.7 | 2.75× | 449,209 → 6,666 ns | 0.50 → 0.00 → 0.00 |
| `price_levels/10` | 11.1 → 45.8 → 43.6 | 3.93× | 271,834 → 8,500 ns | 0.50 → 0.00 → 0.00 |
| `price_levels/100` | 12.4 → 38.0 → 37.6 | 3.04× | 450,333 → 4,250 ns | 0.50 → 0.00 → 0.00 |
| `price_levels/1k` | 12.3 → 29.9 → 28.9 | 2.35× | 234,000 → 1,458 ns | 0.50 → 0.00 → 0.00 |
| `price_levels/10k` | 11.4 → 20.0 → 19.3 | 1.70× | 394,125 → 6,583 ns | 0.50 → 0.00 → 0.00 |
| `patterns/passive` | 16.3 → 18.1 → 19.1 | 1.17× | 534,208 → 1,150,375 ns | 1.00 → 0.00 → 0.00 |
| `patterns/maker_taker` | 14.8 → 23.7 → 28.6 | 1.93× | 8,042 → 6,125 ns | 1.46 → 0.96 → 0.50 |
| `patterns/sweep_1` | 14.8 → 25.3 → 33.8 | 2.28× | 917 → 375 ns | 1.45 → 0.55 → 0.27 |
| `patterns/sweep_10` | 15.2 → 29.0 → 29.6 | 1.95× | 4,583 → 6,375 ns | 1.17 → 0.18 → 0.15 |
| `patterns/sweep_50` | 14.9 → 29.5 → 29.2 | 1.97× | 26,500 → 14,500 ns | 1.12 → 0.12 → 0.11 |
| `patterns/mixed` | 14.5 → 23.8 → 26.7 | 1.84× | 13,250 → 6,500 ns | 0.84 → 0.33 → 0.22 |
| `cancel/random/100k` | 13.5 → 61.8 → 65.9 | 4.88× | 424,583 → 3,250 ns | 0.00 → 0.00 → 0.00 |
| `cancel/oldest_first/100k` | 30.1 → 79.9 → 85.3 | 2.83× | 100,583 → 3,084 ns | 0.00 → 0.00 → 0.00 |
| `cancel/newest_first/100k` | 38.0 → 84.4 → 88.8 | 2.34× | 7,333 → 1,458 ns | 0.00 → 0.00 → 0.00 |
| `cancel/random/1M` | 3.1 → 14.9 → 14.9 | 4.80× | 551,125 → 15,292 ns | 0.00 → 0.00 → 0.00 |
| `cancel/oldest_first/1M` | 19.8 → 28.6 → 30.2 | 1.53× | 293,541 → 13,334 ns | 0.00 → 0.00 → 0.00 |
| `cancel/newest_first/1M` | 29.3 → 29.5 → 30.6 | 1.04× | 39,708 → 13,375 ns | 0.00 → 0.00 → 0.00 |
| `cancel/missing` | 372.3 → 315.4 → 357.9 | 0.96× | 12,542 → 5,750 ns | 0.00 → 0.00 → 0.00 |
| `cancel/requote` | 39.7 → 61.2 → 61.1 | 1.54× | 32,584 → 1,375 ns | 0.51 → 0.01 → 0.01 |
| `exchange/mixed` | 13.6 → 22.7 → 25.2 | 1.85× | 35,959 → 25,209 ns | 0.84 → 0.33 → 0.22 |
<!-- bench:comparison:end -->

**Worst cases.**
* The baseline's 13.6 ms maximum in `book_size/1M` was not the index: that phase never grows
  it. It was a one-off event outside the engine, of the kind the *Max* column records.
* The new index does have a cost of its own: it rehashes every entry in one insert when it
  doubles. The harness times exactly those inserts, building a book one order at a time in
  three fresh processes:

<!-- bench:growth:start -->
| Resting orders after the insert | Entries rehashed | Insert time, median | Each process |
| ---: | ---: | ---: | ---: |
| 1,025 | 1,024 | 5.7 µs | 5.7 µs, 5.5 µs, 5.9 µs |
| 2,049 | 2,048 | 9.4 µs | 9.8 µs, 9.0 µs, 9.4 µs |
| 4,097 | 4,096 | 38.5 µs | 41.9 µs, 38.5 µs, 36.7 µs |
| 8,193 | 8,192 | 57.5 µs | 55.0 µs, 57.5 µs, 58.7 µs |
| 16,385 | 16,384 | 82.2 µs | 86.6 µs, 82.2 µs, 81.7 µs |
| 32,769 | 32,768 | 140.2 µs | 148.5 µs, 139.0 µs, 140.2 µs |
| 65,537 | 65,536 | 308.9 µs | 312.5 µs, 308.9 µs, 306.4 µs |
| 131,073 | 131,072 | 650.8 µs | 653.8 µs, 650.8 µs, 647.3 µs |
| 262,145 | 262,144 | 1.5 ms | 1.5 ms, 1.5 ms, 1.5 ms |
| 524,289 | 524,288 | 3.0 ms | 3.0 ms, 3.1 ms, 2.8 ms |
| 1,048,577 | 1,048,576 | 6.1 ms | 6.1 ms, 6.9 ms, 6.0 ms |
<!-- bench:growth:end -->

The doubling at 2^18 entries also shows as the maximum of `patterns/passive`, whose book grows
past it. A production engine would pre-size the index or rehash incrementally; here growth is
amortised O(1).

**What still costs the most** is `OrderBook::insert` and `OrderBook::remove`. They include the
price levels' `std::map`: a tree walk per new order, and a node allocated or freed whenever a
level appears or empties. This is visible in the price-level sweep, where throughput falls as
levels thin out. A flat, price-indexed ladder would be the next step, but it is a deeper change
to the book's invariants, and these measurements do not yet justify it.

## Linux (GitHub-hosted runner)

From the CI benchmark job of commit 32c7cba (run 41801293859). The engine is unchanged since.
The harness has since gained the index-growth timing, which this run predates. Hosted runners
are shared virtual machines whose hardware varies between runs, so read these as a second
platform's shape, not as a comparison with the M4.

<!-- bench:linux:start -->
AMD EPYC 7763 64-Core Processor (2 logical CPUs), Linux 7.0.0-1017-azure x86_64, GCC 13.3.0, Release ( -O3 -DNDEBUG). Clock: `std::chrono::steady_clock`: ticks of 1.00 ns; two consecutive reads at least 20 ns apart (median 30 ns). Source: `bench/results/linux-ci.json`, from the CI job.

| Workload | M ops/s (min–max) | Mean ns | p50 | p99 | p99.9 | Max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `book_size/1k` | 9.2 (6.4–9.3) | 108 | 131 | 221 | 290 | 39,073 |
| `book_size/10k` | 11.9 (11.9–12.1) | 84 | 110 | 261 | 511 | 40,727 |
| `book_size/100k` | 4.0 (3.8–4.3) | 253 | 261 | 611 | 932 | 47,269 |
| `book_size/1M` | 3.2 (2.9–3.2) | 316 | 310 | 642 | 1032 | 26,159 |
| `price_levels/1` | 5.6 (5.5–5.9) | 177 | 220 | 571 | 801 | 27,091 |
| `price_levels/10` | 5.4 (5.2–5.9) | 185 | 221 | 571 | 822 | 39,905 |
| `price_levels/100` | 4.3 (4.2–4.6) | 231 | 240 | 581 | 842 | 29,034 |
| `price_levels/1k` | 3.8 (3.6–4.1) | 264 | 290 | 691 | 1072 | 28,884 |
| `price_levels/10k` | 3.5 (3.4–3.6) | 285 | 311 | 741 | 1362 | 67,246 |
| `patterns/passive` | 6.6 (6.4–6.6) | 152 | 170 | 441 | 561 | 2,626,777 |
| `patterns/maker_taker` | 15.7 (15.4–15.8) | 64 | 80 | 180 | 251 | 20,859 |
| `patterns/sweep_1` | 13.0 (13.0–13.2) | 77 | 60 | 441 | 491 | 13,105 |
| `patterns/sweep_10` | 11.3 (11.2–11.4) | 88 | 70 | 431 | 4208 | 23,023 |
| `patterns/sweep_50` | 11.3 (11.3–11.4) | 88 | 70 | 151 | 19156 | 51,386 |
| `patterns/mixed` | 13.2 (13.2–13.2) | 76 | 90 | 270 | 461 | 91,102 |
| `cancel/random/100k` | 6.2 (6.1–7.2) | 160 | 241 | 581 | 812 | 30,357 |
| `cancel/oldest_first/100k` | 11.1 (10.8–12.6) | 90 | 160 | 451 | 581 | 19,888 |
| `cancel/newest_first/100k` | 15.9 (12.0–16.8) | 63 | 80 | 420 | 581 | 21,661 |
| `cancel/random/1M` | 5.2 (5.0–5.5) | 193 | 321 | 671 | 1052 | 59,832 |
| `cancel/oldest_first/1M` | 8.8 (8.5–9.0) | 113 | 180 | 481 | 642 | 71,675 |
| `cancel/newest_first/1M` | 8.9 (8.3–9.1) | 112 | 180 | 471 | 571 | 36,349 |
| `cancel/missing` | 109.4 (107.5–110.1) | 9 | 40 | 60 | 110 | 23,474 |
| `cancel/requote` | 24.8 (24.3–25.0) | 40 | 61 | 120 | 180 | 20,538 |
| `exchange/mixed` | 12.2 (12.1–12.2) | 82 | 100 | 300 | 492 | 81,476 |
<!-- bench:linux:end -->

## The Python simulation

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

**Kernel** (Apple M4, CPython 3.12.15, the final engine;
[`python-final.json`](../bench/results/python-final.json)):

<!-- bench:python-kernel:start -->
| | Events/s | Orders/s | Trades/s | Simulated seconds per second |
| --- | ---: | ---: | ---: | ---: |
| Headless | 86,835 | 32,897 | 11,248 | 1,397 |
| With the analytics observer | 71,071 | 26,925 | 9,206 | 1,144 |
<!-- bench:python-kernel:end -->

Each order costs about 30 µs of Python, against about 40 ns in the engine. At this level the
engine work above is invisible: headless kernel throughput with the final engine is within 2%
of that with the old one (85,583 events/s in
[`python-before.json`](../bench/results/python-before.json)).

**Live analytics.** Profiling showed that every request recomputed the analytics over the
whole run:
* every trade, once per instrument;
* four logarithms per sample and instrument, for volatility and pricing error;
* every participant's Sharpe ratio and drawdown.

The cost grew with the run, and the terminal polls it. `MarketObserver` now keeps running
aggregates as samples, trades and markouts arrive, and `analyze()` reads them
([quantitative-metrics.md](quantitative-metrics.md)). The results are the same:
* sums and means are bit-for-bit identical (`FloatSum` reproduces Python's `sum`);
* drawdowns and counts follow the same steps;
* variances use Welford's algorithm, within 1e-12 of the two-pass result;
* a test runs whole simulations through both and compares every metric.

Milliseconds per request, before → after (the same engine build;
[`python-before.json`](../bench/results/python-before.json),
[`python-after.json`](../bench/results/python-after.json)):

<!-- bench:python-live:start -->
| Run length | Analytics endpoint | Sharpe, all participants | Leaderboard |
| --- | ---: | ---: | ---: |
| 10 min (602 samples, 4,837 trades, 28 participants) | 11.2 → 0.56 | 2.01 → 0.010 | 2.78 → 0.68 |
| 30 min (1,802 samples, 12,526 trades, 28 participants) | 32.4 → 0.56 | 6.18 → 0.013 | 6.97 → 0.69 |
| 120 min (7,202 samples, 46,345 trades, 28 participants) | 130.6 → 0.66 | 25.67 → 0.029 | 26.98 → 0.70 |
<!-- bench:python-live:end -->

* **The cost moved, it did not vanish.** It is now spread across the run: with the observer
  attached, kernel throughput fell about 7% (74.8k → 69.8k events/s in the same comparison).
* **A whole experiment costs about 5% more.** From the result files, an observed 10-minute
  run plus one `analyze()` took 0.498 + 0.011 = 0.509 s before and 0.534 + 0.001 = 0.535 s
  after. That is the price of a per-request cost that no longer grows with the run.
* **The live server's per-request cost no longer grows.** That makes the terminal's polling
  safe for runs of any length.

## Reading these numbers

* **These are matching costs in process.** A production exchange's latency is dominated by
  networking, kernel bypass and serialisation, none of which exists here. The comparison that
  matters is between workloads, and between before and after.
* **Single thread, single instrument.** Books are independent, so instruments could be
  sharded across cores. That is not measured.
* **Worst cases are rare events.** The *Max* column includes:
  * a new 4,096-order chunk in the order pool;
  * the doubling of the id index (above);
  * page faults on first touch;
  * interrupts.

  p99.9 is the better guide to steady-state tails.
* **Big books are memory-bound.** At a million resting orders the working set (about 130 MB)
  is far beyond the caches, and a random cancel waits on main memory: compare
  `cancel/random/1M` with `cancel/oldest_first/1M`.
