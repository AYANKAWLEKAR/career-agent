"""Tests for the LCA wage normalization and aggregation (design doc Stage 1)."""

from __future__ import annotations

import pytest

from navigator.lca import (
    BAY_AREA_COUNTIES,
    aggregate_wages,
    annualize,
    is_bay_area,
    is_blank_row,
    occupation_soc6,
    parse_rate,
    row_passes,
    soc6,
)


class TestParseRate:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (120000.0, 120000.0),
            ("120000", 120000.0),
            ("$120,000.00", 120000.0),
            (" 85000.50 ", 85000.5),
            (None, None),
            ("", None),
            ("N/A", None),
            (0, None),
            (-5, None),
        ],
    )
    def test_parses_numbers_dollars_and_garbage(self, raw, expected):
        assert parse_rate(raw) == expected


class TestAnnualize:
    @pytest.mark.parametrize(
        ("rate", "unit", "expected"),
        [
            (120000.0, "Year", 120000.0),
            (10000.0, "Month", 120000.0),
            (5000.0, "Bi-Weekly", 130000.0),
            (2000.0, "Week", 104000.0),
            (60.0, "Hour", 124800.0),
            (60.0, "hour", 124800.0),      # unit casing varies in the raw file
            (60.0, " HOUR ", 124800.0),
        ],
    )
    def test_standard_factors(self, rate, unit, expected):
        assert annualize(rate, unit) == expected

    def test_unknown_unit_is_none(self):
        assert annualize(100.0, "Fortnight") is None
        assert annualize(100.0, None) is None

    @pytest.mark.parametrize(
        ("rate", "unit"),
        [
            (5.0, "Hour"),          # 10,400/yr: below the sanity floor
            (2_000_000.0, "Year"),  # above the ceiling: fat-fingered filing
            (1.0, "Year"),
        ],
    )
    def test_out_of_sanity_window_is_none(self, rate, unit):
        # Window [20k, 1.5M] drops data-entry errors without touching real wages.
        assert annualize(rate, unit) is None

    @pytest.mark.parametrize(
        ("rate", "unit"),
        [
            (55_000.0, "Month"),     # annual salary filed as Month -> $660k
            (26_000.0, "Week"),
            (30_000.0, "Bi-Weekly"),
        ],
    )
    def test_annual_salary_filed_under_subannual_unit_is_dropped(self, rate, unit):
        # Profiled on FY2026 Q3: ~54 such rows pass the window at $660k-$1.5M
        # and would distort p90 in thin SOCs. FROM > 25k is implausible as a
        # real sub-annual rate.
        assert annualize(rate, unit) is None

    def test_subannual_guard_boundary_and_hour_exemption(self):
        assert annualize(25_000.0, "Month") == 300_000.0   # boundary stays in
        # High Hour rates are REAL (physician comp to $356/hr) -- never guarded.
        assert annualize(300.0, "Hour") == 624_000.0


class TestSocNormalization:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("15-1252", "15-1252"),
            ("15-1252.00", "15-1252"),   # some LCA rows carry the O*NET suffix
            (" 15-1252 ", "15-1252"),
            ("151252", None),            # malformed: no hyphen pattern
            (None, None),
            ("", None),
        ],
    )
    def test_lca_soc_codes(self, raw, expected):
        assert soc6(raw) == expected

    def test_onet_8_digit_truncates(self):
        assert occupation_soc6("15-1252.00") == "15-1252"
        assert occupation_soc6("29-1141.03") == "29-1141"


class TestRowPasses:
    def _row(self, **overrides):
        row = {
            "CASE_STATUS": "Certified",
            "FULL_TIME_POSITION": "Y",
            "SOC_CODE": "15-1252",
        }
        row.update(overrides)
        return row

    def test_certified_full_time_passes(self):
        assert row_passes(self._row()) is True

    @pytest.mark.parametrize("status", ["Denied", "Withdrawn", "Certified - Withdrawn"])
    def test_non_certified_rejected(self, status):
        # Certified-Withdrawn was certified once, but the position was pulled;
        # counting it would inflate thin SOCs.
        assert row_passes(self._row(CASE_STATUS=status)) is False

    def test_part_time_rejected(self):
        assert row_passes(self._row(FULL_TIME_POSITION="N")) is False

    def test_status_casing_and_whitespace_tolerated(self):
        assert row_passes(self._row(CASE_STATUS=" certified ")) is True


class TestIsBayArea:
    @pytest.mark.parametrize(
        ("county", "state", "expected"),
        [
            ("ALAMEDA COUNTY", "CA", True),     # real file format: suffixed
            ("San Francisco County", "CA", True),
            ("Alameda   County", "CA", True),   # internal whitespace collapses
            ("MARIN", "CA", True),              # bare name still accepted
            ("SANTA CLARA COUNTY", "CA", False),
            ("ALAMEDA COUNTY", "WA", False),
            (None, "CA", False),
        ],
    )
    def test_county_suffix_normalization(self, county, state, expected):
        # Profiled on FY2026 Q3: every WORKSITE_COUNTY value carries a
        # ' COUNTY' suffix; a bare-name match returns zero rows.
        row = {"WORKSITE_COUNTY": county, "WORKSITE_STATE": state}
        assert is_bay_area(row) is expected


class TestIsBlankRow:
    def test_excel_padding_rows_detected(self):
        # 57.6% of the slim CSV is openpyxl used-range padding: all 12 columns
        # empty. Skipping them is lossless (zero blank-status rows carry data).
        assert is_blank_row({"CASE_STATUS": "", "SOC_CODE": None}) is True
        assert is_blank_row({"CASE_STATUS": "  ", "SOC_CODE": ""}) is True
        assert is_blank_row({"CASE_STATUS": "Certified", "SOC_CODE": ""}) is False


class TestAggregateWages:
    def _rows(self, wages, county="SANTA CLARA COUNTY", state="CA"):
        return [
            {"soc6": "15-1252", "annual_wage": w,
             "WORKSITE_COUNTY": county, "WORKSITE_STATE": state}
            for w in wages
        ]

    def test_percentiles_on_known_distribution(self):
        rows = self._rows([100.0, 200.0, 300.0, 400.0, 500.0])
        stats = aggregate_wages(rows, min_count=1)
        national = stats[("15-1252", None)]
        assert national.count == 5
        assert national.median == 300.0
        assert national.p10 == pytest.approx(140.0)   # linear interpolation
        assert national.p90 == pytest.approx(460.0)
        assert national.mean == 300.0

    def test_bay_area_rows_split_out_by_county(self):
        rows = self._rows([100.0] * 5) + self._rows(
            [200.0] * 5, county="Alameda County")
        stats = aggregate_wages(rows, min_count=1)
        assert stats[("15-1252", None)].count == 10       # national = all rows
        assert stats[("15-1252", "bay")].count == 5       # bay = Alameda only
        assert stats[("15-1252", "bay")].median == 200.0

    def test_bay_filter_requires_california(self):
        # "San Francisco" county exists elsewhere in no state, but guard anyway:
        rows = self._rows([100.0] * 5, county="San Francisco County", state="WA")
        stats = aggregate_wages(rows, min_count=1)
        assert ("15-1252", "bay") not in stats

    def test_min_count_drops_thin_cells(self):
        rows = self._rows([100.0] * 4)
        stats = aggregate_wages(rows, min_count=5)
        assert stats == {}

    def test_bay_county_set_is_the_msa_definition(self):
        assert BAY_AREA_COUNTIES == {
            "ALAMEDA", "CONTRA COSTA", "MARIN", "SAN FRANCISCO", "SAN MATEO"}

    def test_alameda_rows_feed_national_bay_and_alameda_segments(self):
        rows = self._rows([100.0] * 5, county="Alameda County")
        stats = aggregate_wages(rows, min_count=1)
        assert stats[("15-1252", None)].count == 5
        assert stats[("15-1252", "bay")].count == 5
        assert stats[("15-1252", "alameda")].count == 5

    def test_other_bay_counties_are_not_alameda(self):
        rows = self._rows([100.0] * 5, county="Contra Costa County")
        stats = aggregate_wages(rows, min_count=1)
        assert ("15-1252", "bay") in stats
        assert ("15-1252", "alameda") not in stats

    def test_scope_region_codes_are_stable(self):
        # The loader writes these metro_areas codes; paths.py queries by them.
        from navigator.lca import WAGE_SCOPE_CODES
        assert WAGE_SCOPE_CODES == {"bay": "41860", "alameda": "C06001"}
