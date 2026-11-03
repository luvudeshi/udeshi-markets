#!/usr/bin/env python3
"""Mutation testing for the matching engine.

Each mutant is a small, realistic bug (an off-by-one price comparison, LIFO instead of FIFO,
a skipped sequence number, a missing rollback, ...) expressed as one or more exact text
replacements. For every mutant the script applies the change to a scratch copy of the
repository, rebuilds with ASan/UBSan, and runs the GoogleTest suite. A mutant is *killed* if the
build fails, any test fails, or a sanitizer fires. A surviving mutant means the test suite
cannot tell the buggy engine from the real one, i.e. a missing test.

Usage: tools/mutation_test.py [--jobs N] [--work-dir DIR] [--only SUBSTRING ...]
Exit status is non-zero if any mutant survives, no longer applies, or fails to compile for a
reason other than a failing static_assert (compile-time tests count as kills).
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass

REPO = pathlib.Path(__file__).resolve().parent.parent
ME = "engine/src/matching_engine.cpp"
MEH = "engine/include/udeshi/matching_engine.hpp"
OB = "engine/src/order_book.cpp"
OBH = "engine/include/udeshi/order_book.hpp"
PL = "engine/include/udeshi/price_level.hpp"
POOL = "engine/include/udeshi/order_pool.hpp"
EX = "engine/src/exchange.cpp"
TY = "engine/include/udeshi/types.hpp"

ORDER = "engine/include/udeshi/order.hpp"
EXH = "engine/include/udeshi/exchange.hpp"

# Both test executables; the fault-injection tests live in their own binary because they
# replace the global operator new/delete.
TEST_BINARIES = (
    "build/asan/engine/tests/udeshi_engine_tests",
    "build/asan/engine/tests/udeshi_fault_injection_tests",
)


@dataclass(frozen=True)
class Mutant:
    name: str
    edits: tuple[tuple[str, str, str], ...]  # (file, old text, new text)


def m(name: str, *edits: tuple[str, str, str]) -> Mutant:
    return Mutant(name, tuple(edits))


MUTANTS = [
    # ---- Price-time priority and execution ---------------------------------------------------
    m("buy cannot hit equal ask", (ME, "level_price <= limit_price", "level_price < limit_price")),
    m("sell cannot hit equal bid", (ME, "level_price >= limit_price", "level_price > limit_price")),
    m("trade at aggressor price", (ME, "trade.price = resting.price;",
      "trade.price = request.type == OrderType::Market ? resting.price : request.price;")),
    m("bids sorted ascending", (OBH, "return side == Side::Buy ? lhs > rhs : lhs < rhs;", "return lhs < rhs;")),
    m("LIFO within level", (ME, "Order& resting = *level->front();", "Order& resting = *level->back();")),
    m("FIFO keyed on timestamp", (PL,
      "    order->links_.prev = tail_;\n    order->links_.next = nullptr;\n    order->links_.level = this;\n"
      "    if (tail_ != nullptr) {\n      tail_->links_.next = order;\n    } else {\n      head_ = order;\n    }\n"
      "    tail_ = order;\n",
      "    order->links_.level = this;\n    Order* after = tail_;\n"
      "    while (after != nullptr && after->timestamp > order->timestamp) after = after->links_.prev;\n"
      "    order->links_.prev = after;\n"
      "    order->links_.next = after != nullptr ? after->links_.next : head_;\n"
      "    if (order->links_.next != nullptr) order->links_.next->links_.prev = order; else tail_ = order;\n"
      "    if (after != nullptr) after->links_.next = order; else head_ = order;\n")),
    m("level-exhausted not tracked after fill", (ME,
      "level_exhausted = level_exhausted && fill == resting.remaining_quantity;", "level_exhausted = false;")),
    m("resting order overfilled", (ME,
      "const Quantity fill = std::min(remaining, resting.remaining_quantity);", "const Quantity fill = remaining;")),
    m("resting order read after release",
      (ME, "      const Quantity fill = std::min(remaining, resting.remaining_quantity);\n      Trade trade;",
           "      const Quantity fill = std::min(remaining, resting.remaining_quantity);\n"
           "      level_exhausted = level_exhausted && fill == resting.remaining_quantity;\n"
           "      book.execute(resting, fill);\n      Trade trade;"),
      (ME, "      level_exhausted = level_exhausted && fill == resting.remaining_quantity;\n"
           "      book.execute(resting, fill);\n    }",
           "    }")),
    m("level total not reduced on exhausting fill", (PL,
      "    order->remaining_quantity -= quantity;\n    total_quantity_ -= quantity;",
      "    order->remaining_quantity -= quantity;\n    total_quantity_ -= order->remaining_quantity > 0 ? quantity : 0;")),
    m("empty level not erased", (OB, "  if (level->empty()) {\n    mutable_side(order_side).erase(price);",
      "  if (false) {\n    mutable_side(order_side).erase(price);")),
    # ---- Remainder handling and reporting ----------------------------------------------------
    m("IOC rests like GTC", (ME, "request.time_in_force == TimeInForce::GoodTillCancel) {", "true) {")),
    m("IOC remainder reports no cancel reason", (ME,
      "    result.cancel_reason = CancelReason::NoLiquidity;\n", "")),
    m("market STP stop reported as NoLiquidity", (ME,
      "} else if (result.cancel_reason == CancelReason::SelfTradePrevention) {",
      "} else if (result.cancel_reason == CancelReason::SelfTradePrevention &&\n"
      "             request.type == OrderType::Limit &&\n"
      "             request.time_in_force == TimeInForce::GoodTillCancel) {")),
    m("cancel reports original qty", (ME,
      "result.cancelled_quantity = order->remaining_quantity;", "result.cancelled_quantity = order->original_quantity;")),
    m("cancel reports wrong side", (ME, "result.side = order->side;", "result.side = Side::Buy;")),
    m("resting timestamp dropped", (ME, "resting.timestamp = request.timestamp;", "resting.timestamp = 0;")),
    m("trade instrument not set", (ME, "trade.instrument = request.instrument;", "trade.instrument = 0;")),
    # ---- Self-trade prevention ---------------------------------------------------------------
    m("STP ignores anonymous exemption", (ME,
      "return request.owner != kAnonymousTrader && resting.owner == request.owner;", "return resting.owner == request.owner;")),
    m("CancelIncoming behaves as CancelResting", (ME,
      "if (request.self_trade_prevention == SelfTradePrevention::CancelIncoming) {", "if (false) {")),
    m("STP cancel reports original qty", (ME,
      "resting.price,\n            resting.remaining_quantity, CancelReason::SelfTradePrevention",
      "resting.price,\n            resting.original_quantity, CancelReason::SelfTradePrevention")),
    m("STP cancel reports incoming price", (ME,
      "resting.id, next_sequence_++, resting.owner, resting.side, resting.price,",
      "resting.id, next_sequence_++, resting.owner, resting.side, request.price,")),
    # ---- Sequencing and identifiers ----------------------------------------------------------
    m("trades consume two sequence numbers", (ME,
      "trade.sequence = next_sequence_++;", "trade.sequence = next_sequence_++;\n      ++next_sequence_;")),
    m("sequence depends on order size", (ME,
      "  result.sequence = next_sequence_++;\n  result.requested_quantity",
      "  result.sequence = next_sequence_++;\n  if (request.quantity > 50) ++next_sequence_;\n  result.requested_quantity")),
    m("STP cancel consumes two sequence numbers", (ME,
      "resting.id, next_sequence_++, resting.owner", "resting.id, (++next_sequence_)++, resting.owner")),
    m("reject consumes extra sequence number", (ME,
      "    result.status = OrderStatus::Rejected;\n    return result;",
      "    result.status = OrderStatus::Rejected;\n    ++next_sequence_;\n    return result;")),
    m("trade ids not dense", (ME, "trade.id = next_trade_id_++;", "trade.id = next_trade_id_;\n      next_trade_id_ += 2;")),
    m("rejects consume order ids", (ME, "  result.order_id = next_order_id_++;\n",
      "  result.order_id = next_order_id_; next_order_id_ += 2;\n")),
    m("order ids not global across instruments", (ME, "  result.order_id = next_order_id_++;\n",
      "  result.order_id = request.instrument == 0 ? next_order_id_++ : 1'000'000 + next_order_id_++;\n")),
    m("resting sequence not recorded", (ME, "resting.sequence = result.sequence;", "resting.sequence = 0;")),
    m("resting sequence taken from last event", (ME, "resting.sequence = result.sequence;",
      "resting.sequence = next_sequence_ - 1;")),
    # ---- Validation ----------------------------------------------------------------------------
    m("zero quantity accepted", (ME, "request.quantity <= 0 ||", "request.quantity < 0 ||")),
    m("kMaxPrice rejected", (ME, "request.price > kMaxPrice)", "request.price >= kMaxPrice)")),
    m("market price check skipped", (ME, "if (request.price != 0) {", "if (false) {")),
    m("negative market price accepted", (ME, "if (request.price != 0) {", "if (request.price > 0) {")),
    m("market price checked before quantity", (ME,
      "  if (request.quantity <= 0 || request.quantity > kMaxOrderQuantity) {",
      "  if (request.type == OrderType::Market && request.price != 0) {\n"
      "    return RejectReason::MarketOrderWithPrice;\n  }\n"
      "  if (request.quantity <= 0 || request.quantity > kMaxOrderQuantity) {")),
    m("side not validated", (ME, "  if (!is_valid(request.side)) {\n    return RejectReason::InvalidSide;\n  }\n", "")),
    m("STP mode not validated", (ME,
      "  if (!is_valid(request.self_trade_prevention)) {\n    return RejectReason::InvalidSelfTradePrevention;\n  }\n", "")),
    m("book insert accepts invalid side", (OB,
      "if (!is_valid(order.side) || order.price <= 0 || order.price > kMaxPrice) {", "if (false) {")),
    m("book insert accepts other instrument", (OB, "if (order.instrument != instrument_) {", "if (false) {")),
    m("book insert accepts out-of-order sequence", (OB,
      "if (!level.empty() && level.back()->sequence >= order.sequence) {", "if (false) {")),
    m("order copies keep their links", (ORDER,
      "    Links(const Links& /*other*/) noexcept {}",
      "    Links(const Links& other) noexcept : prev(other.prev), next(other.next), level(other.level) {}")),
    # ---- Failure handling and memory safety ----------------------------------------------------
    m("submit not fail-fast",
      (ME, "SubmitResult MatchingEngine::submit(const OrderRequest& request) noexcept {",
           "SubmitResult MatchingEngine::submit(const OrderRequest& request) {"),
      (MEH, "SubmitResult submit(const OrderRequest& request) noexcept;", "SubmitResult submit(const OrderRequest& request);")),
    m("Exchange submit not fail-fast",
      (EX, "SubmitResult Exchange::submit(const OrderRequest& request) noexcept {",
           "SubmitResult Exchange::submit(const OrderRequest& request) {"),
      (EXH, "SubmitResult submit(const OrderRequest& request) noexcept;", "SubmitResult submit(const OrderRequest& request);")),
    m("pool chunks freed with scalar delete",
      (POOL, "std::vector<std::unique_ptr<Order[]>> chunks_;", "std::vector<std::unique_ptr<Order>> chunks_;"),
      (POOL, "auto chunk = std::make_unique<Order[]>(kChunkSize);",
             "auto chunk = std::unique_ptr<Order>(new Order[kChunkSize]);")),
    m("add_instrument skips symbol rollback", (EX,
      "      symbol_index_.erase(symbol_slot);\n", "      static_cast<void>(symbol_slot);\n")),
    m("released pool nodes not poisoned", (POOL, "    UDESHI_POISON(order, sizeof(Order));\n", "")),
    m("const level exposes mutable order", (PL,
      "[[nodiscard]] const Order* front() const noexcept { return head_; }",
      "[[nodiscard]] Order* front() const noexcept { return head_; }")),
    # ---- Exchange market data and conversions --------------------------------------------------
    m("high tracks min", (EX, "stats.high_price = std::max(", "stats.high_price = std::min(")),
    m("ring buffer ignores wrap position", (EX, "ring.trades[(ring.oldest + i) % stored]", "ring.trades[i]")),
    m("recent trades never wrap", (EX, "    ring.oldest = (ring.oldest + 1) % recent_trade_capacity_;\n", "")),
    m("tick tolerance relative to magnitude", (EX,
      "constexpr double kTickGridRelativeTolerance = 16.0 * std::numeric_limits<double>::epsilon();",
      "constexpr double kTickGridRelativeTolerance = 1e-6;")),
    m("negative prices converted", (EX, "if (currency_price < 0.0) {", "if (false) {")),
    m("infinite and subnormal tick sizes accepted", (EX,
      "if (!(spec.tick_size >= kMinTickSize && spec.tick_size <= kMaxTickSize)) {",
      "if (std::isnan(spec.tick_size) || spec.tick_size <= 0.0) {")),
    m("symbol length bound off by one", (EX, "symbol.size() > kMaxSymbolLength", "symbol.size() >= kMaxSymbolLength")),
    m("reference price bound off by one", (EX,
      "spec.reference_price > kMaxPrice)", "spec.reference_price >= kMaxPrice)")),
    m("volume not saturated at call site", (EX,
      "const Quantity volume = saturating_add(stats.volume, trade.quantity);",
      "const Quantity volume = stats.volume + trade.quantity;")),
    m("VWAP ignores volume saturation", (EX, "  if (stats.volume_saturated) {", "  if (false) {")),
    m("symbols may start with punctuation", (EX, "      !is_ascii_upper_or_digit(symbol.front())) {", "      false) {")),
    m("symbol check uses locale",
      (EX, "[[nodiscard]] constexpr bool is_ascii_upper_or_digit(char c) noexcept {\n"
           "  return (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9');",
           "[[nodiscard]] bool is_ascii_upper_or_digit(char c) noexcept {\n"
           "  return std::isupper(static_cast<unsigned char>(c)) != 0 ||\n"
           "         std::isdigit(static_cast<unsigned char>(c)) != 0;"),
      (EX, "#include <algorithm>\n", "#include <algorithm>\n#include <cctype>\n")),
    m("volume overflows instead of saturating", (TY,
      "return total > kMax - increment ? kMax : total + increment;", "return total + increment;")),
]


def sanitizer_environment() -> dict[str, str]:
    """The environment of the `asan` test preset (e.g. ASAN_OPTIONS), so mutants are judged
    with the same sanitizer checks as `ctest --preset asan`."""
    presets = json.loads((REPO / "CMakePresets.json").read_text())
    preset = next(p for p in presets["testPresets"] if p["name"] == "asan")
    return {**os.environ, **preset.get("environment", {})}


ENVIRONMENT = sanitizer_environment()


def run(cmd: list[str], cwd: pathlib.Path, timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                          env=ENVIRONMENT)


def run_tests(work: pathlib.Path, extra: list[str], timeout: int) -> tuple[int, str]:
    """Runs every test binary, stopping at the first failing one."""
    output = ""
    for binary in TEST_BINARIES:
        result = run([binary, "--gtest_brief=1", *extra], work, timeout=timeout)
        output += result.stdout + result.stderr
        if result.returncode != 0:
            return result.returncode, output
    return 0, output


def prepare(work: pathlib.Path) -> None:
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(REPO, work, ignore=shutil.ignore_patterns(".git", "build", "node_modules"))
    for step in (["cmake", "--preset", "asan"], ["cmake", "--build", "--preset", "asan"]):
        result = run(step, work)
        if result.returncode != 0:
            raise RuntimeError(f"baseline step {step} failed:\n{result.stdout[-3000:]}{result.stderr[-3000:]}")
    code, output = run_tests(work, [], timeout=1800)
    if code != 0:
        raise RuntimeError(f"baseline tests fail in {work}:\n{output[-3000:]}")


def evaluate(work: pathlib.Path, mutant: Mutant) -> tuple[str, str]:
    originals: dict[pathlib.Path, str] = {}
    try:
        for rel, old, new in mutant.edits:
            path = work / rel
            if path not in originals:
                originals[path] = path.read_text()
            text = path.read_text()  # Includes this mutant's earlier edits to the same file.
            if text.count(old) != 1:
                return "STALE", f"pattern in {rel} matched {text.count(old)} times"
            path.write_text(text.replace(old, new))
        build = run(["cmake", "--build", "--preset", "asan"], work)
        if build.returncode != 0:
            # Only a failing compile-time test counts as a kill. Any other compile error (e.g. an
            # unused variable under -Werror) means the mutant itself is malformed.
            if "static assert" in build.stdout + build.stderr:
                return "KILLED", "static_assert"
            return "INVALID", "mutant does not compile: " + next(
                (line.strip() for line in build.stdout.splitlines() if "error:" in line), "?")
        try:
            code, output = run_tests(work, ["--gtest_fail_fast"], timeout=600)
        except subprocess.TimeoutExpired:
            return "KILLED", "timeout"
        if code == 0:
            return "SURVIVED", ""
        failed = [line.split("]", 1)[1].strip().split(" ")[0]
                  for line in output.splitlines() if line.startswith("[  FAILED  ]") and "," not in line]
        sanitizer = "AddressSanitizer" in output or "runtime error:" in output
        detail = ", ".join(sorted(set(failed))[:2]) or ("sanitizer" if sanitizer else f"exit {code}")
        return "KILLED", detail + (" (sanitizer)" if sanitizer and failed else "")
    finally:
        for path, text in originals.items():
            path.write_text(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--jobs", type=int, default=4, help="parallel work copies (default 4)")
    parser.add_argument("--work-dir", type=pathlib.Path,
                        default=pathlib.Path(tempfile.gettempdir()) / "udeshi-mutation")
    parser.add_argument("--only", action="append", default=[],
                        help="run only mutants whose name contains this (repeatable)")
    args = parser.parse_args()

    mutants = [mutant for mutant in MUTANTS
               if not args.only or any(part in mutant.name for part in args.only)]
    jobs = max(1, min(args.jobs, len(mutants)))
    works = [args.work_dir / f"copy{i}" for i in range(jobs)]
    print(f"Preparing {jobs} work copies under {args.work_dir} ...", flush=True)
    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        list(pool.map(prepare, works))

    buckets = [mutants[i::jobs] for i in range(jobs)]
    results: dict[str, tuple[str, str]] = {}

    def worker(index: int) -> None:
        for mutant in buckets[index]:
            outcome = evaluate(works[index], mutant)
            results[mutant.name] = outcome
            print(f"{outcome[0]:<9} {mutant.name}  {outcome[1]}", flush=True)

    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        list(pool.map(worker, range(jobs)))

    survived = [name for name, (status, _) in results.items() if status == "SURVIVED"]
    broken = [name for name, (status, _) in results.items() if status in ("STALE", "INVALID")]
    killed = len(mutants) - len(survived) - len(broken)
    print(f"\n{killed}/{len(mutants)} mutants killed", end="")
    print(f"; survived: {survived}" if survived else "", end="")
    print(f"; stale or invalid mutants: {broken}" if broken else "")
    return 1 if survived or broken else 0


if __name__ == "__main__":
    sys.exit(main())
