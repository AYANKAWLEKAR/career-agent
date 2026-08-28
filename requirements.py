"""Requirement coverage and gap analysis.

The database stores one vocabulary shared by occupations and user profiles.  This
module keeps the decision logic independent from Oracle: :func:`coverage` only
fetches plain rows, while :func:`build_coverage_report` performs all matching and
threshold arithmetic in memory.  That separation makes the rules easy to test and
keeps a gap report explainable item by item.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

RL_TO_JOB_ZONE: dict[int, int] = {
    1: 1,
    2: 2,
    3: 3,
    4: 3,
    5: 3,
    6: 4,
    7: 4,
    8: 5,
    9: 5,
    10: 5,
    11: 5,
    12: 5,
}

RW_TO_YEARS: dict[int, float] = {
    1: 0.0,
    2: 0.0,
    3: 1 / 12,
    4: 0.25,
    5: 0.5,
    6: 1.0,
    7: 2.0,
    8: 4.0,
    9: 6.0,
    10: 8.0,
    11: 10.0,
}


@dataclass(frozen=True)
class Requirement:
    """One occupation requirement with profile-match evidence attached."""

    requirement_id: int
    requirement_type: str
    canonical_text: str
    ordinal_value: float | None
    alternative_group: int | None
    held: bool


@dataclass(frozen=True)
class CoverageReport:
    occupation_id: int
    met_tools: list[str]
    missing_tools: list[str]
    education_satisfied: bool | None
    education_needed: str | None
    experience_satisfied: bool | None
    experience_needed_years: float | None
    coverage_pct: float


def normalize_key(value: str) -> str:
    """Normalize a vocabulary key exactly as the ingestion loader does."""
    return re.sub(r"\s+", " ", value.strip().lower())


def rl_category_to_zone(category: int) -> int:
    """Map an O*NET RL education category onto the five job-zone levels."""
    try:
        return RL_TO_JOB_ZONE[category]
    except KeyError as exc:
        raise ValueError(f"unknown RL category: {category}") from exc


def rw_category_to_years(category: int) -> float:
    """Return the lower-bound years represented by an O*NET RW band."""
    try:
        return RW_TO_YEARS[category]
    except KeyError as exc:
        raise ValueError(f"unknown RW category: {category}") from exc


def median_experience_band(distribution: Iterable[tuple[int, float]]) -> int:
    """Return the first RW category whose cumulative percentage reaches 50.

    Input order is intentionally ignored; O*NET category number defines the
    progression.  Negative percentages and incomplete distributions are rejected
    rather than silently producing a misleading experience threshold.
    """
    rows = sorted(distribution)
    cumulative = 0.0
    for category, percentage in rows:
        rw_category_to_years(category)
        if percentage < 0:
            raise ValueError("experience percentages cannot be negative")
        cumulative += percentage
        if cumulative >= 50:
            return category
    raise ValueError("experience distribution does not reach 50 percent")


def _threshold_satisfied(held: float | None, required: float) -> bool:
    return held is not None and held >= required


def build_coverage_report(
    occupation_id: int,
    inferred_job_zone: int | None,
    years_experience: float | None,
    requirements: Sequence[Requirement],
) -> CoverageReport:
    """Compute coverage from already-fetched profile and requirement values."""
    tools = sorted(
        (requirement for requirement in requirements if requirement.requirement_type == "tool"),
        key=lambda requirement: requirement.canonical_text.casefold(),
    )
    met_tools = [requirement.canonical_text for requirement in tools if requirement.held]
    missing_tools = [requirement.canonical_text for requirement in tools if not requirement.held]

    education = [
        requirement
        for requirement in requirements
        if requirement.requirement_type == "education_level"
        and requirement.ordinal_value is not None
    ]
    if education:
        easiest = min(education, key=lambda requirement: requirement.ordinal_value or 0)
        assert easiest.ordinal_value is not None
        education_satisfied = _threshold_satisfied(
            float(inferred_job_zone) if inferred_job_zone is not None else None,
            float(easiest.ordinal_value),
        )
        education_needed = None if education_satisfied else easiest.canonical_text
    else:
        education_satisfied = None
        education_needed = None

    experience = [
        requirement
        for requirement in requirements
        if requirement.requirement_type == "experience" and requirement.ordinal_value is not None
    ]
    if experience:
        thresholds = [
            float(requirement.ordinal_value)
            for requirement in experience
            if requirement.ordinal_value is not None
        ]
        threshold = max(thresholds)
        experience_satisfied = _threshold_satisfied(years_experience, threshold)
        experience_needed_years = None if experience_satisfied else threshold
    else:
        experience_satisfied = None
        experience_needed_years = None

    applicable = (
        len(tools) + int(education_satisfied is not None) + int(experience_satisfied is not None)
    )
    met = len(met_tools) + int(education_satisfied is True) + int(experience_satisfied is True)
    coverage_pct = round(100 * met / applicable, 2) if applicable else 0.0

    return CoverageReport(
        occupation_id=occupation_id,
        met_tools=met_tools,
        missing_tools=missing_tools,
        education_satisfied=education_satisfied,
        education_needed=education_needed,
        experience_satisfied=experience_satisfied,
        experience_needed_years=experience_needed_years,
        coverage_pct=coverage_pct,
    )


def coverage(pool, profile_id: int, occupation_id: int) -> CoverageReport:
    """Fetch and compute one profile's requirement coverage for an occupation.

    Unknown identifiers raise :class:`ValueError`.  Profile requirements without a
    canonical ``requirement_id`` never appear in the join and therefore cannot
    inflate either the numerator or denominator.
    """
    with pool.acquire() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            select inferred_job_zone, years_experience
            from user_profiles
            where profile_id = :profile_id
            """,
            profile_id=profile_id,
        )
        profile = cursor.fetchone()
        if profile is None:
            raise ValueError(f"unknown profile id: {profile_id}")

        cursor.execute(
            "select occupation_id from occupations where occupation_id = :occupation_id",
            occupation_id=occupation_id,
        )
        if cursor.fetchone() is None:
            raise ValueError(f"unknown occupation id: {occupation_id}")

        cursor.execute(
            """
            select ri.requirement_id,
                   ri.requirement_type,
                   ri.canonical_text,
                   ri.ordinal_value,
                   orq.alternative_group,
                   case when held.requirement_id is null then 0 else 1 end as held
            from occupation_requirements orq
            join requirement_items ri on ri.requirement_id = orq.requirement_id
            left join (
              select distinct requirement_id
              from profile_requirements
              where profile_id = :profile_id and requirement_id is not null
            ) held on held.requirement_id = ri.requirement_id
            where orq.occupation_id = :occupation_id
            order by ri.requirement_type, ri.canonical_text
            """,
            profile_id=profile_id,
            occupation_id=occupation_id,
        )
        rows = cursor.fetchall()

    requirements = [
        Requirement(
            requirement_id=int(row[0]),
            requirement_type=str(row[1]),
            canonical_text=str(row[2]),
            ordinal_value=float(row[3]) if row[3] is not None else None,
            alternative_group=int(row[4]) if row[4] is not None else None,
            held=bool(row[5]),
        )
        for row in rows
    ]
    return build_coverage_report(
        occupation_id=occupation_id,
        inferred_job_zone=int(profile[0]) if profile[0] is not None else None,
        years_experience=float(profile[1]) if profile[1] is not None else None,
        requirements=requirements,
    )
