# Matching Engine

The matching engine is the core of Udeshi Markets: a deterministic, single-threaded,
continuous-auction limit order book written in C++20 with no dependencies beyond the standard
library. It lives in [`engine/`](../engine) and knows nothing about agents, simulation time,
HTTP, or the frontend.

## Components

| Type | File | Responsibility |
| --- | --- | --- |
| `OrderRequest`, `Order` | [`order.hpp`](../engine/include/udeshi/order.hpp) | What a trader submits; a resting order (also an intrusive list node). |
| `Trade` | [`trade.hpp`](../engine/include/udeshi/trade.hpp) | One execution between an aggressive and a resting order. |
| `PriceLevel` | [`price_level.hpp`](../engine/include/udeshi/price_level.hpp) | FIFO queue of resting orders at one price, with cached totals. |
| `OrderPool` | [`order_pool.hpp`](../engine/include/udeshi/order_pool.hpp) | Slab allocator giving stable `Order*` pointers without per-order heap allocation. |
| `BookSide`, `OrderBook` | [`order_book.hpp`](../engine/include/udeshi/order_book.hpp) | Resting orders of one instrument; queries; structural invariant checker. |
| `MatchingEngine` | [`matching_engine.hpp`](../engine/include/udeshi/matching_engine.hpp) | Validation, id/sequence assignment, matching, cancellation across instruments. |
| `Exchange` | [`exchange.hpp`](../engine/include/udeshi/exchange.hpp) | Symbology, tick-size conversion, trade-derived market statistics, recent trades. |

## Numeric representation

* **Prices are `int64` tick counts.** Each instrument has a tick size (e.g. `0.01`) used only at
  the `Exchange` boundary (`to_ticks` / `to_currency`). Floating point never enters the matching
  path, so price comparisons are exact and results are bit-for-bit reproducible.
* **Quantities are signed `int64`**, so a subtraction bug produces a negative value an invariant
  check can catch instead of silently wrapping.
* **Bounds:** `0 < price ≤ 10⁹` ticks and `0 < quantity ≤ 10⁹`. Any single notional
  `price × quantity ≤ 10¹⁸ < 2⁶³ ≈ 9.22 × 10¹⁸`, so no intermediate value can overflow.

## Data structures

```
OrderBook (one per instrument)
├── bids : BookSide = std::map<Price, PriceLevel, BestFirst{Buy}>    // descending
├── asks : BookSide = std::map<Price, PriceLevel, BestFirst{Sell}>   // ascending
├── pool : OrderPool                                                 // owns every Order
└── index: std::unordered_map<OrderId, Order*>                       // O(1) cancel lookup

PriceLevel
  price, total_quantity, order_count
  head ⇄ order ⇄ order ⇄ … ⇄ tail          // intrusive doubly linked list, arrival order
```

* **Best-first ordering.** The two sides use opposite comparators so that `begin()` is always the
  best price. The best price is therefore O(1) to read, and the book is never re-sorted.
  (Using `std::less` for both sides and reading bids from `rbegin()` would also work, but
  `--end()` is O(log L) in libc++, where L is the number of price levels.)
* **Intrusive FIFO.** Each `Order` carries `prev`/`next` pointers and a back-pointer to its
  `PriceLevel`. Appending at the tail, consuming from the head and unlinking a cancelled order
  from the middle are all O(1) with no allocation. The level caches `total_quantity` and
  `order_count`, so depth queries never walk the queue.
* **Stable addresses.** `std::map` nodes never move, so a `PriceLevel*` stays valid until its level
  is erased. `OrderPool` allocates `Order`s in 4096-element chunks that are never freed while the
  book lives, and reuses released nodes from a LIFO free list. `PriceLevel` and `OrderBook` are
  deliberately non-copyable and non-movable so that nothing can alias these pointers.
* **Detached copies.** The list links live in a hook whose copy operations never transfer
  links. Copying a resting `Order` gives a plain snapshot that cannot reach, corrupt or dangle
  into the live book. Read-only views are deep-const: a `const Order` or `const PriceLevel` only
  ever hands out `const` neighbours.
* **Self-validating primitives.** `OrderBook::insert` rejects anything `check_invariants()` would
  flag: wrong instrument, duplicate id, quantity or price out of range, invalid side, or a
  sequence number that isn't younger than the level's tail. It gives the strong exception
  guarantee.

| Operation | Complexity |
| --- | --- |
| Best bid / ask | O(1) |
| Add order at an existing price level | O(log L) map lookup + O(1) append |
| Add order at a new price level | O(log L) |
| Cancel | O(1) average hash lookup + O(1) unlink (+ O(log L) if the level empties) |
| Match k orders across m levels | O(k + m) (+ amortised O(1) per emptied best level) |
| Depth snapshot of d levels | O(d) |

## Price–time priority

`MatchingEngine::match` implements the continuous double auction:

```
remaining ← requested quantity
while remaining > 0:
    level ← best level on the opposite side           # begin() of the opposite BookSide
    if no level: break
    if limit order and level.price does not cross the limit: break
    while remaining > 0 and level not exhausted:
        resting ← level.front()                       # earliest arrival at this price
        [self-trade prevention, see below]
        fill ← min(remaining, resting.remaining)
        emit Trade(price = resting.price, quantity = fill)
        remaining -= fill; resting.remaining -= fill
        if resting fully filled: remove it (and the level, if now empty)
```

1. **Price priority.** The outer loop always takes `begin()` of the opposite side, which is the
   lowest ask for a buy and the highest bid for a sell. A buy limit at `P` crosses a level at
   price `p` iff `p ≤ P`; a sell limit at `P` crosses iff `p ≥ P`. Market orders cross every
   level.
2. **Time priority.** Orders only ever join a level at its tail, and their `sequence` numbers are
   globally strictly increasing, so the list order is exactly the arrival order. Matching always
   consumes the head. A partially filled resting order keeps its place at the head.
3. **Execution price.** Every trade executes at the **resting** order's price. An aggressive limit
   order can therefore only receive price improvement: a buy never pays more than its limit and a
   sell never receives less.
4. **Remainder handling** after matching:

   | Order | Remainder |
   | --- | --- |
   | Limit, `GoodTillCancel` | Rests at its limit price (status `Resting`). |
   | Limit, `ImmediateOrCancel` | Cancelled (`Cancelled`, reason `NoLiquidity`). |
   | Market (any time-in-force) | Cancelled (`Cancelled`, reason `NoLiquidity`). |

   A resting remainder can never cross the book. Matching stops only when the incoming quantity
   is exhausted, the opposite side is empty, or the best opposite price no longer crosses the
   limit. In all three cases the remainder does not cross.

### Worked example (from the specification)

| Seq | Action | Book after |
| --- | --- | --- |
| 1 | Sell 10 @ 101 | asks: 101×10 |
| 2 | Sell 5 @ 102 | asks: 101×10, 102×5 |
| 3 | Buy 12 @ 102 | trades 10 @ 101 (seq 4) and 2 @ 102 (seq 5); asks: 102×3 |

`MatchingEngineTest.SpecExampleBuySweepsTwoAskLevels` checks the fills and the resulting book,
and `EngineContractTest.SpecExampleHasExactIdsAndSequences` checks the exact ids and sequence
numbers in this table.

## Self-trade prevention (STP)

Market makers quote both sides, so an agent's aggressive order can meet its own resting quote.
STP is controlled by the **incoming** order's `self_trade_prevention` field:

| Mode | Behaviour when the next resting order has the same owner |
| --- | --- |
| `Allow` (default) | Trade normally (a wash trade; net position change is zero). |
| `CancelResting` | Remove the resting order, report it in `SubmitResult::resting_cancels`, continue matching. |
| `CancelIncoming` | Stop matching; cancel the incoming remainder (fills already made stand). A GTC remainder is also cancelled, because resting it would cross the owner's own order. |

Owner `0` (`kAnonymousTrader`) has no identity, so STP never applies to it.

## Identifiers, sequencing, and determinism

* One global `Sequence` counter covers all instruments. Each of the following consumes exactly
  one sequence number, in the order it happens:
  * every `submit` and `cancel` call, including rejected submissions and failed cancels, because
    they are events too;
  * every trade;
  * every resting order removed by self-trade prevention.

  The events caused by one submission follow its own sequence number contiguously. Merging
  `SubmitResult::trades` and `SubmitResult::resting_cancels` by `sequence` therefore reproduces
  the exact interleaving, and a consumer can detect a lost event from a gap.
* `OrderId`s are dense (1, 2, 3, …) and global across instruments, and only accepted orders
  get one. `TradeId`s are dense and global as well.
* `Timestamp` is caller-supplied **simulated** time in nanoseconds. It is recorded on orders and
  trades but never used for priority. Orders submitted with decreasing timestamps still queue in
  arrival order.
* Nothing on the matching path depends on wall-clock time, randomness, pointer addresses, or
  iteration order of an unordered container. The order-id index is only used for point lookups.
  First-party code is compiled with `-ffp-contract=off`, so floating-point results cannot differ
  between targets that do and don't have fused multiply-add.

## Validation and errors

Bad input never throws. `submit` returns `OrderStatus::Rejected` and reports the **first**
failing check, in this order:

| Reason | Condition |
| --- | --- |
| `UnknownInstrument` | No book with that id. |
| `InvalidSide` / `InvalidOrderType` / `InvalidTimeInForce` / `InvalidSelfTradePrevention` | An enum field holds a value outside its declared enumerators. This can happen when an integer crosses a language boundary, for example from Python. The engine rejects it rather than silently reinterpreting it. |
| `InvalidQuantity` | `quantity ≤ 0` or `> kMaxOrderQuantity`. |
| `InvalidPrice` | Limit order with `price ≤ 0` or `> kMaxPrice`. |
| `MarketOrderWithPrice` | Market order with non-zero price (ambiguous intent). |

`cancel` returns `CancelOutcome::{Cancelled, UnknownInstrument, OrderNotFound}`. `OrderNotFound`
covers ids that never existed, that are already fully filled or already cancelled, or that belong
to a different instrument. Exceptions are reserved for programmer errors such as an unknown
instrument passed to a query accessor or a duplicate symbol at registration.

For every accepted order the result satisfies the partition identity

```
filled_quantity + resting_quantity + cancelled_quantity = requested_quantity
filled_quantity = Σ trade.quantity over SubmitResult::trades
```

### Failure model

`submit` and `cancel` are `noexcept`. The only thing that can fail inside them is memory
allocation (growing the result vectors or inserting a resting order), and that can happen
*after* the book has been modified. If an exception escaped at that point, executed fills would
go unreported and the caller's view would silently diverge from the book. Instead the process
terminates (fail-fast), and a test injects an allocation failure mid-match to check this.

Operations that can be made atomic are atomic:
* `OrderBook::insert` and `OrderPool` growth give the strong exception guarantee.
* `Exchange::add_instrument` registers with the engine last and rolls back its own earlier
  steps on failure. The engine and the exchange can therefore never disagree about which
  instruments exist. A fault-injection test fails every allocation inside these operations in
  turn and checks this.

## Market data (`Exchange`)

The `Exchange` derives every price-related number from executed trades or from the live book.
Nothing can set a price directly.

* `last`, `open`, `high`, `low`, `trade_count`: updated from each trade.
* `volume`: cumulative traded quantity. It **saturates** at `INT64_MAX` instead of
  overflowing, and sets `volume_saturated`. Per-order bounds limit each trade but not the
  running total.
* `notional = Σ price·quantity` in ticks, and `VWAP = notional / volume`. Each product is
  computed exactly in `int64` (at most 10¹⁸) and then added as a `double`. There is no
  floating-point multiply for a compiler to fuse, so the value is bit-identical on every
  platform. A test pins an exact value whose products exceed 2⁵³. Once volume has saturated,
  VWAP is reported as undefined rather than as a ratio that no longer averages traded prices.
* These per-trade updates live in a pure function, `apply_trade(MarketStatistics&, const
  Trade&)`, so edge cases such as saturation are unit-tested directly.
* `best_bid`, `best_ask`; `midprice = (bid + ask) / 2` and `spread = ask − bid`, both undefined
  unless both sides are present. These are returned as `std::optional`, never as a sentinel
  value.
* `current_price()` = last trade price, falling back to the instrument's reference price before
  the first trade.
* Recent trades per instrument are kept in a fixed-capacity ring buffer. It grows to capacity
  once and then overwrites the oldest entry, so steady-state recording never allocates.

**Tick conversion.**
* Tick sizes must lie in `[1e-12, 1e12]`. Within that range every on-grid price is a normal,
  finite `double`, which the tolerance below assumes.
* `to_ticks` rejects any negative price, however tiny. It accepts a non-negative price only if
  it lies on the tick grid, allowing for floating-point rounding error: a relative tolerance of
  16 machine epsilons, about 3.6e‑6 of a tick at `kMaxPrice`.
* Off-grid prices are rejected, never rounded. Rounding could turn a buy limit of 12345.678 into
  12345.68, more aggressive than the price the caller stated.

**Symbols** are 1–16 ASCII characters from `A–Z 0–9 . -`, starting with a letter or digit. They
are checked without `<cctype>`, whose classification depends on the process locale.

The exchange deliberately does **not** know an asset's fundamental value. A real venue cannot
observe it; it belongs to the market-environment layer (Phase 3).

## Testing

The suite lives in [`engine/tests`](../engine/tests) and runs under Debug, Release and
ASan/UBSan on Linux (GCC, libstdc++) and macOS (Clang, libc++) in CI.

1. **Scenario and contract tests**
   ([`test_matching_engine.cpp`](../engine/tests/test_matching_engine.cpp),
   [`test_engine_contract.cpp`](../engine/tests/test_engine_contract.cpp)) check every rule
   above with hand-computed values:
   * price priority, FIFO, and arrival order winning over timestamps;
   * partial and full fills, multi-level and 5,000-level sweeps, boundary prices `1` and
     `kMaxPrice`;
   * market and IOC remainders, every status and cancel reason, every STP mode;
   * exact sequence and id values, validation order, and multi-instrument id density.
2. **Structural invariants.** `OrderBook::check_invariants()` checks:
   * cached level totals and counts are correct;
   * list links and level back-pointers are consistent;
   * sequences strictly increase within each level;
   * the index and the pool agree with the book;
   * the book is never crossed or locked.
3. **Differential testing.** [`reference_engine.hpp`](../engine/tests/reference_engine.hpp) is
   a deliberately naive model of the documented contract.
   * It shares no code with the engine: flat vectors, linear best-order scans, its own id and
     sequence counters, and its own validation of raw enum values.
   * 39 seeded streams (about 250k operations) run across four regimes: narrow, moderate, wide,
     and a deep book of 5k–10k resting orders spanning several pool chunks.
   * They mix limit, market and IOC orders, all STP modes, non-monotone timestamps, cancels of
     live, stale, unknown and wrong-instrument ids, and malformed input.
   * After **every** operation, every field of every result must equal the model.
   * The full L3 book (owner, sequence, original quantity, timestamp), L2 aggregates, per-side
     volume accounting and the structural invariants are also compared after every operation,
     except in the deep-book profile. There they run every 25 operations, because the naive
     model's book dump costs O(n log n).
   * A stream fails if it never produced each reject reason, or if it stops reaching other
     interesting paths (sweeps, STP in both modes, market partial fills, multi-chunk books).
4. **Contract properties checked directly**, independently of the model, so that a bug the
   model happened to share would still be caught:
   * sequence numbers are contiguous, and order and trade ids are dense;
   * no execution is worse than either side's limit, and every execution is at the resting
     order's price;
   * **quantity conservation:**
     * for every order, `filled + cancelled + still resting = submitted`;
     * on each instrument and side, resting volume equals what was rested, minus fills against
       it, minus cancels;
   * a finished order never trades again, and the book holds an order exactly when the engine
     reported it as resting;
   * self-trade prevention only cancels the incoming owner's own, marketable orders, only in
     the mode that asks for it, and never for anonymous orders. With STP active no self-trade
     executes. A `CancelIncoming` stop leaves the blocking own order at the front of the best
     opposite level;
   * a remainder is rested or cancelled for lack of liquidity only when no marketable
     liquidity is left.
5. **Determinism.** Golden FNV‑1a digests of every output and the final books for fixed streams
   are checked in, and CI confirms they are identical on Linux/GCC/libstdc++ and
   macOS/Clang/libc++.
   * Each digest is computed by two engines alive at once, so dependence on absolute addresses
     (for example hashing pointers) would show up.
   * Dependence on the *relative* order of addresses is caught by the golden values and the
     reference model instead, because both engines allocate in the same pattern.
6. **Fault injection and memory safety.**
   * A separate test binary replaces `operator new` and fails each allocation in turn. It
     checks the exception guarantees above and the fail-fast termination of both
     `MatchingEngine::submit` and `Exchange::submit`.
   * The replacement lives in its own binary so the main suite keeps ASan's new/delete mismatch
     checks. `static_assert`s pin the `noexcept` contract.
   * Sanitizers instrument every target, googletest included; mixing instrumented and
     uninstrumented code causes false container-overflow reports. The ASan preset enables
     alloc/dealloc-mismatch and initialization-order checks on every platform.
   * `OrderPool` poisons released nodes under ASan, so reading through a stale `Order*` is
     reported like a heap use-after-free. A death test checks this.

**Mutation testing.** [`tools/mutation_test.py`](../tools/mutation_test.py) applies realistic
bugs, rebuilds each under ASan/UBSan, and requires the suite to fail. The bugs include:
* off-by-one crossing checks, LIFO instead of FIFO, FIFO keyed on timestamp;
* trading at the aggressor's price, reading a released order, chunks freed with the wrong
  `delete`;
* sequence gaps, per-instrument ids, wrong cancel reasons or sides, a reordered validation
  chain;
* a missing rollback, unpoisoned pool nodes, locale-dependent symbol checks, an unsaturated
  volume.

The tool runs with the ASan preset's sanitizer options. A GitHub Actions workflow
([`mutation.yml`](../.github/workflows/mutation.yml)) runs it weekly and on demand on Linux and
macOS, so the result below is checked continuously rather than claimed once.

A compile failure counts as a kill only if a `static_assert` fires, since some properties
(deep-const accessors, saturating arithmetic) are tested at compile time. Any other build
failure marks the mutant itself as invalid.

**Current result: 49/49 killed.** The history behind that number:
* The first version of the suite, with 20 mutants, missed one: an untested STP cancel
  quantity.
* An independent adversarial review then built about 15 more mutants that the suite of the time
  missed. They included sequence-number gaps, per-instrument id counters, timestamp-keyed FIFO,
  wrong cancel reasons, and use of a released pool node.
* Each one led to a new test, and all are now in the mutant list.

## Known limitations

* Single-threaded by design. Sharding by instrument is the natural scaling path, since books are
  independent.
* Only limit (GTC/IOC) and market orders. There are no stop, iceberg, post-only or fill-or-kill
  orders, and no order modification (cancel and replace instead).
* `cancel` does not check ownership, and `Trade` carries both participants' ids. The Phase 2
  gateway must enforce that agents cancel only their own orders and must publish an anonymized
  public tape.
* `to_currency` returns a binary `double`. For example, 3 ticks of 0.05 is
  `0.15000000000000002`, so canonical decimal formatting is left to the presentation layer.
* `std::map` levels and the `unordered_map` index allocate nodes. Phase 6 benchmarks will decide
  whether a flat price-indexed ladder or an open-addressing index is worth the complexity.
* `Exchange::notional` is a `double` accumulator. Each term is exact, but the running sum has a
  relative rounding error of about 1e‑16.
