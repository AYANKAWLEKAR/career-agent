"""Tests for path ranking and the LLM parse/narration plumbing."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from navigator.llm import (
    AnthropicBackend,
    OllamaBackend,
    ParsedExperience,
    ParsedResume,
    build_narration_context,
    parse_resume,
    parsed_to_profile,
)
from navigator.paths import (
    Hop,
    Path,
    overlay_wages,
    paths_to_destination,
    rank_paths,
)
from navigator.profile import Candidate, MatchResult, ProfileInput


def _hop(dst: str, weight: float, edge_type: str = "career_ladder",
         src_wage: float | None = None, dst_wage: float | None = None) -> Hop:
    return Hop(
        src_title="Src", dst_title=dst, src_zone=3, dst_zone=4,
        edge_type=edge_type, weight=weight,
        src_wage=src_wage, dst_wage=dst_wage,
    )


class TestHopWages:
    def test_wage_delta_requires_both_ends(self):
        assert _hop("A", 0.8).wage_delta is None
        assert _hop("A", 0.8, src_wage=100_000.0).wage_delta is None
        assert _hop("A", 0.8, src_wage=100_000.0,
                    dst_wage=121_500.0).wage_delta == 21_500.0


class TestRankPaths:
    def test_keeps_cheapest_path_per_destination(self):
        cheap = Path(hops=[_hop("Analyst", 0.9)])
        dear = Path(hops=[_hop("X", 0.5), _hop("Analyst", 0.5)])
        ranked = rank_paths([dear, cheap])
        assert [p.destination for p in ranked].count("Analyst") == 1
        assert ranked[0].cost == cheap.cost

    def test_orders_by_total_cost_and_truncates(self):
        paths = [Path(hops=[_hop(f"D{i}", w)]) for i, w in enumerate([0.5, 0.9, 0.7])]
        ranked = rank_paths(paths, top_k=2)
        assert [p.destination for p in ranked] == ["D1", "D2"]

    def test_cost_sums_one_minus_weight_across_hops(self):
        path = Path(hops=[_hop("A", 0.8), _hop("B", 0.6)])
        assert abs(path.cost - 0.6) < 1e-9


class TestPathsToDestination:
    def test_filters_to_named_destination_cheapest_first(self):
        direct = Path(hops=[_hop("Statisticians", 0.6)])
        via = Path(hops=[_hop("X", 0.9), _hop("Statisticians", 0.9)])
        other = Path(hops=[_hop("Economists", 0.9)])
        routes = paths_to_destination([direct, via, other], "Statisticians")
        assert [p.cost for p in routes] == [via.cost, direct.cost]
        assert all(p.destination == "Statisticians" for p in routes)

    def test_no_route_returns_empty(self):
        assert paths_to_destination([Path(hops=[_hop("A", 0.5)])], "B") == []


class TestOverlayWages:
    def test_scoped_rows_win_and_national_fills_gaps(self):
        national = {1: 100_000.0, 2: 90_000.0, 3: 80_000.0}
        alameda = {1: 135_000.0}
        merged = overlay_wages(national, alameda)
        assert merged == {1: 135_000.0, 2: 90_000.0, 3: 80_000.0}
        # inputs are not mutated
        assert national[1] == 100_000.0

    def test_empty_scope_is_identity(self):
        national = {1: 100_000.0}
        assert overlay_wages(national, {}) == national


class TestParsedToProfile:
    def test_maps_all_fields_and_preserves_experience_order(self):
        parsed = ParsedResume(
            current_title="PM Intern",
            years_experience=1.5,
            education_level="bachelor's",
            education_in_progress=False,
            education_majors=["Applied Mathematics", "Data Science"],
            location_text="Berkeley, CA",
            experiences=[
                ParsedExperience(accomplishment_summary="Newest thing.", title="A",
                                 organization="Oracle", duration_months=3),
                ParsedExperience(accomplishment_summary="Older thing.", title="B",
                                 organization=None, duration_months=1_200_000_000),
            ],
        )
        profile = parsed_to_profile("u@x", "raw resume", parsed)
        assert profile.current_title == "PM Intern"
        assert profile.education_majors == ["Applied Mathematics", "Data Science"]
        assert [e.raw_text for e in profile.experiences] == ["Newest thing.", "Older thing."]
        assert profile.experiences[0].organization == "Oracle"

    def test_out_of_range_numerics_become_none_not_errors(self):
        # Observed live: a local model emitted duration_months=1.2e15, which
        # Oracle's number(5) column rejects with ORA-01438.
        parsed = ParsedResume(
            current_title=None, years_experience=9999.0, education_level=None,
            education_in_progress=False, education_majors=[], location_text=None,
            experiences=[ParsedExperience(accomplishment_summary="x", title=None,
                                          organization=None,
                                          duration_months=1_200_000_000)],
        )
        profile = parsed_to_profile("u", "r", parsed)
        assert profile.years_experience is None
        assert profile.experiences[0].duration_months is None


class TestClipping:
    def test_long_parse_fields_are_clipped_to_column_widths(self):
        parsed = ParsedResume(
            current_title="T" * 400, years_experience=1.0,
            education_level="Bachelor of Science in " + "x" * 60,
            education_in_progress=False, education_majors=[],
            location_text="L" * 300,
            experiences=[ParsedExperience(
                accomplishment_summary="s", title="t" * 400,
                organization="o" * 400, duration_months=1)],
        )
        profile = parsed_to_profile("u", "r", parsed)
        assert len(profile.current_title) == 300
        assert len(profile.education_level) == 50
        assert len(profile.location_text) == 200
        assert len(profile.experiences[0].title) == 300
        assert len(profile.experiences[0].organization) == 300


class TestNarrationContext:
    def _match(self) -> MatchResult:
        best = Candidate(1, "15-1252.00", "Software Developers", 4, 0.61)
        return MatchResult(
            profile_id=7, matched=best,
            candidates=[best, Candidate(2, "15-1254.00", "Web Developers", 3, 0.55)],
            zone_clamped=True, zone_clamp_failed=False,
        )

    def test_context_carries_every_number_the_story_may_cite(self):
        profile = ProfileInput(user_ref="u", raw_text="r", current_title="Dev")
        paths = [Path(hops=[_hop("Database Architects", 0.71)])]
        context = build_narration_context(profile, self._match(), paths)
        assert context["starting_point"]["matched_occupation"] == "Software Developers"
        assert context["starting_point"]["start_moved_off_top_match"] is True
        hop = context["surfaced_paths"][0]["hops"][0]
        assert hop == {
            "from": "Src", "to": "Database Architects", "move": "up one zone",
            "from_zone": 3, "to_zone": 4, "skill_transfer_weight": 0.71,
        }
        assert "thresholds" in context["methodology_notes"]

    def test_lateral_hops_are_labelled_lateral(self):
        profile = ProfileInput(user_ref="u", raw_text="r")
        paths = [Path(hops=[_hop("Peers", 0.8, edge_type="skill_overlap")])]
        context = build_narration_context(profile, self._match(), paths)
        assert context["surfaced_paths"][0]["hops"][0]["move"] == "lateral"

    def test_hop_wages_surface_only_when_both_ends_priced(self):
        profile = ProfileInput(user_ref="u", raw_text="r")
        paths = [Path(hops=[
            _hop("Priced", 0.8, src_wage=110_000.0, dst_wage=132_000.0),
            _hop("Unpriced", 0.7),
        ])]
        context = build_narration_context(profile, self._match(), paths)
        priced, unpriced = context["surfaced_paths"][0]["hops"]
        assert priced["median_wage_from"] == 110_000.0
        assert priced["median_wage_to"] == 132_000.0
        assert priced["wage_delta"] == 22_000.0
        # A hop without wages must not carry null wage keys the model could
        # misread as "zero pay" — the keys are simply absent.
        assert "wage_delta" not in unpriced and "median_wage_from" not in unpriced
        assert "wages" in context["methodology_notes"]

    def test_wage_methodology_note_names_the_scope(self):
        profile = ProfileInput(user_ref="u", raw_text="r")
        paths = [Path(hops=[_hop("A", 0.8)])]
        default = build_narration_context(profile, self._match(), paths)
        assert "national" in default["methodology_notes"]["wages"]
        county = build_narration_context(
            profile, self._match(), paths, wage_scope="alameda")
        assert "Alameda County" in county["methodology_notes"]["wages"]
        assert "national" in county["methodology_notes"]["wages"]  # fallback named


class TestAnthropicBackend:
    def test_parse_uses_structured_output_and_returns_parsed_object(self):
        parsed = ParsedResume(
            current_title="X", years_experience=None, education_level=None,
            education_in_progress=False, education_majors=[], location_text=None,
            experiences=[],
        )
        captured = {}

        def fake_parse(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(parsed_output=parsed)

        client = SimpleNamespace(messages=SimpleNamespace(parse=fake_parse))
        result = parse_resume("resume text", backend=AnthropicBackend(client=client))
        assert result is parsed
        assert captured["output_format"] is ParsedResume
        assert captured["messages"][0]["content"] == "resume text"

    def test_parse_raises_when_no_structured_output(self):
        client = SimpleNamespace(messages=SimpleNamespace(
            parse=lambda **kw: SimpleNamespace(parsed_output=None, stop_reason="refusal")
        ))
        with pytest.raises(RuntimeError, match="no structured output"):
            AnthropicBackend(client=client).parse("sys", "user")


class TestOllamaBackend:
    def _respond(self, monkeypatch, payload, captured):
        def fake_post(url, json=None, timeout=None):
            captured["url"] = url
            captured["payload"] = json
            return SimpleNamespace(
                raise_for_status=lambda: None, json=lambda: payload
            )
        monkeypatch.setattr("navigator.llm.httpx.post", fake_post)

    def test_parse_constrains_decoding_to_the_schema(self, monkeypatch):
        captured: dict = {}
        content = ('{"current_title": "Dev", "years_experience": 1.0, '
                   '"education_level": null, "education_in_progress": false, '
                   '"education_majors": [], '
                   '"location_text": null, "experiences": []}')  # schema-valid
        self._respond(monkeypatch, {"message": {"content": content}}, captured)
        parsed = OllamaBackend(model="qwen3:8b").parse("sys", "resume")
        assert parsed.current_title == "Dev"
        assert captured["payload"]["format"] == ParsedResume.model_json_schema()
        assert captured["payload"]["think"] is False

    def test_complete_strips_leaked_thinking(self, monkeypatch):
        captured: dict = {}
        self._respond(monkeypatch, {"message": {"content":
            "<think>internal</think>\nThe story."},
            "prompt_eval_count": 10, "eval_count": 5}, captured)
        text, tokens_in, tokens_out = OllamaBackend(model="m").complete("sys", "user")
        assert text == "The story."
        assert (tokens_in, tokens_out) == (10, 5)
