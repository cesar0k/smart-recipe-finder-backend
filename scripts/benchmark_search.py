"""Benchmark Redis cache effectiveness for recipe vector search.

Usage:
    Run benchmark with default settings:
        docker compose exec app python scripts/benchmark_search.py

    Run benchmark with more iterations:
        docker compose exec app python scripts/benchmark_search.py \
            --iterations 30
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.cache import close_redis, get_cache, init_redis
from app.core.vector_store import VectorStore
from app.db.session import AsyncSessionLocal
from app.services import recipe_service
from app.services.search_cache import bump_search_version

matplotlib.use("Agg")

BASE_PATH = Path(__file__).resolve().parents[1]

DEFAULT_QUERIES = [
    "pasta",
    "chicken soup",
    "vegetarian salad",
    "chocolate dessert",
    "quick breakfast",
    "Italian dinner",
    "паста с курицей",
    "суп на обед",
    "быстрый завтрак",
    "рыба на ужин",
]


@dataclass
class ScenarioResult:
    name: str
    latencies_ms: list[float] = field(default_factory=list)
    vector_calls_delta: int = 0
    search_calls: int = 0

    @property
    def mean(self) -> float:
        return statistics.fmean(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def median(self) -> float:
        return statistics.median(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def p95(self) -> float:
        if not self.latencies_ms:
            return 0.0
        return _percentile(self.latencies_ms, 95)

    @property
    def p99(self) -> float:
        if not self.latencies_ms:
            return 0.0
        return _percentile(self.latencies_ms, 99)

    @property
    def min(self) -> float:
        return min(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def max(self) -> float:
        return max(self.latencies_ms) if self.latencies_ms else 0.0


def _percentile(data: list[float], pct: int) -> float:
    xs = sorted(data)
    k = (len(xs) - 1) * pct / 100
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    if f == c:
        return xs[f]
    return xs[f] + (xs[c] - xs[f]) * (k - f)


async def _run_search(query: str, cache) -> float:  # type: ignore[no-untyped-def]
    async with AsyncSessionLocal() as db:
        start = time.perf_counter()
        await recipe_service.search_recipes_by_vector(
            db=db, query_str=query, cache=cache
        )
        return (time.perf_counter() - start) * 1000.0


async def run_cold_scenario(
    queries: list[str],
    iterations: int,
) -> ScenarioResult:
    print(f"\n[cold] Running {iterations} iterations × {len(queries)} queries "
          f"with forced cache MISS on every call...")

    cache = await get_cache()
    result = ScenarioResult(name="cold")
    calls_before = VectorStore.search_calls_count

    for i in range(iterations):
        for q in queries:
            await bump_search_version(cache)
            latency = await _run_search(q, cache)
            result.latencies_ms.append(latency)
            result.search_calls += 1
        if (i + 1) % 5 == 0:
            print(f"  ...{(i + 1) * len(queries)} / {iterations * len(queries)} done")

    result.vector_calls_delta = VectorStore.search_calls_count - calls_before
    return result


async def run_warm_scenario(
    queries: list[str],
    iterations: int,
) -> ScenarioResult:
    print(f"\n[warm] Pre-warming cache with {len(queries)} queries "
          f"then running {iterations} iterations...")

    cache = await get_cache()

    for q in queries:
        await _run_search(q, cache)

    result = ScenarioResult(name="warm")
    calls_before = VectorStore.search_calls_count

    for i in range(iterations):
        for q in queries:
            latency = await _run_search(q, cache)
            result.latencies_ms.append(latency)
            result.search_calls += 1
        if (i + 1) % 5 == 0:
            print(f"  ...{(i + 1) * len(queries)} / {iterations * len(queries)} done")

    result.vector_calls_delta = VectorStore.search_calls_count - calls_before
    return result


def print_summary(cold: ScenarioResult, warm: ScenarioResult) -> None:
    print("\n" + "=" * 78)
    print(
        "BENCHMARK SUMMARY — recipe_service.search_recipes_by_vector "
        "with Redis cache"
    )
    print("=" * 78)

    header = f"{'Scenario':<10} {'calls':>7} {'vec_calls':>10} {'min':>8} " \
             f"{'p50':>8} {'p95':>8} {'p99':>8} {'max':>8} {'avg':>8}"
    print(header)
    print("-" * len(header))

    for r in (cold, warm):
        print(
            f"{r.name:<10} {r.search_calls:>7} {r.vector_calls_delta:>10} "
            f"{r.min:>7.1f}ms {r.median:>7.1f}ms {r.p95:>7.1f}ms "
            f"{r.p99:>7.1f}ms {r.max:>7.1f}ms {r.mean:>7.1f}ms"
        )

    print("-" * len(header))

    if warm.mean > 0 and cold.mean > 0:
        speedup_mean = cold.mean / warm.mean
        speedup_p95 = cold.p95 / warm.p95 if warm.p95 > 0 else 0
        print(f"\nSpeedup (avg): {speedup_mean:.1f}×")
        print(f"Speedup (p95): {speedup_p95:.1f}×")

    if cold.search_calls > 0:
        cold_ratio = cold.vector_calls_delta / cold.search_calls
        warm_ratio = warm.vector_calls_delta / warm.search_calls
        print("\nvector_store.search() calls per service call:")
        print(
            f"  cold: {cold.vector_calls_delta}/{cold.search_calls} "
            f"= {cold_ratio:.2f}"
        )
        print(
            f"  warm: {warm.vector_calls_delta}/{warm.search_calls} "
            f"= {warm_ratio:.2f}"
        )
        reduction = (1 - warm_ratio / cold_ratio) * 100 if cold_ratio > 0 else 0
        print(
            f"  → cache eliminates {reduction:.1f}% of heavy backend calls"
        )

    print("=" * 78)


def plot_results(cold: ScenarioResult, warm: ScenarioResult, output: Path) -> None:
    print(f"\nRendering chart to {output}...")
    plt.style.use("ggplot")

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle(
        "Redis cache impact on recipe_service.search_recipes_by_vector",
        fontsize=14,
    )

    ax1.hist(
        cold.latencies_ms,
        bins=30,
        alpha=0.55,
        label=f"cold (MISS) — avg {cold.mean:.0f} ms",
        color="#e74c3c",
    )
    ax1.hist(
        warm.latencies_ms,
        bins=30,
        alpha=0.55,
        label=f"warm (HIT) — avg {warm.mean:.0f} ms",
        color="#27ae60",
    )
    ax1.set_xscale("log")
    ax1.set_xlabel("Latency (ms, log scale)")
    ax1.set_ylabel("Number of calls")
    ax1.set_title("Latency distribution")
    ax1.legend()

    labels = ["min", "p50", "p95", "p99", "max", "avg"]
    cold_vals = [cold.min, cold.median, cold.p95, cold.p99, cold.max, cold.mean]
    warm_vals = [warm.min, warm.median, warm.p95, warm.p99, warm.max, warm.mean]

    import numpy as np
    x = np.arange(len(labels))
    bar_width = 0.4

    ax2.bar(x - bar_width / 2, cold_vals, bar_width,
            label="cold (MISS)", color="#e74c3c")
    ax2.bar(x + bar_width / 2, warm_vals, bar_width,
            label="warm (HIT)", color="#27ae60")
    ax2.set_yscale("log")
    ax2.set_ylabel("Latency (ms, log scale)")
    ax2.set_xticks(x)
    ax2.set_xticklabels(labels)
    ax2.set_title(
        f"vector_store.search() calls: "
        f"cold={cold.vector_calls_delta}, warm={warm.vector_calls_delta}"
    )
    ax2.legend()

    for i, (c, w) in enumerate(zip(cold_vals, warm_vals, strict=True)):
        ax2.text(i - bar_width / 2, c, f"{c:.0f}", ha="center",
                 va="bottom", fontsize=8)
        ax2.text(i + bar_width / 2, w, f"{w:.1f}", ha="center",
                 va="bottom", fontsize=8)

    plt.tight_layout()
    plt.savefig(output, dpi=100)
    print(f"Chart saved to {output}")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark recipe_service.search_recipes_by_vector "
                    "with/without Redis cache."
    )
    parser.add_argument(
        "--iterations", type=int, default=10,
        help="Iterations per scenario (default: 10). Each iteration runs "
             "through all queries once.",
    )
    parser.add_argument(
        "--output", default=str(BASE_PATH / "benchmark_results.png"),
        help="Path to save the chart (default: ./benchmark_results.png)",
    )
    args = parser.parse_args()

    queries = DEFAULT_QUERIES

    print(f"Queries:      {len(queries)} "
          f"({sum(1 for q in queries if not any(ord(c) > 127 for c in q))} EN, "
          f"{sum(1 for q in queries if any(ord(c) > 127 for c in q))} RU)")
    print(f"Iterations:   {args.iterations} per scenario")
    print(f"Total calls:  {args.iterations * len(queries) * 2}")

    await init_redis()

    try:
        cold = await run_cold_scenario(queries, args.iterations)
        warm = await run_warm_scenario(queries, args.iterations)
    finally:
        await close_redis()

    print_summary(cold, warm)
    plot_results(cold, warm, Path(args.output))


if __name__ == "__main__":
    asyncio.run(main())
