"""Ranked weighted path surfacing from a starting occupation (design section 5.1).

Paths are assembled at query time from one-hop edges — never precomputed. Cost is
``sum(1 - weight)`` over the hops, so the cheapest path is the strongest chain of
skill transfer. ``related`` edges are excluded from routing per section 4.1.

The hop-by-hop UNION formulation (1-, 2-, and 3-hop joins) is used instead of a
variable-length GRAPH_TABLE match because narration needs every hop's edge type,
weight, and zone individually — the graph must stay explainable edge by edge.
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_HOPS = 3
DEFAULT_TOP_K = 5

_EDGE_JOIN = """
  select e.src_occupation_id, e.dst_occupation_id, e.edge_type, e.weight
  from occupation_edges e
  where e.source like 'derived_%'
"""

_PATHS_SQL = f"""
with hop as ({_EDGE_JOIN})
select 1 as n_hops,
       h1.src_occupation_id, h1.dst_occupation_id, null, null,
       h1.edge_type, h1.weight, null, null, null, null
from hop h1
where h1.src_occupation_id = :start_id
union all
select 2,
       h1.src_occupation_id, h1.dst_occupation_id, h2.dst_occupation_id, null,
       h1.edge_type, h1.weight, h2.edge_type, h2.weight, null, null
from hop h1
join hop h2 on h2.src_occupation_id = h1.dst_occupation_id
where h1.src_occupation_id = :start_id
  and h2.dst_occupation_id <> h1.src_occupation_id
union all
select 3,
       h1.src_occupation_id, h1.dst_occupation_id, h2.dst_occupation_id,
       h3.dst_occupation_id,
       h1.edge_type, h1.weight, h2.edge_type, h2.weight, h3.edge_type, h3.weight
from hop h1
join hop h2 on h2.src_occupation_id = h1.dst_occupation_id
join hop h3 on h3.src_occupation_id = h2.dst_occupation_id
where h1.src_occupation_id = :start_id
  and h2.dst_occupation_id <> h1.src_occupation_id
  and h3.dst_occupation_id not in (h1.src_occupation_id, h1.dst_occupation_id)
"""


@dataclass
class Hop:
    src_title: str
    dst_title: str
    src_zone: int | None
    dst_zone: int | None
    edge_type: str
    weight: float
    # National median offered wage (H-1B LCA aggregates, metro_id-null rows).
    # None when the occupation has no wage row; the hop still traverses.
    src_wage: float | None = None
    dst_wage: float | None = None

    @property
    def cost(self) -> float:
        return 1.0 - self.weight

    @property
    def wage_delta(self) -> float | None:
        if self.src_wage is None or self.dst_wage is None:
            return None
        return self.dst_wage - self.src_wage


@dataclass
class Path:
    hops: list[Hop]

    @property
    def destination(self) -> str:
        return self.hops[-1].dst_title

    @property
    def cost(self) -> float:
        return sum(hop.cost for hop in self.hops)


def rank_paths(paths: list[Path], top_k: int = DEFAULT_TOP_K) -> list[Path]:
    """Keep the cheapest path per destination, then the top_k cheapest overall.

    Deduplicating by destination first keeps the surfaced set diverse — five
    routes to the same occupation is one answer, not five.
    """
    best: dict[str, Path] = {}
    for path in paths:
        current = best.get(path.destination)
        if current is None or path.cost < current.cost:
            best[path.destination] = path
    return sorted(best.values(), key=lambda p: p.cost)[:top_k]


def paths_to_destination(
    paths: list[Path], destination_title: str, top_k: int = DEFAULT_TOP_K
) -> list[Path]:
    """The cheapest routes to ONE destination from an already-enumerated set.

    Destination mode: a strong match above the start ceiling is a place the
    user resembles but cannot start at yet, so the graph builds the ladder
    toward it rather than seeding them there.
    """
    hits = [p for p in paths if p.destination == destination_title]
    return sorted(hits, key=lambda p: p.cost)[:top_k]


def enumerate_paths(pool, start_occupation_id: int) -> list[Path]:
    """Every path out of one occupation up to MAX_HOPS, unranked and undeduped.

    The ranked surface (``surface_paths``) and destination mode
    (``paths_to_destination``) both consume this; destination mode needs the
    full enumeration because ranking dedupes to one path per destination.
    """
    with pool.acquire() as connection:
        cursor = connection.cursor()
        cursor.execute(
            "select occupation_id, title, job_zone from occupations"
        )
        meta = {int(oid): (title, zone) for oid, title, zone in cursor.fetchall()}
        cursor.execute(
            """
            select occupation_id, wage_median from metro_wages
            where metro_id is null
            """
        )
        wages = {int(oid): float(w) for oid, w in cursor.fetchall() if w is not None}
        cursor.execute(_PATHS_SQL, start_id=start_occupation_id)
        paths: list[Path] = []
        for row in cursor.fetchall():
            n_hops = int(row[0])
            node_ids = [int(v) for v in row[1 : 2 + n_hops]]
            edge_cols = row[5 : 5 + 2 * n_hops]
            hops = []
            for i in range(n_hops):
                src, dst = node_ids[i], node_ids[i + 1]
                hops.append(
                    Hop(
                        src_title=meta[src][0],
                        dst_title=meta[dst][0],
                        src_zone=meta[src][1],
                        dst_zone=meta[dst][1],
                        edge_type=str(edge_cols[2 * i]),
                        weight=float(edge_cols[2 * i + 1]),
                        src_wage=wages.get(src),
                        dst_wage=wages.get(dst),
                    )
                )
            paths.append(Path(hops=hops))
    return paths


def surface_paths(pool, start_occupation_id: int, top_k: int = DEFAULT_TOP_K) -> list[Path]:
    """Return the ranked weighted paths out of one occupation, up to MAX_HOPS."""
    return rank_paths(enumerate_paths(pool, start_occupation_id), top_k)


def surface_paths_to(
    pool, start_occupation_id: int, destination_title: str,
    top_k: int = DEFAULT_TOP_K,
) -> list[Path]:
    """The cheapest routes from a start to one named destination."""
    return paths_to_destination(
        enumerate_paths(pool, start_occupation_id), destination_title, top_k
    )
