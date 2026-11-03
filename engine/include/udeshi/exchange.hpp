#pragma once

#include <cstddef>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

#include "udeshi/matching_engine.hpp"
#include "udeshi/order_book.hpp"
#include "udeshi/trade.hpp"
#include "udeshi/types.hpp"

namespace udeshi {

// Static description of a tradable instrument.
struct InstrumentSpec {
  std::string symbol;
  double tick_size = 0.01;   // Currency value of one price tick.
  Price reference_price = 0;  // Opening reference price in ticks (e.g. previous close).
};

// Accepted tick sizes. Within this range every on-grid price from 1 to kMaxPrice ticks is a
// normal, finite double, which is what the floating-point tolerance in Exchange::to_ticks
// assumes (subnormal tick sizes lose relative precision; huge ones overflow to infinity).
inline constexpr double kMinTickSize = 1e-12;
inline constexpr double kMaxTickSize = 1e12;

// Market data for one instrument. Every price-derived field comes from executed trades or
// from the live book; nothing here can be set directly.
struct MarketStatistics {
  InstrumentId instrument = 0;
  Price reference_price = 0;
  std::optional<Price> last_price;  // Last executed trade price.
  std::optional<Price> open_price;  // First executed trade price.
  std::optional<Price> high_price;
  std::optional<Price> low_price;
  Quantity volume = 0;  // Saturates at INT64_MAX instead of overflowing.
  bool volume_saturated = false;
  std::uint64_t trade_count = 0;
  // Sum of price * quantity in tick units. Each product is computed exactly in int64 and then
  // added as a double, so the value is bit-identical on every platform (no FMA contraction).
  double notional = 0.0;
  // notional / volume, in ticks. Undefined before the first trade, and once volume has
  // saturated (the ratio would no longer be an average of traded prices).
  std::optional<double> vwap;
  std::optional<Price> best_bid;
  std::optional<Price> best_ask;
  std::optional<double> midprice;  // (best_bid + best_ask) / 2, in ticks; needs both sides.
  std::optional<Price> spread;     // best_ask - best_bid, in ticks; needs both sides.
  Quantity bid_depth = 0;          // Total resting buy quantity.
  Quantity ask_depth = 0;          // Total resting sell quantity.
  std::size_t resting_orders = 0;

  // Last trade price, falling back to the reference price before the first trade.
  [[nodiscard]] Price current_price() const noexcept {
    return last_price.value_or(reference_price);
  }
};

// Updates trade-derived statistics (last / open / high / low, volume, count, notional, VWAP)
// with one execution. Used by Exchange for every trade; exposed so that edge cases such as
// volume saturation can be tested directly.
void apply_trade(MarketStatistics& stats, const Trade& trade) noexcept;

// A multi-instrument exchange: the matching engine plus symbology and market data.
//
// The exchange owns no simulation concepts (agents, fundamental values, news): a real venue
// cannot observe an asset's "true" value, so those live in the market environment layer.
class Exchange {
 public:
  static constexpr std::size_t kDefaultRecentTradeCapacity = 1000;

  explicit Exchange(std::size_t recent_trade_capacity = kDefaultRecentTradeCapacity);

  // Registers an instrument. Throws std::invalid_argument for an invalid or duplicate symbol
  // (symbols are 1-16 ASCII characters from A-Z 0-9 . -, starting with A-Z or 0-9), a tick
  // size outside [kMinTickSize, kMaxTickSize] (or NaN), or a reference price outside
  // (0, kMaxPrice]. Strong exception guarantee: on any failure the exchange and its engine are
  // left unchanged.
  InstrumentId add_instrument(InstrumentSpec spec);

  [[nodiscard]] std::size_t instrument_count() const noexcept { return instruments_.size(); }
  [[nodiscard]] std::optional<InstrumentId> find_instrument(std::string_view symbol) const;
  // Throws std::out_of_range for an unknown symbol / id.
  [[nodiscard]] InstrumentId instrument_id(std::string_view symbol) const;
  [[nodiscard]] const InstrumentSpec& instrument(InstrumentId instrument) const;
  [[nodiscard]] std::vector<std::string> symbols() const;

  // ---- Order entry -------------------------------------------------------------------------
  // Same contract as MatchingEngine::submit / cancel, plus market-data recording.
  SubmitResult submit(const OrderRequest& request) noexcept;
  CancelResult cancel(InstrumentId instrument, OrderId order_id) noexcept;

  // ---- Market data (all prices in ticks) ---------------------------------------------------
  [[nodiscard]] const OrderBook& order_book(InstrumentId instrument) const;
  [[nodiscard]] BookSnapshot book_snapshot(InstrumentId instrument, std::size_t max_levels) const;
  // Up to `limit` most recent trades, oldest first.
  [[nodiscard]] std::vector<Trade> recent_trades(InstrumentId instrument, std::size_t limit) const;
  [[nodiscard]] std::optional<Price> best_bid(InstrumentId instrument) const;
  [[nodiscard]] std::optional<Price> best_ask(InstrumentId instrument) const;
  [[nodiscard]] std::optional<double> midprice(InstrumentId instrument) const;
  [[nodiscard]] std::optional<Price> spread(InstrumentId instrument) const;
  [[nodiscard]] MarketStatistics statistics(InstrumentId instrument) const;

  // ---- Unit conversion ---------------------------------------------------------------------
  [[nodiscard]] double to_currency(InstrumentId instrument, Price ticks) const;
  // Converts a non-negative currency price to ticks. Throws std::invalid_argument if the value
  // is not finite, is negative or above kMaxPrice ticks, or is not an exact multiple of the
  // tick size up to floating-point rounding error (a relative error of 16 machine epsilons,
  // i.e. about 3.6e-6 of a tick at kMaxPrice). Off-grid prices are rejected, never rounded,
  // so a converted limit price can never be more aggressive than the price the caller stated.
  [[nodiscard]] Price to_ticks(InstrumentId instrument, double currency_price) const;

  [[nodiscard]] const MatchingEngine& engine() const noexcept { return engine_; }

 private:
  // Fixed-capacity ring of the most recent trades. Grows until it reaches capacity, then
  // overwrites the oldest entry, so steady-state recording never allocates.
  struct TradeRing {
    std::vector<Trade> trades;
    std::size_t oldest = 0;  // Index of the oldest trade once the ring is full.
  };

  struct InstrumentState {
    InstrumentSpec spec;
    MarketStatistics stats;
    TradeRing recent;
  };

  [[nodiscard]] const InstrumentState& state(InstrumentId instrument) const;
  void record_trade(InstrumentState& state, const Trade& trade);

  MatchingEngine engine_;
  std::vector<InstrumentState> instruments_;
  std::unordered_map<std::string, InstrumentId> symbol_index_;
  std::size_t recent_trade_capacity_;
};

}  // namespace udeshi
