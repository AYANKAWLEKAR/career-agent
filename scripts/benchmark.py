"""Benchmark the 26ai-native query paths on the live instance.

Reports p50/p95 latency (ms) per operation after a warmup, plus the one
number that keeps an approximate index honest: recall of the HNSW APPROX
result against the exact result over the same query vectors.

Sections:
  paths     GRAPH_TABLE vs hop-join UNION ALL enumeration, sampled starts
  vectors   occupations (124 rows) and tasks (2,382 rows): exact vs APPROX
            top-k latency, plus recall@k of APPROX vs exact
  outcomes  in-database major -> CIP match, program medians, fused feeder
            ranking, and spatial radius search over institutions

Usage:
    uv run python scripts/benchmark.py [--json out.json] [--quick]
"""

from __future__ import annotations

import argparse
import json
import time
from typing import Any

from navigator.config import get_settings
from navigator.db.pool import create_pool
from navigator.outcomes import (
    GRADUATE_LEVELS,
    Anchor,
    feeder_cips,
    match_majors,
    nearby_programs,
    program_outcome,
)
from navigator.paths import enumerate_paths

MAJORS = ["Applied Mathematics", "Computer Science", "Statistics", "Molecular Biology",
          "Finance", "Data Science", "Economics", "Nursing"]
ANCHORS = [Anchor("Berkeley, CA", 37.87, -122.26), Anchor("Boston, MA", 42.36, -71.06),
           Anchor("Austin, TX", 30.27, -97.74)]


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


def timed(fn, reps: int, warmup: int = 2) -> dict[str, float]:
    for _ in range(warmup):
        fn()
    samples = []
    for _ in range(reps):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000)
    return {"p50": percentile(samples, 0.5), "p95": percentile(samples, 0.95),
            "mean": sum(samples) / len(samples), "n": len(samples)}


def bench_paths(pool, results: dict[str, Any], quick: bool) -> None:
    with pool.acquire() as connection:
        cursor = connection.cursor()
        cursor.execute("select occupation_id from occupations where job_zone is not null "
                       "order by occupation_id")
        ids = [int(r[0]) for r in cursor.fetchall()]
    step = 12 if quick else 4
    starts = ids[::step]
    for engine in ("graph_table", "hop_join"):
        samples = []
        counts = []
        for start in starts:
            began = time.perf_counter()
            paths = enumerate_paths(pool, start, wage_scope="national", engine=engine)
            samples.append((time.perf_counter() - began) * 1000)
            counts.append(len(paths))
        results[f"paths.{engine}"] = {
            "p50": percentile(samples, 0.5), "p95": percentile(samples, 0.95),
            "mean": sum(samples) / len(samples), "n": len(samples),
            "mean_paths": sum(counts) / len(counts)}


def bench_vectors(pool, results: dict[str, Any], quick: bool) -> None:
    k = 8
    with pool.acquire() as connection:
        cursor = connection.cursor()
        for table, key, sample_n in (("occupations", "occupation_id", 40),
                                     ("tasks", "task_id", 30 if quick else 60)):
            cursor.execute(
                f"select {key}, embedding from {table} where embedding is not null "
                f"order by mod({key}, 37), {key} fetch first {sample_n} rows only")
            queries = cursor.fetchall()
            exact_sql = (f"select {key} from {table} order by "
                         f"vector_distance(embedding, :qv, cosine) fetch first :k rows only")
            approx_sql = (f"select {key} from {table} order by "
                          f"vector_distance(embedding, :qv, cosine) "
                          f"fetch approx first :k rows only")
            recalls: list[float] = []
            exact_ms: list[float] = []
            approx_ms: list[float] = []
            for _id, vector in queries:
                began = time.perf_counter()
                cursor.execute(exact_sql, qv=vector, k=k)
                exact = {r[0] for r in cursor.fetchall()}
                exact_ms.append((time.perf_counter() - began) * 1000)
                began = time.perf_counter()
                cursor.execute(approx_sql, qv=vector, k=k)
                approx = {r[0] for r in cursor.fetchall()}
                approx_ms.append((time.perf_counter() - began) * 1000)
                recalls.append(len(exact & approx) / k)
            for label, samples in (("exact", exact_ms), ("approx", approx_ms)):
                results[f"vectors.{table}.{label}"] = {
                    "p50": percentile(samples, 0.5), "p95": percentile(samples, 0.95),
                    "mean": sum(samples) / len(samples), "n": len(samples)}
            results[f"vectors.{table}.recall_at_{k}"] = {
                "mean": sum(recalls) / len(recalls), "min": min(recalls), "n": len(recalls)}


def bench_outcomes(pool, results: dict[str, Any], quick: bool) -> None:
    reps = 8 if quick else 20
    state = {"i": 0}

    def major():
        state["i"] += 1
        match_majors(pool, [MAJORS[state["i"] % len(MAJORS)]])

    results["outcomes.match_major"] = timed(major, reps)
    results["outcomes.program_outcome"] = timed(
        lambda: program_outcome(pool, "2703", "Applied Mathematics", "Bachelor's Degree"), reps)
    with pool.acquire() as connection:
        cursor = connection.cursor()
        cursor.execute("select occupation_id from occupations where title = 'Statisticians'")
        oid = int(cursor.fetchone()[0])
    results["outcomes.feeder_cips_fused"] = timed(
        lambda: feeder_cips(pool, oid, GRADUATE_LEVELS, 4), reps)

    def spatial():
        state["i"] += 1
        nearby_programs(pool, ["2705", "2703", "1107"], GRADUATE_LEVELS,
                        ANCHORS[state["i"] % len(ANCHORS)], 100, 5)

    results["outcomes.spatial_radius_100mi"] = timed(spatial, reps)


def render(results: dict[str, Any]) -> str:
    lines = [f"{'operation':<36}{'p50 ms':>9}{'p95 ms':>9}{'mean ms':>9}{'n':>5}", "-" * 68]
    for name, row in results.items():
        if "p50" in row:
            lines.append(f"{name:<36}{row['p50']:>9.1f}{row['p95']:>9.1f}"
                         f"{row['mean']:>9.1f}{row['n']:>5}")
    lines.append("")
    for name, row in results.items():
        if name.startswith("paths.") and "mean_paths" in row:
            lines.append(f"{name}: {row['mean_paths']:.0f} acyclic paths per start (mean)")
        if "recall" in name:
            lines.append(f"{name}: mean {row['mean']:.3f}, min {row['min']:.3f} "
                         f"over {row['n']} query vectors")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", help="also write raw results to this path")
    parser.add_argument("--quick", action="store_true", help="fewer samples")
    args = parser.parse_args(argv)
    pool = create_pool(get_settings())
    with pool.acquire() as connection:
        cursor = connection.cursor()
        cursor.execute("select banner_full from v$version")
        print(cursor.fetchone()[0].splitlines()[0])
    results: dict[str, Any] = {}
    for name, fn in (("paths", bench_paths), ("vectors", bench_vectors),
                     ("outcomes", bench_outcomes)):
        started = time.perf_counter()
        fn(pool, results, args.quick)
        print(f"[{name} done in {time.perf_counter() - started:.1f}s]", flush=True)
    print()
    print(render(results))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(results, handle, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
