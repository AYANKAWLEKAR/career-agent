"""Populate the requirement vocabulary and per-occupation requirements.

Implements section 4 of the requirements ingestion design
(docs/superpowers/specs/2026-08-28-requirements-ingestion-design.md):

* Tools: hot-technology software_skills rows only (the full 3,192-tool list
  makes gap output unusable; the recorded consequence is that non-hot tools
  earn no coverage credit).
* Education: the 12 RL categories seed the vocabulary once; per occupation,
  every category holding >= 10% of incumbents becomes one alternative group.
* Experience: the 11 RW bands seed the vocabulary; per occupation, one
  threshold at the median band.

Idempotent by provenance: each run deletes occupation_requirements rows whose
source belongs to this loader and re-inserts. Vocabulary rows are upserted on
the (requirement_type, normalized_key) unique key and never deleted — the
vocabulary grows monotonically per section 6.3 of the parent design.

Usage:
    uv run python scripts/load_requirements.py
"""

from __future__ import annotations

import argparse
import time

from navigator.config import get_settings
from navigator.db.pool import create_pool
from navigator.requirements import (
    RL_TO_ZONE,
    RW_TO_YEARS,
    education_alternatives,
    median_band,
    normalize_key,
)

SOURCES = ("onet_software_hot", "onet_education_rl", "onet_experience_rw")


def _log(message: str) -> None:
    print(message, flush=True)


def _vocab_id(cursor, rtype: str, key: str, canonical: str,
              ordinal: float | None) -> int:
    """Upsert one vocabulary row; return its requirement_id."""
    cursor.execute(
        """
        select requirement_id from requirement_items
        where requirement_type = :t and normalized_key = :k
        """,
        t=rtype, k=key,
    )
    row = cursor.fetchone()
    if row is not None:
        # True upsert: refresh the display text and ordinal so a mapping change
        # (e.g. a re-tuned RL->zone table) propagates on re-run instead of the
        # stale values living forever behind the unique key.
        cursor.execute(
            """
            update requirement_items
            set canonical_text = :c, ordinal_value = :o, updated_at = systimestamp
            where requirement_id = :rid
            """,
            c=canonical, o=ordinal, rid=int(row[0]),
        )
        return int(row[0])
    id_var = cursor.var(int)
    cursor.execute(
        """
        insert into requirement_items
          (requirement_type, canonical_text, normalized_key, ordinal_value)
        values (:t, :c, :k, :o)
        returning requirement_id into :rid
        """,
        t=rtype, c=canonical, k=key, o=ordinal, rid=id_var,
    )
    return int(id_var.getvalue()[0])


def load_tools(cursor) -> tuple[int, int]:
    """Section 4.1: hot-technology tools for in-scope occupations."""
    cursor.execute(
        """
        select s.onetsoc_code, s.workplace_example
        from software_skills s
        where s.hot_technology = 'Y'
          and exists (select 1 from occupations o where o.soc_code = s.onetsoc_code)
        """
    )
    pairs = cursor.fetchall()
    cursor.execute("select soc_code, occupation_id from occupations")
    occupation_by_soc = {soc: int(oid) for soc, oid in cursor.fetchall()}

    vocab: dict[str, int] = {}
    rows: set[tuple[int, int]] = set()
    for soc, example in pairs:
        key = normalize_key(example)
        if key not in vocab:
            vocab[key] = _vocab_id(cursor, "tool", key, example.strip(), None)
        rows.add((occupation_by_soc[soc], vocab[key]))

    cursor.executemany(
        """
        insert into occupation_requirements
          (occupation_id, requirement_id, source)
        values (:1, :2, 'onet_software_hot')
        """,
        sorted(rows),
    )
    return len(vocab), len(rows)


def _distributions(cursor, table: str, scale: str) -> dict[int, dict[int, float]]:
    """occupation_id -> {category: percentage} for in-scope occupations."""
    cursor.execute(
        f"""
        select o.occupation_id, t.category, t.data_value
        from {table} t
        join occupations o on o.soc_code = t.onetsoc_code
        where t.scale_id = :s
        """,  # noqa: S608
        s=scale,
    )
    out: dict[int, dict[int, float]] = {}
    for oid, category, value in cursor.fetchall():
        out.setdefault(int(oid), {})[int(category)] = float(value)
    return out


def load_education(cursor) -> tuple[int, int]:
    """Section 4.2: RL alternatives at >= 10%, one alternative_group each."""
    cursor.execute(
        """
        select category, category_description from education_categories
        where scale_id = 'RL'
        """
    )
    vocab = {
        int(cat): _vocab_id(
            cursor, "education_level", f"rl-{int(cat)}", desc, RL_TO_ZONE[int(cat)]
        )
        for cat, desc in cursor.fetchall()
    }

    inserted = 0
    for oid, dist in sorted(_distributions(cursor, "education", "RL").items()):
        for category in education_alternatives(dist):
            cursor.execute(
                """
                insert into occupation_requirements
                  (occupation_id, requirement_id, frequency_pct,
                   alternative_group, source)
                values (:o, :r, :f, 1, 'onet_education_rl')
                """,
                o=oid, r=vocab[category], f=round(dist[category], 2),
            )
            inserted += 1
    return len(vocab), inserted


def load_experience(cursor) -> tuple[int, int]:
    """Section 4.3: one experience threshold per occupation at the median band."""
    cursor.execute(
        """
        select category, category_description from training_and_experience_categories
        where scale_id = 'RW'
        """
    )
    vocab = {
        int(cat): _vocab_id(
            cursor, "experience", f"rw-{int(cat)}", desc, RW_TO_YEARS[int(cat)]
        )
        for cat, desc in cursor.fetchall()
    }

    inserted = 0
    for oid, dist in sorted(
        _distributions(cursor, "training_and_experience", "RW").items()
    ):
        band = median_band(dist)
        if band is None:
            continue
        cursor.execute(
            """
            insert into occupation_requirements
              (occupation_id, requirement_id, frequency_pct, source)
            values (:o, :r, :f, 'onet_experience_rw')
            """,
            o=oid, r=vocab[band], f=round(dist.get(band, 0.0), 2),
        )
        inserted += 1
    return len(vocab), inserted


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    pool = create_pool(get_settings())
    started = time.monotonic()
    with pool.acquire() as connection:
        cursor = connection.cursor()
        for source in SOURCES:
            cursor.execute(
                "delete from occupation_requirements where source = :s", s=source
            )
        _log(f"cleared prior rows for sources {SOURCES}")

        n_vocab, n_rows = load_tools(cursor)
        _log(f"  tools: {n_vocab} vocabulary entries, {n_rows} occupation requirements")
        n_vocab, n_rows = load_education(cursor)
        _log(f"  education: {n_vocab} RL categories, {n_rows} alternative rows")
        n_vocab, n_rows = load_experience(cursor)
        _log(f"  experience: {n_vocab} RW bands, {n_rows} thresholds")
        connection.commit()

        cursor.execute(
            """
            select r.requirement_type, count(distinct r.requirement_id),
                   count(o.occupation_requirement_id)
            from requirement_items r
            left join occupation_requirements o on o.requirement_id = r.requirement_id
            group by r.requirement_type order by 1
            """
        )
        _log("verification (type, vocab, occupation rows):")
        for row in cursor.fetchall():
            _log(f"  {row}")
    _log(f"done in {time.monotonic() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
