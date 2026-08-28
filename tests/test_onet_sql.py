"""Tests for the O*NET Oracle distribution parser.

Each case here is a dialect detail taken from the real distribution; they are the
constructs that silently corrupt a naive comma-splitting parser.
"""

from __future__ import annotations

from datetime import date

import pytest

from navigator.ingest.onet_sql import (
    INSERT_RE,
    format_value,
    parse_ddl,
    parse_values,
    split_top_level,
)


class TestParseValues:
    def test_plain_row(self):
        assert parse_values("'11-1011.00', 'Chief Executives', 4.62") == [
            "11-1011.00",
            "Chief Executives",
            4.62,
        ]

    def test_null_and_integer(self):
        assert parse_values("NULL, 8, 'N'") == [None, 8, "N"]

    def test_escaped_quote_is_one_value(self):
        assert parse_values("'the firm''s profits'") == ["the firm's profits"]

    def test_comma_inside_literal_is_not_a_separator(self):
        assert parse_values("'Analyst, Programmer', 3") == ["Analyst, Programmer", 3]

    def test_to_date_collapses_to_a_single_value(self):
        # A comma-splitting parser reads this as two values and the bind count breaks.
        values = parse_values("'x', TO_DATE('2023-08-01', 'yyyy-mm-dd'), 'y'")
        assert values == ["x", date(2023, 8, 1), "y"]

    def test_ampersand_concatenation_rejoins(self):
        # The distribution splits literals around '&' to dodge SQL*Plus substitution.
        values = parse_values("'ABB Optimize IT Predict &'||' Control', '2.E.9.b'")
        assert values == ["ABB Optimize IT Predict & Control", "2.E.9.b"]

    def test_multiple_concatenated_fragments(self):
        assert parse_values("'a &'||'b &'||'c'") == ["a &b &c"]

    def test_empty_string_is_preserved_as_empty_not_null(self):
        # The fixer relies on telling '' apart from NULL.
        assert parse_values("'x', '', NULL") == ["x", "", None]

    def test_unknown_function_is_rejected(self):
        with pytest.raises(ValueError, match="unsupported SQL function"):
            parse_values("SYSDATE()")


class TestFormatValue:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, "NULL"),
            ("plain", "'plain'"),
            ("it's", "'it''s'"),
            (date(2020, 1, 31), "TO_DATE('2020-01-31', 'yyyy-mm-dd')"),
            (7, "7"),
        ],
    )
    def test_renders_oracle_literal(self, value, expected):
        assert format_value(value) == expected

    def test_round_trips_through_parse(self):
        original = ["a'b", None, 3.5, date(2021, 6, 1), "x & y"]
        rendered = ", ".join(format_value(v) for v in original)
        assert parse_values(rendered) == original


class TestSplitTopLevel:
    def test_ignores_commas_inside_parentheses(self):
        assert split_top_level("a DECIMAL(5,2), PRIMARY KEY (x, y)") == [
            "a DECIMAL(5,2)",
            "PRIMARY KEY (x, y)",
        ]


class TestParseDdl:
    DDL = """CREATE TABLE abilities (
      onetsoc_code CHARACTER(10) NOT NULL,
      data_value DECIMAL(5,2) NOT NULL,
      n DECIMAL(4,0),
      PRIMARY KEY (onetsoc_code),
      FOREIGN KEY (onetsoc_code) REFERENCES occupation_data(onetsoc_code))"""

    def test_extracts_columns_and_constraints(self):
        spec = parse_ddl(self.DDL)
        assert spec.name == "abilities"
        assert list(spec.columns) == ["onetsoc_code", "data_value", "n"]
        assert spec.not_null == {"onetsoc_code", "data_value"}
        assert spec.primary_key == ["onetsoc_code"]
        assert spec.foreign_keys[0].ref_table == "occupation_data"

    def test_render_round_trips(self):
        spec = parse_ddl(self.DDL)
        assert parse_ddl(spec.render().rstrip(";")).columns == spec.columns

    def test_render_reflects_relaxed_not_null(self):
        spec = parse_ddl(self.DDL)
        spec.not_null.discard("data_value")
        rendered = spec.render()
        assert "data_value DECIMAL(5,2)," in rendered
        assert "onetsoc_code CHARACTER(10) NOT NULL" in rendered

    def test_render_drops_removed_foreign_key(self):
        spec = parse_ddl(self.DDL)
        spec.foreign_keys = []
        assert "FOREIGN KEY" not in spec.render()

    def test_rejects_non_create_statement(self):
        with pytest.raises(ValueError, match="not a CREATE TABLE"):
            parse_ddl("INSERT INTO t (a) VALUES (1)")


class TestInsertRe:
    def test_captures_table_columns_and_values(self):
        match = INSERT_RE.match("INSERT INTO t (a, b) VALUES ('x', 1);\n")
        assert match.groups() == ("t", "a, b", "'x', 1")
