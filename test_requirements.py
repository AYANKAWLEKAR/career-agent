"""Tests for requirements ingestion rules and profile gap math."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from navigator.requirements import (
    Requirement,
    build_coverage_report,
    coverage,
    median_experience_band,
    normalize_key,
    rl_category_to_zone,
    rw_category_to_years,
)


def _requirement(
    requirement_id: int,
    requirement_type: str,
    text: str,
    *,
    ordinal: float | None = None,
    alternative_group: int | None = None,
    held: bool = False,
) -> Requirement:
    return Requirement(
        requirement_id=requirement_id,
        requirement_type=requirement_type,
        canonical_text=text,
        ordinal_value=ordinal,
        alternative_group=alternative_group,
        held=held,
    )


def test_normalize_key_collapses_case_and_whitespace():
    assert normalize_key("  Microsoft\t Power   BI  ") == "microsoft power bi"


@pytest.mark.parametrize(
    ("category", "zone"),
    [(1, 1), (2, 2), (3, 3), (5, 3), (6, 4), (7, 4), (8, 5), (12, 5)],
)
def test_rl_category_mapping(category, zone):
    assert rl_category_to_zone(category) == zone


@pytest.mark.parametrize(
    ("category", "years"),
    [(1, 0), (2, 0), (3, 1 / 12), (4, 0.25), (5, 0.5), (7, 2), (11, 10)],
)
def test_rw_category_mapping(category, years):
    assert rw_category_to_years(category) == pytest.approx(years)


def test_unknown_categories_raise_clear_errors():
    with pytest.raises(ValueError, match="unknown RL category"):
        rl_category_to_zone(13)
    with pytest.raises(ValueError, match="unknown RW category"):
        rw_category_to_years(0)


def test_median_band_sorts_distribution_and_uses_first_crossing():
    assert median_experience_band([(3, 25), (1, 20), (2, 30), (4, 25)]) == 2


def test_median_band_rejects_invalid_distribution():
    with pytest.raises(ValueError, match="negative"):
        median_experience_band([(1, -1), (2, 101)])
    with pytest.raises(ValueError, match="does not reach"):
        median_experience_band([(1, 20), (2, 20)])


def test_coverage_report_explains_each_requirement_type():
    report = build_coverage_report(
        occupation_id=42,
        inferred_job_zone=4,
        years_experience=1.5,
        requirements=[
            _requirement(1, "tool", "Tableau"),
            _requirement(2, "tool", "Python", held=True),
            _requirement(
                3,
                "education_level",
                "Bachelor's Degree",
                ordinal=4,
                alternative_group=1,
            ),
            _requirement(
                4,
                "education_level",
                "Master's Degree",
                ordinal=5,
                alternative_group=1,
            ),
            _requirement(5, "experience", "Over 2 years", ordinal=2),
        ],
    )

    assert report.met_tools == ["Python"]
    assert report.missing_tools == ["Tableau"]
    assert report.education_satisfied is True
    assert report.education_needed is None
    assert report.experience_satisfied is False
    assert report.experience_needed_years == 2
    assert report.coverage_pct == 50.0


def test_unsatisfied_education_names_the_cheapest_alternative():
    report = build_coverage_report(
        occupation_id=1,
        inferred_job_zone=3,
        years_experience=None,
        requirements=[
            _requirement(1, "education_level", "Master's", ordinal=5, alternative_group=1),
            _requirement(2, "education_level", "Bachelor's", ordinal=4, alternative_group=1),
        ],
    )
    assert report.education_satisfied is False
    assert report.education_needed == "Bachelor's"
    assert report.coverage_pct == 0.0


def test_absent_requirement_types_are_not_counted():
    report = build_coverage_report(
        occupation_id=1, inferred_job_zone=None, years_experience=None, requirements=[]
    )
    assert report.met_tools == []
    assert report.missing_tools == []
    assert report.education_satisfied is None
    assert report.experience_satisfied is None
    assert report.coverage_pct == 0.0


def test_coverage_database_wrapper_fetches_then_delegates():
    pool = MagicMock()
    connection = pool.acquire.return_value.__enter__.return_value
    cursor = connection.cursor.return_value
    cursor.fetchone.side_effect = [(4, 3.5), (99,)]
    cursor.fetchall.return_value = [
        (10, "tool", "Python", None, None, 1),
        (11, "tool", "Tableau", None, None, 0),
        (12, "education_level", "Bachelor's Degree", 4, 1, 0),
        (13, "experience", "Over 2 years", 2, None, 0),
    ]

    report = coverage(pool, profile_id=7, occupation_id=99)

    assert report.met_tools == ["Python"]
    assert report.missing_tools == ["Tableau"]
    assert report.education_satisfied is True
    assert report.experience_satisfied is True
    assert report.coverage_pct == 75.0
    assert cursor.execute.call_count == 3


def test_coverage_rejects_unknown_profile_before_other_queries():
    pool = MagicMock()
    cursor = pool.acquire.return_value.__enter__.return_value.cursor.return_value
    cursor.fetchone.return_value = None
    with pytest.raises(ValueError, match="unknown profile id: 404"):
        coverage(pool, profile_id=404, occupation_id=1)
    assert cursor.execute.call_count == 1


def test_coverage_rejects_unknown_occupation():
    pool = MagicMock()
    cursor = pool.acquire.return_value.__enter__.return_value.cursor.return_value
    cursor.fetchone.side_effect = [(4, 2), None]
    with pytest.raises(ValueError, match="unknown occupation id: 404"):
        coverage(pool, profile_id=1, occupation_id=404)
    assert cursor.execute.call_count == 2
