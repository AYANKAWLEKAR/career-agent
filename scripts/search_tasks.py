"""Free-text task search, entirely in-database: embed, APPROX top-k, prove it.

Takes a natural-language description of work, embeds it with the in-database
ONNX model (the text binds straight into ``vector_embedding``), runs an
approximate top-k cosine search over the task-statement embeddings, and
resolves the hits to occupation titles and task statements. ``--plan`` pulls
the search's real execution plan from v$sql to show the VECTOR INDEX HNSW
SCAN, plus the dbms_vector accuracy report for the same query vector.

Run scripts/embed_tasks.py first: it populates the embeddings and rebuilds the
index this search needs.

Usage:
    uv run python scripts/search_tasks.py "clean and analyze sequencing data" --top-k 8
"""

from __future__ import annotations

import argparse
import time
from typing import Any

from navigator.config import get_settings
from navigator.db.pool import create_pool

MODEL_NAME = "ALL_MINILM_L12_V2"
INDEX_NAME = "TASKS_EMBEDDING_VIX"

EMBED_SQL = f"select vector_embedding({MODEL_NAME} using :q as data) from dual"

# Standalone query on tasks only: a join here would disqualify
# TASKS_EMBEDDING_VIX and flip the plan to a full scan, so occupation titles
# and task statements resolve in follow-up lookups instead.
SEARCH_SQL = """
select /* TASK_SEARCH */ task_id, occupation_id,
       vector_distance(embedding, :qv, cosine) as dist
from tasks
order by vector_distance(embedding, :qv, cosine)
fetch approx first :k rows only
"""

CURSOR_LOOKUP_SQL = """
select sql_id, child_number from v$sql
where sql_text like :m and sql_text not like :x
order by last_active_time desc
fetch first 1 rows only
"""

PLAN_SQL = """
select plan_table_output
from table(dbms_xplan.display_cursor(:sid, :child, 'BASIC'))
"""

ACCURACY_SQL = f"""
begin
  :report := dbms_vector.index_accuracy_query(
    owner_name => user, index_name => '{INDEX_NAME}',
    qv => :qv, top_k => 10, target_accuracy => 95);
end;
"""


def _clob_text(value: Any) -> str:
    """task_statement is a CLOB: the driver returns str or LOB by fetch size."""
    return value.read() if hasattr(value, "read") else str(value)


def _lookup(cursor: Any, sql: str, prefix: str, ids: list[int]) -> dict[int, Any]:
    binds = {f"{prefix}{i}": value for i, value in enumerate(dict.fromkeys(ids))}
    placeholders = ", ".join(f":{name}" for name in binds)
    cursor.execute(sql.format(placeholders=placeholders), binds)
    return {int(key): value for key, value in cursor.fetchall()}


def show_plan(cursor: Any, query_vector: Any) -> None:
    cursor.execute(CURSOR_LOOKUP_SQL, m="%TASK_SEARCH%", x="%v$sql%")
    row = cursor.fetchone()
    if row is None:
        print("\nplan: no TASK_SEARCH cursor found in v$sql")
        return
    sql_id, child_number = row
    cursor.execute(PLAN_SQL, sid=sql_id, child=child_number)
    print(f"\nplan from v$sql (sql_id={sql_id}, child={child_number}):")
    for (line,) in cursor.fetchall():
        text = line or ""
        prefix = ">> " if "VECTOR INDEX HNSW SCAN" in text else "   "
        print(prefix + text)

    report = cursor.var(str)
    cursor.execute(ACCURACY_SQL, report=report, qv=query_vector)
    print(f"\naccuracy report ({INDEX_NAME}, top_k=10, target accuracy 95):")
    print(report.getvalue())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="free-text description of work")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--plan", default=True, action=argparse.BooleanOptionalAction,
                        help="pull the execution plan from v$sql and the accuracy report")
    args = parser.parse_args(argv)
    if not 1 <= args.top_k <= 500:
        # The IN-list lookups bind one placeholder per hit; Oracle rejects
        # expression lists past 1,000 entries (ORA-01795).
        parser.error("--top-k must be between 1 and 500")

    pool = create_pool(get_settings())
    with pool.acquire() as connection:
        cursor = connection.cursor()
        print(f'query: "{args.query}"')
        pipeline_started = time.monotonic()

        started = time.monotonic()
        cursor.execute(EMBED_SQL, q=args.query)
        query_vector = cursor.fetchone()[0]
        print(f"[in-db] query embedding ({MODEL_NAME}): "
              f"{(time.monotonic() - started) * 1000:.0f} ms")

        started = time.monotonic()
        cursor.execute(SEARCH_SQL, qv=query_vector, k=args.top_k)
        hits = [(int(t), int(o), float(d)) for t, o, d in cursor.fetchall() if d is not None]
        print(f"[in-db] APPROX top-{args.top_k} cosine search over tasks: "
              f"{(time.monotonic() - started) * 1000:.0f} ms, {len(hits)} hits")

        if hits:
            titles = _lookup(
                cursor,
                "select occupation_id, title from occupations "
                "where occupation_id in ({placeholders})",
                "o", [occupation_id for _, occupation_id, _ in hits])
            statements = _lookup(
                cursor,
                "select task_id, task_statement from tasks where task_id in ({placeholders})",
                "t", [task_id for task_id, _, _ in hits])
            print()
            for task_id, occupation_id, dist in hits:
                statement = " ".join(_clob_text(statements.get(task_id, "")).split())
                print(f"  {1 - dist:6.4f}  {titles.get(occupation_id, '?')}: "
                      f"{statement[:140]}")
        pipeline_ms = (time.monotonic() - pipeline_started) * 1000

        if args.plan:
            try:
                show_plan(cursor, query_vector)
            except Exception as exc:  # plan proof needs v$ grants; results stand
                print(f"\nplan: unavailable on this session ({exc})")

        print(f"\ntotal: {pipeline_ms:.0f} ms in-database (embed + search + resolve), "
              f"{len(hits)} rows returned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
