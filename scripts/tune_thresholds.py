"""Sweep candidate edge thresholds and report what each one does to the graph.

The design's thresholds (0.65 ladder / 0.75 lateral) were chosen against raw
importance cosine, whose values on this dataset run 0.912-0.997 -- so both sit
below the observed minimum and admit 85% of all pairs. Switching to centered
cosine changes the scale entirely, so the thresholds have to be re-derived rather
than carried over.

This pulls every job-zone-gated pair once, then scores threshold candidates in
memory against four properties that decide whether the graph is usable:

* **isolated** -- occupations with no outgoing edge can never start a path.
* **out-degree** -- the branching factor a traversal actually sees.
* **reach3** -- ordered pairs connected within three hops; the graph's coverage.
* **1-hop share** -- of those, the fraction already adjacent. A high share means
  multi-hop surfacing has nothing to add.

Usage:
    uv run python scripts/tune_thresholds.py
"""

from __future__ import annotations

import argparse
from collections import deque
from statistics import median

from navigator.config import get_settings
from navigator.db.pool import create_pool

# Lateral is always held to a stricter bar than a rung up, per design section 4.3.
LADDER_GRID = (0.25, 0.30, 0.35, 0.40)
LATERAL_GRID = (0.35, 0.40, 0.45, 0.50, 0.55)
MAX_HOPS = 3

PAIR_SQL = """
with mu as (
  select element_id, avg(importance) as m
  from occupation_skills
  where importance is not null
  group by element_id
),
vec as (
  select s.occupation_id, s.element_id, s.importance - mu.m as v
  from occupation_skills s
  join mu on mu.element_id = s.element_id
  where s.importance is not null
),
dots as (
  -- Shared-subspace norms, matching build_graph.derive_edges.
  select a.occupation_id as src_id, b.occupation_id as dst_id, sum(a.v * b.v) as dot,
         sqrt(sum(a.v * a.v)) as nrm_src, sqrt(sum(b.v * b.v)) as nrm_dst
  from vec a
  join vec b on b.element_id = a.element_id and b.occupation_id <> a.occupation_id
  group by a.occupation_id, b.occupation_id
)
select d.src_id, d.dst_id, d.dot / (d.nrm_src * d.nrm_dst) as cs,
       case when b.job_zone = a.job_zone + 1 then 'career_ladder' else 'skill_overlap' end
from dots d
join occupations a on a.occupation_id = d.src_id
join occupations b on b.occupation_id = d.dst_id
where d.nrm_src > 0 and d.nrm_dst > 0
  and a.job_zone is not null and b.job_zone is not null
  and b.job_zone between a.job_zone and a.job_zone + 1
"""


def reachability(adjacency: dict[int, list[int]], nodes: list[int]) -> tuple[float, float]:
    """Return (share of ordered pairs reachable within MAX_HOPS, share of those at 1 hop)."""
    reachable = one_hop = 0
    for start in nodes:
        seen = {start: 0}
        queue = deque([start])
        while queue:
            node = queue.popleft()
            if seen[node] == MAX_HOPS:
                continue
            for nxt in adjacency.get(node, ()):
                if nxt not in seen:
                    seen[nxt] = seen[node] + 1
                    queue.append(nxt)
        for node, depth in seen.items():
            if node == start:
                continue
            reachable += 1
            if depth == 1:
                one_hop += 1
    total = len(nodes) * (len(nodes) - 1)
    return (reachable / total if total else 0.0, one_hop / reachable if reachable else 0.0)


def evaluate(pairs, nodes, ladder: float, lateral: float) -> dict[str, float]:
    adjacency: dict[int, list[int]] = {}
    for src, dst, cosine, kind in pairs:
        threshold = ladder if kind == "career_ladder" else lateral
        if cosine >= threshold:
            adjacency.setdefault(src, []).append(dst)
    degrees = [len(adjacency.get(n, ())) for n in nodes]
    edges = sum(degrees)
    reach, one_hop = reachability(adjacency, nodes)
    return {
        "edges": edges,
        "density": 100 * edges / len(pairs),
        "isolated": 100 * sum(1 for d in degrees if d == 0) / len(nodes),
        "median_out": median(degrees),
        "max_out": max(degrees),
        "reach3": 100 * reach,
        "one_hop": 100 * one_hop,
    }


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    pool = create_pool(get_settings())
    with pool.acquire() as connection:
        cursor = connection.cursor()
        cursor.execute(PAIR_SQL)
        pairs = [(int(a), int(b), float(c), d) for a, b, c, d in cursor.fetchall()]
    nodes = sorted({p[0] for p in pairs} | {p[1] for p in pairs})

    print(f"{len(pairs)} gated pairs over {len(nodes)} occupations\n")
    columns = ("ladder", "lateral", "edges", "dens%", "isol%", "medOut", "maxOut")
    header = " ".join(f"{c:>7}" for c in columns) + f" {'reach3%':>8} {'1hop%':>7}"
    print(header)
    print("-" * len(header))
    for ladder in LADDER_GRID:
        for lateral in LATERAL_GRID:
            if lateral <= ladder:
                continue  # design 4.3: a lateral move must clear a higher bar
            m = evaluate(pairs, nodes, ladder, lateral)
            print(
                f"{ladder:>7.2f} {lateral:>8.2f} {m['edges']:>7.0f} {m['density']:>7.1f} "
                f"{m['isolated']:>7.1f} {m['median_out']:>7.0f} {m['max_out']:>7.0f} "
                f"{m['reach3']:>8.1f} {m['one_hop']:>7.1f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
