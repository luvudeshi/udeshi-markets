#!/usr/bin/env python3
"""Mutation testing for the matching engine.

Copies the repository to a scratch directory, then for each hand-written mutant (a realistic
bug such as an off-by-one price comparison or LIFO instead of FIFO) applies the change,
rebuilds with ASan/UBSan, and runs the GoogleTest suite. A mutant is "killed" if the build
fails, any test fails, or a sanitizer fires. Surviving mutants indicate missing tests.

Usage: tools/mutation_test.py [WORK_DIR]   (default: $TMPDIR/udeshi-mutation)
Exit status is non-zero if any mutant survives.
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
WORK = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(tempfile.gettempdir()) / "udeshi-mutation"
ME = "engine/src/matching_engine.cpp"
OB = "engine/src/order_book.cpp"
OBH = "engine/include/udeshi/order_book.hpp"
EX = "engine/src/exchange.cpp"
MUTANTS = [
    ("buy cannot hit equal ask", ME, "level_price <= limit_price", "level_price < limit_price"),
    ("sell cannot hit equal bid", ME, "level_price >= limit_price", "level_price > limit_price"),
    ("trade at aggressor price", ME, "trade.price = resting.price;", "trade.price = request.type == OrderType::Market ? resting.price : request.price;"),
    ("bids sorted ascending", OBH, "return side == Side::Buy ? lhs > rhs : lhs < rhs;", "return lhs < rhs;"),
    ("LIFO within level", ME, "Order& resting = *level->front();", "Order& resting = *level->back();"),
    ("level-exhausted not tracked after fill", ME, "level_exhausted = level_exhausted && fill == resting.remaining_quantity;", "level_exhausted = false;"),
    ("IOC rests like GTC", ME, "request.time_in_force == TimeInForce::GoodTillCancel) {", "true) {"),
    ("STP ignores anonymous exemption", ME, "return request.owner != kAnonymousTrader && resting.owner == request.owner;", "return resting.owner == request.owner;"),
    ("CancelIncoming behaves as CancelResting", ME, "if (request.self_trade_prevention == SelfTradePrevention::CancelIncoming) {", "if (false) {"),
    ("cancel reports original qty", ME, "result.cancelled_quantity = order->remaining_quantity;", "result.cancelled_quantity = order->original_quantity;"),
    ("empty level not erased", OB, "  if (level->empty()) {\n    mutable_side(order_side).erase(price);", "  if (false) {\n    mutable_side(order_side).erase(price);"),
    ("zero quantity accepted", ME, "request.quantity <= 0 ||", "request.quantity < 0 ||"),
    ("kMaxPrice rejected", ME, "request.price > kMaxPrice)", "request.price >= kMaxPrice)"),
    ("high tracks min", EX, "stats.high_price = std::max(", "stats.high_price = std::min("),
    ("recent trades evict one early", EX, "recent_trades.size() > recent_trade_capacity_", "recent_trades.size() >= recent_trade_capacity_"),
    ("resting order overfilled", ME, "const Quantity fill = std::min(remaining, resting.remaining_quantity);", "const Quantity fill = remaining;"),
    ("resting sequence not recorded", ME, "resting.sequence = result.sequence;", "resting.sequence = 0;"),
    ("rejects consume order ids", ME, "  result.order_id = next_order_id_++;\n", "  result.order_id = next_order_id_; next_order_id_ += 2;\n"),
    ("market price check skipped", ME, "if (request.price != 0) {", "if (false) {"),
    ("partial cancel quantity wrong in STP report", ME, "resting.remaining_quantity,\n                                                       CancelReason::SelfTradePrevention", "resting.original_quantity,\n                                                       CancelReason::SelfTradePrevention"),
]

def run(cmd, cwd, timeout=900):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)

if WORK.exists():
    shutil.rmtree(WORK)
shutil.copytree(REPO, WORK, ignore=shutil.ignore_patterns(".git", "build"))
assert run(["cmake", "--preset", "asan"], WORK).returncode == 0
assert run(["cmake", "--build", "--preset", "asan"], WORK).returncode == 0
baseline = run(["./build/asan/engine/tests/udeshi_engine_tests", "--gtest_brief=1"], WORK)
assert baseline.returncode == 0, baseline.stdout[-2000:]

survivors = 0
for name, rel, old, new in MUTANTS:
    path = WORK / rel
    original = path.read_text()
    count = original.count(old)
    if count != 1:
        print(f"SKIP  {name}: pattern matched {count} times")
        continue
    path.write_text(original.replace(old, new))
    try:
        build = run(["cmake", "--build", "--preset", "asan"], WORK)
        if build.returncode != 0:
            print(f"KILLED(compile) {name}")
            continue
        try:
            test = run(["./build/asan/engine/tests/udeshi_engine_tests", "--gtest_brief=1"], WORK, timeout=300)
            rc, out = test.returncode, test.stdout + test.stderr
        except subprocess.TimeoutExpired:
            rc, out = -1, "TIMEOUT"
        if rc == 0:
            survivors += 1
            print(f"SURVIVED {name}")
        else:
            failed = [l for l in out.splitlines() if l.startswith("[  FAILED  ]")]
            asan = "AddressSanitizer" in out or "runtime error" in out
            print(f"KILLED  {name}  ({len(failed)} failing{', sanitizer' if asan else ''}{', '+out if out=='TIMEOUT' else ''})")
    finally:
        path.write_text(original)
print(f"\n{len(MUTANTS) - survivors}/{len(MUTANTS)} mutants killed")
sys.exit(1 if survivors else 0)
