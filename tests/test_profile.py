"""Tests for profile intake and A-inference logic (design sections 6.1, 7.1)."""

from __future__ import annotations

import pytest

from navigator.profile import (
    Candidate,
    ExperienceInput,
    ProfileInput,
    compose_embedding_source,
    infer_job_zone,
    pick_start,
)


class TestInferJobZone:
    @pytest.mark.parametrize(
        ("stated", "zone"),
        [
            ("bachelor's", 4),
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


class TestPickStart:
    def test_top_match_kept_when_zone_satisfied(self):
        choice, clamped, failed = pick_start(_candidates(4, 5), inferred_zone=4)
        assert (choice.occupation_id, clamped, failed) == (0, False, False)

    def test_top_match_kept_when_no_inferred_zone(self):
        choice, clamped, failed = pick_start(_candidates(2, 5), inferred_zone=None)
        assert (choice.occupation_id, clamped, failed) == (0, False, False)

    def test_clamp_walks_down_to_first_qualifying_zone(self):
        choice, clamped, failed = pick_start(_candidates(3, 3, 5), inferred_zone=4)
        assert (choice.occupation_id, clamped, failed) == (2, True, False)

    def test_clamp_failure_accepts_top_and_flags(self):
        choice, clamped, failed = pick_start(_candidates(3, 3, 3), inferred_zone=5)
        assert (choice.occupation_id, clamped, failed) == (0, False, True)

    def test_null_zone_candidate_never_satisfies_clamp(self):
        choice, clamped, failed = pick_start(_candidates(3, None, 5), inferred_zone=4)
        assert (choice.occupation_id, clamped, failed) == (2, True, False)
