#include "udeshi/exchange.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <utility>

namespace udeshi {

namespace {

constexpr std::size_t kMaxSymbolLength = 16;

// Maximum relative distance from the tick grid, measured in ticks, that is attributed to
// floating-point rounding rather than to an off-grid price. Converting a decimal currency
// price involves at most ~3 roundings (the decimal input, the tick size, the division), each
// bounded by half an ulp, so 16 epsilon leaves a wide margin while staying far below one tick
// anywhere in the valid range (3.6e-6 ticks at kMaxPrice).
constexpr double kTickGridRelativeTolerance = 16.0 * std::numeric_limits<double>::epsilon();

// Locale-independent on purpose: <cctype> classification depends on the C locale, which a
// host process (e.g. a Python interpreter) may set to accept non-ASCII bytes.
[[nodiscard]] constexpr bool is_ascii_upper_or_digit(char c) noexcept {
  return (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9');
}

[[nodiscard]] bool is_valid_symbol(std::string_view symbol) noexcept {
  if (symbol.empty() || symbol.size() > kMaxSymbolLength ||
      !is_ascii_upper_or_digit(symbol.front())) {
    return false;
  }
  return std::all_of(symbol.begin(), symbol.end(), [](char c) {
    return is_ascii_upper_or_digit(c) || c == '.' || c == '-';
  });
}

}  // namespace

Exchange::Exchange(std::size_t recent_trade_capacity)
    : recent_trade_capacity_(recent_trade_capacity) {
  if (recent_trade_capacity_ == 0) {
    throw std::invalid_argument("Exchange: recent trade capacity must be positive");
  }
}

InstrumentId Exchange::add_instrument(InstrumentSpec spec) {
  if (!is_valid_symbol(spec.symbol)) {
    throw std::invalid_argument("Exchange: invalid symbol '" + spec.symbol +
                                "' (expected 1-16 chars of A-Z, 0-9, '.', '-', starting with "
                                "A-Z or 0-9)");
  }
  if (symbol_index_.contains(spec.symbol)) {
    throw std::invalid_argument("Exchange: duplicate symbol '" + spec.symbol + "'");
  }
  if (!(spec.tick_size >= kMinTickSize && spec.tick_size <= kMaxTickSize)) {  // Also rejects NaN.
    throw std::invalid_argument("Exchange: tick size must be within [1e-12, 1e12]");
  }
  if (spec.reference_price <= 0 || spec.reference_price > kMaxPrice) {
    throw std::invalid_argument("Exchange: reference price out of range for '" + spec.symbol +
                                "'");
  }
  if (engine_.instrument_count() != instruments_.size()) {
    throw std::logic_error("Exchange: engine and exchange instrument ids diverged");
  }

  // Commit in three steps, undoing earlier steps if a later one throws, so that the engine
  // and the exchange can never disagree about which instruments exist. The engine goes last
  // because it is the one step that cannot be undone.
  const auto id = static_cast<InstrumentId>(instruments_.size());
  InstrumentState state;
  state.stats.instrument = id;
  state.stats.reference_price = spec.reference_price;
  state.spec = std::move(spec);
  instruments_.push_back(std::move(state));  // Strong guarantee.
  try {
    const auto [symbol_slot, inserted] =
        symbol_index_.try_emplace(instruments_.back().spec.symbol, id);
    try {
      const InstrumentId engine_id = engine_.add_instrument();  // Strong guarantee.
      if (engine_id != id) {
        throw std::logic_error("Exchange: engine assigned an unexpected instrument id");
      }
    } catch (...) {
      symbol_index_.erase(symbol_slot);
      throw;
    }
  } catch (...) {
    instruments_.pop_back();
    throw;
  }
  return id;
}

std::optional<InstrumentId> Exchange::find_instrument(std::string_view symbol) const {
  const auto it = symbol_index_.find(std::string(symbol));
  if (it == symbol_index_.end()) {
    return std::nullopt;
  }
  return it->second;
}

InstrumentId Exchange::instrument_id(std::string_view symbol) const {
  const auto id = find_instrument(symbol);
  if (!id) {
    throw std::out_of_range("Exchange: unknown symbol '" + std::string(symbol) + "'");
  }
  return *id;
}

const InstrumentSpec& Exchange::instrument(InstrumentId instrument) const {
  return state(instrument).spec;
}

std::vector<std::string> Exchange::symbols() const {
  std::vector<std::string> result;
  result.reserve(instruments_.size());
  for (const auto& instrument_state : instruments_) {
    result.push_back(instrument_state.spec.symbol);
  }
  return result;
}

const Exchange::InstrumentState& Exchange::state(InstrumentId instrument) const {
  if (instrument >= instruments_.size()) {
    throw std::out_of_range("Exchange: unknown instrument id " + std::to_string(instrument));
  }
  return instruments_[instrument];
}

SubmitResult Exchange::submit(const OrderRequest& request) noexcept {
  SubmitResult result = engine_.submit(request);
  if (!result.trades.empty()) {
    // The engine only produces trades for instruments it knows, and add_instrument keeps the
    // engine's instruments identical to ours, so this index is always in range.
    InstrumentState& instrument_state = instruments_[request.instrument];
    for (const Trade& trade : result.trades) {
      record_trade(instrument_state, trade);
    }
  }
  return result;
}

CancelResult Exchange::cancel(InstrumentId instrument, OrderId order_id) noexcept {
  return engine_.cancel(instrument, order_id);
}

void apply_trade(MarketStatistics& stats, const Trade& trade) noexcept {
  if (!stats.open_price) {
    stats.open_price = trade.price;
  }
  stats.last_price = trade.price;
  stats.high_price = std::max(stats.high_price.value_or(trade.price), trade.price);
  stats.low_price = std::min(stats.low_price.value_or(trade.price), trade.price);
  const Quantity volume = saturating_add(stats.volume, trade.quantity);
  stats.volume_saturated = stats.volume_saturated || volume - stats.volume != trade.quantity;
  stats.volume = volume;
  stats.trade_count += 1;
  // price * quantity <= 1e18 is exact in int64; converting the exact product (rather than
  // multiplying two doubles) leaves no multiply-add for the compiler to fuse differently on
  // different targets.
  stats.notional += static_cast<double>(trade.price * trade.quantity);
  if (stats.volume_saturated) {
    stats.vwap.reset();
  } else {
    stats.vwap = stats.notional / static_cast<double>(stats.volume);
  }
}

void Exchange::record_trade(InstrumentState& instrument_state, const Trade& trade) {
  apply_trade(instrument_state.stats, trade);

  TradeRing& ring = instrument_state.recent;
  if (ring.trades.size() < recent_trade_capacity_) {
    ring.trades.push_back(trade);
  } else {
    ring.trades[ring.oldest] = trade;
    ring.oldest = (ring.oldest + 1) % recent_trade_capacity_;
  }
}

const OrderBook& Exchange::order_book(InstrumentId instrument) const {
  static_cast<void>(state(instrument));  // Validates the id with a consistent error.
  return engine_.book(instrument);
}

BookSnapshot Exchange::book_snapshot(InstrumentId instrument, std::size_t max_levels) const {
  return order_book(instrument).snapshot(max_levels);
}

std::vector<Trade> Exchange::recent_trades(InstrumentId instrument, std::size_t limit) const {
  const TradeRing& ring = state(instrument).recent;
  const std::size_t stored = ring.trades.size();
  const std::size_t count = std::min(limit, stored);
  std::vector<Trade> result;
  result.reserve(count);
  // Oldest-first: skip the (stored - count) oldest entries, starting from the ring's head.
  for (std::size_t i = stored - count; i < stored; ++i) {
    result.push_back(ring.trades[(ring.oldest + i) % stored]);
  }
  return result;
}

std::optional<Price> Exchange::best_bid(InstrumentId instrument) const {
  return order_book(instrument).best_bid();
}

std::optional<Price> Exchange::best_ask(InstrumentId instrument) const {
  return order_book(instrument).best_ask();
}

std::optional<double> Exchange::midprice(InstrumentId instrument) const {
  const OrderBook& book = order_book(instrument);
  const auto bid = book.best_bid();
  const auto ask = book.best_ask();
  if (!bid || !ask) {
    return std::nullopt;
  }
  return (static_cast<double>(*bid) + static_cast<double>(*ask)) / 2.0;
}

std::optional<Price> Exchange::spread(InstrumentId instrument) const {
  const OrderBook& book = order_book(instrument);
  const auto bid = book.best_bid();
  const auto ask = book.best_ask();
  if (!bid || !ask) {
    return std::nullopt;
  }
  return *ask - *bid;
}

MarketStatistics Exchange::statistics(InstrumentId instrument) const {
  MarketStatistics stats = state(instrument).stats;
  const OrderBook& book = order_book(instrument);
  stats.best_bid = book.best_bid();
  stats.best_ask = book.best_ask();
  stats.midprice = midprice(instrument);
  stats.spread = spread(instrument);
  stats.bid_depth = book.total_volume(Side::Buy);
  stats.ask_depth = book.total_volume(Side::Sell);
  stats.resting_orders = book.order_count();
  return stats;
}

double Exchange::to_currency(InstrumentId instrument, Price ticks) const {
  return static_cast<double>(ticks) * state(instrument).spec.tick_size;
}

Price price_to_ticks(double currency_price, double tick_size) {
  if (!(tick_size >= kMinTickSize && tick_size <= kMaxTickSize)) {  // Also rejects NaN.
    throw std::invalid_argument("price_to_ticks: tick size must be within [1e-12, 1e12]");
  }
  if (!std::isfinite(currency_price)) {
    throw std::invalid_argument("price_to_ticks: price must be finite");
  }
  if (currency_price < 0.0) {  // -0.0 compares equal to 0.0 and is accepted.
    throw std::invalid_argument("price_to_ticks: price must be non-negative");
  }
  const double ticks = currency_price / tick_size;
  // Range-check before rounding and casting. Allowing half a tick above kMaxPrice admits
  // floating-point noise at the bound; rounding then lands inside the range.
  if (!(ticks < static_cast<double>(kMaxPrice) + 0.5)) {
    throw std::invalid_argument("price_to_ticks: price out of range");
  }
  const double rounded = std::round(ticks);
  if (std::fabs(ticks - rounded) > kTickGridRelativeTolerance * std::max(1.0, ticks)) {
    throw std::invalid_argument("price_to_ticks: price is not a multiple of the tick size");
  }
  return static_cast<Price>(rounded);
}

Price Exchange::to_ticks(InstrumentId instrument, double currency_price) const {
  return price_to_ticks(currency_price, state(instrument).spec.tick_size);
}

}  // namespace udeshi
