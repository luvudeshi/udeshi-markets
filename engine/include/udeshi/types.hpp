#pragma once

// Core vocabulary types shared by every engine component.
//
// Prices are integral tick counts, never floating point: the matching engine must compare
// prices exactly, and binary floating point cannot represent most decimal prices. Conversion
// to human-readable currency units happens at the Exchange boundary using each instrument's
// tick size.

#include <cstdint>
#include <limits>
#include <string_view>

namespace udeshi {

using Price = std::int64_t;          // Price in integer ticks.
using Quantity = std::int64_t;       // Signed so that differences never silently wrap.
using OrderId = std::uint64_t;       // Engine-assigned, unique across all instruments.
using TradeId = std::uint64_t;       // Engine-assigned, unique across all instruments.
using TraderId = std::uint64_t;      // Identifies the owner (agent / user) of an order.
using InstrumentId = std::uint32_t;  // Dense index assigned at instrument registration.
using Sequence = std::uint64_t;      // Global, strictly increasing event sequence number.
using Timestamp = std::int64_t;      // Simulated time in nanoseconds (not wall-clock time).

inline constexpr OrderId kInvalidOrderId = 0;
inline constexpr TraderId kAnonymousTrader = 0;

// Hard bounds that keep every intermediate value (level totals, notional = price * quantity,
// cumulative volume) far away from int64 overflow. 1e9 * 1e9 = 1e18 < 9.22e18.
inline constexpr Quantity kMaxOrderQuantity = 1'000'000'000;
inline constexpr Price kMaxPrice = 1'000'000'000;

enum class Side : std::uint8_t { Buy, Sell };

enum class OrderType : std::uint8_t {
  Limit,   // Executes at the limit price or better; any remainder handled per TimeInForce.
  Market,  // Executes against the best available prices; any remainder is always cancelled
           // (TimeInForce does not apply to market orders).
};

enum class TimeInForce : std::uint8_t {
  GoodTillCancel,     // Remainder rests on the book (limit orders only).
  ImmediateOrCancel,  // Remainder is cancelled after matching.
};

// What happens when an incoming order would trade against a resting order of the same owner.
enum class SelfTradePrevention : std::uint8_t {
  Allow,           // Self-trades execute normally.
  CancelResting,   // Cancel the resting order and continue matching.
  CancelIncoming,  // Stop matching and cancel the incoming order's remaining quantity.
};

// Final state of an incoming order once the engine has finished processing it.
enum class OrderStatus : std::uint8_t {
  Rejected,         // Failed validation; nothing happened.
  Resting,          // Some (possibly all) quantity rests on the book; filled may be > 0.
  Filled,           // Fully executed.
  Cancelled,        // Remainder cancelled (market / IOC / self-trade prevention); filled may be > 0.
};

enum class RejectReason : std::uint8_t {
  None,
  UnknownInstrument,
  InvalidQuantity,
  InvalidPrice,
  MarketOrderWithPrice,
};

enum class CancelReason : std::uint8_t {
  None,
  UserRequested,
  NoLiquidity,              // Market / IOC remainder that could not be matched.
  SelfTradePrevention,
};

enum class CancelOutcome : std::uint8_t {
  Cancelled,
  UnknownInstrument,
  OrderNotFound,  // Never existed, already fully filled, or already cancelled.
};

[[nodiscard]] constexpr Side opposite(Side side) noexcept {
  return side == Side::Buy ? Side::Sell : Side::Buy;
}

[[nodiscard]] std::string_view to_string(Side value) noexcept;
[[nodiscard]] std::string_view to_string(OrderType value) noexcept;
[[nodiscard]] std::string_view to_string(TimeInForce value) noexcept;
[[nodiscard]] std::string_view to_string(SelfTradePrevention value) noexcept;
[[nodiscard]] std::string_view to_string(OrderStatus value) noexcept;
[[nodiscard]] std::string_view to_string(RejectReason value) noexcept;
[[nodiscard]] std::string_view to_string(CancelReason value) noexcept;
[[nodiscard]] std::string_view to_string(CancelOutcome value) noexcept;

}  // namespace udeshi
