#include "udeshi/types.hpp"

namespace udeshi {

std::string_view to_string(Side value) noexcept {
  switch (value) {
    case Side::Buy: return "Buy";
    case Side::Sell: return "Sell";
  }
  return "Unknown";
}

std::string_view to_string(OrderType value) noexcept {
  switch (value) {
    case OrderType::Limit: return "Limit";
    case OrderType::Market: return "Market";
  }
  return "Unknown";
}

std::string_view to_string(TimeInForce value) noexcept {
  switch (value) {
    case TimeInForce::GoodTillCancel: return "GoodTillCancel";
    case TimeInForce::ImmediateOrCancel: return "ImmediateOrCancel";
  }
  return "Unknown";
}

std::string_view to_string(SelfTradePrevention value) noexcept {
  switch (value) {
    case SelfTradePrevention::Allow: return "Allow";
    case SelfTradePrevention::CancelResting: return "CancelResting";
    case SelfTradePrevention::CancelIncoming: return "CancelIncoming";
  }
  return "Unknown";
}

std::string_view to_string(OrderStatus value) noexcept {
  switch (value) {
    case OrderStatus::Rejected: return "Rejected";
    case OrderStatus::Resting: return "Resting";
    case OrderStatus::Filled: return "Filled";
    case OrderStatus::Cancelled: return "Cancelled";
  }
  return "Unknown";
}

std::string_view to_string(RejectReason value) noexcept {
  switch (value) {
    case RejectReason::None: return "None";
    case RejectReason::UnknownInstrument: return "UnknownInstrument";
    case RejectReason::InvalidSide: return "InvalidSide";
    case RejectReason::InvalidOrderType: return "InvalidOrderType";
    case RejectReason::InvalidTimeInForce: return "InvalidTimeInForce";
    case RejectReason::InvalidSelfTradePrevention: return "InvalidSelfTradePrevention";
    case RejectReason::InvalidQuantity: return "InvalidQuantity";
    case RejectReason::InvalidPrice: return "InvalidPrice";
    case RejectReason::MarketOrderWithPrice: return "MarketOrderWithPrice";
  }
  return "Unknown";
}

std::string_view to_string(CancelReason value) noexcept {
  switch (value) {
    case CancelReason::None: return "None";
    case CancelReason::UserRequested: return "UserRequested";
    case CancelReason::NoLiquidity: return "NoLiquidity";
    case CancelReason::SelfTradePrevention: return "SelfTradePrevention";
  }
  return "Unknown";
}

std::string_view to_string(CancelOutcome value) noexcept {
  switch (value) {
    case CancelOutcome::Cancelled: return "Cancelled";
    case CancelOutcome::UnknownInstrument: return "UnknownInstrument";
    case CancelOutcome::OrderNotFound: return "OrderNotFound";
  }
  return "Unknown";
}

}  // namespace udeshi
