"""User profile intake and A-inference: the entry point into the occupation graph.

Design sections 6.1 (profile tables) and 7.1 (A-inference). The user describes what
they have done in natural language -- effectively a resume. The profile and each
experience are embedded separately with the in-database ONNX model, and A-inference
searches the occupation vector index with EACH of those vectors, taking the
strongest signal per occupation: averaging a resume into one vector lands it
between all its entries and near none.

The matched occupation is cached on the profile row so a wrong A is visible and
correctable rather than buried in an ephemeral call, and the top-5 candidates are
logged to tool_log so a bad match is diagnosable.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

MODEL_NAME = "ALL_MINILM_L12_V2"
# Ten candidates, not five: student profiles often match zone-5 occupations
# hardest, and the start band needs lower-zone candidates to choose from.
TOP_CANDIDATES = 10
ASPIRATION_LIMIT = 3

# Text markers meaning the stated education is not yet completed. Detected at
# create_profile time (alongside the explicit flag) so the band math itself
# stays a pure function over stored fields.
_IN_PROGRESS_MARKERS = (
    "in progress", "expected", "pursuing", "candidate", "currently enrolled",
    "junior year", "senior year", "sophomore", "freshman", "undergraduate",
)

# Four or more years of real experience substitutes for the next preparation
# tier, mirroring O*NET's own zone definitions (zone 5 is "extensive
# preparation": a graduate degree OR extensive experience).
EXPERIENCE_BUMP_YEARS = 4.0

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
# Abbreviations matter: an LLM parse of a real resume returns "B.A.", not
# "bachelor's", and an unrecognized level silently disables the zone clamp.
# "undergraduate" is checked before the graduate tier: substring matching would
# otherwise find "graduate" inside it and map a student to zone 5 -- recreating
# the exact overplacement bug the start ceiling exists to fix.
_ZONE_RULES: tuple[tuple[tuple[str, ...], int], ...] = (
    (("undergraduate",), 4),
    (("graduate", "master", "phd", "ph.d", "doctor", "professional degree",
      "mba", "m.s", "m.a.", "md", "jd"), 5),
    (("bachelor", "b.a", "b.s", "bsc", "b.sc"), 4),
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
    education_in_progress: bool = False
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
    floor_zone: int | None = None
    ceiling_zone: int | None = None
    # True when the raw top match sat above the ceiling and the start moved down.
    ceiling_applied: bool = False
    # True when EVERY candidate sat above the ceiling: the start is the
    # lowest-zone candidate anyway, flagged rather than hidden.
    ceiling_unmet: bool = False
    # True when the experience gate had to be ignored to find any start at all.
    experience_gate_relaxed: bool = False
    # Higher-zone matches the user resembles but cannot start at yet — these are
    # destinations for the graph, not entry points.
    aspirations: list[Candidate] = field(default_factory=list)


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


def education_looks_in_progress(education_level: str | None) -> bool:
    """Detect in-progress markers in the stated education text."""
    if not education_level:
        return False
    text = education_level.lower()
    return any(marker in text for marker in _IN_PROGRESS_MARKERS)


def infer_start_band(
    education_zone: int | None,
    education_in_progress: bool,
    years_experience: float | None,
) -> tuple[int | None, int]:
    """The zone band [floor, ceiling] a person can realistically START in.

    Job zones measure required preparation, so the band comes from COMPLETED
    preparation: a degree in progress supports the tier below it. Four or more
    years of experience buys one tier back (O*NET's own zone-5 definition is
    a graduate degree OR extensive experience). The ceiling is what fixes the
    student-overplacement failure: previously the zone clamp was a floor with
    no ceiling, so a sophomore parsed as bachelor's-tier and A-inference seeded
    students into zone-5 occupations -- which are terminal, handing the
    product's core audience the one output with no ladder in it.

    Returns (floor, ceiling). Floor is None when education is unstated -- there
    is nothing to walk up toward -- and the ceiling then defaults conservatively
    to zone 3 plus any experience bump.
    """
    if education_zone is None:
        completed = None
    elif education_in_progress:
        completed = max(1, education_zone - 1)
    else:
        completed = education_zone
    bump = 1 if (years_experience or 0.0) >= EXPERIENCE_BUMP_YEARS else 0
    ceiling = min(5, (completed if completed is not None else 3) + bump)
    floor = min(completed, ceiling) if completed is not None else None
    return floor, ceiling


@dataclass
class StartDecision:
    choice: Candidate
    clamped: bool
    floor_unmet: bool
    ceiling_applied: bool
    ceiling_unmet: bool
    experience_gate_relaxed: bool
    aspirations: list[Candidate]


def pick_start(
    candidates: list[Candidate],
    floor_zone: int | None,
    ceiling_zone: int,
    experience_ok: set[int] | None = None,
) -> StartDecision:
    """Choose the starting occupation from a similarity-ordered candidate list.

    Selection order:

    1. The strongest candidate inside the band [floor, ceiling] that also
       passes the experience gate.
    2. Failing that, the strongest candidate below the floor (education
       outranking the evidence is flagged, not hidden -- section 7.1).
    3. Failing that, retry 1-2 ignoring the experience gate, flagged.
    4. Failing that -- every candidate sits above the ceiling -- start at the
       LOWEST zone available (tie broken by similarity) and flag ceiling_unmet.

    Candidates above the ceiling never become starts under rules 1-3; the
    strongest of them become ``aspirations``: destinations the traversal can
    build a ladder toward.
    """
    # (zone, candidate) pairs so the zone is a plain int from here on.
    zoned = [(c.job_zone, c) for c in candidates if c.job_zone is not None]
    top = candidates[0]

    def gate(candidate: Candidate) -> bool:
        return experience_ok is None or candidate.occupation_id in experience_ok

    def decide(use_gate: bool) -> tuple[Candidate, bool] | None:
        allowed = [(z, c) for z, c in zoned if not use_gate or gate(c)]
        in_band = [
            c for z, c in allowed
            if z <= ceiling_zone and (floor_zone is None or z >= floor_zone)
        ]
        if in_band:
            return in_band[0], False
        if floor_zone is not None:
            below = [c for z, c in allowed if z < floor_zone]
            if below:
                return below[0], True
        return None

    picked = decide(use_gate=True)
    relaxed = False
    if picked is None and experience_ok is not None:
        picked = decide(use_gate=False)
        relaxed = picked is not None

    if picked is not None:
        choice, floor_unmet = picked
        ceiling_unmet = False
    elif zoned:
        # Every candidate sits above the ceiling. Prefer gate-passing candidates
        # for the lowest-zone start; only when none passes does the gate get
        # ignored here too -- and then the relaxation is flagged, because the
        # chosen start genuinely fails its experience requirement.
        gated_zoned = [(z, c) for z, c in zoned if gate(c)]
        pool_ = gated_zoned or zoned
        choice = min(pool_, key=lambda zc: (zc[0], -zc[1].similarity))[1]
        relaxed = relaxed or (not gated_zoned and experience_ok is not None)
        floor_unmet = False
        ceiling_unmet = True
    else:
        choice = top
        floor_unmet = False
        ceiling_unmet = False

    # An aspiration is a candidate the user genuinely cannot START at yet --
    # above the ceiling, or blocked by the experience gate -- sitting strictly
    # above the zone of the start actually chosen. In-band candidates that
    # simply scored lower are startable alternatives, not aspirations.
    choice_zone = choice.job_zone if choice.job_zone is not None else ceiling_zone
    aspirations = [
        c for z, c in zoned
        if z > choice_zone
        and (z > ceiling_zone or not gate(c))
        and c.occupation_id != choice.occupation_id
    ][:ASPIRATION_LIMIT]
    return StartDecision(
        choice=choice,
        clamped=choice.occupation_id != top.occupation_id,
        floor_unmet=floor_unmet,
        ceiling_applied=(
            top.job_zone is not None
            and top.job_zone > ceiling_zone
            and choice.occupation_id != top.occupation_id
            and not ceiling_unmet  # unmet means the start is still above the band
        ),
        ceiling_unmet=ceiling_unmet,
        experience_gate_relaxed=relaxed,
        aspirations=aspirations,
    )


def create_profile(pool, profile: ProfileInput) -> int:
    """Insert the profile and its experiences, then embed both in-database."""
    with pool.acquire() as connection:
        cursor = connection.cursor()
        profile_id_var = cursor.var(int)
        cursor.execute(
            """
            insert into user_profiles
              (user_ref, raw_text, current_title, years_experience, education_level,
               education_in_progress, education_majors, inferred_job_zone,
               location_text, embedding_source)
            values (:user_ref, :raw_text, :title, :years, :edu, :in_prog, :majors,
                    :zone, :loc, :src)
            returning profile_id into :pid
            """,
            user_ref=profile.user_ref,
            raw_text=profile.raw_text,
            title=profile.current_title,
            years=profile.years_experience,
            edu=profile.education_level,
            in_prog="Y"
            if profile.education_in_progress
            or education_looks_in_progress(profile.education_level)
            else "N",
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

    Candidates come from one approximate nearest-neighbor search per query
    vector (profile AND each experience); per occupation, the best weighted
    similarity wins. The start is chosen inside the zone band
    the person's COMPLETED preparation supports, gated by each occupation's
    experience requirement where one is loaded; stronger matches above the
    ceiling become aspirations (destinations), never starts. Everything is
    logged to tool_log.
    """
    from navigator.requirements import experience_thresholds

    started = time.monotonic()
    thresholds = experience_thresholds(pool)
    with pool.acquire() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            select inferred_job_zone, education_in_progress, years_experience
            from user_profiles where profile_id = :pid
            """,
            pid=profile_id,
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError(f"no such profile: {profile_id}")
        inferred_zone, in_progress_flag, years = row
        floor_zone, ceiling_zone = infer_start_band(
            int(inferred_zone) if inferred_zone is not None else None,
            in_progress_flag == "Y",
            float(years) if years is not None else None,
        )

        cursor.execute(
            """
            select embedding from user_profiles
            where profile_id = :pid and embedding is not null
            """,
            pid=profile_id,
        )
        vectors: list[tuple[Any, float]] = [(row[0], 1.0) for row in cursor.fetchall()]
        cursor.execute(
            """
            select ordinal, embedding from profile_experiences
            where profile_id = :pid and embedding is not null
            """,
            pid=profile_id,
        )
        vectors.extend(
            (emb, max(RECENCY_FLOOR, 1.0 - RECENCY_DECAY * float(ordinal)))
            for ordinal, emb in cursor.fetchall()
        )
        if not vectors:
            raise ValueError(f"profile {profile_id} has no embeddings to match")

        # Null-zone occupations (the "All Other" catch-all codes: no zone, no
        # edges, so a match strands the user) are filtered in Python, not SQL:
        # ANY WHERE clause on the candidate query disqualifies
        # OCCUPATIONS_EMBEDDING_VIX and flips the plan back to a full scan
        # (verified live 2026-11-10). k is padded by the null-zone row count so
        # the post-fetch filter cannot shrink the real top-TOP_CANDIDATES.
        cursor.execute("select count(*) from occupations where job_zone is null")
        fetch_k = TOP_CANDIDATES + int(cursor.fetchone()[0])

        # Within one vector, ordering by distance equals ordering by
        # (1 - dist) * wt (wt is constant per vector), so the union of the
        # per-vector approximate top-k contains the global top-TOP_CANDIDATES
        # by max-score.
        best: dict[int, tuple[float, Any, Any, Any]] = {}
        for emb, wt in vectors:
            cursor.execute(
                """
                select occupation_id, soc_code, title, job_zone,
                       vector_distance(embedding, :qv, cosine) as dist
                from occupations
                order by vector_distance(embedding, :qv, cosine)
                fetch approx first :k rows only
                """,
                qv=emb,
                k=fetch_k,
            )
            for oid, soc, title, zone, dist in cursor.fetchall():
                if dist is None:
                    continue
                sim = (1 - float(dist)) * wt
                key = int(oid)
                if key not in best or sim > best[key][0]:
                    best[key] = (sim, soc, title, zone)
        ranked = sorted(
            ((oid, row) for oid, row in best.items() if row[3] is not None),
            key=lambda item: (-item[1][0], item[0]),
        )
        candidates = [
            Candidate(oid, soc, title, zone, round(float(sim), 4))
            for oid, (sim, soc, title, zone) in ranked[:TOP_CANDIDATES]
        ]
        if not candidates:
            raise ValueError(f"profile {profile_id} has no embeddings to match")

        # Experience gate: the occupation's required prior experience (RW median
        # band, loaded by scripts/load_requirements.py) must be within reach.
        # Unknown years or an occupation without a loaded threshold pass the
        # gate -- missing data must not masquerade as ineligibility.
        if years is None:
            experience_ok = None
        else:
            experience_ok = {
                c.occupation_id for c in candidates
                if float(years) >= thresholds.get(c.occupation_id, 0.0)
            }
        decision = pick_start(candidates, floor_zone, ceiling_zone, experience_ok)
        choice = decision.choice

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
                {"profile_id": profile_id, "inferred_job_zone": inferred_zone,
                 "education_in_progress": in_progress_flag == "Y",
                 "years_experience": float(years) if years is not None else None,
                 "start_floor": floor_zone, "start_ceiling": ceiling_zone,
                 "approx": True, "fetch_k": fetch_k,
                 "query_vectors": len(vectors)}
            ),
            outputs=json.dumps(
                {
                    "candidates": [
                        {"soc": c.soc_code, "title": c.title,
                         "zone": c.job_zone, "sim": c.similarity}
                        for c in candidates
                    ],
                    "matched_soc": choice.soc_code,
                    "zone_clamped": decision.clamped,
                    "zone_clamp_failed": decision.floor_unmet,
                    "ceiling_applied": decision.ceiling_applied,
                    "ceiling_unmet": decision.ceiling_unmet,
                    "experience_gate_relaxed": decision.experience_gate_relaxed,
                    "aspirations": [c.soc_code for c in decision.aspirations],
                }
            ),
            n=len(candidates),
            top_sim=candidates[0].similarity,
            latency=round(latency_ms, 1),
            model=MODEL_NAME,
        )
        connection.commit()

    return MatchResult(
        profile_id=profile_id,
        matched=choice,
        candidates=candidates,
        zone_clamped=decision.clamped,
        zone_clamp_failed=decision.floor_unmet,
        floor_zone=floor_zone,
        ceiling_zone=ceiling_zone,
        ceiling_applied=decision.ceiling_applied,
        ceiling_unmet=decision.ceiling_unmet,
        experience_gate_relaxed=decision.experience_gate_relaxed,
        aspirations=decision.aspirations,
    )
