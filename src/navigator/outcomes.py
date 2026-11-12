"""Degree outcomes from the College Scorecard (Stage 2).

Everything here is a plain SQL query over data already inside the database:

* ``match_majors`` resolves a free-text major ("B.A. Applied Mathematics") to a
  4-digit CIP by in-database vector similarity over embedded CIP titles;
* ``program_outcome`` summarizes a CIP+credential across institutions;
* ``feeder_cips`` names the fields of study that feed an occupation (NCES
  CIP-SOC crosswalk), ranked by graduates;
* ``resolve_anchor`` + ``nearby_programs`` answer "which programs are near me",
  using Oracle Spatial over institution coordinates.

Earnings are federal tax-record medians for graduates working and not
enrolled; the Scorecard suppresses small cells, so published figures are a
subset of programs and the helpers report that count rather than imply more.
"""

from __future__ import annotations

import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from navigator.llm import _log
from navigator.programs import BACHELORS, DOCTORAL, MASTERS

MODEL_NAME = "ALL_MINILM_L12_V2"
# Below this a free-text major has no real CIP counterpart; dropping the match
# is better than attaching someone's degree to an unrelated field's earnings.
MIN_MAJOR_SIMILARITY = 0.55
GRADUATE_LEVELS = (MASTERS, DOCTORAL)

_DOCTORAL_RE = re.compile(r"ph\.?\s?d|doctor|d\.phil")
_MASTERS_RE = re.compile(r"master|mba|\bm\.?[sab]\b|\bm\.?eng")
_CITY_STATE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z.' \-]*?)\s*,\s*([A-Za-z]{2})(?![A-Za-z])")


@dataclass
class MajorMatch:
    text: str
    cip_code: str
    title: str
    similarity: float


@dataclass
class ProgramOutcome:
    cip_code: str
    title: str
    credential_level: str
    n_programs: int
    n_priced: int
    e1: float | None
    e4_p25: float | None
    e4: float | None
    e4_p75: float | None
    e7: float | None
    debt: float | None


@dataclass
class Feeder:
    cip_code: str
    title: str
    n_programs: int
    awards: int


@dataclass
class Anchor:
    label: str
    lat: float
    lon: float


@dataclass
class NearbyProgram:
    institution: str
    city: str | None
    state: str | None
    credential_level: str
    cip_code: str
    program_name: str
    distance_mi: float | None
    e1: float | None
    e4: float | None
    e7: float | None
    debt: float | None


def credential_for(education_level: str | None) -> str:
    """The credential a stated education level corresponds to (default bachelor's)."""
    text = (education_level or "").lower()
    if _DOCTORAL_RE.search(text):
        return DOCTORAL
    if _MASTERS_RE.search(text):
        return MASTERS
    return BACHELORS


def parse_city_state(text: str | None) -> tuple[str, str] | None:
    """"Berkeley, CA" -> ("Berkeley", "CA"); anything without both parts -> None."""
    if not text:
        return None
    match = _CITY_STATE_RE.match(text)
    if not match:
        return None
    return match.group(1).strip(), match.group(2).upper()


def _in_binds(prefix: str, values: Sequence[Any]) -> tuple[str, dict[str, Any]]:
    binds = {f"{prefix}{i}": value for i, value in enumerate(values)}
    return ", ".join(f":{name}" for name in binds), binds


def match_majors(
    pool, majors: Sequence[str], min_similarity: float = MIN_MAJOR_SIMILARITY,
    thread_id: str | None = None,
) -> list[MajorMatch]:
    """Resolve free-text majors to CIPs by in-database vector similarity.

    One APPROX top-1 probe per major (no WHERE clause: a filter would
    disqualify the HNSW index). Matches below ``min_similarity`` are dropped;
    when two majors land on one CIP the stronger match is kept.
    """
    texts = [m.strip() for m in majors if m and m.strip()]
    if not texts:
        return []
    started = time.monotonic()
    best: dict[str, MajorMatch] = {}
    with pool.acquire() as connection:
        cursor = connection.cursor()
        for text in texts:
            cursor.execute(
                f"""
                select cip_code, title,
                       1 - vector_distance(embedding,
                             vector_embedding({MODEL_NAME} using :q as data), cosine) as sim
                from cip_codes
                order by vector_distance(embedding,
                           vector_embedding({MODEL_NAME} using :q as data), cosine)
                fetch approx first 1 rows only
                """,  # noqa: S608
                q=text,
            )
            for cip, title, sim in cursor.fetchall():
                if sim is None or float(sim) < min_similarity:
                    continue
                current = best.get(cip)
                if current is None or float(sim) > current.similarity:
                    best[cip] = MajorMatch(text, cip, title, round(float(sim), 4))
    matches = sorted(best.values(), key=lambda m: -m.similarity)
    if thread_id is not None:
        _log(pool, "relational_query_programs", thread_id,
             {"op": "match_majors", "majors": texts},
             {"matches": [{"cip": m.cip_code, "title": m.title, "sim": m.similarity}
                          for m in matches]},
             (time.monotonic() - started) * 1000, MODEL_NAME)
    return matches


def program_outcome(
    pool, cip_code: str, title: str, credential_level: str
) -> ProgramOutcome | None:
    """Median-of-institution-medians outcomes for one CIP at one credential level."""
    with pool.acquire() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            select count(*), count(median_earnings_4yr),
                   percentile_cont(0.5) within group (order by median_earnings_1yr),
                   percentile_cont(0.25) within group (order by median_earnings_4yr),
                   percentile_cont(0.5) within group (order by median_earnings_4yr),
                   percentile_cont(0.75) within group (order by median_earnings_4yr),
                   percentile_cont(0.5) within group (order by median_earnings_7yr),
                   percentile_cont(0.5) within group (order by median_debt)
            from programs
            where cip_code = :cip and credential_level = :lvl
            """,
            cip=cip_code, lvl=credential_level,
        )
        n, n_priced, e1, p25, e4, p75, e7, debt = cursor.fetchone()
    if not n or all(v is None for v in (e1, e4, e7)):
        return None
    return ProgramOutcome(cip_code, title, credential_level, int(n), int(n_priced),
                          e1, p25, e4, p75, e7, debt)


def feeder_cips(
    pool, occupation_id: int, credential_levels: Sequence[str], limit: int = 3
) -> list[Feeder]:
    """Fields of study that feed an occupation at the given credential levels.

    The NCES crosswalk is many-to-many and unweighted ("Ecology" maps to
    Statisticians), so candidates are ordered by reciprocal-rank fusion of two
    signals: in-database vector similarity between the occupation's embedding
    and the CIP title (specificity), and graduate counts (how many people
    actually take that route). Either signal alone misleads: awards alone
    surfaces broad fields, similarity alone drops Computer Science for
    Software Developers.
    """
    marks, binds = _in_binds("l", credential_levels)
    with pool.acquire() as connection:
        cursor = connection.cursor()
        cursor.execute(
            f"""
            with agg as (
              select p.cip_code, count(*) as n, nvl(sum(p.awards), 0) as awards
              from program_occupations po
              join programs p on p.cip_code = po.cip_code
              where po.occupation_id = :oid and p.credential_level in ({marks})
              group by p.cip_code
            ), cand as (
              select a.cip_code, c.title, a.n, a.awards,
                     1 - vector_distance(o.embedding, c.embedding, cosine) as sim
              from agg a
              join cip_codes c on c.cip_code = a.cip_code
              join occupations o on o.occupation_id = :oid
            ), ranked as (
              select cand.*, rank() over (order by sim desc nulls last) as rank_sim,
                     rank() over (order by awards desc) as rank_awards
              from cand
            )
            select cip_code, title, n, awards from ranked
            order by 1 / (10 + rank_sim) + 1 / (10 + rank_awards) desc, awards desc
            fetch first :n rows only
            """,  # noqa: S608
            oid=occupation_id, n=limit, **binds,
        )
        return [Feeder(cip, title, int(n), int(awards))
                for cip, title, n, awards in cursor.fetchall()]


def resolve_anchor(pool, location_text: str | None) -> Anchor | None:
    """Centroid of a city's institutions, from our own table (no external geocoder)."""
    parsed = parse_city_state(location_text)
    if parsed is None:
        return None
    city, state = parsed
    with pool.acquire() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            select avg(i.location.sdo_point.x), avg(i.location.sdo_point.y), count(*)
            from institutions i
            where upper(i.city) = upper(:city) and i.state = :state
              and i.location is not null
            """,
            city=city, state=state,
        )
        lon, lat, count = cursor.fetchone()
    if not count or lon is None or lat is None:
        return None
    return Anchor(f"{city}, {state}", float(lat), float(lon))


def nearby_programs(
    pool, cip_codes: Sequence[str], credential_levels: Sequence[str],
    anchor: Anchor, radius_miles: float, limit: int = 5,
    thread_id: str | None = None,
) -> list[NearbyProgram]:
    """Programs in the given CIPs within a radius, nearest first (Oracle Spatial)."""
    if not cip_codes:
        return []
    started = time.monotonic()
    cip_marks, cip_binds = _in_binds("c", cip_codes)
    level_marks, level_binds = _in_binds("l", credential_levels)
    radius = int(radius_miles)
    point = "sdo_geometry(2001, 4326, sdo_point_type(:lon, :lat, null), null, null)"
    with pool.acquire() as connection:
        cursor = connection.cursor()
        cursor.execute(
            f"""
            select i.name, i.city, i.state, p.credential_level, p.cip_code,
                   p.program_name,
                   round(sdo_geom.sdo_distance(i.location, {point}, 0.05, 'unit=mile'), 1),
                   p.median_earnings_1yr, p.median_earnings_4yr,
                   p.median_earnings_7yr, p.median_debt
            from institutions i
            join programs p on p.institution_id = i.institution_id
            where p.cip_code in ({cip_marks})
              and p.credential_level in ({level_marks})
              and sdo_within_distance(i.location, {point},
                                      'distance={radius} unit=mile') = 'TRUE'
            order by 7, p.median_earnings_4yr desc nulls last
            fetch first :n rows only
            """,  # noqa: S608
            lat=anchor.lat, lon=anchor.lon, n=limit, **cip_binds, **level_binds,
        )
        found = [
            NearbyProgram(name, city, state, level, cip, program, float(dist),
                          e1, e4, e7, debt)
            for name, city, state, level, cip, program, dist, e1, e4, e7, debt
            in cursor.fetchall()
        ]
    if thread_id is not None:
        _log(pool, "spatial_query_metros", thread_id,
             {"op": "nearby_programs", "near": anchor.label, "radius_miles": radius,
              "cips": list(cip_codes)},
             {"programs": len(found)}, (time.monotonic() - started) * 1000, "sdo_spatial")
    return found


def top_programs(
    pool, cip_codes: Sequence[str], credential_levels: Sequence[str], limit: int = 3,
) -> list[NearbyProgram]:
    """The best-paid programs nationally (by published 4-year earnings) in given CIPs."""
    if not cip_codes:
        return []
    cip_marks, cip_binds = _in_binds("c", cip_codes)
    level_marks, level_binds = _in_binds("l", credential_levels)
    with pool.acquire() as connection:
        cursor = connection.cursor()
        cursor.execute(
            f"""
            select i.name, i.city, i.state, p.credential_level, p.cip_code,
                   p.program_name, null, p.median_earnings_1yr, p.median_earnings_4yr,
                   p.median_earnings_7yr, p.median_debt
            from institutions i
            join programs p on p.institution_id = i.institution_id
            where p.cip_code in ({cip_marks}) and p.credential_level in ({level_marks})
              and p.median_earnings_4yr is not null
            order by p.median_earnings_4yr desc
            fetch first :n rows only
            """,  # noqa: S608
            n=limit, **cip_binds, **level_binds,
        )
        return [
            NearbyProgram(name, city, state, level, cip, program, None, e1, e4, e7, debt)
            for name, city, state, level, cip, program, _dist, e1, e4, e7, debt
            in cursor.fetchall()
        ]


def _k(value: float) -> str:
    return f"${value / 1000:.1f}k"


def _horizons(e1: float | None, e4: float | None, e7: float | None) -> str:
    return " · ".join(f"{_k(v)} @{label}"
                      for v, label in ((e1, "1yr"), (e4, "4yr"), (e7, "7yr"))
                      if v is not None)


def format_outcomes_report(
    majors: Sequence[tuple[MajorMatch, ProgramOutcome | None]],
    aspiration: str | None,
    feeders: Sequence[Feeder],
    nearby: Sequence[NearbyProgram],
    top: Sequence[NearbyProgram],
    anchor: Anchor | None,
    radius_miles: float,
    graduate: bool = True,
) -> list[str]:
    """Terminal lines for the degree-outcomes section; empty when there is nothing to say."""
    lines: list[str] = []
    priced = [(m, o) for m, o in majors if o is not None]
    if priced:
        lines.append("DEGREE OUTCOMES (College Scorecard; major matched in-database by "
                     "vector similarity)")
        for match, outcome in priced:
            lines.append(f'  "{match.text}" -> {match.title} (CIP {match.cip_code}, '
                         f"sim {match.similarity:.3f}) · {outcome.credential_level}")
            parts = [_horizons(outcome.e1, outcome.e4, outcome.e7)]
            if outcome.e4 is not None and outcome.e4_p25 and outcome.e4_p75:
                parts.append(f"4yr middle half {_k(outcome.e4_p25)}-{_k(outcome.e4_p75)}")
            if outcome.debt is not None:
                parts.append(f"typical debt {_k(outcome.debt)}")
            lines.append("      " + " · ".join(p for p in parts if p))
            note = (f"4yr earnings published for {outcome.n_priced} of "
                    f"{outcome.n_programs} programs")
            if outcome.e4 is None:
                note += "; 4yr/7yr not yet published for this field"
            lines.append(f"      ({note})")
    if aspiration and feeders:
        if lines:
            lines.append("")
        kind = "graduate fields" if graduate else "fields"
        lines.append(f"TO REACH {aspiration}: {kind} that feed it: "
                     + ", ".join(f.title for f in feeders))
        where = f"within {int(radius_miles)} mi of {anchor.label}" if anchor else "nearby"
        if nearby:
            lines.append(f"  {where} ({len(nearby)} program"
                         f"{'s' if len(nearby) != 1 else ''} with published outcomes):")
            for p in nearby:
                dist = f"{p.distance_mi:.1f} mi" if p.distance_mi is not None else ""
                pay = _horizons(p.e1, p.e4, p.e7) or "earnings n/a"
                lines.append(f"    {dist:>8}  {p.institution} — {p.program_name} "
                             f"({p.credential_level})  {pay}")
        elif anchor:
            lines.append(f"  {where}: no programs with published outcomes in these fields")
        if top:
            lines.append("  best-paid nationally:")
            for p in top:
                lines.append(f"    {p.institution} — {p.program_name} "
                             f"({p.credential_level})  {_horizons(p.e1, p.e4, p.e7)}")
    return lines


@dataclass
class OutcomeReport:
    lines: list[str]
    context: dict[str, Any] | None


def gather_outcomes(
    pool, majors: Sequence[str], education_level: str | None, *,
    aspiration_title: str | None, aspiration_id: int | None,
    aspiration_zone: int | None, location_text: str | None,
    near: str | None = None, radius_miles: float = 100,
    thread_id: str | None = None,
) -> OutcomeReport:
    """Everything the demos show and the narration may cite about degree outcomes.

    The student's own majors resolve to CIPs and get national outcomes at their
    credential level. A zone-5 aspiration (which needs graduate preparation)
    adds the graduate fields that feed it, programs near the student, and the
    best-paid programs nationally; lower-zone aspirations use bachelor's fields.
    """
    credential = credential_for(education_level)
    matches = match_majors(pool, majors, thread_id=thread_id)
    major_outcomes = [(m, program_outcome(pool, m.cip_code, m.title, credential))
                      for m in matches]
    anchor = resolve_anchor(pool, near or location_text)
    feeders: list[Feeder] = []
    nearby: list[NearbyProgram] = []
    top: list[NearbyProgram] = []
    graduate = aspiration_zone is not None and aspiration_zone >= 5
    if aspiration_id is not None:
        levels = GRADUATE_LEVELS if graduate else (BACHELORS,)
        feeders = feeder_cips(pool, aspiration_id, levels, 4)
        cips = [f.cip_code for f in feeders]
        if anchor is not None:
            nearby = nearby_programs(pool, cips, levels, anchor, radius_miles, 5,
                                     thread_id=thread_id)
        top = top_programs(pool, cips, levels, 3)
    lines = format_outcomes_report(major_outcomes, aspiration_title, feeders, nearby, top,
                                   anchor, radius_miles, graduate=graduate)
    has_facts = any(o is not None for _, o in major_outcomes) or bool(feeders)
    context = (build_outcomes_context(major_outcomes, feeders, nearby, anchor, top)
               if has_facts else None)
    return OutcomeReport(lines, context)


def _money(value: float | None) -> int | None:
    return None if value is None else int(value + 0.5)


def build_outcomes_context(
    majors: Sequence[tuple[MajorMatch, ProgramOutcome | None]],
    feeders: Sequence[Feeder] | None,
    nearby: Sequence[NearbyProgram],
    anchor: Anchor | None,
    top: Sequence[NearbyProgram] = (),
) -> dict[str, Any]:
    """The degree-outcome facts the narration may cite; nothing else reaches it."""
    context: dict[str, Any] = {
        "your_majors": [
            {
                "stated_major": match.text,
                "matched_field": match.title,
                "credential": outcome.credential_level,
                "programs_with_published_earnings": outcome.n_priced,
                "median_earnings_1yr": _money(outcome.e1),
                "median_earnings_4yr": _money(outcome.e4),
                "median_earnings_7yr": _money(outcome.e7),
                "typical_debt": _money(outcome.debt),
            }
            for match, outcome in majors if outcome is not None
        ],
        "methodology": (
            "College Scorecard field-of-study data: median earnings of "
            "graduates (federal tax records, working and not enrolled) at 1, 4 "
            "and 7 years, summarized as the median across institutions' "
            "published program medians; programs with small cohorts are "
            "suppressed, so only published figures appear; earnings describe "
            "past graduates, not a guarantee"
        ),
    }
    if feeders:
        context["aspiration_feeder_fields"] = [f.title for f in feeders]
    if nearby:
        context["graduate_programs_nearby"] = {
            "near": anchor.label if anchor else None,
            "programs": [
                {
                    "institution": p.institution, "field": p.program_name,
                    "credential": p.credential_level,
                    "miles_away": round(p.distance_mi or 0.0, 1),
                    **({"median_earnings_4yr": _money(p.e4)} if p.e4 is not None else {}),
                }
                for p in nearby
            ],
        }
    if top:
        context["best_paid_graduate_programs_nationally"] = [
            {"institution": p.institution, "field": p.program_name,
             "credential": p.credential_level, "median_earnings_4yr": _money(p.e4)}
            for p in top
        ]
    return context
