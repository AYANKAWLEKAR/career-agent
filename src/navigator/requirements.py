"""Requirement vocabulary mappings and coverage/gap math.

Implements the decision rules of the requirements design
(docs/superpowers/specs/2026-08-28-requirements-ingestion-design.md):

* the RL education-category -> job-zone mapping (section 4.2),
* the RW experience-band -> years mapping and median-band selection (4.3),
* coverage of a stored profile against one occupation's requirements (5).

Everything that decides is a pure function over plain values; the database
functions are thin fetch-then-decide wrappers. The loader script
(``scripts/load_requirements.py``) imports the mappings from here so the code
that writes the vocabulary and the code that interprets it cannot drift apart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Section 4.2: RL education categories (1-12) onto the section 7.1 zone scale,
# keeping profile comparison on the same 1-5 axis as inferred_job_zone.
RL_TO_ZONE: dict[int, int] = {
    1: 1,               # Less than a high school diploma
    2: 2,               # High school diploma or GED
    3: 3, 4: 3, 5: 3,   # Certificate, some college, associate's
    6: 4, 7: 4,         # Bachelor's, post-baccalaureate certificate
    8: 5, 9: 5, 10: 5, 11: 5, 12: 5,  # Master's through post-doctoral
}

# Section 4.3: RW experience bands (1-11) onto threshold years — each band's
# lower bound. Bands 1 ("None") and 2 ("up to a month") both mean no meaningful
# prior experience and resolve to 0.
RW_TO_YEARS: dict[int, float] = {
    1: 0.0, 2: 0.0, 3: 0.083, 4: 0.25, 5: 0.5,
    6: 1.0, 7: 2.0, 8: 4.0, 9: 6.0, 10: 8.0, 11: 10.0,
}

EDUCATION_ALTERNATIVE_MIN_PCT = 10.0  # RL categories below this share are noise


def normalize_key(text: str) -> str:
    """Vocabulary key: lowercased, whitespace collapsed, trimmed."""
    return re.sub(r"\s+", " ", text).strip().lower()


def median_band(distribution: dict[int, float]) -> int | None:
    """The smallest band whose cumulative share reaches 50% (section 4.3).

    ``distribution`` maps band/category number to percentage of incumbents.
    Returns None for an empty distribution. Shares that do not sum to exactly
    100 (rounding in the source data) are handled by normalizing against the
    actual total.
    """
    total = sum(distribution.values())
    if total <= 0:
        return None
    cumulative = 0.0
    # The epsilon absorbs float accumulation error: a prefix that is EXACTLY
    # half in decimal (e.g. 30.7 + 24.4 vs a 110.2 total) can land one ulp
    # below total/2 in binary and silently push the threshold one band high.
    for band in sorted(distribution):
        cumulative += distribution[band]
        if cumulative >= total / 2 - 1e-9:
            return band
    return max(distribution)  # pragma: no cover - defensive; loop always hits


def education_alternatives(distribution: dict[int, float]) -> list[int]:
    """RL categories that hold at least 10% of incumbents (section 4.2).

    These become one alternative_group: satisfying ANY satisfies the
    requirement. Falls back to the modal category when nothing clears 10%,
    so an occupation with a flat distribution still gets a requirement.
    """
    chosen = [c for c, pct in distribution.items() if pct >= EDUCATION_ALTERNATIVE_MIN_PCT]
    if not chosen and distribution:
        chosen = [max(distribution, key=lambda c: distribution[c])]
    return sorted(chosen)


@dataclass
class CoverageReport:
    occupation_id: int
    met_tools: list[str]
    missing_tools: list[str]
    education_satisfied: bool | None      # None: occupation has no education rows
    education_needed: str | None          # cheapest alternative when unsatisfied
    experience_satisfied: bool | None
    experience_needed_years: float | None
    coverage_pct: float


def education_group_satisfied(
    profile_zone: int | None, alternative_ordinals: list[float]
) -> bool | None:
    """Any alternative satisfies the group (section 5)."""
    if not alternative_ordinals:
        return None
    if profile_zone is None:
        return False
    return any(profile_zone >= ordinal for ordinal in alternative_ordinals)


def experience_satisfied(
    profile_years: float | None, threshold_years: float | None
) -> bool | None:
    if threshold_years is None:
        return None
    if profile_years is None:
        return False
    return profile_years >= threshold_years


def assemble_coverage(
    occupation_id: int,
    required_tools: dict[int, str],       # requirement_id -> canonical_text
    matched_requirement_ids: set[int],
    profile_zone: int | None,
    education_ordinals: list[float],
    education_texts: dict[float, str],    # ordinal -> canonical_text
    profile_years: float | None,
    experience_threshold: float | None,
) -> CoverageReport:
    """Pure assembly of a coverage report (section 5 rules).

    The denominator counts tools plus at most one education group and one
    experience requirement; absent requirement types are not counted, so an
    occupation with no RL data is not "missing education".
    """
    met_tools = sorted(
        text for rid, text in required_tools.items() if rid in matched_requirement_ids
    )
    missing_tools = sorted(
        text for rid, text in required_tools.items() if rid not in matched_requirement_ids
    )
    education_ok = education_group_satisfied(profile_zone, education_ordinals)
    education_needed = None
    if education_ok is False:
        cheapest = min(education_ordinals)
        education_needed = education_texts.get(cheapest)
    experience_ok = experience_satisfied(profile_years, experience_threshold)

    met = len(met_tools) + (1 if education_ok else 0) + (1 if experience_ok else 0)
    applicable = (
        len(required_tools)
        + (1 if education_ok is not None else 0)
        + (1 if experience_ok is not None else 0)
    )
    return CoverageReport(
        occupation_id=occupation_id,
        met_tools=met_tools,
        missing_tools=missing_tools,
        education_satisfied=education_ok,
        education_needed=education_needed,
        experience_satisfied=experience_ok,
        experience_needed_years=experience_threshold if experience_ok is False else None,
        coverage_pct=round(100 * met / applicable, 1) if applicable else 0.0,
    )


def experience_thresholds(pool) -> dict[int, float]:
    """occupation_id -> required years, from the loaded requirement layer.

    Empty when the loader has not run; callers treat a missing occupation as
    having no experience requirement.
    """
    with pool.acquire() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            select o.occupation_id, r.ordinal_value
            from occupation_requirements o
            join requirement_items r on r.requirement_id = o.requirement_id
            where r.requirement_type = 'experience'
            """
        )
        return {int(oid): float(years) for oid, years in cursor.fetchall()}


def coverage(pool, profile_id: int, occupation_id: int) -> CoverageReport:
    """Coverage of one stored profile against one occupation's requirements."""
    with pool.acquire() as connection:
        cursor = connection.cursor()
        cursor.execute(
            "select inferred_job_zone, years_experience from user_profiles"
            " where profile_id = :p",
            p=profile_id,
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError(f"no such profile: {profile_id}")
        profile_zone, profile_years = row

        cursor.execute(
            "select count(*) from occupations where occupation_id = :o", o=occupation_id
        )
        if cursor.fetchone()[0] == 0:
            raise ValueError(f"no such occupation: {occupation_id}")

        cursor.execute(
            """
            select r.requirement_type, r.requirement_id, r.canonical_text,
                   r.ordinal_value
            from occupation_requirements o
            join requirement_items r on r.requirement_id = o.requirement_id
            where o.occupation_id = :o
            """,
            o=occupation_id,
        )
        required_tools: dict[int, str] = {}
        education_ordinals: list[float] = []
        education_texts: dict[float, str] = {}
        experience_threshold: float | None = None
        # Sorted by requirement_id so education_needed is deterministic when
        # several RL categories share one zone ordinal (e.g. 3, 4 and 5 all
        # map to zone 3): the lowest-numbered vocabulary entry wins.
        for rtype, rid, text, ordinal in sorted(cursor.fetchall(), key=lambda r: r[1]):
            if rtype == "tool":
                required_tools[int(rid)] = text
            elif rtype == "education_level":
                education_ordinals.append(float(ordinal))
                education_texts.setdefault(float(ordinal), text)
            elif rtype == "experience":
                experience_threshold = float(ordinal)

        # Mapped profile requirements only; unmapped rows are excluded from
        # coverage math per section 6.1 of the parent design.
        cursor.execute(
            """
            select requirement_id from profile_requirements
            where profile_id = :p and requirement_id is not null
            """,
            p=profile_id,
        )
        matched = {int(r[0]) for r in cursor.fetchall()}

    return assemble_coverage(
        occupation_id=occupation_id,
        required_tools=required_tools,
        matched_requirement_ids=matched,
        profile_zone=int(profile_zone) if profile_zone is not None else None,
        education_ordinals=education_ordinals,
        education_texts=education_texts,
        profile_years=float(profile_years) if profile_years is not None else None,
        experience_threshold=experience_threshold,
    )
