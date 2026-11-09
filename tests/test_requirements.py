"""Tests for the requirement mappings and coverage math (spec sections 4-5)."""

from __future__ import annotations

import pytest

from navigator.requirements import (
    RL_TO_ZONE,
    RW_TO_YEARS,
    assemble_coverage,
    education_alternatives,
    education_group_satisfied,
    experience_satisfied,
    median_band,
    normalize_key,
)


class TestMappings:
    def test_rl_covers_all_twelve_categories(self):
        assert sorted(RL_TO_ZONE) == list(range(1, 13))
        assert RL_TO_ZONE[6] == 4   # bachelor's
        assert RL_TO_ZONE[8] == 5   # master's

    def test_rw_covers_all_eleven_bands_and_is_monotonic(self):
        assert sorted(RW_TO_YEARS) == list(range(1, 12))
        values = [RW_TO_YEARS[b] for b in sorted(RW_TO_YEARS)]
        assert values == sorted(values)
        assert RW_TO_YEARS[1] == RW_TO_YEARS[2] == 0.0  # both mean "none needed"

    def test_normalize_key_collapses_case_and_whitespace(self):
        assert normalize_key("  Microsoft   Excel ") == "microsoft excel"


class TestMedianBand:
    def test_smallest_band_reaching_half_wins(self):
        # cumulative: 30, 70 -> band 6 crosses 50%
        assert median_band({5: 30.0, 6: 40.0, 8: 30.0}) == 6

    def test_exact_half_counts(self):
        assert median_band({1: 50.0, 7: 50.0}) == 1

    def test_exact_half_survives_float_accumulation(self):
        # 30.7 + 24.4 is exactly half of 110.2 in decimal but one ulp short in
        # binary; the epsilon must keep band 2 as the answer, not band 3.
        assert median_band({1: 30.7, 2: 24.4, 3: 55.1}) == 2
        assert median_band({1: 14.7, 2: 43.9, 3: 58.6}) == 2

    def test_normalizes_when_shares_do_not_sum_to_100(self):
        # source data rounds; 45+45=90 total, half is 45 -> band 3
        assert median_band({3: 45.0, 9: 45.0}) == 3

    def test_empty_distribution_is_none(self):
        assert median_band({}) is None


class TestEducationAlternatives:
    def test_categories_at_ten_percent_or_more(self):
        dist = {2: 5.0, 5: 12.0, 6: 62.0, 8: 21.0}
        assert education_alternatives(dist) == [5, 6, 8]

    def test_flat_distribution_falls_back_to_mode(self):
        dist = {c: 9.0 for c in range(1, 12)}
        dist[6] = 9.5
        assert education_alternatives(dist) == [6]

    def test_empty_distribution_is_empty(self):
        assert education_alternatives({}) == []


class TestSatisfaction:
    def test_any_alternative_satisfies_the_group(self):
        assert education_group_satisfied(4, [4.0, 5.0]) is True
        assert education_group_satisfied(3, [4.0, 5.0]) is False
        assert education_group_satisfied(None, [4.0]) is False
        assert education_group_satisfied(4, []) is None

    def test_experience_threshold_comparison(self):
        assert experience_satisfied(3.0, 2.0) is True
        assert experience_satisfied(1.0, 2.0) is False
        assert experience_satisfied(None, 2.0) is False
        assert experience_satisfied(3.0, None) is None


class TestAssembleCoverage:
    def test_full_report_with_partial_tool_match(self):
        report = assemble_coverage(
            occupation_id=7,
            required_tools={11: "Python", 12: "Tableau", 13: "SQL"},
            matched_requirement_ids={11, 13},
            profile_zone=4,
            education_ordinals=[4.0, 5.0],
            education_texts={4.0: "Bachelor's Degree", 5.0: "Master's Degree"},
            profile_years=1.0,
            experience_threshold=2.0,
        )
        assert report.met_tools == ["Python", "SQL"]
        assert report.missing_tools == ["Tableau"]
        assert report.education_satisfied is True
        assert report.education_needed is None
        assert report.experience_satisfied is False
        assert report.experience_needed_years == 2.0
        # met: 2 tools + education = 3; applicable: 3 tools + edu + exp = 5
        assert report.coverage_pct == 60.0

    def test_unsatisfied_education_names_cheapest_alternative(self):
        report = assemble_coverage(
            occupation_id=7, required_tools={}, matched_requirement_ids=set(),
            profile_zone=3, education_ordinals=[4.0, 5.0],
            education_texts={4.0: "Bachelor's Degree", 5.0: "Master's Degree"},
            profile_years=None, experience_threshold=None,
        )
        assert report.education_needed == "Bachelor's Degree"
        assert report.coverage_pct == 0.0

    def test_absent_requirement_types_are_not_missing(self):
        report = assemble_coverage(
            occupation_id=7, required_tools={}, matched_requirement_ids=set(),
            profile_zone=4, education_ordinals=[], education_texts={},
            profile_years=5.0, experience_threshold=None,
        )
        assert report.education_satisfied is None
        assert report.experience_satisfied is None
        assert report.coverage_pct == 0.0


class TestZoneGateIntegration:
    @pytest.mark.parametrize("band,years", [(7, 2.0), (8, 4.0)])
    def test_rw_band_thresholds_align_with_years(self, band, years):
        assert RW_TO_YEARS[band] == years
