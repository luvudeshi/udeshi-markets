#pragma once

#include <cstddef>
#include <deque>
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

// Market data for one instrument. Every price-derived field comes from executed trades or
// from the live book; nothing here can be set directly.
struct MarketStatistics {
  InstrumentId instrument = 0;
  Price reference_price = 0;
  std::optional<Price> last_price;  // Last executed trade price.
  std::optional<Price> open_price;  // First executed trade price.
  std::optional<Price> high_price;
  std::optional<Price> low_price;
  Quantity volume = 0;
  std::uint64_t trade_count = 0;
  double notional = 0.0;  // Sum of price * quantity in tick units.
  std::optional<double> vwap;  // notional / volume, in ticks.
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

// A multi-instrument exchange: the matching engine plus symbology and market data.
//
// The exchange owns no simulation concepts (agents, fundamental values, news): a real venue
// cannot observe an asset's "true" value, so those live in the market environment layer.
class Exchange {
 public:
  static constexpr std::size_t kDefaultRecentTradeCapacity = 1000;

  explicit Exchange(std::size_t recent_trade_capacity = kDefaultRecentTradeCapacity);

  // Registers an instrument. Throws std::invalid_argument for an empty or duplicate symbol,
  // a non-positive / non-finite tick size, or an out-of-range reference price.
  InstrumentId add_instrument(InstrumentSpec spec);

  [[nodiscard]] std::size_t instrument_count() const noexcept { return instruments_.size(); }
  [[nodiscard]] std::optional<InstrumentId> find_instrument(std::string_view symbol) const;
  // Throws std::out_of_range for an unknown symbol / id.
  [[nodiscard]] InstrumentId instrument_id(std::string_view symbol) const;
  [[nodiscard]] const InstrumentSpec& instrument(InstrumentId instrument) const;
  [[nodiscard]] std::vector<std::string> symbols() const;

  // ---- Order entry -------------------------------------------------------------------------
  SubmitResult submit(const OrderRequest& request);
  CancelResult cancel(InstrumentId instrument, OrderId order_id);

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
  // Converts a currency price to ticks. Throws std::invalid_argument if the value is not
  // (within floating-point tolerance) an exact multiple of the tick size.
  [[nodiscard]] Price to_ticks(InstrumentId instrument, double currency_price) const;

  [[nodiscard]] const MatchingEngine& engine() const noexcept { return engine_; }

 private:
  struct InstrumentState {
    InstrumentSpec spec;
    MarketStatistics stats;
    std::deque<Trade> recent_trades;
  };

  [[nodiscard]] const InstrumentState& state(InstrumentId instrument) const;
  void record_trade(InstrumentState& state, const Trade& trade);

  MatchingEngine engine_;
  std::vector<InstrumentState> instruments_;
  std::unordered_map<std::string, InstrumentId> symbol_index_;
  std::size_t recent_trade_capacity_;
};

}  // namespace udeshi
