#!/usr/bin/env python3
"""Turns udeshi_bench's JSON results into the Markdown tables of docs/benchmarking.md.

    tools/bench_report.py RESULTS.json [--baseline BASELINE.json] [--linux LINUX.json]
                          [--update docs/benchmarking.md]

Without --update the tables are printed. With it, the text between the markers
``<!-- bench:NAME:start -->`` and ``<!-- bench:NAME:end -->`` in the given file is replaced, for
NAME in environment, memory, workloads, mixed, comparison (with --baseline) and linux (with
--linux: a second machine's results, summarised).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Any

Result = dict[str, Any]

DASH = "\u2013"  # An en dash, for ranges.
TIMES = "\u00d7"  # A multiplication sign, for speed-ups.

GROUP_TITLES = {
    "book_size": "Book size",
    "price_levels": "Price levels",
    "patterns": "Submission patterns",
    "cancellation": "Cancellation",
    "exchange": "Through the Exchange (matching plus market data)",
}


def millions(value: float) -> str:
    return f"{value / 1e6:.1f}"


def nanoseconds(value: float) -> str:
    return f"{value:,.0f}"


def clock_text(clock: Result) -> str:
    return (
        f"`std::chrono::steady_clock`: ticks of {clock['tick_ns']:.2f} ns; two consecutive "
        f"reads at least {clock['min_read_gap_ns']} ns apart (median {clock['read_gap_p50_ns']} ns)"
    )


def environment(results: Result) -> str:
    env = results["environment"]
    clock = results["clock"]
    config = results["config"]
    sizes = results["sizes"]
    rows = [
        ("CPU", f"{env['cpu']} ({env['logical_cpus']} logical CPUs)"),
        ("Memory", f"{env['memory_bytes'] / 2**30:.0f} GiB"),
        ("OS", env["os"]),
        ("Compiler", env["compiler"]),
        ("Build", env["build"]),
        ("Scheduling", env["scheduling"]),
        ("Clock", clock_text(clock)),
        ("Repetitions", f"{config['repetitions']} timed replays per workload, after a warm-up"),
        (
            "Object sizes",
            f"`Order` {sizes['order_bytes']} B, `PriceLevel` {sizes['price_level_bytes']} B, "
            f"`Trade` {sizes['trade_bytes']} B",
        ),
    ]
    lines = ["| | |", "| --- | --- |"]
    lines += [f"| {name} | {value} |" for name, value in rows]
    return "\n".join(lines)


def memory(results: Result) -> str:
    lines = [
        "| Resting orders | Levels per side | Heap bytes per order | Allocations per order "
        "| RSS growth per order |",
        "| ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in results["memory"]:
        lines.append(
            f"| {row['orders']:,} | {row['levels_per_side']:,} "
            f"| {row['heap_bytes_per_order']:.1f} | {row['allocations_per_order']:.3f} "
            f"| {row['rss_bytes_per_order']:.1f} |"
        )
    return "\n".join(lines)


def workloads(results: Result) -> str:
    out: list[str] = []
    by_group: dict[str, list[Result]] = {}
    for workload in results["workloads"]:
        by_group.setdefault(workload["group"], []).append(workload)
    for group, rows in by_group.items():
        out.append(f"**{GROUP_TITLES.get(group, group)}**\n")
        out.append(
            f"| Workload | Operations | M ops/s (min{DASH}max) | Mean ns | p50 | p95 | p99 | p99.9 "
            "| Max | Allocations per op |"
        )
        out.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for row in rows:
            rate = row["ops_per_second"]
            latency = row["latency"]
            by_kind = row["by_kind"]
            operations = sum(kind["latency"]["count"] for kind in by_kind.values())
            allocations = sum(
                kind["allocations_per_op"] * kind["latency"]["count"] for kind in by_kind.values()
            )
            out.append(
                f"| `{row['name']}` | {row['measured_ops']:,} "
                f"| {millions(rate['median'])} "
                f"({millions(rate['min'])}{DASH}{millions(rate['max'])}) "
                f"| {nanoseconds(1e9 / rate['median'])} | {latency['p50_ns']} "
                f"| {latency['p95_ns']} | {latency['p99_ns']} | {latency['p999_ns']} "
                f"| {latency['max_ns']:,} | {allocations / operations:.2f} |"
            )
        out.append("")
    return "\n".join(out).rstrip()


def by_kind(results: Result, name: str) -> str:
    """Latency per kind of operation, for one workload."""
    workload = next(w for w in results["workloads"] if w["name"] == name)
    lines = [
        "| Operation | Count | Mean ns | p50 | p95 | p99 | p99.9 | Max | Allocations per op |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for kind, data in workload["by_kind"].items():
        latency = data["latency"]
        lines.append(
            f"| {kind} | {latency['count']:,} | {latency['mean_ns']:.1f} | {latency['p50_ns']} "
            f"| {latency['p95_ns']} | {latency['p99_ns']} | {latency['p999_ns']} "
            f"| {latency['max_ns']:,} | {data['allocations_per_op']:.2f} |"
        )
    return "\n".join(lines)


def comparison(results: Result, baseline: Result, intermediate: Result | None) -> str:
    """Before, (after the first change,) and after: throughput, worst case, allocations."""
    before = {w["name"]: w for w in baseline["workloads"]}
    middle = {} if intermediate is None else {w["name"]: w for w in intermediate["workloads"]}
    rate_columns = "Before | Index only | After" if middle else "Before | After"
    alloc_columns = "before → index only → after" if middle else "before → after"
    lines = [
        f"| Workload | M ops/s: {rate_columns} | Speed-up | Max latency before → after "
        f"| Allocations per op, {alloc_columns} |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for row in results["workloads"]:
        old = before.get(row["name"])
        if old is None:
            continue
        mid = middle.get(row["name"])
        rates = [old, *([mid] if mid else []), row]
        rate_text = " → ".join(millions(w["ops_per_second"]["median"]) for w in rates)
        alloc_text = " → ".join(f"{allocations_per_op(w):.2f}" for w in rates)
        speedup = row["ops_per_second"]["median"] / old["ops_per_second"]["median"]
        lines.append(
            f"| `{row['name']}` | {rate_text} | {speedup:.2f}{TIMES} "
            f"| {old['latency']['max_ns']:,} → {row['latency']['max_ns']:,} ns | {alloc_text} |"
        )
    return "\n".join(lines)


def growth(results: Result) -> str:
    """The inserts that double the id index, one timing per process."""
    lines = [
        "| Resting orders after the insert | Entries rehashed | Insert time, median "
        "| Each process |",
        "| ---: | ---: | ---: | ---: |",
    ]
    for point in results["index_growth"]:
        samples = sorted(point["latency_ns"])
        median_ns = samples[len(samples) // 2]
        each = ", ".join(duration(ns) for ns in point["latency_ns"])
        lines.append(
            f"| {point['orders']:,} | {point['orders'] - 1:,} | {duration(median_ns)} | {each} |"
        )
    return "\n".join(lines)


def python_kernel(final: Result) -> str:
    lines = [
        "| | Events/s | Orders/s | Trades/s | Simulated seconds per second |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for row in final["kernel"]:
        label = "With the analytics observer" if row["observed"] else "Headless"
        lines.append(
            f"| {label} | {row['events_per_second']:,.0f} | {row['orders_per_second']:,.0f} "
            f"| {row['trades_per_second']:,.0f} | {row['simulated_seconds_per_second']:,.0f} |"
        )
    return "\n".join(lines)


def python_live(before: Result, after: Result) -> str:
    lines = [
        "| Run length | Analytics endpoint | Sharpe, all participants | Leaderboard |",
        "| --- | ---: | ---: | ---: |",
    ]
    for old, new in zip(before["live"], after["live"], strict=True):
        lines.append(
            f"| {old['simulated_minutes']} min ({old['samples']:,} samples, "
            f"{old['trades']:,} trades, {old['participants']} participants) "
            f"| {old['analytics_endpoint_ms']:.1f} → {new['analytics_endpoint_ms']:.2f} "
            f"| {old['sharpe_all_participants_ms']:.2f} → {new['sharpe_all_participants_ms']:.3f} "
            f"| {old['leaderboard_ms']:.2f} → {new['leaderboard_ms']:.2f} |"
        )
    return "\n".join(lines)


def summary(results: Result, source: str) -> str:
    """A second machine: its environment, then throughput and latency per workload."""
    env = results["environment"]
    clock = results["clock"]
    lines = [
        f"{env['cpu']} ({env['logical_cpus']} logical CPUs), {env['os']}, {env['compiler']}, "
        f"{env['build']}. Clock: {clock_text(clock)}. Source: {source}.",
        "",
        f"| Workload | M ops/s (min{DASH}max) | Mean ns | p50 | p99 | p99.9 | Max |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in results["workloads"]:
        rate = row["ops_per_second"]
        latency = row["latency"]
        lines.append(
            f"| `{row['name']}` | {millions(rate['median'])} "
            f"({millions(rate['min'])}{DASH}{millions(rate['max'])}) "
            f"| {nanoseconds(1e9 / rate['median'])} | {latency['p50_ns']} | {latency['p99_ns']} "
            f"| {latency['p999_ns']} | {latency['max_ns']:,} |"
        )
    return "\n".join(lines)


def allocations_per_op(workload: Result) -> float:
    kinds = workload["by_kind"].values()
    operations = sum(kind["latency"]["count"] for kind in kinds)
    allocations = sum(kind["allocations_per_op"] * kind["latency"]["count"] for kind in kinds)
    return allocations / operations


README_ROWS = (
    (
        "patterns/mixed",
        None,
        "Mixed flow: adds, cancels, IOC and market orders, about 10k resting orders",
    ),
    ("exchange/mixed", None, "Same flow through the `Exchange` (plus market data)"),
    ("book_size/100k", None, "Add and cancel on a 100k-order book"),
    ("cancel/random/1M", None, "Random cancels on a 1M-order book"),
    ("patterns/sweep_10", "sweep", "A market order sweeping 10 levels (100 fills), timed alone"),
)


def readme(results: Result) -> str:
    """The README's summary: stream throughput and mean, and percentiles timed alone."""
    by_name = {w["name"]: w for w in results["workloads"]}
    lines = [
        "| Engine workload | Throughput | Mean | p99 | p99.9 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for name, kind, label in README_ROWS:
        workload = by_name[name]
        if kind is None:
            rate = workload["ops_per_second"]["median"]
            latency = workload["latency"]
            throughput, mean = f"{millions(rate)} M ops/s", f"{1e9 / rate:,.0f} ns"
        else:
            latency = workload["by_kind"][kind]["latency"]
            throughput, mean = "—", duration(latency["mean_ns"])
        lines.append(
            f"| {label} | {throughput} | {mean} | {duration(latency['p99_ns'])} "
            f"| {duration(latency['p999_ns'])} |"
        )
    return "\n".join(lines)


def duration(nanoseconds: float) -> str:
    if nanoseconds >= 1e6:
        return f"{nanoseconds / 1e6:.1f} ms"
    return f"{nanoseconds / 1000:.1f} µs" if nanoseconds >= 1000 else f"{nanoseconds:,.0f} ns"


def replace_section(text: str, name: str, body: str) -> str:
    # The markers may be adjacent (an empty section) or enclose earlier output.
    pattern = re.compile(
        rf"(<!-- bench:{name}:start -->\n)(?:.*?\n)?(<!-- bench:{name}:end -->)", re.DOTALL
    )
    if not pattern.search(text):
        raise SystemExit(f"no bench:{name} markers in the document")
    return pattern.sub(lambda match: match.group(1) + body + "\n" + match.group(2), text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("results", type=pathlib.Path)
    parser.add_argument("--baseline", type=pathlib.Path)
    parser.add_argument("--intermediate", type=pathlib.Path)
    parser.add_argument("--linux", type=pathlib.Path)
    parser.add_argument("--python", type=pathlib.Path, help="directory of python-*.json")
    parser.add_argument("--update", type=pathlib.Path)
    args = parser.parse_args()
    results = json.loads(args.results.read_text())
    sections = {
        "environment": environment(results),
        "memory": memory(results),
        "workloads": workloads(results),
        "mixed": by_kind(results, "patterns/mixed"),
    }
    if results.get("index_growth"):
        sections["growth"] = growth(results)
    if args.baseline is not None:
        intermediate = (
            None if args.intermediate is None else json.loads(args.intermediate.read_text())
        )
        sections["comparison"] = comparison(
            results, json.loads(args.baseline.read_text()), intermediate
        )
    if args.python is not None:

        def load(name: str) -> Result:
            loaded: Result = json.loads((args.python / f"python-{name}.json").read_text())
            return loaded

        sections["python-kernel"] = python_kernel(load("final"))
        sections["python-live"] = python_live(load("before"), load("after"))
    if args.linux is not None:
        sections["linux"] = summary(
            json.loads(args.linux.read_text()), f"`{args.linux.as_posix()}`, from the CI job"
        )
    if args.update is None:
        for name, body in sections.items():
            print(f"## {name}\n\n{body}\n")
        return 0
    sections["readme"] = readme(results)
    text = args.update.read_text()
    # Only the sections the file has markers for: the benchmark page has most, the README one.
    present = [name for name in sections if f"<!-- bench:{name}:start -->" in text]
    if not present:
        raise SystemExit(f"no bench markers in {args.update}")
    for name in present:
        text = replace_section(text, name, sections[name])
    args.update.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
