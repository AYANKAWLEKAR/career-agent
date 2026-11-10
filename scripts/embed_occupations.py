"""Compose embedding_source and generate occupation embeddings (design section 3).

Composition per occupation:

    title
    + description
    + top 10 task statements by importance
    + top 15 element names by importance (across element types combined)

Posting text is part of the section 3 composition but no postings are ingested yet;
when they are, they go last because they are the least trustworthy component.

Task importance comes from the raw ``task_ratings`` table (scale IM) because the
curated ``tasks.importance`` is not populated; tasks without ratings keep their
statement order. Ordering by importance matters because all-MiniLM-L12-v2 truncates
at 512 tokens -- the most diagnostic content must survive truncation.

Embedding generation itself is one set-based statement running the in-database
ONNX model, per the phase plan. Load the model first (scripts/load_onnx_model.py).

Usage:
    uv run python scripts/embed_occupations.py
"""

from __future__ import annotations

import argparse
import time

from navigator.config import get_settings
from navigator.db.pool import create_pool

TOP_TASKS = 10
TOP_ELEMENTS = 15
MODEL_NAME = "ALL_MINILM_L12_V2"

COMPOSE_SQL = f"""
update occupations o set embedding_source =
  o.title || chr(10) || o.description
  || (select coalesce(chr(10) || listagg(stmt, chr(10)), '')
      from (select cast(substr(ts.task, 1, 500) as varchar2(2000)) as stmt,
                   row_number() over (
                     order by r.data_value desc nulls last, ts.task_id) as rn
            from task_statements ts
            left join task_ratings r
              on r.onetsoc_code = ts.onetsoc_code
             and r.task_id = ts.task_id
             and r.scale_id = 'IM'
            where ts.onetsoc_code = o.soc_code)
      where rn <= {TOP_TASKS})
  || (select coalesce(chr(10) || listagg(element_name, ', '), '')
      from (select e.element_name,
                   row_number() over (
                     order by s.importance desc nulls last) as rn
            from occupation_skills s
            join skill_elements e on e.element_id = s.element_id
            where s.occupation_id = o.occupation_id)
      where rn <= {TOP_ELEMENTS})
"""

EMBED_SQL = f"""
update occupations
set embedding = vector_embedding({MODEL_NAME} using embedding_source as data)
where embedding_source is not null
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
        cursor.execute(COMPOSE_SQL)
        print(f"embedding_source composed for {cursor.rowcount} occupations")

        cursor.execute(EMBED_SQL)
        print(f"embeddings generated for {cursor.rowcount} occupations "
              f"in {time.monotonic() - started:.1f}s total")
        connection.commit()

        cursor.execute(
            "select count(*) from occupations where embedding is null"
        )
        missing = cursor.fetchone()[0]
        print(f"occupations still without embedding: {missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
