#include "udeshi/matching_engine.hpp"

#include <algorithm>
#include <stdexcept>
#include <string>

namespace udeshi {

namespace {

// True if a resting level at `level_price` is executable for an incoming limit order.
[[nodiscard]] constexpr bool crosses(Side incoming_side, Price limit_price,
                                     Price level_price) noexcept {
  return incoming_side == Side::Buy ? level_price <= limit_price : level_price >= limit_price;
}

[[nodiscard]] constexpr bool is_self_trade(const OrderRequest& request,
                                           const Order& resting) noexcept {
  return request.owner != kAnonymousTrader && resting.owner == request.owner;
}

}  // namespace

InstrumentId MatchingEngine::add_instrument() {
  const auto id = static_cast<InstrumentId>(books_.size());
  books_.push_back(std::make_unique<OrderBook>(id));
  return id;
}

const OrderBook& MatchingEngine::book(InstrumentId instrument) const {
  if (!has_instrument(instrument)) {
    throw std::out_of_range("MatchingEngine::book: unknown instrument " +
                            std::to_string(instrument));
  }
  return *books_[instrument];
}

RejectReason MatchingEngine::validate(const OrderRequest& request) const noexcept {
  if (!has_instrument(request.instrument)) {
    return RejectReason::UnknownInstrument;
  }
  if (request.quantity <= 0 || request.quantity > kMaxOrderQuantity) {
    return RejectReason::InvalidQuantity;
  }
  if (request.type == OrderType::Market) {
    if (request.price != 0) {
      return RejectReason::MarketOrderWithPrice;
    }
  } else if (request.price <= 0 || request.price > kMaxPrice) {
    return RejectReason::InvalidPrice;
  }
  return RejectReason::None;
}

SubmitResult MatchingEngine::submit(const OrderRequest& request) {
  SubmitResult result;
  result.sequence = next_sequence_++;
  result.requested_quantity = request.quantity;
  result.reject_reason = validate(request);
  if (result.reject_reason != RejectReason::None) {
    result.status = OrderStatus::Rejected;
    return result;
  }

  result.order_id = next_order_id_++;
  OrderBook& book = *books_[request.instrument];
  match(book, request, result.order_id, result);

  const Quantity remaining = request.quantity - result.filled_quantity;
  if (remaining == 0) {
    result.status = OrderStatus::Filled;
  } else if (result.cancel_reason == CancelReason::SelfTradePrevention) {
    // The remainder would cross the owner's own resting order, so it can never rest.
    result.status = OrderStatus::Cancelled;
    result.cancelled_quantity = remaining;
  } else if (request.type == OrderType::Limit &&
             request.time_in_force == TimeInForce::GoodTillCancel) {
    Order resting;
    resting.id = result.order_id;
    resting.owner = request.owner;
    resting.instrument = request.instrument;
    resting.side = request.side;
    resting.self_trade_prevention = request.self_trade_prevention;
    resting.price = request.price;
    resting.original_quantity = request.quantity;
    resting.remaining_quantity = remaining;
    resting.sequence = result.sequence;
    resting.timestamp = request.timestamp;
    book.insert(resting);
    result.status = OrderStatus::Resting;
    result.resting_quantity = remaining;
  } else {
    // Market orders and IOC limit orders never rest.
    result.status = OrderStatus::Cancelled;
    result.cancel_reason = CancelReason::NoLiquidity;
    result.cancelled_quantity = remaining;
  }
  return result;
}

// Price-time priority: repeatedly take the best opposing level (begin() of the opposite side)
// while it is marketable, and within it consume orders strictly from the front of the FIFO.
// Every execution happens at the resting order's price, so an aggressive order can only ever
// receive price improvement relative to its limit, never a worse price.
void MatchingEngine::match(OrderBook& book, const OrderRequest& request, OrderId order_id,
                           SubmitResult& result) {
  const Side resting_side = opposite(request.side);
  const bool is_market = request.type == OrderType::Market;
  Quantity remaining = request.quantity;
  bool stop = false;

  while (remaining > 0 && !stop) {
    PriceLevel* level = book.best_level(resting_side);
    if (level == nullptr) {
      break;  // Opposite side is empty.
    }
    if (!is_market && !crosses(request.side, request.price, level->price())) {
      break;  // Best opposing price is worse than our limit; so is every other level.
    }

    // Consume the level front-to-back. `level` is destroyed by the book as soon as its last
    // order is removed, so we detect that case *before* the removal and leave the loop.
    bool level_exhausted = false;
    while (remaining > 0 && !level_exhausted) {
      Order& resting = *level->front();
      level_exhausted = level->order_count() == 1;

      if (request.self_trade_prevention != SelfTradePrevention::Allow &&
          is_self_trade(request, resting)) {
        if (request.self_trade_prevention == SelfTradePrevention::CancelIncoming) {
          result.cancel_reason = CancelReason::SelfTradePrevention;
          stop = true;
          break;
        }
        result.resting_cancels.push_back(RestingCancel{resting.id, resting.owner, resting.side,
                                                       resting.price,
                                                       resting.remaining_quantity,
                                                       CancelReason::SelfTradePrevention});
        book.remove(resting);
        continue;
      }

      const Quantity fill = std::min(remaining, resting.remaining_quantity);
      Trade trade;
      trade.id = next_trade_id_++;
      trade.sequence = next_sequence_++;
      trade.timestamp = request.timestamp;
      trade.instrument = request.instrument;
      trade.price = resting.price;
      trade.quantity = fill;
      trade.aggressor_side = request.side;
      if (request.side == Side::Buy) {
        trade.buy_order_id = order_id;
        trade.buyer = request.owner;
        trade.sell_order_id = resting.id;
        trade.seller = resting.owner;
      } else {
        trade.sell_order_id = order_id;
        trade.seller = request.owner;
        trade.buy_order_id = resting.id;
        trade.buyer = resting.owner;
      }
      result.trades.push_back(trade);
      remaining -= fill;

      // A resting order that is only partially filled keeps its place at the front.
      level_exhausted = level_exhausted && fill == resting.remaining_quantity;
      book.execute(resting, fill);
    }
  }

  result.filled_quantity = request.quantity - remaining;
}

CancelResult MatchingEngine::cancel(InstrumentId instrument, OrderId order_id) {
  CancelResult result;
  result.sequence = next_sequence_++;
  result.order_id = order_id;
  if (!has_instrument(instrument)) {
    result.outcome = CancelOutcome::UnknownInstrument;
    return result;
  }
  OrderBook& book = *books_[instrument];
  Order* order = book.find_mutable(order_id);
  if (order == nullptr) {
    result.outcome = CancelOutcome::OrderNotFound;
    return result;
  }
  result.outcome = CancelOutcome::Cancelled;
  result.owner = order->owner;
  result.side = order->side;
  result.price = order->price;
  result.cancelled_quantity = order->remaining_quantity;
  book.remove(*order);
  return result;
}

}  // namespace udeshi
