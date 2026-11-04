#include "workloads.hpp"

#include <algorithm>
#include <deque>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>

#include "rng.hpp"

namespace udeshi::bench {

std::string_view to_string(OpKind kind) noexcept {
  switch (kind) {
    case OpKind::Add:
      return "add";
    case OpKind::Cancel:
      return "cancel";
    case OpKind::CancelMissing:
      return "cancel_missing";
    case OpKind::Take:
      return "take";
    case OpKind::Market:
      return "market";
    case OpKind::Sweep:
      return "sweep";
  }
  return "unknown";
}

namespace {

constexpr Price kMid = kMidPrice;  // 1,000.00 at a tick of 0.01.
constexpr std::uint64_t kSeed = 20'261'104;
constexpr InstrumentId kInstrument = 0;

// Records operations against a real engine and keeps track of which orders rest, so that the
// operations it generates (a cancel of a random resting order, an order joining the best
// price) are valid at the moment they are replayed.
class Planner {
 public:
  explicit Planner(std::uint64_t seed) : rng_(seed) { engine_.add_instrument(); }

  [[nodiscard]] SplitMix64& rng() noexcept { return rng_; }
  [[nodiscard]] const OrderBook& book() const { return engine_.book(kInstrument); }
  [[nodiscard]] std::size_t live_count() const noexcept { return live_.size(); }
  // Resting orders in the order they were placed (exact while nothing has been removed).
  [[nodiscard]] const std::vector<OrderId>& live() const noexcept { return live_; }

  // The operations recorded so far build the starting book; later ones are measured.
  void end_setup() {
    setup_end_ = ops_.size();
    start_orders_ = book().order_count();
    start_levels_ = std::max(book().level_count(Side::Buy), book().level_count(Side::Sell));
  }

  // A limit order that must rest without trading.
  void add(Side side, Price price, Quantity quantity) {
    const SubmitResult result = submit(OpKind::Add, limit(side, price, quantity));
    if (result.status != OrderStatus::Resting || !result.trades.empty()) {
      throw std::logic_error("workload bug: an Add order traded or was rejected");
    }
  }

  SubmitResult submit(OpKind kind, const OrderRequest& request) {
    Op op;
    op.kind = kind;
    op.request = request;
    ops_.push_back(op);
    SubmitResult result = engine_.submit(request);
    const Side resting_side = opposite(request.side);
    for (const Trade& trade : result.trades) {
      const OrderId resting = resting_side == Side::Buy ? trade.buy_order_id : trade.sell_order_id;
      if (book().find(resting) == nullptr) {
        forget(resting);  // Fully filled.
      }
    }
    if (result.status == OrderStatus::Resting) {
      remember(result.order_id);
    }
    return result;
  }

  void cancel(OrderId id, OpKind kind = OpKind::Cancel) {
    Op op;
    op.kind = kind;
    op.is_cancel = true;
    op.cancel_id = id;
    ops_.push_back(op);
    const CancelResult result = engine_.cancel(kInstrument, id);
    const bool expected = kind == OpKind::CancelMissing ? !result.cancelled() : result.cancelled();
    if (!expected) {
      throw std::logic_error("workload bug: a cancel did not have the intended outcome");
    }
    forget(id);
  }

  [[nodiscard]] OrderId random_live() {
    if (live_.empty()) {
      throw std::logic_error("workload bug: no resting order to pick");
    }
    return live_[rng_.below(live_.size())];
  }

  // A passive price `offset` ticks away from the touch: below the best ask for a bid, above
  // the best bid for an ask (around the mid when that side is empty), so it never crosses.
  [[nodiscard]] Price passive_price(Side side, Price offset) const {
    if (side == Side::Buy) {
      const auto ask = book().best_ask();
      return (ask ? *ask : kMid + 1) - 1 - offset;
    }
    const auto bid = book().best_bid();
    return (bid ? *bid : kMid - 1) + 1 + offset;
  }

  [[nodiscard]] OrderRequest limit(Side side, Price price, Quantity quantity) {
    OrderRequest request;
    request.instrument = kInstrument;
    request.owner = 1 + rng_.below(1000);
    request.side = side;
    request.type = OrderType::Limit;
    request.time_in_force = TimeInForce::GoodTillCancel;
    request.price = price;
    request.quantity = quantity;
    request.timestamp = ++now_;
    return request;
  }

  [[nodiscard]] OrderRequest market(Side side, Quantity quantity) {
    OrderRequest request = limit(side, 0, quantity);
    request.type = OrderType::Market;
    request.time_in_force = TimeInForce::ImmediateOrCancel;
    return request;
  }

  [[nodiscard]] Workload finish() {
    Workload workload;
    const auto split = ops_.begin() + static_cast<std::ptrdiff_t>(setup_end_);
    workload.setup.assign(ops_.begin(), split);
    workload.measured.assign(split, ops_.end());
    workload.final_sequence = engine_.last_sequence();
    workload.final_order_count = book().order_count();
    workload.start_order_count = start_orders_;
    workload.start_levels_per_side = start_levels_;
    return workload;
  }

 private:
  void remember(OrderId id) {
    slot_.emplace(id, live_.size());
    live_.push_back(id);
  }

  void forget(OrderId id) {
    const auto it = slot_.find(id);
    if (it == slot_.end()) {
      return;
    }
    const std::size_t index = it->second;
    slot_.erase(it);
    if (index + 1 != live_.size()) {
      live_[index] = live_.back();
      slot_[live_[index]] = index;
    }
    live_.pop_back();
  }

  MatchingEngine engine_;
  SplitMix64 rng_;
  std::vector<Op> ops_;
  std::size_t setup_end_ = 0;
  std::size_t start_orders_ = 0;
  std::size_t start_levels_ = 0;
  std::vector<OrderId> live_;
  std::unordered_map<OrderId, std::size_t> slot_;
  Timestamp now_ = 0;
};

Side random_side(SplitMix64& rng) { return rng.chance(1, 2) ? Side::Buy : Side::Sell; }

// A resting order at a uniformly chosen one of `levels` prices on a fixed grid next to the mid:
// bids at mid - 1 - i, asks at mid + 1 + i. Without takers the grid never crosses.
void add_on_grid(Planner& planner, std::size_t levels, Side side) {
  const auto level = static_cast<Price>(planner.rng().below(levels));
  const Price price = side == Side::Buy ? kMid - 1 - level : kMid + 1 + level;
  planner.add(side, price, planner.rng().between(1, 100));
}

// `orders` resting orders, alternately bids and asks, spread over `levels` prices per side.
void populate(Planner& planner, std::size_t orders, std::size_t levels) {
  for (std::size_t i = 0; i < orders; ++i) {
    add_on_grid(planner, levels, i % 2 == 0 ? Side::Buy : Side::Sell);
  }
}

// Steady state: alternately a new resting order on the grid and a cancel of a random resting
// order, so the book keeps its size.
Workload add_cancel_steady(std::size_t orders, std::size_t levels, std::size_t ops) {
  Planner planner(kSeed);
  populate(planner, orders, levels);
  planner.end_setup();
  for (std::size_t i = 0; i < ops; ++i) {
    if (i % 2 == 0) {
      add_on_grid(planner, levels, random_side(planner.rng()));
    } else {
      planner.cancel(planner.random_live());
    }
  }
  return planner.finish();
}

Workload passive_adds(std::size_t ops) {
  Planner planner(kSeed);
  populate(planner, 10'000, 100);
  planner.end_setup();
  for (std::size_t i = 0; i < ops; ++i) {
    add_on_grid(planner, 100, random_side(planner.rng()));
  }
  return planner.finish();
}

// Pairs of a maker joining the best price and a taker (IOC) consuming the same quantity there:
// every taker trades against the front of the best level, and the book keeps its shape.
Workload maker_taker(std::size_t ops) {
  Planner planner(kSeed);
  populate(planner, 10'000, 100);
  planner.end_setup();
  for (std::size_t i = 0; i + 1 < ops; i += 2) {
    const Side maker = random_side(planner.rng());
    const auto best = planner.book().best_price(maker);
    const Price price = best ? *best : planner.passive_price(maker, 0);
    const Quantity quantity = planner.rng().between(1, 100);
    planner.add(maker, price, quantity);
    OrderRequest taker = planner.limit(opposite(maker), price, quantity);
    taker.time_in_force = TimeInForce::ImmediateOrCancel;
    planner.submit(OpKind::Take, taker);
  }
  return planner.finish();
}

// A market order that empties exactly the best `depth` levels (10 orders of 10 per level),
// then refills them, alternating sides.
Workload market_sweeps(std::size_t depth, std::size_t sweeps) {
  constexpr std::size_t kLevels = 100;
  constexpr std::size_t kOrdersPerLevel = 10;
  constexpr Quantity kQuantity = 10;
  Planner planner(kSeed);
  for (std::size_t level = 0; level < kLevels; ++level) {
    for (std::size_t order = 0; order < kOrdersPerLevel; ++order) {
      const auto offset = static_cast<Price>(level);
      planner.add(Side::Buy, kMid - 1 - offset, kQuantity);
      planner.add(Side::Sell, kMid + 1 + offset, kQuantity);
    }
  }
  planner.end_setup();
  for (std::size_t sweep = 0; sweep < sweeps; ++sweep) {
    const Side taker = sweep % 2 == 0 ? Side::Buy : Side::Sell;
    const Side resting = opposite(taker);
    std::vector<Price> prices;
    for (const auto& [price, level] : planner.book().side(resting)) {
      if (prices.size() == depth) {
        break;
      }
      prices.push_back(price);
    }
    const auto quantity = static_cast<Quantity>(depth * kOrdersPerLevel) * kQuantity;
    planner.submit(OpKind::Sweep, planner.market(taker, quantity));
    for (const Price price : prices) {
      for (std::size_t order = 0; order < kOrdersPerLevel; ++order) {
        planner.add(resting, price, kQuantity);
      }
    }
  }
  return planner.finish();
}

// A message mix like a busy order book's: about half new orders near the touch, a third
// cancels, and the rest marketable IOC orders (through up to 2 ticks) and small market orders.
// The book is held near 10,000 resting orders.
Workload mixed_flow(std::size_t ops) {
  constexpr std::size_t kTarget = 10'000;
  Planner planner(kSeed);
  populate(planner, kTarget, 100);
  planner.end_setup();
  for (std::size_t i = 0; i < ops; ++i) {
    std::uint64_t roll = planner.rng().below(100);
    if (planner.live_count() < kTarget * 9 / 10) {
      roll = 0;
    } else if (planner.live_count() > kTarget * 11 / 10) {
      roll = 50;
    }
    SplitMix64& rng = planner.rng();
    const Side side = random_side(rng);
    if (roll < 50) {
      // Offsets skewed towards the touch: most new orders join or improve the near levels.
      const auto offset = static_cast<Price>(rng.below(rng.below(100) + 1));
      planner.add(side, planner.passive_price(side, offset), rng.between(1, 100));
    } else if (roll < 85) {
      planner.cancel(planner.random_live());
    } else if (roll < 95) {
      const auto best = planner.book().best_price(opposite(side));
      if (!best) {
        planner.add(side, planner.passive_price(side, 0), rng.between(1, 100));
        continue;
      }
      const Price through = rng.between(0, 2);
      OrderRequest taker =
          planner.limit(side, side == Side::Buy ? *best + through : *best - through,
                        rng.between(1, 200));
      taker.time_in_force = TimeInForce::ImmediateOrCancel;
      planner.submit(OpKind::Take, taker);
    } else {
      planner.submit(OpKind::Market, planner.market(side, rng.between(1, 50)));
    }
  }
  return planner.finish();
}

enum class CancelOrder : std::uint8_t { Random, OldestFirst, NewestFirst };

// Every resting order of a book of `orders`, cancelled in the given order.
Workload cancel_all(std::size_t orders, CancelOrder order) {
  Planner planner(kSeed);
  populate(planner, orders, 1000);
  planner.end_setup();
  std::vector<OrderId> ids = planner.live();  // Placement order: oldest first.
  if (order == CancelOrder::NewestFirst) {
    std::reverse(ids.begin(), ids.end());
  } else if (order == CancelOrder::Random) {
    for (std::size_t i = ids.size(); i > 1; --i) {
      std::swap(ids[i - 1], ids[planner.rng().below(i)]);
    }
  }
  for (const OrderId id : ids) {
    planner.cancel(id);
  }
  return planner.finish();
}

Workload cancel_missing(std::size_t ops) {
  Planner planner(kSeed);
  populate(planner, 10'000, 100);
  planner.end_setup();
  constexpr OrderId kNeverAssigned = 1'000'000'000'000ULL;
  for (std::size_t i = 0; i < ops; ++i) {
    planner.cancel(kNeverAssigned + i, OpKind::CancelMissing);
  }
  return planner.finish();
}

// A market maker requoting: each step cancels its oldest quote (once it has 20) and places a
// new one within 4 ticks of the touch.
Workload requote(std::size_t ops) {
  Planner planner(kSeed);
  populate(planner, 10'000, 100);
  planner.end_setup();
  std::deque<OrderId> quotes;
  std::size_t done = 0;
  while (done < ops) {
    if (quotes.size() >= 20) {
      const OrderId oldest = quotes.front();
      quotes.pop_front();
      if (planner.book().find(oldest) != nullptr) {
        planner.cancel(oldest);
        ++done;
      }
    }
    const Side side = random_side(planner.rng());
    const auto offset = static_cast<Price>(planner.rng().below(5));
    planner.add(side, planner.passive_price(side, offset), planner.rng().between(1, 100));
    quotes.push_back(planner.live().back());
    ++done;
  }
  return planner.finish();
}

std::string count_label(std::size_t value) {
  if (value >= 1'000'000 && value % 1'000'000 == 0) {
    return std::to_string(value / 1'000'000) + "M";
  }
  if (value >= 1'000 && value % 1'000 == 0) {
    return std::to_string(value / 1'000) + "k";
  }
  return std::to_string(value);
}

}  // namespace

std::vector<OrderRequest> resting_orders(std::size_t orders, std::size_t levels) {
  // The same requests as populate(), drawn in the same order from the same generator: the
  // level, the quantity, then (in Planner::limit) the owner.
  SplitMix64 rng(kSeed);
  std::vector<OrderRequest> requests;
  requests.reserve(orders);
  Timestamp now = 0;
  for (std::size_t i = 0; i < orders; ++i) {
    const Side side = i % 2 == 0 ? Side::Buy : Side::Sell;
    const auto level = static_cast<Price>(rng.below(levels));
    const Quantity quantity = rng.between(1, 100);
    OrderRequest request;
    request.instrument = kInstrument;
    request.owner = 1 + rng.below(1000);
    request.side = side;
    request.price = side == Side::Buy ? kMid - 1 - level : kMid + 1 + level;
    request.quantity = quantity;
    request.timestamp = ++now;
    requests.push_back(request);
  }
  return requests;
}

std::vector<WorkloadSpec> workload_specs(bool quick) {
  const std::size_t steady_ops = quick ? 20'000 : 400'000;
  const std::size_t mixed_ops = quick ? 50'000 : 2'000'000;
  const std::size_t sweeps = quick ? 200 : 2'000;
  const std::vector<std::size_t> book_sizes =
      quick ? std::vector<std::size_t>{1'000, 10'000}
            : std::vector<std::size_t>{1'000, 10'000, 100'000, 1'000'000};
  const std::size_t level_book = quick ? 10'000 : 100'000;
  const std::vector<std::size_t> level_counts =
      quick ? std::vector<std::size_t>{1, 100, 1'000}
            : std::vector<std::size_t>{1, 10, 100, 1'000, 10'000};
  const std::vector<std::size_t> cancel_sizes =
      quick ? std::vector<std::size_t>{10'000} : std::vector<std::size_t>{100'000, 1'000'000};

  std::vector<WorkloadSpec> specs;
  for (const std::size_t orders : book_sizes) {
    specs.push_back({"book_size/" + count_label(orders), "book_size",
                     "Add and cancel alternately on a book of " + count_label(orders) +
                         " orders over 1,000 levels per side",
                     false, [=] { return add_cancel_steady(orders, 1'000, steady_ops); }});
  }
  for (const std::size_t levels : level_counts) {
    specs.push_back({"price_levels/" + count_label(levels), "price_levels",
                     "Add and cancel alternately on a book of " + count_label(level_book) +
                         " orders over " + count_label(levels) + " levels per side",
                     false, [=] { return add_cancel_steady(level_book, levels, steady_ops); }});
  }
  specs.push_back({"patterns/passive", "patterns",
                   "New resting orders only, on a book of 10k orders over 100 levels per side",
                   false, [=] { return passive_adds(steady_ops); }});
  specs.push_back({"patterns/maker_taker", "patterns",
                   "A maker joins the best price, then an IOC taker trades the same quantity",
                   false, [=] { return maker_taker(steady_ops); }});
  for (const std::size_t depth : {std::size_t{1}, std::size_t{10}, std::size_t{50}}) {
    specs.push_back({"patterns/sweep_" + std::to_string(depth), "patterns",
                     "A market order empties the best " + std::to_string(depth) +
                         " levels (10 orders each), then they are refilled",
                     false, [=] { return market_sweeps(depth, sweeps); }});
  }
  specs.push_back({"patterns/mixed", "patterns",
                   "50% adds near the touch, 35% cancels, 10% IOC takers, 5% market orders; "
                   "about 10k resting orders",
                   false, [=] { return mixed_flow(mixed_ops); }});
  for (const std::size_t orders : cancel_sizes) {
    const std::string label = count_label(orders);
    specs.push_back({"cancel/random/" + label, "cancellation",
                     "Cancel all " + label + " resting orders in random order", false,
                     [=] { return cancel_all(orders, CancelOrder::Random); }});
    specs.push_back({"cancel/oldest_first/" + label, "cancellation",
                     "Cancel all " + label + " resting orders, oldest first", false,
                     [=] { return cancel_all(orders, CancelOrder::OldestFirst); }});
    specs.push_back({"cancel/newest_first/" + label, "cancellation",
                     "Cancel all " + label + " resting orders, newest first", false,
                     [=] { return cancel_all(orders, CancelOrder::NewestFirst); }});
  }
  specs.push_back({"cancel/missing", "cancellation",
                   "Cancels of orders that are not on the book (already gone)", false,
                   [=] { return cancel_missing(steady_ops); }});
  specs.push_back({"cancel/requote", "cancellation",
                   "A market maker cancels its oldest quote and requotes near the touch", false,
                   [=] { return requote(steady_ops); }});
  specs.push_back({"exchange/mixed", "exchange",
                   "patterns/mixed through Exchange: matching plus market data (statistics, "
                   "trade ring)",
                   true, [=] { return mixed_flow(mixed_ops); }});
  return specs;
}

}  // namespace udeshi::bench
