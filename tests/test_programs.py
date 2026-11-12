"""Pure transforms for the Scorecard outcome layer."""

from __future__ import annotations

import pytest

from navigator.programs import (
    BACHELORS,
    cip4,
    clean_cip_title,
    crosswalk_pairs,
    flatten_school,
)

SCHOOL = {
    "id": 110635,
    "school.name": "University of California-Berkeley",
    "school.city": "Berkeley",
    "school.state": "CA",
    "school.ownership": 1,
    "location.lat": 37.87,
    "location.lon": -122.26,
    "latest.programs.cip_4_digit": [
        {
            "code": "1107", "title": "Computer Science.",
            "credential": {"level": 3, "title": "Bachelor's Degree"},
            "counts": {"ipeds_awards1": 994},
            "earnings": {"1_yr": {"overall_median_earnings": 161143},
                         "4_yr": {"overall_median_earnings": 204379},
                         "5_yr": {"overall_median_earnings": 228465},
                         "7_yr": {"overall_median_earnings": 242740}},
            "debt": {"staff_grad_plus": {"all": {"eval_inst": {
                "median": 13289, "median_payment": 156}}}},
        },
        {   # fully suppressed: nothing to store
            "code": "0101", "title": "Agricultural Business and Management.",
            "credential": {"level": 5, "title": "Master's Degree"},
            "counts": {"ipeds_awards1": 4},
            "earnings": {"1_yr": {"overall_median_earnings": None},
                         "4_yr": {"overall_median_earnings": None},
                         "5_yr": {"overall_median_earnings": None},
                         "7_yr": {"overall_median_earnings": None}},
            "debt": {"staff_grad_plus": {"all": {"eval_inst": {
                "median": None, "median_payment": None}}}},
        },
        {   # level 1 certificate: outside the loaded levels
            "code": "4801", "title": "Construction Trades.",
            "credential": {"level": 1, "title": "Undergraduate Certificate"},
            "counts": {"ipeds_awards1": 12},
            "earnings": {"1_yr": {"overall_median_earnings": 40000},
                         "4_yr": {"overall_median_earnings": None},
                         "5_yr": {"overall_median_earnings": None},
                         "7_yr": {"overall_median_earnings": None}},
            "debt": {"staff_grad_plus": {"all": {"eval_inst": {
                "median": None, "median_payment": None}}}},
        },
    ],
}


class TestCip4:
    @pytest.mark.parametrize("raw,expected", [
        ("11.0701", "1107"), ("1107", "1107"), ("01.0000", "0100"),
        (" 27.0301 ", "2703"), ("52.0201", "5202"),
    ])
    def test_normalizes(self, raw, expected):
        assert cip4(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "99.9999x", "abc", "11"])
    def test_rejects_garbage(self, raw):
        assert cip4(raw) is None

    def test_scorecard_codes_keep_leading_zero(self):
        assert cip4("0301") == "0301"


class TestCleanTitle:
    def test_strips_trailing_period(self):
        assert clean_cip_title("Computer Science.") == "Computer Science"

    def test_leaves_internal_punctuation(self):
        assert clean_cip_title("Business/Commerce, General.") == "Business/Commerce, General"


class TestCrosswalk:
    def test_groups_soc_by_four_digit_cip(self):
        rows = [
            ("11.0701", "Computer Science.", "15-1252", "Software Developers"),
            ("11.0701", "Computer Science.", "15-1211", "Computer Systems Analysts"),
            ("11.0101", "Computer and Information Sciences, General.", "15-1252", "x"),
        ]
        assert crosswalk_pairs(rows) == {"1107": {"15-1252", "15-1211"},
                                         "1101": {"15-1252"}}

    def test_drops_no_match_and_header_rows(self):
        rows = [
            ("CIP2020Code", "CIP2020Title", "SOC2018Code", "SOC2018Title"),
            ("01.0508", "Taxidermy.", "99-9999", "NO MATCH"),
            ("11.0701", "Computer Science.", "15-1252", "Software Developers"),
        ]
        assert crosswalk_pairs(rows) == {"1107": {"15-1252"}}

    def test_skips_blank_rows(self):
        assert crosswalk_pairs([(None, None, None, None)]) == {}


class TestFlattenSchool:
    def test_institution_fields(self):
        inst, _ = flatten_school(SCHOOL, data_year=2026)
        assert inst == {
            "unit_id": "110635", "name": "University of California-Berkeley",
            "city": "Berkeley", "state": "CA", "control": "Public",
            "lat": 37.87, "lon": -122.26,
        }

    def test_keeps_only_priced_programs_at_loaded_levels(self):
        _, programs = flatten_school(SCHOOL, data_year=2026)
        assert [p["cip_code"] for p in programs] == ["1107"]

    def test_program_fields(self):
        _, (program,) = flatten_school(SCHOOL, data_year=2026)
        assert program == {
            "cip_code": "1107", "program_name": "Computer Science",
            "credential_level": BACHELORS, "e1": 161143, "e4": 204379,
            "e5": 228465, "e7": 242740, "debt": 13289, "payment": 156,
            "awards": 994, "data_year": 2026,
        }

    def test_missing_coordinates_become_none(self):
        school = {**SCHOOL, "location.lat": None, "location.lon": None}
        inst, _ = flatten_school(school, data_year=2026)
        assert inst["lat"] is None and inst["lon"] is None

    def test_unknown_ownership_has_no_control_label(self):
        inst, _ = flatten_school({**SCHOOL, "school.ownership": 9}, data_year=2026)
        assert inst["control"] is None

    def test_school_without_programs_array(self):
        school = {k: v for k, v in SCHOOL.items() if k != "latest.programs.cip_4_digit"}
        inst, programs = flatten_school(school, data_year=2026)
        assert inst["unit_id"] == "110635" and programs == []

    def test_non_numeric_metric_is_dropped_not_crashed(self):
        school = {**SCHOOL, "latest.programs.cip_4_digit": [{
            "code": "1107", "title": "Computer Science.",
            "credential": {"level": 3, "title": "Bachelor's Degree"},
            "counts": {"ipeds_awards1": None},
            "earnings": {"1_yr": {"overall_median_earnings": "PrivacySuppressed"},
                         "4_yr": {"overall_median_earnings": 90000},
                         "5_yr": {"overall_median_earnings": None},
                         "7_yr": {"overall_median_earnings": None}},
            "debt": {"staff_grad_plus": {"all": {"eval_inst": {
                "median": None, "median_payment": None}}}},
        }]}
        _, (program,) = flatten_school(school, data_year=2026)
        assert program["e1"] is None and program["e4"] == 90000
