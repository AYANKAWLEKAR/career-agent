"""Transform the raw O*NET staging tables into the curated schema and derive the graph.

Implements sections 1 (occupational universe), 4.1-4.4 (graph construction) of
docs/superpowers/specs/2026-08-17-path-surfacing-design.md.

The raw tables loaded by ``load_onet_sql.py`` mirror the vendor distribution. This
script projects the three in-scope verticals out of them into ``occupations``,
``skill_elements``, ``occupation_skills`` and ``tasks``, then derives
``occupation_edges``.

Every step is idempotent: curated rows are deleted and rebuilt, so the script can be
re-run after a threshold change without leaving stale edges behind.

Edge derivation departs from the spec in one place: similarity is mean-centered
before the cosine, and the thresholds are retuned to match. The reason and the
supporting measurements are recorded in NOTES.md and above ``LADDER_THRESHOLD``.

Usage:
    uv run python scripts/build_graph.py [--skip-transform] [--raw-cosine]
"""

from __future__ import annotations

import argparse
import time

from navigator.config import get_settings
from navigator.db.pool import create_pool

# Section 1: the occupational universe. SOC families, matched as prefixes.
VERTICALS = {
    "tech": ("15-1", "15-2"),
    "finance": ("13-2", "11-3031"),
    "research": ("19-",),
}
SOC_PREFIXES = tuple(p for prefixes in VERTICALS.values() for p in prefixes)

# Section 4.3: directional gating thresholds, retuned for centered cosine.
#
# The spec's 0.65/0.75 were set against raw importance cosine, which on this data
# runs 0.912-0.997 -- both thresholds fall below the observed minimum, so 85% of
# candidate pairs became edges. Centering moves the scale to -0.714..0.934, and
# these values come from the sweep in scripts/tune_thresholds.py:
#
#   * lateral is capped at 0.45 because at 0.50 occupations begin losing every
#     outgoing edge (3.7% isolated, rising to 13% at 0.55/0.60), and an isolated
#     occupation can never start a path;
#   * 0.30/0.45 then rejects 84% of candidate pairs, leaves a median out-degree of
#     9, and still leaves 75% of connected pairs needing two or three hops, which
#     is what makes multi-hop surfacing worth doing;
#   * lateral stays above ladder, per 4.3 -- a sideways move that does not raise
#     your level has to justify itself with stronger skill transfer.
LADDER_THRESHOLD = 0.30
LATERAL_THRESHOLD = 0.45

# The content-model domains that become skill_elements rows: raw table ->
# (element_type, importance scale, level scale).
#
# The first four are rated on IM (Importance, 1-5) and LV (Level, 0-7). Work styles
# are rated on WI ("Work Styles Impact", -3..3) with no level equivalent, so WI is
# mapped onto importance. Work styles are included because they are the only
# quantitative signal O*NET publishes for its eight newest in-scope occupations --
# Data Scientists and Financial and Investment Analysts among them -- which
# otherwise have no skill vector and therefore no derived edges at all.
SKILL_SOURCES = {
    "essential_skills": ("skill", "IM", "LV"),
    "abilities": ("ability", "IM", "LV"),
    "knowledge": ("knowledge", "IM", "LV"),
    "work_activities": ("work_activity", "IM", "LV"),
    "work_styles": ("work_style", "WI", None),
}

def _soc_filter(alias: str = "") -> str:
    """A parenthesized predicate restricting onetsoc_code to the in-scope verticals.

    Parenthesized because callers AND it with other conditions, and the body is a
    chain of ORs.
    """
    prefix = f"{alias}." if alias else ""
    return "(" + " or ".join(f"{prefix}onetsoc_code like '{p}%'" for p in SOC_PREFIXES) + ")"


def _log(message: str) -> None:
    print(message, flush=True)


def transform(cursor) -> None:
    """Project the in-scope verticals from the raw tables into the curated schema."""
    # Children first: occupation_edges and occupation_skills reference occupations.
    for table in ("occupation_edges", "occupation_skills", "tasks", "occupations"):
        cursor.execute(f"delete from {table}")  # noqa: S608
    cursor.execute("delete from skill_elements")

    cursor.execute(
        f"""
        insert into occupations (soc_code, title, description, job_zone)
        select o.onetsoc_code, o.title, o.description, z.job_zone
        from occupation_data o
        left join job_zones z on z.onetsoc_code = o.onetsoc_code
        where {_soc_filter("o")}
        """  # noqa: S608
    )
    _log(f"  occupations: {cursor.rowcount}")

    # skill_elements is the union of the four rating domains, described by the
    # content model. An element_id appears in exactly one domain.
    union = " union all ".join(
        f"select distinct element_id, '{etype}' as etype from {table} where {_soc_filter()}"
        for table, (etype, _, _) in SKILL_SOURCES.items()
    )
    cursor.execute(
        f"""
        insert into skill_elements (element_code, element_name, element_type, description)
        select u.element_id, c.element_name, u.etype, c.description
        from ({union}) u
        join content_model_reference c on c.element_id = u.element_id
        """  # noqa: S608
    )
    _log(f"  skill_elements: {cursor.rowcount}")

    # Importance and level arrive as separate rows on different scales; pivot them
    # onto one row per (occupation, element) pair.
    for table, (_, imp_scale, lvl_scale) in SKILL_SOURCES.items():
        level_expr = (
            "max(case when r.scale_id = :lvl then r.data_value end)" if lvl_scale else "null"
        )
        binds = {"imp": imp_scale}
        scales = ":imp"
        if lvl_scale:
            binds["lvl"] = lvl_scale
            scales = ":imp, :lvl"
        cursor.execute(
            f"""
            insert into occupation_skills (occupation_id, element_id, importance, level_value)
            select o.occupation_id,
                   e.element_id,
                   max(case when r.scale_id = :imp then r.data_value end),
                   {level_expr}
            from {table} r
            join occupations o on o.soc_code = r.onetsoc_code
            join skill_elements e on e.element_code = r.element_id
            where r.scale_id in ({scales})
            group by o.occupation_id, e.element_id
            """,  # noqa: S608
            **binds,
        )
        _log(f"  occupation_skills from {table}: {cursor.rowcount}")

    cursor.execute(
        """
        insert into tasks (occupation_id, task_statement, task_type, importance)
        select o.occupation_id, t.task, t.task_type, null
        from task_statements t
        join occupations o on o.soc_code = t.onetsoc_code
        """
    )
    _log(f"  tasks: {cursor.rowcount}")


def load_related_edges(cursor) -> None:
    """Section 4.1: `related` edges, loaded verbatim from the published pairs."""
    cursor.execute("delete from occupation_edges where source = 'onet_related'")
    cursor.execute(
        """
        insert into occupation_edges (src_occupation_id, dst_occupation_id, edge_type,
                                      weight, source)
        select src.occupation_id, dst.occupation_id, 'related',
               1.0 / r.related_index, 'onet_related'
        from related_occupations r
        join occupations src on src.soc_code = r.onetsoc_code
        join occupations dst on dst.soc_code = r.related_onetsoc_code
        where r.related_index is not null and r.related_index > 0
        """
    )
    _log(f"  related edges: {cursor.rowcount}")


# Raw importance cosine over this dataset ranges 0.912-0.997 (see NOTES.md), which is
# entirely above both thresholds and yields an 85%-dense graph. Centering each element
# on its cross-occupation mean removes the universally-high elements that cause it.
_RAW_VECTOR = """
        vec as (
          select occupation_id, element_id, importance as v
          from occupation_skills
          where importance is not null
        ),"""

_CENTERED_VECTOR = """
        mu as (
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
        ),"""


def derive_edges(cursor, centered: bool = False) -> None:
    """Sections 4.2-4.4: exact all-pairs skill cosine, gated by job zone.

    Wage penalty is applied from OEWS national medians (``metro_id is null``).
    Where no median exists for either endpoint the penalty is 1.0, so edges build
    correctly whether or not BLS data is loaded.

    ``centered`` (the default) uses importance minus each element's
    cross-occupation mean. The spec specifies raw importance, but raw does not
    discriminate on this data -- see the threshold comment above and NOTES.md.
    """
    cursor.execute("delete from occupation_edges where source like 'derived_%'")
    source = "derived_centered_cosine" if centered else "derived_skill_cosine"
    cursor.execute(
        f"""
        insert into occupation_edges (src_occupation_id, dst_occupation_id, edge_type,
                                      weight, source)
        with {_CENTERED_VECTOR if centered else _RAW_VECTOR}
        dots as (
          -- Norms are computed inside the pair join, so each vector is normalized
          -- over the elements the two occupations share rather than its own full
          -- length. Section 4.2 specifies full-length norms; that is deliberately
          -- departed from here because O*NET rates the eight newest occupations on
          -- work styles only. Dividing a 21-element dot product by a 157-element
          -- norm understates their similarity by a factor of roughly sqrt(21/157),
          -- which is a measurement gap being scored as dissimilarity. For every
          -- pair whose element sets match -- all 107 fully rated occupations --
          -- the shared subspace is the full vector and the two formulations are
          -- numerically identical, so this changes nothing for them.
          select a.occupation_id as src_id,
                 b.occupation_id as dst_id,
                 sum(a.v * b.v) as dot,
                 sqrt(sum(a.v * a.v)) as nrm_src,
                 sqrt(sum(b.v * b.v)) as nrm_dst
          from vec a
          join vec b on b.element_id = a.element_id
                    and b.occupation_id <> a.occupation_id
          group by a.occupation_id, b.occupation_id
        ),
        cosine as (
          select src_id, dst_id, dot / (nrm_src * nrm_dst) as skill_cosine
          from dots
          where nrm_src > 0 and nrm_dst > 0
        ),
        gated as (
          select c.src_id, c.dst_id, c.skill_cosine,
                 case when b.job_zone = a.job_zone + 1 then 'career_ladder'
                      when b.job_zone = a.job_zone     then 'skill_overlap'
                 end as edge_type
          from cosine c
          join occupations a on a.occupation_id = c.src_id
          join occupations b on b.occupation_id = c.dst_id
          where a.job_zone is not null and b.job_zone is not null
            and b.job_zone between a.job_zone and a.job_zone + 1
        ),
        priced as (
          select g.*,
                 (select w.wage_median from metro_wages w
                   where w.occupation_id = g.src_id and w.metro_id is null
                     and rownum = 1) as src_wage,
                 (select w.wage_median from metro_wages w
                   where w.occupation_id = g.dst_id and w.metro_id is null
                     and rownum = 1) as dst_wage
          from gated g
          where g.edge_type is not null
            and ((g.edge_type = 'career_ladder' and g.skill_cosine >= :ladder)
              or (g.edge_type = 'skill_overlap' and g.skill_cosine >= :lateral))
        )
        select src_id, dst_id, edge_type,
               skill_cosine * case
                 when src_wage is null or dst_wage is null or src_wage = 0 then 1.0
                 when dst_wage / src_wage >= 1.0 then 1.0
                 else 0.5 + 0.5 * (dst_wage / src_wage)
               end,
               :src_label
        from priced
        """,
        src_label=source,
        ladder=LADDER_THRESHOLD,
        lateral=LATERAL_THRESHOLD,
    )
    _log(f"  derived edges: {cursor.rowcount}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-transform", action="store_true", help="rebuild edges only"
    )
    parser.add_argument(
        "--raw-cosine",
        action="store_true",
        help="use the spec's uncentered importance cosine (produces an 85%%-dense graph)",
    )
    args = parser.parse_args(argv)

    pool = create_pool(get_settings())
    started = time.monotonic()
    with pool.acquire() as connection:
        cursor = connection.cursor()
        if not args.skip_transform:
            _log("Transforming raw O*NET into the curated schema:")
            transform(cursor)
        _log("Building the graph:")
        load_related_edges(cursor)
        derive_edges(cursor, centered=not args.raw_cosine)
        connection.commit()
    _log(f"Done in {time.monotonic() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
