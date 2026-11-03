#include "udeshi/exchange.hpp"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <stdexcept>
#include <utility>

namespace udeshi {

namespace {

constexpr std::size_t kMaxSymbolLength = 16;
// Relative tolerance, in ticks, when checking that a currency price lies on the tick grid.
constexpr double kTickGridTolerance = 1e-6;

[[nodiscard]] bool is_valid_symbol(std::string_view symbol) noexcept {
  if (symbol.empty() || symbol.size() > kMaxSymbolLength) {
    return false;
  }
  return std::all_of(symbol.begin(), symbol.end(), [](char c) {
    const auto uc = static_cast<unsigned char>(c);
    return std::isupper(uc) != 0 || std::isdigit(uc) != 0 || c == '.' || c == '-';
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
                                "' (expected 1-16 chars of A-Z, 0-9, '.', '-')");
  }
  if (symbol_index_.contains(spec.symbol)) {
    throw std::invalid_argument("Exchange: duplicate symbol '" + spec.symbol + "'");
  }
  if (!std::isfinite(spec.tick_size) || spec.tick_size <= 0.0) {
    throw std::invalid_argument("Exchange: tick size must be positive and finite");
  }
  if (spec.reference_price <= 0 || spec.reference_price > kMaxPrice) {
    throw std::invalid_argument("Exchange: reference price out of range for '" + spec.symbol +
                                "'");
  }

  const InstrumentId id = engine_.add_instrument();
  if (id != instruments_.size()) {
    throw std::logic_error("Exchange: engine and exchange instrument ids diverged");
  }
  InstrumentState state;
  state.stats.instrument = id;
  state.stats.reference_price = spec.reference_price;
  symbol_index_.emplace(spec.symbol, id);
  state.spec = std::move(spec);
  instruments_.push_back(std::move(state));
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
  for (const auto& state : instruments_) {
    result.push_back(state.spec.symbol);
  }
  return result;
}

const Exchange::InstrumentState& Exchange::state(InstrumentId instrument) const {
  if (instrument >= instruments_.size()) {
    throw std::out_of_range("Exchange: unknown instrument id " + std::to_string(instrument));
  }
  return instruments_[instrument];
}

SubmitResult Exchange::submit(const OrderRequest& request) {
  SubmitResult result = engine_.submit(request);
  if (!result.trades.empty()) {
    InstrumentState& instrument_state = instruments_[request.instrument];
    for (const Trade& trade : result.trades) {
      record_trade(instrument_state, trade);
    }
  }
  return result;
}

CancelResult Exchange::cancel(InstrumentId instrument, OrderId order_id) {
  return engine_.cancel(instrument, order_id);
}

void Exchange::record_trade(InstrumentState& instrument_state, const Trade& trade) {
  MarketStatistics& stats = instrument_state.stats;
  if (!stats.open_price) {
    stats.open_price = trade.price;
  }
  stats.last_price = trade.price;
  stats.high_price = std::max(stats.high_price.value_or(trade.price), trade.price);
  stats.low_price = std::min(stats.low_price.value_or(trade.price), trade.price);
  stats.volume += trade.quantity;
  stats.trade_count += 1;
  stats.notional += static_cast<double>(trade.price) * static_cast<double>(trade.quantity);

  instrument_state.recent_trades.push_back(trade);
  if (instrument_state.recent_trades.size() > recent_trade_capacity_) {
    instrument_state.recent_trades.pop_front();
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
  const auto& trades = state(instrument).recent_trades;
  const std::size_t count = std::min(limit, trades.size());
  return {trades.end() - static_cast<std::ptrdiff_t>(count), trades.end()};
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
  if (stats.volume > 0) {
    stats.vwap = stats.notional / static_cast<double>(stats.volume);
  }
  return stats;
}

double Exchange::to_currency(InstrumentId instrument, Price ticks) const {
  return static_cast<double>(ticks) * state(instrument).spec.tick_size;
}

Price Exchange::to_ticks(InstrumentId instrument, double currency_price) const {
  const double tick_size = state(instrument).spec.tick_size;
  if (!std::isfinite(currency_price)) {
    throw std::invalid_argument("Exchange::to_ticks: price must be finite");
  }
  const double ticks = currency_price / tick_size;
  if (std::fabs(ticks) > static_cast<double>(kMaxPrice)) {
    throw std::invalid_argument("Exchange::to_ticks: price out of range");
  }
  const double rounded = std::round(ticks);
  if (std::fabs(ticks - rounded) > kTickGridTolerance * std::max(1.0, std::fabs(ticks))) {
    throw std::invalid_argument("Exchange::to_ticks: price is not a multiple of the tick size");
  }
  return static_cast<Price>(rounded);
}

}  // namespace udeshi
