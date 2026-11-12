"""End-to-end health check for the Career Path Navigator stack.

Runs read-only probes against every layer and prints PASS/FAIL with timing:
database, in-database ONNX, property graph (both traversal engines), vector
indexes (execution-plan proof of HNSW use), wages, requirements, the College
Scorecard outcome layer (spatial + JSON duality), and the local LLM. Exit code
is nonzero if any check fails.

Usage:
    uv run python scripts/health_check.py
"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx

from navigator.config import get_settings
from navigator.db.pool import create_pool
from navigator.paths import enumerate_paths

pool = create_pool(get_settings())
settings = get_settings()
CHECKS: list[tuple[str, Callable[[], str]]] = []


def check(name: str):
    def register(fn: Callable[[], str]) -> Callable[[], str]:
        CHECKS.append((name, fn))
        return fn
    return register


def scalar(sql: str, **binds):
    with pool.acquire() as connection:
        cursor = connection.cursor()
        cursor.execute(sql, binds)
        return cursor.fetchone()[0]


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def plan_uses_hnsw(sql: str, marker: str, **binds) -> bool:
    with pool.acquire() as connection:
        cursor = connection.cursor()
        cursor.execute(sql.replace("select ", f"select /* {marker} */ ", 1), binds)
        cursor.fetchall()
        cursor.execute(
            "select sql_id, child_number from v$sql where sql_text like :m "
            "and sql_text not like '%v$sql%' order by last_active_time desc "
            "fetch first 1 rows only", m=f"%{marker}%")
        sql_id, child = cursor.fetchone()
        cursor.execute("select plan_table_output from table("
                       "dbms_xplan.display_cursor(:s, :c, 'BASIC'))", s=sql_id, c=child)
        return any("VECTOR INDEX HNSW SCAN" in r[0] for r in cursor.fetchall())


@check("database is 26ai")
def _db() -> str:
    banner = scalar("select banner_full from v$version").splitlines()[0]
    expect("26ai" in banner, banner)
    return banner.split(" Release ")[-1]


@check("in-database ONNX embeds to 384 dims")
def _onnx() -> str:
    dims = scalar("select vector_dims(vector_embedding(ALL_MINILM_L12_V2 "
                  "using 'health check' as data)) from dual")
    expect(dims == 384, f"dims={dims}")
    return "ALL_MINILM_L12_V2, 384"


@check("occupation corpus + embeddings")
def _occ() -> str:
    n = scalar("select count(*) from occupations")
    missing = scalar("select count(*) from occupations where embedding is null")
    expect(n == 124 and missing == 0, f"n={n}, unembedded={missing}")
    return f"{n} occupations, all embedded"


@check("career graph edges")
def _edges() -> str:
    n = scalar("select count(*) from occupation_edges")
    routable = scalar("select count(*) from occupation_edges "
                      "where edge_type in ('career_ladder','skill_overlap')")
    expect(n > 2000 and routable > 1000, f"n={n}, routable={routable}")
    return f"{n} edges ({routable} routable)"


@check("GRAPH_TABLE and hop-join engines agree")
def _engines() -> str:
    with pool.acquire() as connection:
        cursor = connection.cursor()
        cursor.execute("select occupation_id from occupations where job_zone is not null "
                       "order by occupation_id")
        ids = [int(r[0]) for r in cursor.fetchall()][::40]
    total = 0
    for oid in ids:
        a = enumerate_paths(pool, oid, wage_scope="national", engine="graph_table")
        b = enumerate_paths(pool, oid, wage_scope="national", engine="hop_join")

        def key(p):
            return tuple((h.src_title, h.dst_title, h.edge_type) for h in p.hops)

        expect({key(p) for p in a} == {key(p) for p in b}, f"engines differ at {oid}")
        total += len(a)
    return f"{len(ids)} starts, {total} paths identical"


@check("vector indexes valid")
def _vix() -> str:
    with pool.acquire() as connection:
        cursor = connection.cursor()
        cursor.execute("select index_name, status from user_indexes "
                       "where index_type = 'VECTOR' order by 1")
        rows = cursor.fetchall()
    bad = [n for n, s in rows if s != "VALID"]
    names = {n for n, _ in rows}
    for need in ("OCCUPATIONS_EMBEDDING_VIX", "TASKS_EMBEDDING_VIX", "CIP_CODES_EMBEDDING_VIX"):
        expect(need in names, f"missing {need}")
    expect(not bad, f"invalid: {bad}")
    return ", ".join(sorted(names))


@check("HNSW graphs loaded in memory for every populated index")
def _graphs() -> str:
    # A VALID index can still have an empty in-memory graph (observed after an
    # instance restart); the optimizer then silently falls back to a full scan.
    with pool.acquire() as connection:
        cursor = connection.cursor()
        cursor.execute("select index_name, num_vectors from v$vector_graph_index "
                       "where owner = user")
        loaded = {n: v for n, v in cursor.fetchall()}
    wanted = {"OCCUPATIONS_EMBEDDING_VIX": "occupations", "TASKS_EMBEDDING_VIX": "tasks",
              "CIP_CODES_EMBEDDING_VIX": "cip_codes"}
    problems = []
    for index, table in wanted.items():
        rows = scalar(f"select count(*) from {table} where embedding is not null")  # noqa: S608
        if loaded.get(index, 0) != rows:
            problems.append(f"{index}: {loaded.get(index, 0)}/{rows} vectors in memory")
    expect(not problems, "; ".join(problems) + " (rebuild the index)")
    return ", ".join(f"{t}={loaded[i]}" for i, t in wanted.items())


@check("occupation search uses HNSW (plan proof)")
def _hnsw_occ() -> str:
    qv = scalar("select embedding from occupations fetch first 1 rows only")
    expect(plan_uses_hnsw(
        "select occupation_id from occupations order by "
        "vector_distance(embedding, :qv, cosine) fetch approx first 10 rows only",
        "HC_OCC", qv=qv), "no VECTOR INDEX HNSW SCAN in plan")
    return "VECTOR INDEX HNSW SCAN"


@check("task corpus embedded + HNSW")
def _tasks() -> str:
    n = scalar("select count(*) from tasks")
    missing = scalar("select count(*) from tasks where embedding is null")
    expect(n > 2000 and missing == 0, f"n={n}, unembedded={missing}")
    qv = scalar("select vector_embedding(ALL_MINILM_L12_V2 using 'analyze data' as data) "
                "from dual")
    expect(plan_uses_hnsw(
        "select task_id from tasks order by vector_distance(embedding, :qv, cosine) "
        "fetch approx first 8 rows only", "HC_TASKS", qv=qv), "tasks plan has no HNSW scan")
    return f"{n} tasks embedded, HNSW scan"


@check("wages: national + regional segments")
def _wages() -> str:
    national = scalar("select count(*) from metro_wages where metro_id is null")
    scoped = scalar("select count(*) from metro_wages where metro_id is not null")
    expect(national >= 100 and scoped >= 100, f"national={national}, scoped={scoped}")
    return f"{national} national, {scoped} regional rows"


@check("requirements layer")
def _req() -> str:
    n = scalar("select count(*) from requirement_items")
    expect(n > 100, f"n={n}")
    return f"{n} requirement items"


@check("scorecard: institutions + programs loaded")
def _scorecard() -> str:
    inst = scalar("select count(*) from institutions")
    progs = scalar("select count(*) from programs")
    priced = scalar("select count(*) from programs where median_earnings_4yr is not null")
    expect(inst > 5000 and progs > 50_000 and priced > 30_000,
           f"institutions={inst}, programs={progs}, priced={priced}")
    return f"{inst:,} institutions, {progs:,} programs ({priced:,} with 4yr earnings)"


@check("CIP->occupation crosswalk covers every occupation")
def _crosswalk() -> str:
    covered = scalar("select count(distinct occupation_id) from program_occupations")
    expect(covered == 124, f"covered={covered}")
    return f"{scalar('select count(*) from program_occupations')} links, {covered}/124"


@check("CIP titles embedded + HNSW (major match)")
def _cip() -> str:
    missing = scalar("select count(*) from cip_codes where embedding is null")
    expect(missing == 0, f"unembedded={missing}")
    qv = scalar("select vector_embedding(ALL_MINILM_L12_V2 using 'applied math' as data) "
                "from dual")
    expect(plan_uses_hnsw(
        "select cip_code from cip_codes order by vector_distance(embedding, :qv, cosine) "
        "fetch approx first 1 rows only", "HC_CIP", qv=qv), "cip plan has no HNSW scan")
    top = scalar("select cip_code from cip_codes order by vector_distance(embedding, :qv, "
                 "cosine) fetch first 1 rows only", qv=qv)
    expect(top == "2703", f"'applied math' -> {top}")
    return "'applied math' -> 2703, HNSW scan"


@check("spatial: Berkeley institutions within 5 miles")
def _spatial() -> str:
    n = scalar("""
        select count(*) from institutions i
        where sdo_within_distance(i.location,
              sdo_geometry(2001, 4326, sdo_point_type(-122.26, 37.87, null), null, null),
              'distance=5 unit=mile') = 'TRUE'""")
    expect(n >= 1, f"n={n}")
    return f"{n} institutions"


@check("JSON Relational Duality view serves documents")
def _duality() -> str:
    n = scalar("select count(*) from major_outcomes_dv d where d.data.state = 'CA'")
    expect(n > 100, f"n={n}")
    return f"major_outcomes_dv: {n} California institution documents"


@check("tool_log observability table writable path exists")
def _log() -> str:
    n = scalar("select count(*) from tool_log")
    expect(n >= 0, "tool_log unreadable")
    return f"{n} rows"


@check("local LLM reachable (Ollama)")
def _llm() -> str:
    if settings.llm_backend != "ollama":
        return f"backend={settings.llm_backend} (skipped)"
    response = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=5)
    models = [m["name"] for m in response.json().get("models", [])]
    expect(settings.ollama_model in models, f"{settings.ollama_model} not in {models}")
    return f"{settings.ollama_model} installed"


def main() -> int:
    failures = 0
    for name, fn in CHECKS:
        started = time.perf_counter()
        try:
            detail = fn()
            status = "PASS"
        except Exception as exc:  # noqa: BLE001 - report every failure, keep going
            detail, status = f"{type(exc).__name__}: {exc}", "FAIL"
            failures += 1
        print(f"[{status}] {name} ({(time.perf_counter() - started) * 1000:.0f} ms) — {detail}")
    print(f"\n{len(CHECKS) - failures}/{len(CHECKS)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
