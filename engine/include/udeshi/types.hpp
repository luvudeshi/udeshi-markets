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

// Per-order bounds. They guarantee that the notional of any single order or trade
// (price * quantity <= 1e18 < 2^63 ~ 9.22e18) is exactly representable as int64, and that the
// cached quantity of a price level cannot overflow before memory is exhausted (it would take
// more than 9e9 resting orders). They do NOT bound cumulative counters such as total traded
// volume; those are handled explicitly where they are accumulated (see Exchange).
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

// Validation is applied in exactly this order; the first failing check is reported.
enum class RejectReason : std::uint8_t {
  None,
  UnknownInstrument,
  InvalidSide,                 // Enum value outside {Buy, Sell}.
  InvalidOrderType,            // Enum value outside {Limit, Market}.
  InvalidTimeInForce,          // Enum value outside {GoodTillCancel, ImmediateOrCancel}.
  InvalidSelfTradePrevention,  // Enum value outside the declared modes.
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

// Enum fields can carry out-of-range values when they cross a language or serialisation
// boundary (e.g. an integer converted to an enum by Python bindings). These predicates let the
// engine reject such input instead of silently reinterpreting it.
[[nodiscard]] constexpr bool is_valid(Side value) noexcept {
  return value == Side::Buy || value == Side::Sell;
}
[[nodiscard]] constexpr bool is_valid(OrderType value) noexcept {
  return value == OrderType::Limit || value == OrderType::Market;
}
[[nodiscard]] constexpr bool is_valid(TimeInForce value) noexcept {
  return value == TimeInForce::GoodTillCancel || value == TimeInForce::ImmediateOrCancel;
}
[[nodiscard]] constexpr bool is_valid(SelfTradePrevention value) noexcept {
  return value == SelfTradePrevention::Allow || value == SelfTradePrevention::CancelResting ||
         value == SelfTradePrevention::CancelIncoming;
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
