"""End-to-end demo: resume text -> profile schema -> graph entry -> paths -> story.

Runs two legs:

* Three synthetic personas exercise the database pipeline (intake, A-inference,
  path surfacing) with no LLM involved.
* A real resume file goes through the full loop: LLM parse to schema, profile
  creation, in-database ONNX matching, graph traversal, and an LLM-narrated
  story grounded in the decision record.

Usage:
    uv run python scripts/demo.py [--resume resumetext/ayan.txt] [--skip-llm]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path as FilePath

from navigator.config import get_settings
from navigator.db.pool import create_pool
from navigator.llm import narrate_paths, parse_resume, parsed_to_profile
from navigator.paths import surface_paths, surface_paths_to
from navigator.profile import ExperienceInput, ProfileInput, create_profile, infer_start

RULE = "=" * 74


def _wage_tag(hop) -> str:
    """' +$21k' when both hop ends have LCA wage medians, else ''."""
    delta = hop.wage_delta
    if delta is None:
        return ""
    return f" {'+' if delta >= 0 else '-'}${abs(delta) / 1000:.0f}k"


def _ms(started: float) -> str:
    return f"{(time.monotonic() - started) * 1000:.0f} ms"

PERSONAS = [
    ProfileInput(
        user_ref="demo-frontend@example.com",
        raw_text=("CS degree, two years frontend at a logistics startup, "
                  "genomics internship, calculus tutoring."),
        current_title="Frontend Engineer", years_experience=2.0,
        education_level="bachelor's", education_majors=["Computer Science"],
        experiences=[
            ExperienceInput(
                "Built customer-facing web app and internal dashboards with React, "
                "TypeScript and Python at a logistics startup.",
                title="Frontend Engineer"),
            ExperienceInput(
                "Research internship: cleaned and analyzed genomics sequencing data in R.",
                title="Research Intern"),
            ExperienceInput(
                "Tutored undergraduate calculus for three semesters.", title="Tutor"),
        ],
    ),
    ProfileInput(
        user_ref="demo-tax@example.com",
        raw_text="Master's in finance. Three years preparing individual tax returns.",
        current_title="Tax Associate", years_experience=3.0,
        education_level="master's degree", education_majors=["Finance"],
        experiences=[
            ExperienceInput(
                "Prepared individual and small-business tax returns; client intake "
                "and IRS correspondence.",
                title="Tax Preparer"),
        ],
    ),
    ProfileInput(
        user_ref="demo-phd@example.com",
        raw_text="PhD in statistics, five years ML for clinical trials.",
        current_title="Senior Data Scientist", years_experience=5.0,
        education_level="PhD", education_majors=["Statistics"],
        experiences=[
            ExperienceInput(
                "Built machine learning models and statistical analyses for "
                "clinical trial outcomes; Python, R.",
                title="Senior Data Scientist"),
            ExperienceInput(
                "Doctoral research in Bayesian statistics.", title="PhD Researcher"),
        ],
    ),
]


def show_engine(pool) -> None:
    with pool.acquire() as connection:
        cursor = connection.cursor()
        cursor.execute("select banner_full from v$version")
        print(f"Database : {cursor.fetchone()[0].splitlines()[0]}")
        cursor.execute("select graph_name from user_property_graphs")
        print(f"Graph    : {cursor.fetchone()[0]} (SQL property graph)")
        cursor.execute("select model_name from user_mining_models")
        print(f"ONNX     : {', '.join(r[0] for r in cursor.fetchall())} (in-database)")
        cursor.execute("select count(*) from occupations")
        n_occ = cursor.fetchone()[0]
        cursor.execute("select count(*) from occupation_edges")
        print(f"Corpus   : {n_occ} occupations, {cursor.fetchone()[0]} edges")
    print(f"Wages    : scope={get_settings().wage_scope} "
          "(H-1B LCA FY2026; display only — penalty is national)")
    engine = get_settings().graph_engine
    if engine == "hop_join":
        print("Traversal: engine=hop_join (legacy UNION ALL fallback)")
    else:
        print("Traversal: engine=graph_table (ISO SQL/PGQ, ONE ROW PER STEP {1,3})")


def run_pipeline(pool, profile: ProfileInput, label: str) -> None:
    print(f"\n{RULE}\nPERSONA: {label}\n{RULE}")
    print(f"resume text : {profile.raw_text}")
    profile_id = create_profile(pool, profile)
    started = time.monotonic()
    match = infer_start(pool, profile_id)
    print(f"[latency] A-inference round trip: {_ms(started)} (per-vector APPROX top-k)")
    flags = []
    if match.ceiling_applied:
        flags.append("start ceiling moved the match down into the band")
    if match.ceiling_unmet:
        flags.append("ceiling_unmet: every match sits above the band; "
                     "starting at the lowest zone available")
    if match.zone_clamped and not match.ceiling_applied and not match.ceiling_unmet:
        flags.append("education clamp moved the match up")
    if match.zone_clamp_failed:
        flags.append("clamp_failed: education outranks every semantic match")
    if match.experience_gate_relaxed:
        flags.append("experience gate relaxed")
    note = f"  ({'; '.join(flags)})" if flags else ""
    print(f"\nSTART: {match.matched.title} [zone {match.matched.job_zone}] "
          f"similarity {match.matched.similarity}{note}")
    print("candidates considered:")
    for candidate in match.candidates:
        print(f"  {candidate.similarity:6.4f}  [z{candidate.job_zone}] {candidate.title}")
    started = time.monotonic()
    paths = surface_paths(pool, match.matched.occupation_id)
    print(f"[latency] path enumeration ({get_settings().graph_engine}): {_ms(started)}, "
          f"{len(paths)} ranked paths from {match.matched.title}")
    print("\nranked paths (cost = sum of 1 - skill-transfer weight):")
    for path in paths:
        chain = " -> ".join(
            f"{hop.dst_title} [{'UP' if hop.edge_type == 'career_ladder' else 'LAT'} "
            f"w={hop.weight:.3f}{_wage_tag(hop)}]"
            for hop in path.hops
        )
        print(f"  cost {path.cost:.3f}: {path.hops[0].src_title} -> {chain}")


RESUME_USER_REF = "ayan@resume-demo"


def run_resume_leg(pool, resume_path: FilePath, skip_llm: bool) -> None:
    print(f"\n{RULE}\nREAL RESUME: {resume_path}\n{RULE}")
    resume_text = resume_path.read_text(encoding="utf-8")
    print(f"input: {len(resume_text)} chars of raw resume text")
    if skip_llm:
        print("(--skip-llm: stopping before the LLM parse)")
        return

    parsed = parse_resume(resume_text, pool=pool)
    print("\nLLM PARSE (structured output -> profile schema):")
    print(f"  current_title    : {parsed.current_title}")
    print(f"  education        : {parsed.education_level} in {', '.join(parsed.education_majors)}")
    print(f"  years_experience : {parsed.years_experience}")
    print(f"  experiences      : {len(parsed.experiences)}")
    for i, e in enumerate(parsed.experiences):
        print(f"    [{i}] {e.title or '-'} @ {e.organization or '-'}: "
              f"{e.accomplishment_summary[:80]}")

    profile = parsed_to_profile(RESUME_USER_REF, resume_text, parsed)
    # Re-runs replace the prior profile: user_ref carries no unique constraint,
    # so without this each demo run would stack another identical profile.
    with pool.acquire() as connection:
        cursor = connection.cursor()
        cursor.execute("delete from user_profiles where user_ref = :u", u=RESUME_USER_REF)
        connection.commit()
    profile_id = create_profile(pool, profile)
    started = time.monotonic()
    match = infer_start(pool, profile_id)
    print(f"[latency] A-inference round trip: {_ms(started)} (per-vector APPROX top-k)")
    flags = []
    if match.ceiling_applied:
        flags.append("start ceiling moved the match down into the band")
    if match.ceiling_unmet:
        flags.append("ceiling_unmet: every match sits above the band")
    if match.zone_clamp_failed:
        flags.append("clamp_failed: education outranks every semantic match")
    if match.experience_gate_relaxed:
        flags.append("experience gate relaxed")
    note = f"  ({'; '.join(flags)})" if flags else ""
    print(f"\nstart band: zones [{match.floor_zone or '-'}..{match.ceiling_zone}]")
    print(f"START: {match.matched.title} [zone {match.matched.job_zone}] "
          f"similarity {match.matched.similarity}{note}")
    for candidate in match.candidates:
        print(f"  {candidate.similarity:6.4f}  [z{candidate.job_zone}] {candidate.title}")

    started = time.monotonic()
    paths = surface_paths(pool, match.matched.occupation_id)
    print(f"[latency] path enumeration ({get_settings().graph_engine}): {_ms(started)}, "
          f"{len(paths)} ranked paths from {match.matched.title}")
    for path in paths:
        chain = " -> ".join(
            f"{hop.dst_title} [{'UP' if hop.edge_type == 'career_ladder' else 'LAT'} "
            f"w={hop.weight:.3f}{_wage_tag(hop)}]"
            for hop in path.hops
        )
        print(f"  cost {path.cost:.3f}: {path.hops[0].src_title} -> {chain}")

    aspiration_paths = []
    if match.aspirations:
        top_asp = match.aspirations[0]
        print(f"\nASPIRATION: {top_asp.title} [z{top_asp.job_zone}] "
              f"similarity {top_asp.similarity}")
        started = time.monotonic()
        aspiration_paths = surface_paths_to(
            pool, match.matched.occupation_id, top_asp.title, top_k=3)
        print(f"[latency] path enumeration ({get_settings().graph_engine}): {_ms(started)}, "
              f"{len(aspiration_paths)} ranked paths to {top_asp.title}")

    print("\nLLM NARRATION (grounded in the decision record above):\n")
    narration = narrate_paths(profile, match, paths, pool=pool,
                              wage_scope=get_settings().wage_scope,
                              aspiration_paths=aspiration_paths)
    print(narration.story)
    print(f"\n[{narration.output_tokens} output tokens, "
          f"{narration.latency_ms / 1000:.1f}s]")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", type=FilePath, default=FilePath("resumetext/ayan.txt"))
    parser.add_argument("--skip-llm", action="store_true")
    parser.add_argument("--keep-personas", action="store_true",
                        help="leave persona profiles in the database")
    args = parser.parse_args(argv)

    pool = create_pool(get_settings())
    print(RULE)
    print("CAREER PATH NAVIGATOR — END-TO-END DEMO")
    print(RULE)
    show_engine(pool)

    labels = ["Frontend engineer, bachelor's", "Tax associate, master's",
              "Data scientist, PhD"]
    try:
        for label, persona in zip(labels, PERSONAS, strict=True):
            run_pipeline(pool, persona, label)

        if args.resume.is_file():
            run_resume_leg(pool, args.resume, args.skip_llm)
        else:
            print(f"\n(resume file {args.resume} not found — "
                  "skipping the real-resume leg)", file=sys.stderr)
    finally:
        # Cleanup must survive a failure mid-demo (e.g. the LLM leg erroring)
        # or persona profiles accumulate across runs.
        if not args.keep_personas:
            with pool.acquire() as connection:
                cursor = connection.cursor()
                cursor.execute("delete from user_profiles where user_ref like 'demo-%'")
                connection.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
