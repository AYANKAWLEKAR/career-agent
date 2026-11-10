"""Multi-persona demo: three Berkeley students through the full pipeline.

Each persona arrives as a pre-built profile schema (the shape the LLM parse
produces), goes through in-database ONNX embedding, A-inference with the
education zone clamp, graph path surfacing, and an LLM-narrated story grounded
in the decision record.

The three are chosen to stress different parts of the system:

* Statistics major with accounting internships, aiming at finance — the match
  should land on the accounting evidence and the paths should BE the bridge to
  finance.
* Biology major with wet-lab experience, aiming at healthcare investment
  banking — evidence and aspiration point at different verticals; the paths are
  where the tension shows.
* Communications major with marketing internships, aiming at consulting — a
  deliberate out-of-scope probe: marketing and general-consulting occupations
  are outside the ingested SOC families (tech / finance / research), so this
  shows how the system behaves at the edge of its universe.

Usage:
    uv run python scripts/multi_demo.py [--skip-llm] [--keep-profiles]
"""

from __future__ import annotations

import argparse

from navigator.config import get_settings
from navigator.db.pool import create_pool
from navigator.llm import narrate_paths
from navigator.paths import surface_paths, surface_paths_to
from navigator.profile import ExperienceInput, ProfileInput, create_profile, infer_start

RULE = "=" * 74


def _wage_tag(hop) -> str:
    """' +$21k' when both hop ends have LCA wage medians, else ''."""
    delta = hop.wage_delta
    if delta is None:
        return ""
    return f" {'+' if delta >= 0 else '-'}${abs(delta) / 1000:.0f}k"

PERSONAS: list[tuple[str, ProfileInput]] = [
    (
        "Statistics major, accounting experience, aiming at finance",
        ProfileInput(
            user_ref="multi-stats@berkeley.demo",
            raw_text=(
                "UC Berkeley statistics major, junior year. Two accounting "
                "internships. Interested in moving into finance after "
                "graduation — ideally analysis or investment work."
            ),
            current_title="Accounting Intern",
            years_experience=1.0,
            education_level="bachelor's (in progress)",
            education_majors=["Statistics"],
            location_text="Berkeley, CA",
            experiences=[
                ExperienceInput(
                    "Accounting internship at a mid-size firm: prepared journal "
                    "entries and month-end reconciliations, tested internal "
                    "controls, and built Excel models for accounts receivable "
                    "aging and revenue tie-outs.",
                    title="Accounting Intern", organization="Regional CPA firm",
                    duration_months=3),
                ExperienceInput(
                    "Second accounting internship: supported audit fieldwork, "
                    "sampled transactions, drafted workpapers, and reconciled "
                    "fixed-asset schedules under a senior auditor.",
                    title="Audit Intern", organization="Regional CPA firm",
                    duration_months=3),
                ExperienceInput(
                    "Course projects in R and Python: regression analysis, "
                    "time-series forecasting of retail sales data, and a "
                    "statistics club treasurer role managing the budget.",
                    title="Student", organization="UC Berkeley",
                    duration_months=24),
            ],
        ),
    ),
    (
        "Biology major, lab experience, aiming at healthcare investment banking",
        ProfileInput(
            user_ref="multi-bio@berkeley.demo",
            raw_text=(
                "UC Berkeley molecular biology major with two years of wet-lab "
                "research experience. Interested in healthcare investment "
                "banking — using science background to evaluate biotech "
                "companies."
            ),
            current_title="Undergraduate Research Assistant",
            years_experience=2.0,
            education_level="bachelor's (in progress)",
            education_majors=["Molecular and Cell Biology"],
            location_text="Berkeley, CA",
            experiences=[
                ExperienceInput(
                    "Undergraduate research assistant in a genetics lab: ran "
                    "PCR and gel electrophoresis, maintained cell cultures, "
                    "prepared reagents, and analyzed sequencing results for a "
                    "gene-expression study.",
                    title="Research Assistant", organization="UC Berkeley MCB",
                    duration_months=20),
                ExperienceInput(
                    "Summer lab internship at a biotech startup: assisted with "
                    "assay development, logged experimental data, and "
                    "summarized literature on antibody therapeutics for the "
                    "research team.",
                    title="Lab Intern", organization="Biotech startup",
                    duration_months=3),
                ExperienceInput(
                    "Finance club member: built a mock pitch valuing a "
                    "public biotech company, including a simple discounted "
                    "cash flow and comparable-company analysis.",
                    title="Club Member", organization="Berkeley finance club",
                    duration_months=8),
            ],
        ),
    ),
    (
        "Communications major, marketing experience, aiming at consulting",
        ProfileInput(
            user_ref="multi-comm@berkeley.demo",
            raw_text=(
                "UC Berkeley communications major. Marketing internships at "
                "two startups. Interested in management consulting after "
                "graduation."
            ),
            current_title="Marketing Intern",
            years_experience=1.0,
            education_level="bachelor's (in progress)",
            education_majors=["Communications"],
            location_text="Berkeley, CA",
            experiences=[
                ExperienceInput(
                    "Marketing internship at a consumer app startup: ran A/B "
                    "tests on campaign copy, analyzed engagement metrics in "
                    "spreadsheets, and presented weekly growth summaries to "
                    "the team.",
                    title="Marketing Intern", organization="Consumer startup",
                    duration_months=4),
                ExperienceInput(
                    "Social media marketing internship: planned content "
                    "calendars, tracked audience analytics, and wrote market "
                    "research briefs on competitor positioning.",
                    title="Marketing Intern", organization="E-commerce startup",
                    duration_months=3),
                ExperienceInput(
                    "Case competition team: analyzed a retailer's declining "
                    "sales, surveyed students, and recommended a pricing and "
                    "loyalty strategy to a judging panel.",
                    title="Team Lead", organization="Berkeley consulting club",
                    duration_months=6),
            ],
        ),
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


def run_persona(pool, label: str, profile: ProfileInput, skip_llm: bool) -> None:
    print(f"\n{RULE}\nPERSONA: {label}\n{RULE}")
    print(f"self-description : {profile.raw_text}")
    print(f"schema           : {profile.current_title} | "
          f"{profile.education_level} in {', '.join(profile.education_majors)} | "
          f"{profile.years_experience} yrs | {len(profile.experiences)} experiences")
    for i, e in enumerate(profile.experiences):
        print(f"  [{i}] {e.title} @ {e.organization} ({e.duration_months} mo): "
              f"{e.raw_text[:72]}")

    profile_id = create_profile(pool, profile)
    match = infer_start(pool, profile_id)
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
    print(f"\nstart band: zones [{match.floor_zone or '-'}..{match.ceiling_zone}] "
          "(completed education + experience)")
    print(f"START: {match.matched.title} [zone {match.matched.job_zone}] "
          f"similarity {match.matched.similarity}{note}")
    print("candidates considered:")
    for candidate in match.candidates:
        print(f"  {candidate.similarity:6.4f}  [z{candidate.job_zone}] {candidate.title}")

    paths = surface_paths(pool, match.matched.occupation_id)
    print("\nranked paths (cost = sum of 1 - skill-transfer weight):")
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
              f"similarity {top_asp.similarity} — resembles it, cannot start there yet")
        aspiration_paths = surface_paths_to(
            pool, match.matched.occupation_id, top_asp.title, top_k=3)
        if aspiration_paths:
            for path in aspiration_paths:
                chain = " -> ".join(
                    f"{hop.dst_title} "
                    f"[{'UP' if hop.edge_type == 'career_ladder' else 'LAT'} "
                    f"w={hop.weight:.3f}{_wage_tag(hop)}]" for hop in path.hops)
                print(f"  ladder cost {path.cost:.3f}: "
                      f"{path.hops[0].src_title} -> {chain}")
        else:
            print("  no route within 3 moves from the chosen start")

    if skip_llm:
        return
    print("\nLLM NARRATION (grounded in the decision record above):\n")
    narration = narrate_paths(profile, match, paths, pool=pool,
                              thread_id=profile.user_ref,
                              aspiration_paths=aspiration_paths)
    print(narration.story)
    print(f"\n[{narration.output_tokens} output tokens, "
          f"{narration.latency_ms / 1000:.1f}s]")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-llm", action="store_true")
    parser.add_argument("--keep-profiles", action="store_true")
    args = parser.parse_args(argv)

    pool = create_pool(get_settings())
    print(RULE)
    print("CAREER PATH NAVIGATOR — MULTI-PERSONA DEMO (Berkeley students)")
    print(RULE)
    show_engine(pool)

    try:
        for label, persona in PERSONAS:
            run_persona(pool, label, persona, args.skip_llm)
    finally:
        if not args.keep_profiles:
            with pool.acquire() as connection:
                cursor = connection.cursor()
                cursor.execute(
                    "delete from user_profiles where user_ref like 'multi-%'"
                )
                connection.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
