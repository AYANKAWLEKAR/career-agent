"""Load O*NET tools, education, and experience into the requirement vocabulary.

The source O*NET 30.3 tables must already be loaded and migrations through 013
must be applied.  Each section is its own transaction and is idempotent by source,
so a failed or interrupted run can safely be repeated.
"""

from __future__ import annotations

from collections.abc import Callable

from navigator.config import get_settings
from navigator.db.pool import create_pool

TOOL_SOURCE = "onet_software_hot"
EDUCATION_SOURCE = "onet_education_rl"
EXPERIENCE_SOURCE = "onet_experience_rw"

_RL_ZONE_CASE = """
case ec.category
  when 1 then 1
  when 2 then 2
  when 3 then 3 when 4 then 3 when 5 then 3
  when 6 then 4 when 7 then 4
  when 8 then 5 when 9 then 5 when 10 then 5 when 11 then 5 when 12 then 5
end
"""

_RW_YEARS_CASE = """
case tc.category
  when 1 then 0 when 2 then 0
  when 3 then 0.0833333333
  when 4 then 0.25
  when 5 then 0.5
  when 6 then 1
  when 7 then 2
  when 8 then 4
  when 9 then 6
  when 10 then 8
  when 11 then 10
end
"""


def _log(message: str) -> None:
    print(message, flush=True)


def load_tools(cursor) -> None:
    """Load only O*NET hot technologies as concrete tool requirements."""
    cursor.execute("delete from occupation_requirements where source = :source", source=TOOL_SOURCE)
    cursor.execute(
        """
        merge into requirement_items target
        using (
          select min(workplace_example) as canonical_text, normalized_key
          from (
            select workplace_example,
                   regexp_replace(lower(trim(workplace_example)), '[[:space:]]+', ' ')
                     as normalized_key
            from software_skills
            where hot_technology = 'Y' and workplace_example is not null
          )
          group by normalized_key
        ) source
        on (target.requirement_type = 'tool'
            and target.normalized_key = source.normalized_key)
        when not matched then insert
          (requirement_type, canonical_text, normalized_key)
        values ('tool', source.canonical_text, source.normalized_key)
        """
    )
    cursor.execute(
        """
        insert into occupation_requirements
          (occupation_id, requirement_id, importance, frequency_pct,
           alternative_group, source)
        select distinct o.occupation_id, ri.requirement_id, null, null, null, :source
        from software_skills ss
        join occupations o on o.soc_code = ss.onetsoc_code
        join requirement_items ri
          on ri.requirement_type = 'tool'
         and ri.normalized_key =
             regexp_replace(lower(trim(ss.workplace_example)), '[[:space:]]+', ' ')
        where ss.hot_technology = 'Y'
          and not exists (
            select 1 from occupation_requirements existing
            where existing.occupation_id = o.occupation_id
              and existing.requirement_id = ri.requirement_id
          )
        """,
        source=TOOL_SOURCE,
    )


def load_education(cursor) -> None:
    """Load RL alternatives occurring for at least ten percent of incumbents."""
    cursor.execute(
        "delete from occupation_requirements where source = :source", source=EDUCATION_SOURCE
    )
    cursor.execute(
        f"""
        merge into requirement_items target
        using (
          select ec.category_description as canonical_text,
                 'rl-' || to_char(ec.category) as normalized_key,
                 {_RL_ZONE_CASE} as ordinal_value
          from education_categories ec
          where ec.scale_id = 'RL'
        ) source
        on (target.requirement_type = 'education_level'
            and target.normalized_key = source.normalized_key)
        when matched then update set
          target.canonical_text = source.canonical_text,
          target.ordinal_value = source.ordinal_value,
          target.updated_at = systimestamp
        when not matched then insert
          (requirement_type, canonical_text, normalized_key, ordinal_value)
        values
          ('education_level', source.canonical_text, source.normalized_key,
           source.ordinal_value)
        """  # noqa: S608 - expressions are module constants, not user input
    )
    cursor.execute(
        """
        insert into occupation_requirements
          (occupation_id, requirement_id, importance, frequency_pct,
           alternative_group, source)
        select o.occupation_id, ri.requirement_id, null, e.data_value, 1, :source
        from education e
        join occupations o on o.soc_code = e.onetsoc_code
        join requirement_items ri
          on ri.requirement_type = 'education_level'
         and ri.normalized_key = 'rl-' || to_char(e.category)
        where e.scale_id = 'RL'
          and e.data_value >= 10
          and not exists (
            select 1 from occupation_requirements existing
            where existing.occupation_id = o.occupation_id
              and existing.requirement_id = ri.requirement_id
          )
        """,
        source=EDUCATION_SOURCE,
    )


def load_experience(cursor) -> None:
    """Load one cumulative-median RW experience threshold per occupation."""
    cursor.execute(
        "delete from occupation_requirements where source = :source", source=EXPERIENCE_SOURCE
    )
    cursor.execute(
        f"""
        merge into requirement_items target
        using (
          select tc.category_description as canonical_text,
                 'rw-' || to_char(tc.category) as normalized_key,
                 {_RW_YEARS_CASE} as ordinal_value
          from training_and_experience_categories tc
          where tc.scale_id = 'RW'
        ) source
        on (target.requirement_type = 'experience'
            and target.normalized_key = source.normalized_key)
        when matched then update set
          target.canonical_text = source.canonical_text,
          target.ordinal_value = source.ordinal_value,
          target.updated_at = systimestamp
        when not matched then insert
          (requirement_type, canonical_text, normalized_key, ordinal_value)
        values
          ('experience', source.canonical_text, source.normalized_key,
           source.ordinal_value)
        """  # noqa: S608 - expressions are module constants, not user input
    )
    cursor.execute(
        """
        insert into occupation_requirements
          (occupation_id, requirement_id, importance, frequency_pct,
           alternative_group, source)
        with distribution as (
          select te.onetsoc_code,
                 te.category,
                 te.data_value,
                 sum(te.data_value) over (
                   partition by te.onetsoc_code order by te.category
                   rows between unbounded preceding and current row
                 ) as cumulative_pct
          from training_and_experience te
          where te.scale_id = 'RW'
        ),
        median_candidates as (
          select d.*,
                 row_number() over (
                   partition by d.onetsoc_code order by d.category
                 ) as candidate_rank
          from distribution d
          where d.cumulative_pct >= 50
        )
        select o.occupation_id, ri.requirement_id, null, m.data_value, null, :source
        from median_candidates m
        join occupations o on o.soc_code = m.onetsoc_code
        join requirement_items ri
          on ri.requirement_type = 'experience'
         and ri.normalized_key = 'rw-' || to_char(m.category)
        where m.candidate_rank = 1
          and not exists (
            select 1 from occupation_requirements existing
            where existing.occupation_id = o.occupation_id
              and existing.requirement_id = ri.requirement_id
          )
        """,
        source=EXPERIENCE_SOURCE,
    )


def _run_section(connection, name: str, loader: Callable[[object], None]) -> None:
    cursor = connection.cursor()
    try:
        loader(cursor)
        connection.commit()
    except Exception:  # noqa: BLE001 - Oracle driver exceptions vary by version
        connection.rollback()
        raise
    _log(f"  {name}: committed")


def report_counts(cursor) -> dict[str, int]:
    """Return and print the live post-load verification metrics."""
    cursor.execute(
        """
        select requirement_type, count(*)
        from requirement_items
        where requirement_type in ('tool', 'education_level', 'experience')
        group by requirement_type
        """
    )
    counts = {str(row[0]): int(row[1]) for row in cursor.fetchall()}
    cursor.execute(
        """
        select count(*)
        from occupations o
        where o.job_zone is not null
          and exists (
            select 1 from occupation_skills os where os.occupation_id = o.occupation_id
          )
          and not exists (
            select 1
            from occupation_requirements orq
            join requirement_items ri on ri.requirement_id = orq.requirement_id
            where orq.occupation_id = o.occupation_id and ri.requirement_type = 'tool'
          )
        """
    )
    counts["traversable_without_tools"] = int(cursor.fetchone()[0])
    for key in ("tool", "education_level", "experience", "traversable_without_tools"):
        _log(f"  {key}: {counts.get(key, 0)}")
    return counts


def main() -> int:
    pool = create_pool(get_settings())
    with pool.acquire() as connection:
        _log("Loading requirement sections:")
        _run_section(connection, "tools", load_tools)
        _run_section(connection, "education", load_education)
        _run_section(connection, "experience", load_experience)
        _log("Verification:")
        counts = report_counts(connection.cursor())

    if counts.get("education_level") != 12 or counts.get("experience") != 11:
        raise RuntimeError("requirement category seeds are incomplete")
    if counts["traversable_without_tools"]:
        raise RuntimeError("one or more traversable occupations has no tool requirement")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
