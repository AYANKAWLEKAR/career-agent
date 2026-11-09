"""Tests for profile intake and A-inference logic (design sections 6.1, 7.1)."""

from __future__ import annotations

import pytest

from navigator.profile import (
    Candidate,
    ExperienceInput,
    ProfileInput,
    compose_embedding_source,
    education_looks_in_progress,
    infer_job_zone,
    infer_start_band,
    pick_start,
)


class TestInferJobZone:
    @pytest.mark.parametrize(
        ("stated", "zone"),
        [
            ("bachelor's", 4),
            ("B.A.", 4),
            ("undergraduate", 4),
            ("Undergraduate, B.A. expected 2028", 4),
            ("postgraduate degree", 5),
            ("B.S. Computer Science", 4),
            ("M.S. in progress", 5),
            ("Bachelor of Science", 4),
            ("master's degree", 5),
            ("PhD in progress", 5),
            ("MBA", 5),
            ("associate's", 3),
            ("some college, no degree", 3),
            ("certificate program", 3),
            ("high school diploma", 2),
            ("GED", 2),
            ("some high school", 1),
            (None, None),
            ("", None),
            ("unrecognizable text", None),
        ],
    )
    def test_maps_section_7_1_table(self, stated, zone):
        assert infer_job_zone(stated) == zone

    def test_some_high_school_wins_over_high_school_substring(self):
        # "some high school" contains "high school"; rule order must resolve to 1.
        assert infer_job_zone("some high school") == 1


class TestComposeEmbeddingSource:
    def test_uses_title_and_majors_but_not_experiences(self):
        # Experiences carry their own vectors; folding them in here recreates
        # the blurred-mean failure the per-experience vectors exist to avoid.
        profile = ProfileInput(
            user_ref="u",
            raw_text="full resume text",
            current_title="Web Developer",
            education_majors=["Computer Science", "Statistics"],
            experiences=[
                ExperienceInput(raw_text="Built dashboards in React."),
                ExperienceInput(raw_text="Genomics research in R."),
            ],
        )
        source = compose_embedding_source(profile)
        assert source.splitlines() == [
            "Web Developer",
            "Education: Computer Science, Statistics",
        ]

    def test_falls_back_to_raw_text_when_nothing_extracted(self):
        profile = ProfileInput(user_ref="u", raw_text="I do things with computers")
        assert compose_embedding_source(profile) == "I do things with computers"


def _candidates(*zones: int | None) -> list[Candidate]:
    return [
        Candidate(i, f"15-000{i}.00", f"Occ {i}", z, 0.9 - i * 0.1)
        for i, z in enumerate(zones)
    ]


class TestEducationLooksInProgress:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("bachelor's (in progress)", True),
            ("B.A. expected Spring 2028", True),
            ("PhD candidate", True),
            ("junior year, B.S. Computer Science", True),
            ("bachelor's", False),
            (None, False),
        ],
    )
    def test_markers(self, text, expected):
        assert education_looks_in_progress(text) is expected

    def test_undergraduate_is_in_progress(self):
        # "undergraduate" must imply in-progress AND map to the bachelor tier,
        # never to zone 5 via its "graduate" substring.
        assert education_looks_in_progress("undergraduate") is True
        assert infer_job_zone("undergraduate") == 4


class TestInferStartBand:
    def test_completed_bachelors_starts_at_zone_4(self):
        assert infer_start_band(4, False, 1.0) == (4, 4)

    def test_bachelors_in_progress_caps_at_zone_3(self):
        # The student fix: a degree in progress supports the tier below it.
        assert infer_start_band(4, True, 2.0) == (3, 3)

    def test_four_years_experience_buys_one_tier(self):
        assert infer_start_band(4, False, 4.0) == (4, 5)
        assert infer_start_band(4, True, 4.0) == (3, 4)

    def test_masters_holder_reaches_zone_5(self):
        assert infer_start_band(5, False, 0.0) == (5, 5)

    def test_phd_in_progress_is_a_grad_student_at_zone_4(self):
        assert infer_start_band(5, True, 1.0) == (4, 4)

    def test_unknown_education_has_no_floor_and_conservative_ceiling(self):
        assert infer_start_band(None, False, 1.0) == (None, 3)
        assert infer_start_band(None, False, 6.0) == (None, 4)

    def test_high_school_in_progress_bottoms_out_at_zone_1(self):
        assert infer_start_band(2, True, None) == (1, 1)


def _cand(oid: int, zone: int | None, sim: float) -> Candidate:
    return Candidate(oid, f"15-{oid:04d}.00", f"Occ {oid}", zone, sim)


class TestPickStart:
    def test_strongest_in_band_candidate_wins(self):
        candidates = [_cand(1, 5, 0.9), _cand(2, 4, 0.8), _cand(3, 4, 0.7)]
        d = pick_start(candidates, floor_zone=4, ceiling_zone=4)
        assert d.choice.occupation_id == 2
        assert d.clamped and d.ceiling_applied
        assert not d.ceiling_unmet
        assert [a.occupation_id for a in d.aspirations] == [1]

    def test_top_match_kept_when_inside_band(self):
        candidates = [_cand(1, 4, 0.9), _cand(2, 5, 0.8)]
        d = pick_start(candidates, floor_zone=4, ceiling_zone=4)
        assert d.choice.occupation_id == 1
        assert not d.clamped and not d.ceiling_applied

    def test_below_floor_fallback_flags_floor_unmet(self):
        # Tax-master's case: evidence is all below the education floor.
        candidates = [_cand(1, 3, 0.9), _cand(2, 3, 0.8)]
        d = pick_start(candidates, floor_zone=5, ceiling_zone=5)
        assert d.choice.occupation_id == 1
        assert d.floor_unmet and not d.ceiling_unmet

    def test_all_above_ceiling_starts_lowest_zone_and_flags(self):
        # Student case: every match is zone 4-5, ceiling is 3.
        candidates = [_cand(1, 5, 0.9), _cand(2, 4, 0.7), _cand(3, 5, 0.8)]
        d = pick_start(candidates, floor_zone=3, ceiling_zone=3)
        assert d.choice.occupation_id == 2   # lowest zone wins, not similarity
        assert d.ceiling_unmet
        assert [a.occupation_id for a in d.aspirations] == [1, 3]

    def test_experience_gate_filters_in_band_candidates(self):
        candidates = [_cand(1, 4, 0.9), _cand(2, 4, 0.8)]
        d = pick_start(candidates, floor_zone=4, ceiling_zone=4,
                       experience_ok={2})
        assert d.choice.occupation_id == 2
        assert not d.experience_gate_relaxed

    def test_experience_gate_relaxes_rather_than_failing(self):
        candidates = [_cand(1, 4, 0.9)]
        d = pick_start(candidates, floor_zone=4, ceiling_zone=4,
                       experience_ok=set())
        assert d.choice.occupation_id == 1
        assert d.experience_gate_relaxed

    def test_ceiling_unmet_prefers_gate_passing_and_flags_when_none_pass(self):
        # Rule 4 must consult the gate: a gate-passing zone-5 beats a
        # gate-failing zone-4? No -- lowest zone still wins among gate-passers.
        candidates = [_cand(1, 4, 0.9), _cand(2, 5, 0.8)]
        d = pick_start(candidates, floor_zone=3, ceiling_zone=3,
                       experience_ok={2})
        assert d.choice.occupation_id == 2      # only gate-passer
        assert d.ceiling_unmet and not d.experience_gate_relaxed
        # And when nothing passes, the relaxation is flagged.
        d = pick_start(candidates, floor_zone=3, ceiling_zone=3,
                       experience_ok=set())
        assert d.choice.occupation_id == 1      # lowest zone overall
        assert d.ceiling_unmet and d.experience_gate_relaxed

    def test_gate_blocked_in_band_candidate_becomes_aspiration(self):
        # Strong zone-3 match blocked only by the experience gate; start falls
        # to zone 2. The blocked match must surface as an aspiration.
        candidates = [_cand(1, 3, 0.9), _cand(2, 2, 0.5)]
        d = pick_start(candidates, floor_zone=3, ceiling_zone=3,
                       experience_ok={2})
        assert d.choice.occupation_id == 2 and d.floor_unmet
        assert [a.occupation_id for a in d.aspirations] == [1]

    def test_no_floor_means_anything_at_or_below_ceiling_qualifies(self):
        candidates = [_cand(1, 5, 0.9), _cand(2, 2, 0.5)]
        d = pick_start(candidates, floor_zone=None, ceiling_zone=3)
        assert d.choice.occupation_id == 2

    def test_null_zone_candidates_never_start(self):
        candidates = [_cand(1, None, 0.9), _cand(2, 4, 0.5)]
        d = pick_start(candidates, floor_zone=4, ceiling_zone=4)
        assert d.choice.occupation_id == 2

    def test_no_aspirations_when_start_already_at_top_zone(self):
        # ceiling_unmet start at z5: same-zone peers are not "aspirations".
        candidates = [_cand(i, 5, 0.9 - i * 0.1) for i in range(1, 6)]
        d = pick_start(candidates, floor_zone=3, ceiling_zone=3)
        assert d.ceiling_unmet and d.aspirations == []

    def test_ceiling_unmet_and_ceiling_applied_are_mutually_exclusive(self):
        candidates = [_cand(1, 5, 0.9), _cand(2, 4, 0.7)]
        d = pick_start(candidates, floor_zone=3, ceiling_zone=3)
        assert d.ceiling_unmet and not d.ceiling_applied
        # aspirations sit strictly above the z4 start
        assert [a.occupation_id for a in d.aspirations] == [1]

    def test_aspirations_capped_at_three(self):
        candidates = [_cand(9, 4, 0.95)] + [
            _cand(i, 5, 0.9 - i * 0.1) for i in range(1, 6)
        ]
        d = pick_start(candidates, floor_zone=4, ceiling_zone=4)
        assert d.choice.occupation_id == 9
        assert len(d.aspirations) == 3
        assert d.choice.occupation_id not in {a.occupation_id for a in d.aspirations}
