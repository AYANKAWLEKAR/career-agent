"""Embed all task statements in-database and rebuild the task vector index.

Embedding is one set-based UPDATE running the in-database ONNX model straight
over the ``task_statement`` CLOB -- ``vector_embedding`` accepts the CLOB as
data, so there is no composition step. Load the model first
(scripts/load_onnx_model.py).

The index rebuild is mandatory, not hygiene: 007_indexes.sql created
``tasks_embedding_vix`` while every ``tasks.embedding`` was still NULL, so the
HNSW neighbor graph indexes nothing. APPROX task search only touches the index
once it is dropped and recreated over the populated column, which is what the
dbms_vector accuracy report at the end verifies.

Usage:
    uv run python scripts/embed_tasks.py
"""

from __future__ import annotations

import argparse
import time

from navigator.config import get_settings
from navigator.db.pool import create_pool

MODEL_NAME = "ALL_MINILM_L12_V2"
INDEX_NAME = "TASKS_EMBEDDING_VIX"
SAMPLE_QUERY = "analyze experimental data and summarize findings for the research team"

EMBED_SQL = f"""
update tasks
set embedding = vector_embedding({MODEL_NAME} using task_statement as data)
where task_statement is not null
"""

# Exact DDL from src/navigator/db/sql/007_indexes.sql.
CREATE_INDEX_SQL = """
create vector index tasks_embedding_vix on tasks (embedding)
organization inmemory neighbor graph
distance cosine
with target accuracy 95
"""

ACCURACY_SQL = f"""
begin
  :report := dbms_vector.index_accuracy_query(
    owner_name => user, index_name => '{INDEX_NAME}',
    qv => :qv, top_k => 10, target_accuracy => 95);
end;
"""


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    pool = create_pool(get_settings())
    with pool.acquire() as connection:
        cursor = connection.cursor()
        # Call timeout lives on the connection in python-oracledb; a cursor
        # attribute of the same name is silently ignored by the driver.
        connection.call_timeout = 900_000

        started = time.monotonic()
        cursor.execute(EMBED_SQL)
        print(f"embeddings generated for {cursor.rowcount} tasks "
              f"in {time.monotonic() - started:.1f}s")
        connection.commit()

        cursor.execute(
            "select count(*) from user_indexes where index_name = :name",
            name=INDEX_NAME,
        )
        exists = cursor.fetchone()[0] > 0
        started = time.monotonic()
        if exists:
            cursor.execute("drop index tasks_embedding_vix")
        cursor.execute(CREATE_INDEX_SQL)
        print(f"{INDEX_NAME} {'rebuilt' if exists else 'built'} "
              f"in {time.monotonic() - started:.1f}s")

        cursor.execute(
            f"select vector_embedding({MODEL_NAME} using :q as data) from dual",
            q=SAMPLE_QUERY,
        )
        query_vector = cursor.fetchone()[0]
        report = cursor.var(str)
        cursor.execute(ACCURACY_SQL, report=report, qv=query_vector)
        print(f"accuracy report (sample query: {SAMPLE_QUERY!r}):")
        print(report.getvalue())

        cursor.execute("select count(*) from tasks where embedding is null")
        print(f"tasks still without embedding: {cursor.fetchone()[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
