"""Outcome queries: major resolution, program medians, feeders, spatial."""

from __future__ import annotations

import pytest

from navigator.outcomes import (
    Anchor,
    Feeder,
    MajorMatch,
    NearbyProgram,
    OutcomeReport,
    ProgramOutcome,
    build_outcomes_context,
    credential_for,
    feeder_cips,
    format_outcomes_report,
    gather_outcomes,
    match_majors,
    nearby_programs,
    parse_city_state,
    program_outcome,
    resolve_anchor,
    top_programs,
)


class FakeCursor:
    def __init__(self, script):
        self.script = list(script)
        self.calls: list[tuple[str, dict]] = []
        self._rows: list[tuple] = []

    def execute(self, sql, params=None, **kwargs):
        self.calls.append((" ".join(sql.split()), {**(params or {}), **kwargs}))
        self._rows = self.script.pop(0) if self.script else []

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakePool:
    def __init__(self, script):
        self.cursor_obj = FakeCursor(script)
        self.committed = 0

    def acquire(self):
        pool = self

        class _Conn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return pool.cursor_obj

            def commit(self):
                pool.committed += 1
        return _Conn()


class TestCredentialFor:
    @pytest.mark.parametrize("level,expected", [
        ("Bachelor's", "Bachelor's Degree"), ("B.A.", "Bachelor's Degree"),
        ("undergraduate", "Bachelor's Degree"), (None, "Bachelor's Degree"),
        ("Master's", "Master's Degree"), ("M.S.", "Master's Degree"),
        ("PhD", "Doctoral Degree"), ("Ph.D.", "Doctoral Degree"),
        ("doctorate", "Doctoral Degree"),
    ])
    def test_maps_stated_level(self, level, expected):
        assert credential_for(level) == expected


class TestParseCityState:
    @pytest.mark.parametrize("text,expected", [
        ("Berkeley, CA", ("Berkeley", "CA")),
        ("  San Francisco ,ca ", ("San Francisco", "CA")),
        ("Cambridge, MA 02139", ("Cambridge", "MA")),
    ])
    def test_parses(self, text, expected):
        assert parse_city_state(text) == expected

    @pytest.mark.parametrize("text", [None, "", "Berkeley", "the Bay Area", "CA"])
    def test_rejects_unparseable(self, text):
        assert parse_city_state(text) is None


class TestMatchMajors:
    def test_resolves_each_major_and_applies_threshold(self):
        pool = FakePool([
            [("2703", "Applied Mathematics", 0.754)],
            [("3070", "Data Science", 0.40)],  # below threshold: dropped
        ])
        matches = match_majors(pool, ["B.A. Applied Mathematics", "Zzz"])
        assert matches == [MajorMatch("B.A. Applied Mathematics", "2703",
                                      "Applied Mathematics", 0.754)]

    def test_duplicate_cip_keeps_the_stronger_match(self):
        pool = FakePool([
            [("2703", "Applied Mathematics", 0.70)],
            [("2703", "Applied Mathematics", 0.76)],
        ])
        matches = match_majors(pool, ["Applied Math", "Applied Mathematics"])
        assert [(m.cip_code, m.similarity) for m in matches] == [("2703", 0.76)]

    def test_sorted_by_similarity_descending(self):
        pool = FakePool([
            [("3070", "Data Science", 0.66)],
            [("2703", "Applied Mathematics", 0.75)],
        ])
        matches = match_majors(pool, ["a", "b"])
        assert [m.cip_code for m in matches] == ["2703", "3070"]

    def test_no_majors_means_no_queries(self):
        pool = FakePool([])
        assert match_majors(pool, []) == []
        assert pool.cursor_obj.calls == []

    def test_blank_majors_are_skipped(self):
        pool = FakePool([])
        assert match_majors(pool, ["", "  "]) == []
        assert pool.cursor_obj.calls == []

    def test_query_is_an_approx_vector_probe_on_cip_codes(self):
        pool = FakePool([[("2703", "Applied Mathematics", 0.75)]])
        match_majors(pool, ["Applied Math"])
        sql, binds = pool.cursor_obj.calls[0]
        assert "fetch approx first" in sql and "from cip_codes" in sql
        assert "where" not in sql  # a filter would disqualify the HNSW index
        assert binds["q"] == "Applied Math"

    def test_logs_to_tool_log_when_thread_given(self):
        pool = FakePool([[("2703", "Applied Mathematics", 0.75)], []])
        match_majors(pool, ["Applied Math"], thread_id="t1")
        assert any("tool_log" in sql for sql, _ in pool.cursor_obj.calls)
        assert pool.committed == 1


class TestProgramOutcome:
    ROW = (65, 60, 63684.5, 80000.0, 92803.0, 105000.0, 130000.0, 20500.0)

    def test_maps_columns(self):
        pool = FakePool([[self.ROW]])
        outcome = program_outcome(pool, "2703", "Applied Mathematics",
                                  "Bachelor's Degree")
        assert outcome == ProgramOutcome(
            cip_code="2703", title="Applied Mathematics",
            credential_level="Bachelor's Degree", n_programs=65, n_priced=60,
            e1=63684.5, e4_p25=80000.0, e4=92803.0, e4_p75=105000.0,
            e7=130000.0, debt=20500.0)

    def test_none_when_no_program_publishes_earnings(self):
        pool = FakePool([[(12, 0, None, None, None, None, None, 15000.0)]])
        assert program_outcome(pool, "9999", "X", "Bachelor's Degree") is None

    def test_none_when_cip_has_no_programs(self):
        pool = FakePool([[(0, 0, None, None, None, None, None, None)]])
        assert program_outcome(pool, "9999", "X", "Bachelor's Degree") is None

    def test_binds_cip_and_level(self):
        pool = FakePool([[self.ROW]])
        program_outcome(pool, "2703", "Applied Mathematics", "Master's Degree")
        _, binds = pool.cursor_obj.calls[0]
        assert binds["cip"] == "2703" and binds["lvl"] == "Master's Degree"


class TestFeederCips:
    def test_maps_and_orders_as_returned(self):
        pool = FakePool([[("2701", "Mathematics", 400, 9000),
                          ("2703", "Applied Mathematics", 65, 3000)]])
        feeders = feeder_cips(pool, 42, ("Master's Degree", "Doctoral Degree"), limit=2)
        assert feeders == [Feeder("2701", "Mathematics", 400, 9000),
                           Feeder("2703", "Applied Mathematics", 65, 3000)]

    def test_binds_levels_and_occupation(self):
        pool = FakePool([[]])
        feeder_cips(pool, 42, ("Master's Degree", "Doctoral Degree"), limit=3)
        sql, binds = pool.cursor_obj.calls[0]
        assert binds["oid"] == 42 and binds["n"] == 3
        assert binds["l0"] == "Master's Degree" and binds["l1"] == "Doctoral Degree"
        assert "program_occupations" in sql

    def test_orders_by_fused_similarity_and_graduate_rank(self):
        pool = FakePool([[]])
        feeder_cips(pool, 42, ("Bachelor's Degree",))
        sql, _ = pool.cursor_obj.calls[0]
        assert "vector_distance(o.embedding, c.embedding, cosine)" in sql
        assert "rank_sim" in sql and "rank_awards" in sql


class TestResolveAnchor:
    def test_returns_centroid_of_the_citys_institutions(self):
        pool = FakePool([[(-122.27, 37.87, 3)]])
        anchor = resolve_anchor(pool, "Berkeley, CA")
        assert anchor == Anchor("Berkeley, CA", 37.87, -122.27)

    def test_none_when_city_has_no_institutions(self):
        pool = FakePool([[(None, None, 0)]])
        assert resolve_anchor(pool, "Nowhere, ZZ") is None

    def test_none_for_unparseable_text_without_querying(self):
        pool = FakePool([])
        assert resolve_anchor(pool, "the Bay Area") is None
        assert pool.cursor_obj.calls == []


class TestNearbyPrograms:
    ROW = ("California State University-East Bay", "Hayward", "CA", "Master's Degree",
           "2705", "Statistics", 18.8, None, 142606.0, None, 18000.0)

    def test_maps_rows(self):
        pool = FakePool([[self.ROW]])
        found = nearby_programs(pool, ["2705"], ("Master's Degree",),
                                Anchor("Berkeley, CA", 37.87, -122.27), 100, 5)
        assert found == [NearbyProgram(
            institution="California State University-East Bay", city="Hayward",
            state="CA", credential_level="Master's Degree", cip_code="2705",
            program_name="Statistics", distance_mi=18.8, e1=None, e4=142606.0,
            e7=None, debt=18000.0)]

    def test_uses_spatial_filter_and_bound_lists(self):
        pool = FakePool([[]])
        nearby_programs(pool, ["2701", "2705"], ("Master's Degree", "Doctoral Degree"),
                        Anchor("Berkeley, CA", 37.87, -122.27), 100, 5)
        sql, binds = pool.cursor_obj.calls[0]
        assert "sdo_within_distance" in sql and "distance=100 unit=mile" in sql
        assert binds["c0"] == "2701" and binds["c1"] == "2705"
        assert binds["l0"] == "Master's Degree" and binds["l1"] == "Doctoral Degree"
        assert binds["lat"] == 37.87 and binds["lon"] == -122.27 and binds["n"] == 5

    def test_empty_cip_list_returns_nothing_without_querying(self):
        pool = FakePool([])
        assert nearby_programs(pool, [], ("Master's Degree",),
                               Anchor("x", 1.0, 2.0), 50, 5) == []
        assert pool.cursor_obj.calls == []

    def test_radius_is_coerced_to_int(self):
        pool = FakePool([[]])
        nearby_programs(pool, ["2705"], ("Master's Degree",),
                        Anchor("x", 1.0, 2.0), 75.9, 5)
        assert "distance=75 unit=mile" in pool.cursor_obj.calls[0][0]


class TestBuildOutcomesContext:
    def _outcome(self):
        return ProgramOutcome("2703", "Applied Mathematics", "Bachelor's Degree",
                              65, 60, 63684.5, 80000.0, 92803.0, 105000.0,
                              130000.0, 20500.0)

    def test_major_block_rounds_and_labels(self):
        match = MajorMatch("B.A. Applied Mathematics", "2703", "Applied Mathematics", 0.7541)
        context = build_outcomes_context([(match, self._outcome())], None, [], None)
        (major,) = context["your_majors"]
        assert major["stated_major"] == "B.A. Applied Mathematics"
        assert major["matched_field"] == "Applied Mathematics"
        assert major["median_earnings_1yr"] == 63685
        assert major["median_earnings_4yr"] == 92803
        assert major["median_earnings_7yr"] == 130000
        assert major["typical_debt"] == 20500
        assert major["programs_with_published_earnings"] == 60

    def test_major_without_outcome_is_omitted(self):
        match = MajorMatch("x", "9999", "Obscure", 0.9)
        context = build_outcomes_context([(match, None)], None, [], None)
        assert context["your_majors"] == []

    def test_nearby_programs_carry_only_published_numbers(self):
        near = NearbyProgram("CSU East Bay", "Hayward", "CA", "Master's Degree",
                             "2705", "Statistics", 18.84, None, 142606.0, None, None)
        context = build_outcomes_context(
            [], [Feeder("2705", "Statistics", 72, 800)], [near],
            Anchor("Berkeley, CA", 37.87, -122.27))
        (program,) = context["graduate_programs_nearby"]["programs"]
        assert program == {
            "institution": "CSU East Bay", "field": "Statistics",
            "credential": "Master's Degree", "miles_away": 18.8,
            "median_earnings_4yr": 142606}
        assert context["graduate_programs_nearby"]["near"] == "Berkeley, CA"
        assert context["aspiration_feeder_fields"] == ["Statistics"]

    def test_methodology_note_states_the_caveats(self):
        context = build_outcomes_context([], None, [], None)
        note = context["methodology"]
        assert "College Scorecard" in note and "suppressed" in note


class TestTopPrograms:
    ROW = ("Massachusetts Institute of Technology", "Cambridge", "MA", "Master's Degree",
           "2705", "Statistics", None, None, 190000.0, None, 25000.0)

    def test_orders_by_published_earnings_and_has_no_distance(self):
        pool = FakePool([[self.ROW]])
        (top,) = top_programs(pool, ["2705"], ("Master's Degree",), limit=3)
        assert top.institution == "Massachusetts Institute of Technology"
        assert top.e4 == 190000.0 and top.distance_mi is None
        sql, binds = pool.cursor_obj.calls[0]
        assert "median_earnings_4yr is not null" in sql
        assert binds["c0"] == "2705" and binds["l0"] == "Master's Degree" and binds["n"] == 3

    def test_empty_cip_list_does_not_query(self):
        pool = FakePool([])
        assert top_programs(pool, [], ("Master's Degree",)) == []
        assert pool.cursor_obj.calls == []


class TestFormatReport:
    def _major(self):
        match = MajorMatch("B.A. Applied Mathematics", "2703", "Applied Mathematics", 0.754)
        outcome = ProgramOutcome("2703", "Applied Mathematics", "Bachelor's Degree",
                                 65, 45, 63684.0, 74740.0, 92803.0, 104454.0, 107541.0, 20500.0)
        return match, outcome

    def test_major_lines_show_each_published_horizon(self):
        lines = format_outcomes_report([self._major()], "Mathematicians", [], [], [], None, 100)
        text = "\n".join(lines)
        assert "DEGREE OUTCOMES" in text
        assert '"B.A. Applied Mathematics" -> Applied Mathematics (CIP 2703, sim 0.754)' in text
        assert "$63.7k @1yr" in text and "$92.8k @4yr" in text and "$107.5k @7yr" in text
        assert "$74.7k-$104.5k" in text
        assert "typical debt $20.5k" in text
        assert "4yr earnings published for 45 of 65 programs" in text

    def test_unpublished_horizons_are_said_not_invented(self):
        match = MajorMatch("Data Science", "3070", "Data Science", 1.0)
        outcome = ProgramOutcome("3070", "Data Science", "Bachelor's Degree",
                                 16, 0, 87360.0, None, None, None, None, 19590.0)
        text = "\n".join(format_outcomes_report(
            [(match, outcome)], None, [], [], [], None, 100))
        assert "$87.4k @1yr" in text
        assert "@4yr" not in text and "@7yr" not in text
        assert "not yet published" in text

    def test_aspiration_block_lists_nearby_and_best_paid(self):
        near = NearbyProgram("CSU East Bay", "Hayward", "CA", "Master's Degree", "2705",
                             "Statistics", 18.6, None, 142606.0, None, None)
        top = NearbyProgram("MIT", "Cambridge", "MA", "Master's Degree", "2705",
                            "Statistics", None, None, 190000.0, None, None)
        feeders = [Feeder("2703", "Applied Mathematics", 51, 3098),
                   Feeder("2705", "Statistics", 31, 1950)]
        text = "\n".join(format_outcomes_report(
            [], "Mathematicians", feeders, [near], [top],
            Anchor("Berkeley, CA", 37.87, -122.27), 100))
        assert "TO REACH Mathematicians" in text
        assert "Applied Mathematics, Statistics" in text
        assert "within 100 mi of Berkeley, CA" in text
        assert "18.6 mi" in text and "CSU East Bay" in text and "$142.6k @4yr" in text
        assert "best-paid nationally" in text and "MIT" in text and "$190.0k @4yr" in text

    def test_no_nearby_programs_is_stated(self):
        text = "\n".join(format_outcomes_report(
            [], "Mathematicians", [Feeder("2705", "Statistics", 1, 1)], [], [],
            Anchor("Berkeley, CA", 37.87, -122.27), 100))
        assert "no programs with published outcomes" in text

    def test_empty_report_is_empty(self):
        assert format_outcomes_report([], None, [], [], [], None, 100) == []


class TestGatherOutcomes:
    def _stub(self, monkeypatch, calls):
        from navigator import outcomes
        match = MajorMatch("B.A. Applied Mathematics", "2703", "Applied Mathematics", 0.75)
        outcome = ProgramOutcome("2703", "Applied Mathematics", "Bachelor's Degree",
                                 65, 45, 63684.0, 74740.0, 92803.0, 104454.0,
                                 107541.0, 20500.0)
        near = NearbyProgram("CSU East Bay", "Hayward", "CA", "Master's Degree", "2705",
                             "Statistics", 18.6, None, 142606.0, None, None)

        def fake_match(pool, majors, thread_id=None):
            calls.append(("match", list(majors)))
            return [match]

        def fake_outcome(pool, cip, title, level):
            calls.append(("outcome", cip, level))
            return outcome

        def fake_feeders(pool, oid, levels, limit=3):
            calls.append(("feeders", oid, tuple(levels)))
            return [Feeder("2705", "Statistics", 31, 1950)]

        def fake_anchor(pool, text):
            calls.append(("anchor", text))
            return Anchor("Berkeley, CA", 37.87, -122.27)

        def fake_nearby(pool, cips, levels, anchor, radius, limit=5, thread_id=None):
            calls.append(("nearby", list(cips), tuple(levels), radius))
            return [near]

        def fake_top(pool, cips, levels, limit=3):
            calls.append(("top", list(cips)))
            return []

        for name, fn in [("match_majors", fake_match), ("program_outcome", fake_outcome),
                         ("feeder_cips", fake_feeders), ("resolve_anchor", fake_anchor),
                         ("nearby_programs", fake_nearby), ("top_programs", fake_top)]:
            monkeypatch.setattr(outcomes, name, fn)

    def test_full_report_for_a_student_aiming_at_a_zone_five_role(self, monkeypatch):
        calls: list = []
        self._stub(monkeypatch, calls)
        report = gather_outcomes(
            None, ["B.A. Applied Mathematics"], "B.A.", aspiration_title="Mathematicians",
            aspiration_id=42, aspiration_zone=5, location_text="Berkeley, CA")
        assert isinstance(report, OutcomeReport)
        assert ("outcome", "2703", "Bachelor's Degree") in calls
        assert ("feeders", 42, ("Master's Degree", "Doctoral Degree")) in calls
        assert any(c[0] == "nearby" and c[3] == 100 for c in calls)
        text = "\n".join(report.lines)
        assert "DEGREE OUTCOMES" in text and "TO REACH Mathematicians" in text
        assert "graduate fields" in text
        assert report.context["your_majors"][0]["matched_field"] == "Applied Mathematics"
        assert report.context["graduate_programs_nearby"]["near"] == "Berkeley, CA"

    def test_lower_zone_aspiration_uses_bachelors_feeders(self, monkeypatch):
        calls: list = []
        self._stub(monkeypatch, calls)
        gather_outcomes(None, [], None, aspiration_title="Data Scientists",
                        aspiration_id=7, aspiration_zone=4, location_text=None)
        assert ("feeders", 7, ("Bachelor's Degree",)) in calls

    def test_no_location_skips_the_spatial_leg(self, monkeypatch):
        calls: list = []
        self._stub(monkeypatch, calls)
        from navigator import outcomes
        monkeypatch.setattr(outcomes, "resolve_anchor", lambda pool, text: None)
        report = gather_outcomes(None, [], None, aspiration_title="Mathematicians",
                                 aspiration_id=42, aspiration_zone=5, location_text="the Bay")
        assert not any(c[0] == "nearby" for c in calls)
        assert "graduate_programs_nearby" not in (report.context or {})

    def test_nothing_to_say_returns_no_context(self, monkeypatch):
        from navigator import outcomes
        monkeypatch.setattr(outcomes, "match_majors", lambda pool, majors, thread_id=None: [])
        monkeypatch.setattr(outcomes, "resolve_anchor", lambda pool, text: None)
        report = gather_outcomes(None, [], None, aspiration_title=None, aspiration_id=None,
                                 aspiration_zone=None, location_text=None)
        assert report.lines == [] and report.context is None

    def test_near_override_wins_over_profile_location(self, monkeypatch):
        calls: list = []
        self._stub(monkeypatch, calls)
        gather_outcomes(None, [], None, aspiration_title="Mathematicians", aspiration_id=42,
                        aspiration_zone=5, location_text="Boston, MA", near="Berkeley, CA")
        assert ("anchor", "Berkeley, CA") in calls
