"""User profile intake and A-inference: the entry point into the occupation graph.

Design sections 6.1 (profile tables) and 7.1 (A-inference). The user describes what
they have done in natural language -- effectively a resume. The profile and each
experience are embedded separately with the in-database ONNX model, and A-inference
scores every occupation against ALL of those vectors, taking the strongest signal:
averaging a resume into one vector lands it between all its entries and near none.

The matched occupation is cached on the profile row so a wrong A is visible and
correctable rather than buried in an ephemeral call, and the top-5 candidates are
logged to tool_log so a bad match is diagnosable.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

MODEL_NAME = "ALL_MINILM_L12_V2"
TOP_CANDIDATES = 5

# Experience vectors decay by resume position: ordinal 0 (most recent) scores at
# full strength, each older entry 10% less, floored at half. Without this, a
# strong semantic hit from an old internship outranks the user's current job --
# observed directly: a genomics internship matched Bioinformatics Scientists
# harder than the current frontend role matched Web Developers. Resume order is
# stored precisely because recency is signal; this is where it gets used.
RECENCY_DECAY = 0.10
RECENCY_FLOOR = 0.5

# Section 7.1: education_level maps to inferred_job_zone per O*NET's own
# job-zone definitions. Checked in order; first match wins, so the more
# specific phrase ("some high school") precedes the phrase it contains.
_ZONE_RULES: tuple[tuple[tuple[str, ...], int], ...] = (
    (("graduate", "master", "phd", "doctor", "professional degree", "mba", "md", "jd"), 5),
    (("bachelor",), 4),
    (("associate", "some college", "certificate"), 3),
    (("some high school", "no diploma", "none"), 1),
    (("high school", "ged"), 2),
)


@dataclass
class ExperienceInput:
    raw_text: str
    title: str | None = None
    organization: str | None = None
    duration_months: int | None = None


@dataclass
class ProfileInput:
    user_ref: str
    raw_text: str
    current_title: str | None = None
    years_experience: float | None = None
    education_level: str | None = None
    education_majors: list[str] = field(default_factory=list)
    location_text: str | None = None
    experiences: list[ExperienceInput] = field(default_factory=list)


@dataclass
class Candidate:
    occupation_id: int
    soc_code: str
    title: str
    job_zone: int | None
    similarity: float


@dataclass
class MatchResult:
    profile_id: int
    matched: Candidate
    candidates: list[Candidate]
    zone_clamped: bool
    zone_clamp_failed: bool


def infer_job_zone(education_level: str | None) -> int | None:
    """Map a stated education level onto O*NET job zones 1-5 (section 7.1)."""
    if not education_level:
        return None
    text = education_level.lower()
    for keywords, zone in _ZONE_RULES:
        if any(k in text for k in keywords):
            return zone
    return None


def compose_embedding_source(profile: ProfileInput) -> str:
    """Assemble the profile document that gets embedded.

    Deliberately EXCLUDES the experiences: they carry their own vectors, and
    folding them in here reintroduces the blurred-mean failure the per-experience
    vectors exist to avoid. Measured on a three-experience resume, the
    concatenated composite matched Biological Technicians above every occupation
    any individual signal pointed at. The profile vector carries what the
    experience vectors cannot: current title and education.

    Kept inspectable in user_profiles.embedding_source for the same reason
    occupations keep theirs: when a match surprises you, read what was embedded.
    """
    parts = []
    if profile.current_title:
        parts.append(profile.current_title)
    if profile.education_majors:
        parts.append("Education: " + ", ".join(profile.education_majors))
    if not parts:
        parts.append(profile.raw_text)
    return "\n".join(parts)


def pick_start(
    candidates: list[Candidate], inferred_zone: int | None
) -> tuple[Candidate, bool, bool]:
    """Apply the section 7.1 zone clamp to a similarity-ordered candidate list.

    If the top match's zone is below the zone the user's education implies, walk
    down and take the first candidate at or above it. If none qualifies, accept
    the top match anyway -- the user's stated education outranking every semantic
    match is a signal worth seeing, not one worth hiding -- and flag the failure.

    Returns (choice, clamped, clamp_failed).
    """
    top = candidates[0]
    if inferred_zone is None or top.job_zone is None or top.job_zone >= inferred_zone:
        return top, False, False
    for candidate in candidates[1:]:
        if candidate.job_zone is not None and candidate.job_zone >= inferred_zone:
            return candidate, True, False
    return top, False, True


def create_profile(pool, profile: ProfileInput) -> int:
    """Insert the profile and its experiences, then embed both in-database."""
    with pool.acquire() as connection:
        cursor = connection.cursor()
        profile_id_var = cursor.var(int)
        cursor.execute(
            """
            insert into user_profiles
              (user_ref, raw_text, current_title, years_experience, education_level,
               education_majors, inferred_job_zone, location_text, embedding_source)
            values (:user_ref, :raw_text, :title, :years, :edu, :majors, :zone, :loc, :src)
            returning profile_id into :pid
            """,
            user_ref=profile.user_ref,
            raw_text=profile.raw_text,
            title=profile.current_title,
            years=profile.years_experience,
            edu=profile.education_level,
            majors=json.dumps(profile.education_majors),
            zone=infer_job_zone(profile.education_level),
            loc=profile.location_text,
            src=compose_embedding_source(profile),
            pid=profile_id_var,
        )
        profile_id = profile_id_var.getvalue()[0]

        cursor.executemany(
            """
            insert into profile_experiences
              (profile_id, ordinal, raw_text, title, organization, duration_months)
            values (:1, :2, :3, :4, :5, :6)
            """,
            [
                (profile_id, i, e.raw_text, e.title, e.organization, e.duration_months)
                for i, e in enumerate(profile.experiences)
            ],
        )

        # Embed with the in-database ONNX model; set-based, one statement each.
        cursor.execute(
            f"""
            update user_profiles
            set embedding = vector_embedding({MODEL_NAME} using embedding_source as data)
            where profile_id = :pid
            """,  # noqa: S608
            pid=profile_id,
        )
        cursor.execute(
            f"""
            update profile_experiences
            set embedding = vector_embedding({MODEL_NAME} using raw_text as data)
            where profile_id = :pid
            """,  # noqa: S608
            pid=profile_id,
        )
        connection.commit()
    return profile_id


def infer_start(pool, profile_id: int, thread_id: str | None = None) -> MatchResult:
    """A-inference: match the profile into the graph and cache the result.

    Every occupation is scored against the profile vector AND each experience
    vector; the best similarity wins. Top candidate becomes A subject to the
    zone clamp; all five are logged to tool_log.
    """
    started = time.monotonic()
    with pool.acquire() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            select inferred_job_zone from user_profiles where profile_id = :pid
            """,
            pid=profile_id,
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError(f"no such profile: {profile_id}")
        inferred_zone = row[0]

        # job_zone is not null excludes the "All Other" catch-all codes: they have
        # no zone and no edges, so matching a user into one strands them with no
        # paths. A must be a real graph entry point.
        cursor.execute(
            """
            select o.occupation_id, o.soc_code, o.title, o.job_zone,
                   max((1 - vector_distance(o.embedding, v.emb, cosine)) * v.wt) as sim
            from occupations o
            cross join (
              select embedding as emb, 1.0 as wt from user_profiles
              where profile_id = :pid and embedding is not null
              union all
              select embedding, greatest(:floor, 1 - :decay * ordinal)
              from profile_experiences
              where profile_id = :pid and embedding is not null
            ) v
            where o.embedding is not null
              and o.job_zone is not null
            group by o.occupation_id, o.soc_code, o.title, o.job_zone
            order by sim desc
            fetch first :n rows only
            """,
            pid=profile_id,
            floor=RECENCY_FLOOR,
            decay=RECENCY_DECAY,
            n=TOP_CANDIDATES,
        )
        candidates = [
            Candidate(int(oid), soc, title, zone, round(float(sim), 4))
            for oid, soc, title, zone, sim in cursor.fetchall()
        ]
        if not candidates:
            raise ValueError(f"profile {profile_id} has no embeddings to match")

        choice, clamped, clamp_failed = pick_start(candidates, inferred_zone)

        cursor.execute(
            """
            update user_profiles
            set matched_occupation_id = :occ, match_similarity = :sim,
                updated_at = systimestamp
            where profile_id = :pid
            """,
            occ=choice.occupation_id,
            sim=choice.similarity,
            pid=profile_id,
        )

        latency_ms = (time.monotonic() - started) * 1000
        cursor.execute(
            """
            insert into tool_log
              (thread_id, turn_seq, call_type, inputs, outputs, result_count,
               top_similarity, latency_ms, model_name)
            values (:thread, 1, 'vector_search_occupations', :inputs, :outputs,
                    :n, :top_sim, :latency, :model)
            """,
            thread=thread_id or f"profile-{profile_id}",
            inputs=json.dumps(
                {"profile_id": profile_id, "inferred_job_zone": inferred_zone}
            ),
            outputs=json.dumps(
                {
                    "candidates": [
                        {"soc": c.soc_code, "title": c.title,
                         "zone": c.job_zone, "sim": c.similarity}
                        for c in candidates
                    ],
                    "matched_soc": choice.soc_code,
                    "zone_clamped": clamped,
                    "zone_clamp_failed": clamp_failed,
                }
            ),
            n=len(candidates),
            top_sim=candidates[0].similarity,
            latency=round(latency_ms, 1),
            model=MODEL_NAME,
        )
        connection.commit()

    return MatchResult(profile_id, choice, candidates, clamped, clamp_failed)
