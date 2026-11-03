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

This exact scenario is `MatchingEngineTest.SpecExampleBuySweepsTwoAskLevels`.

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

* One global `Sequence` counter covers all instruments. Each `submit` or `cancel` call consumes
  one sequence number, including rejected submissions and failed cancels, because they are
  events too. Each trade consumes one more, in execution order. Event order is therefore a total
  order: a submission's sequence precedes the sequences of the trades it causes.
* `OrderId`s are dense (1, 2, 3, …) and assigned only to accepted orders. `TradeId`s are dense
  as well.
* `Timestamp` is caller-supplied **simulated** time in nanoseconds. It is recorded on orders and
  trades but never used for priority, so wall-clock time cannot affect results.
* Nothing on the matching path depends on wall-clock time, randomness, pointer addresses, or
  iteration order of an unordered container. The order-id index is only used for point lookups.
  Replaying the same call sequence produces identical ids, trades, and books; the
  `DeterministicReplay` tests check this.

## Validation and errors

Bad input on the hot path never throws. `submit` returns `OrderStatus::Rejected` with a
`RejectReason`:

| Reason | Condition |
| --- | --- |
| `UnknownInstrument` | No book with that id. |
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

## Market data (`Exchange`)

The `Exchange` derives every price-related number from executed trades or from the live book.
Nothing can set a price directly.

* `last`, `open`, `high`, `low`, `volume`, `trade_count`: updated from each trade.
* `notional = Σ price·quantity` (in ticks, accumulated as a `double`); `VWAP = notional / volume`.
* `best_bid`, `best_ask`; `midprice = (bid + ask) / 2` and `spread = ask − bid`, both undefined
  unless both sides are present. These are returned as `std::optional`, never as a sentinel
  value.
* `current_price()` = last trade price, falling back to the instrument's reference price before
  the first trade.
* A bounded ring buffer of recent trades per instrument.

The exchange deliberately does **not** know an asset's fundamental value. A real venue cannot
observe it; it belongs to the market-environment layer (Phase 3).

## Testing

The suite lives in [`engine/tests`](../engine/tests) and runs under Debug, Release and
ASan/UBSan in CI. It has four parts.

1. **Scenario tests** for every rule above: price priority, FIFO, partial and full fills,
   multi-level sweeps, market and IOC remainders, cancellation edge cases, validation
   boundaries, STP modes, and instrument isolation.
2. **Structural invariants.** `OrderBook::check_invariants()` checks:
   * cached level totals and counts are correct;
   * list links and level back-pointers are consistent;
   * sequences strictly increase within each level;
   * the index and the pool agree with the book;
   * the book is never crossed or locked.
3. **Differential testing.** [`reference_book.hpp`](../engine/tests/reference_book.hpp) is a
   deliberately naive oracle: a flat vector and a linear best-order scan, sharing no code with the
   engine. 36 seeded random streams (about 190k operations) mix limit, market and IOC orders,
   cancels of live and stale ids, all STP modes, and invalid input, at three price-dispersion
   regimes. After every operation each trade, each outcome and each STP cancel must match the
   oracle exactly. The full L3 book (every resting order, in priority order) is compared every 64
   operations and at the end.
4. **Economic properties**, checked on every trade of those streams:
   * no execution worse than either side's limit;
   * the execution price equals the resting order's limit;
   * no order fills more than its quantity;
   * cancelled or finished orders never trade again;
   * shares and cash summed over all participants stay exactly zero (conservation);
   * trade ids and sequences strictly increase.

**Mutation testing.** [`tools/mutation_test.py`](../tools/mutation_test.py) injects 20 realistic
bugs, including off-by-one crossing checks, LIFO instead of FIFO, trading at the aggressor's
price, and broken level cleanup. It rebuilds each mutant under sanitizers and checks that the
suite fails. The first run exposed one surviving mutant (an untested STP cancel quantity), which
led to a new test. The suite now kills 20/20.

## Known limitations

* Single-threaded by design. Sharding by instrument is the natural scaling path, since books are
  independent.
* Only limit (GTC/IOC) and market orders. There are no stop, iceberg, post-only or fill-or-kill
  orders, and no order modification (cancel and replace instead).
* `std::map` levels and the `unordered_map` index allocate nodes. Phase 6 benchmarks will decide
  whether a flat price-indexed ladder or an open-addressing index is worth the complexity.
* `Exchange::notional` is a `double` accumulator. Its relative error is about 1e‑16, which is
  ample for VWAP but not exact.
