"""Ranked weighted path surfacing from a starting occupation (design section 5.1).

GRAPH_TABLE (ISO SQL/PGQ) is the primary traversal: the quantified variable-length
match returns ONE ROW PER STEP, and MATCHNUM()/ELEMENT_NUMBER() group and order each
walk's rows, so every hop's edge type, weight, and zone stays individually
explainable. This Oracle build rejects the ISO path modes (ACYCLIC/TRAIL), so the
match runs in default WALK mode and cycles are filtered in Python: a walk whose
vertex ids contain any repeat is dropped. The hop-by-hop UNION ALL formulation is
retained behind graph_engine="hop_join" as a verified fallback.

Paths are assembled at query time from one-hop edges — never precomputed. Cost is
``sum(1 - weight)`` over the hops, so the cheapest path is the strongest chain of
skill transfer. ``related`` edges are excluded from routing per section 4.1.
"""

from __future__ import annotations

from dataclasses import dataclass

from navigator.config import get_settings
from navigator.lca import WAGE_SCOPE_CODES

MAX_HOPS = 3
DEFAULT_TOP_K = 5

# Routing excludes ``related`` edges (design section 4.1). Both engines share
# this predicate so they cannot diverge on schema-legal data. On this corpus it
# is equivalent to the historical  source like 'derived_%'  filter
# (source='onet_related' iff edge_type='related' — verified live 2026-11-10).
_ROUTING_FILTER = "edge_type in ('career_ladder', 'skill_overlap')"

_EDGE_JOIN = f"""
  select e.src_occupation_id, e.dst_occupation_id, e.edge_type, e.weight
  from occupation_edges e
  where e.{_ROUTING_FILTER}
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

# This build (23.26.4.1.0) rejects ISO path modes — MATCH ACYCLIC / TRAIL /
# p = ACYCLIC all fail to parse (ORA-00927/ORA-40987) — so the match runs in
# default WALK mode and _paths_from_graph_table_rows drops cyclic walks in
# Python. With the shared _ROUTING_FILTER the result set matches the hop-join
# enumeration (verified live 2026-11-10: 1,625 paths vs 1,625, zero diff
# either direction, start=Computer Programmers). The Python cycle rule is
# strictly acyclic; the hop-join predicates additionally rely on the loader
# never emitting self-loop edges (build_graph.py joins src <> dst).
_GRAPH_TABLE_SQL = f"""
select match_num, step_num, src_id, dst_id, edge_type, weight
from graph_table (
  occupation_graph
  match (a is occupations where a.occupation_id = :start_id)
        (-[e is occupation_edges
           where e.{_ROUTING_FILTER}]->(x)){{1,{MAX_HOPS}}}
        (b is occupations)
  one row per step (v1, e1, v2)
  columns (
    matchnum() as match_num,
    element_number(e1) as step_num,
    v1.occupation_id as src_id,
    v2.occupation_id as dst_id,
    e1.edge_type as edge_type,
    e1.weight as weight
  )
)
order by match_num, step_num
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


def overlay_wages(
    national: dict[int, float], scoped: dict[int, float]
) -> dict[int, float]:
    """Scoped medians where they exist, national everywhere else.

    Per-occupation fallback keeps hop wage tags present even where a county
    has too few filings; the narration's methodology note names the mix.
    """
    return {**national, **scoped}


def _wage_map(cursor, wage_scope: str) -> dict[int, float]:
    cursor.execute(
        """
        select occupation_id, wage_median from metro_wages
        where metro_id is null
        """
    )
    national = {int(o): float(w) for o, w in cursor.fetchall() if w is not None}
    code = WAGE_SCOPE_CODES.get(wage_scope)
    if code is None:
        return national
    cursor.execute(
        """
        select w.occupation_id, w.wage_median
        from metro_wages w
        join metro_areas m on m.metro_id = w.metro_id
        where m.msa_code = :code
        """,
        code=code,
    )
    scoped = {int(o): float(w) for o, w in cursor.fetchall() if w is not None}
    return overlay_wages(national, scoped)


def _hop_between(src: int, dst: int, edge_type, weight, meta, wages) -> Hop:
    return Hop(
        src_title=meta[src][0],
        dst_title=meta[dst][0],
        src_zone=meta[src][1],
        dst_zone=meta[dst][1],
        edge_type=str(edge_type),
        weight=float(weight),
        src_wage=wages.get(src),
        dst_wage=wages.get(dst),
    )


def _paths_from_hop_join_rows(rows, meta, wages) -> list[Path]:
    paths: list[Path] = []
    for row in rows:
        n_hops = int(row[0])
        node_ids = [int(v) for v in row[1 : 2 + n_hops]]
        edge_cols = row[5 : 5 + 2 * n_hops]
        hops = [
            _hop_between(node_ids[i], node_ids[i + 1],
                         edge_cols[2 * i], edge_cols[2 * i + 1], meta, wages)
            for i in range(n_hops)
        ]
        paths.append(Path(hops=hops))
    return paths


def _paths_from_graph_table_rows(rows, meta, wages) -> list[Path]:
    """Assemble ONE ROW PER STEP rows into walks, dropping cyclic ones.

    match_num values are opaque — grouping only, not necessarily contiguous.
    element_number values are the ISO element positions (edges sit at 2, 4, 6)
    and order a walk's hops; the vertex id sequence is the first row's src_id
    followed by every row's dst_id.
    """
    walks: dict[object, list[tuple]] = {}
    for row in rows:
        walks.setdefault(row[0], []).append(row)
    paths: list[Path] = []
    for steps in walks.values():
        steps.sort(key=lambda step: int(step[1]))
        vertex_ids = [int(steps[0][2])] + [int(step[3]) for step in steps]
        if len(set(vertex_ids)) != len(vertex_ids):
            continue
        hops = [
            _hop_between(vertex_ids[i], vertex_ids[i + 1],
                         step[4], step[5], meta, wages)
            for i, step in enumerate(steps)
        ]
        paths.append(Path(hops=hops))
    return paths


def enumerate_paths(
    pool, start_occupation_id: int, wage_scope: str | None = None,
    engine: str | None = None,
) -> list[Path]:
    """Every path out of one occupation up to MAX_HOPS, unranked and undeduped.

    The ranked surface (``surface_paths``) and destination mode
    (``paths_to_destination``) both consume this; destination mode needs the
    full enumeration because ranking dedupes to one path per destination.
    Both engines share ``_ROUTING_FILTER`` and are live-verified to return
    equal path sets (see the note above ``_GRAPH_TABLE_SQL``).
    """
    with pool.acquire() as connection:
        cursor = connection.cursor()
        cursor.execute(
            "select occupation_id, title, job_zone from occupations"
        )
        meta = {int(oid): (title, zone) for oid, title, zone in cursor.fetchall()}
        if wage_scope is None:
            wage_scope = get_settings().wage_scope
        if engine is None:
            engine = get_settings().graph_engine
        wages = _wage_map(cursor, wage_scope)
        if engine == "graph_table":
            cursor.execute(_GRAPH_TABLE_SQL, start_id=start_occupation_id)
            paths = _paths_from_graph_table_rows(cursor.fetchall(), meta, wages)
        elif engine == "hop_join":
            cursor.execute(_PATHS_SQL, start_id=start_occupation_id)
            paths = _paths_from_hop_join_rows(cursor.fetchall(), meta, wages)
        else:
            raise ValueError(
                f"graph_engine must be one of ['graph_table', 'hop_join'], "
                f"got {engine!r}")
    return paths


def surface_paths(
    pool, start_occupation_id: int, top_k: int = DEFAULT_TOP_K,
    wage_scope: str | None = None, engine: str | None = None,
) -> list[Path]:
    """Return the ranked weighted paths out of one occupation, up to MAX_HOPS."""
    return rank_paths(
        enumerate_paths(pool, start_occupation_id, wage_scope, engine), top_k)


def surface_paths_to(
    pool, start_occupation_id: int, destination_title: str,
    top_k: int = DEFAULT_TOP_K, wage_scope: str | None = None,
    engine: str | None = None,
) -> list[Path]:
    """The cheapest routes from a start to one named destination."""
    return paths_to_destination(
        enumerate_paths(pool, start_occupation_id, wage_scope, engine),
        destination_title, top_k,
    )
