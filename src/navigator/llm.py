"""LLM resume parsing and path narration.

Two calls, both logged to ``tool_log``:

* ``parse_resume`` (call_type ``llm_parse``): raw resume text in, the structured
  profile schema out, via the Messages API's structured outputs — the model is
  constrained to the schema, so no output munging.
* ``narrate_paths`` (call_type ``llm_compose``): the A-inference decision record
  and the surfaced paths in, a grounded plain-language story out. The prompt
  carries every number the narration may use and forbids inventing others; the
  story explains WHY each hop exists (skill cosine, zone step, thresholds), not
  just that it does.

The narration is deliberately given the *decision record*, not database access:
it can only explain choices the system actually made.

Two backends implement the calls, selected by ``Settings.llm_backend``:

* ``ollama`` (default): a local model via the Ollama native API. Structured
  extraction uses Ollama's ``format`` parameter, which constrains decoding to the
  JSON schema. Runs entirely on this machine; no key, no data leaves the host.
* ``anthropic``: the Claude API through the official SDK (structured outputs via
  ``messages.parse``, streaming for the narration). Needs ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, Field

from navigator.config import get_settings
from navigator.paths import Path
from navigator.profile import ExperienceInput, MatchResult, ProfileInput

ANTHROPIC_MODEL = "claude-opus-5"
PARSE_MAX_TOKENS = 8_000
NARRATE_MAX_TOKENS = 16_000
OLLAMA_TIMEOUT_S = 900.0  # local CPU/Metal generation is slow; be patient


class ParsedExperience(BaseModel):
    """One work/research/project experience from the resume, most recent first."""

    accomplishment_summary: str = Field(
        description=(
            "2-3 sentences describing what was actually built, researched, or "
            "achieved in this role, drawn from its bullet points — name the "
            "concrete systems and tools. NEVER the location or dates; this text "
            "is embedded for semantic matching, so it must describe the work."
        )
    )
    title: str | None = Field(description="Role title as stated, if any")
    organization: str | None = Field(description="Employer or lab, if stated")
    duration_months: int | None = Field(
        description="Duration in whole months (e.g. a 3-month internship -> 3); null if not datable"
    )


class ParsedResume(BaseModel):
    """The structured extraction of a resume (design section 6.1)."""

    current_title: str | None = Field(
        description="The person's current or most recent role title"
    )
    years_experience: float | None = Field(
        description="Total professional experience in years, internships included"
    )
    education_level: str | None = Field(
        description=(
            "Highest attained or in-progress education, e.g. \"bachelor's\", "
            "\"master's degree\", \"PhD\", \"high school diploma\""
        )
    )
    education_in_progress: bool = Field(
        description=(
            "true when the stated education is not yet completed — an expected "
            "graduation date, 'candidate for', current enrollment, or class year "
            "all mean true"
        )
    )
    education_majors: list[str] = Field(
        description="Degree fields/majors as stated, e.g. ['Applied Mathematics']"
    )
    location_text: str | None = Field(description="Location if stated")
    experiences: list[ParsedExperience] = Field(
        description="Every distinct experience, ordered most recent first"
    )


PARSE_SYSTEM = """You extract structured career profiles from resume text.

Rules:
- Extract only what the text supports; use null for anything not stated.
- Order experiences most recent first (the order carries recency signal downstream).
- accomplishment_summary must restate the role's bullet points (systems built,
  methods used, tools named) — never the location/date lines.
- years_experience: sum dated professional entries, counting overlaps once;
  a currently-enrolled student with internships still has those internship years.
- education_level reflects the highest degree attained OR currently in progress."""


NARRATE_SYSTEM = """You explain a career-path recommendation system's output to the \
person it analyzed, in plain language.

You are given the system's actual decision record: how the resume was parsed, how \
the starting occupation was chosen (vector similarity of each experience against \
occupation profiles, plus an education-based zone clamp), and the ranked paths \
found by graph traversal (each hop carries its edge type, skill-similarity weight, \
and job-zone step).

Hard rules:
- Ground every claim in the numbers provided. Never invent similarities, wages,
  occupations, or requirements that are not in the record.
- Explain decisions, not just results: why THIS starting point (which experience
  drove the match, whether the education clamp moved it), and why each climb is
  plausible (the skill-transfer weight, the one-zone step).
- A career_ladder hop is a step UP one preparation zone; a skill_overlap hop is a
  lateral move held to a stricter similarity bar. Weights near 1.0 mean strong
  skill transfer; the system rejected zone jumps so multi-step paths ARE the point.
- Write directly to the person ("you", "your genomics internship"). No headers,
  no bullet spam — a flowing story of 350-550 words: where you stand, why, and
  two or three climbs worth considering, each justified hop by hop.
- If the zone clamp failed (education outranks every match), say so honestly —
  it means their education suggests a higher level than their experience evidence.
- The start-zone band exists because starting positions must match COMPLETED
  preparation: a student cannot begin at the top rung however strong their
  resume reads. When an aspiration is present, treat it as the arc of the
  story: "you already resemble X — here is the realistic starting point and
  the ladder that gets you to X", using the aspiration ladder's hops. An empty
  ladder means the graph found no route within three moves; say so plainly."""


@dataclass
class NarrationResult:
    story: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


def _clip(text: str | None, limit: int) -> str | None:
    """Model output is untrusted: clip strings to their DB column widths.

    user_profiles declares education_level varchar2(50), current_title and the
    experience title/organization varchar2(300), location_text varchar2(200);
    an unclipped long parse crashes create_profile with ORA-12899.
    """
    if text is None:
        return None
    return text[:limit]


def _sane(value: float | None, lo: float, hi: float) -> float | None:
    """Model output is untrusted: out-of-range numerics become None, not errors.

    Observed on a real resume: duration_months of 1.2e15, which Oracle's
    number(5) column rightly rejected. Dropping the value keeps the rest of the
    extraction usable; the raw resume text is retained for re-parsing anyway.
    """
    if value is None or not (lo <= value <= hi):
        return None
    return value


def parsed_to_profile(user_ref: str, raw_text: str, parsed: ParsedResume) -> ProfileInput:
    """Adapt the LLM extraction onto the intake schema, sanitizing numerics."""
    return ProfileInput(
        user_ref=user_ref,
        raw_text=raw_text,
        current_title=_clip(parsed.current_title, 300),
        years_experience=_sane(parsed.years_experience, 0, 60),
        education_level=_clip(parsed.education_level, 50),
        education_in_progress=parsed.education_in_progress,
        education_majors=parsed.education_majors,
        location_text=_clip(parsed.location_text, 200),
        experiences=[
            ExperienceInput(
                raw_text=e.accomplishment_summary,
                title=_clip(e.title, 300),
                organization=_clip(e.organization, 300),
                duration_months=(
                    int(m) if (m := _sane(e.duration_months, 0, 600)) is not None
                    else None
                ),
            )
            for e in parsed.experiences
        ],
    )


_WAGE_SCOPE_LABELS = {
    "national": "national medians",
    "bay": ("San Francisco-Oakland-Berkeley MSA medians where the region has "
            "enough filings, national medians otherwise"),
    "alameda": ("Alameda County, CA medians where the county has enough "
                "filings, national medians otherwise"),
}


def _hop_wages(hop) -> dict:
    """Wage fields for a hop dict — present only when BOTH ends are priced.

    Null wage keys read as "zero pay" to small narration models, so hops
    without data simply omit the keys.
    """
    if hop.wage_delta is None:
        return {}
    return {
        "median_wage_from": hop.src_wage,
        "median_wage_to": hop.dst_wage,
        "wage_delta": round(hop.wage_delta, 2),
    }


def build_narration_context(
    profile: ProfileInput, match: MatchResult, paths: list[Path],
    aspiration_paths: list[Path] | None = None,
    wage_scope: str = "national",
) -> dict:
    """Assemble the decision record the narration is grounded in.

    Everything the story may cite is in this dict; nothing else reaches the model.
    """
    return {
        "parsed_profile": {
            # The self-description carries stated interests; the match is made on
            # experience evidence, so the story can honestly bridge "where the
            # evidence puts you" and "where you said you want to go".
            "self_description": profile.raw_text[:500],
            "current_title": profile.current_title,
            "education_level": profile.education_level,
            "education_majors": profile.education_majors,
            "years_experience": profile.years_experience,
            "experiences": [e.raw_text for e in profile.experiences],
        },
        "starting_point": {
            "matched_occupation": match.matched.title,
            "job_zone": match.matched.job_zone,
            "similarity": match.matched.similarity,
            "start_zone_band": {
                "floor": match.floor_zone,
                "ceiling": match.ceiling_zone,
                "explanation": (
                    "the band of job zones the person's COMPLETED education and "
                    "actual experience support starting in; stronger matches "
                    "above the ceiling become aspirations, not starts"
                ),
            },
            "start_moved_off_top_match": match.zone_clamped,
            "education_zone_clamp_failed": match.zone_clamp_failed,
            "start_ceiling_applied": match.ceiling_applied,
            "start_ceiling_unmet": match.ceiling_unmet,
            "experience_gate_relaxed": match.experience_gate_relaxed,
            "candidates_considered": [
                {"title": c.title, "job_zone": c.job_zone, "similarity": c.similarity}
                for c in match.candidates
            ],
        },
        "aspiration": None if not match.aspirations else {
            "occupation": match.aspirations[0].title,
            "job_zone": match.aspirations[0].job_zone,
            "similarity": match.aspirations[0].similarity,
            "meaning": (
                "the occupation the person resembles most strongly but cannot "
                "start at yet; the ladder below is how the graph gets there"
            ),
            "ladder": [
                {
                    "total_cost": round(path.cost, 3),
                    "hops": [
                        {
                            "from": hop.src_title, "to": hop.dst_title,
                            "move": "up one zone"
                            if hop.edge_type == "career_ladder" else "lateral",
                            "skill_transfer_weight": round(hop.weight, 3),
                            **_hop_wages(hop),
                        }
                        for hop in path.hops
                    ],
                }
                for path in (aspiration_paths or [])
            ],
        },
        "surfaced_paths": [
            {
                "destination": path.destination,
                "total_cost": round(path.cost, 3),
                "hops": [
                    {
                        "from": hop.src_title,
                        "to": hop.dst_title,
                        "move": "up one zone"
                        if hop.edge_type == "career_ladder"
                        else "lateral",
                        "from_zone": hop.src_zone,
                        "to_zone": hop.dst_zone,
                        "skill_transfer_weight": round(hop.weight, 3),
                        **_hop_wages(hop),
                    }
                    for hop in path.hops
                ],
            }
            for path in paths
        ],
        "methodology_notes": {
            "match_similarity": (
                "MiniLM text-embedding cosine between the person's profile/"
                "experiences and each occupation's description document"
            ),
            "edge_weights": "mean-centered cosine over O*NET skill importance vectors",
            "zones": "O*NET job zones 1-5 measure required preparation",
            "thresholds": "up-one-zone hops need weight >= 0.30; lateral >= 0.45",
            "cost": "path cost = sum of (1 - weight); lower is stronger",
            "wages": (
                "median offered base salary from certified full-time H-1B LCA "
                f"filings (DOL, FY2026): "
                f"{_WAGE_SCOPE_LABELS.get(wage_scope, _WAGE_SCOPE_LABELS['national'])}; "
                "hops without wage fields have no wage data for one of the "
                "two occupations"
            ),
        },
    }


def _log(pool, call_type: str, thread_id: str, inputs: dict, outputs: dict | None,
         latency_ms: float, model_name: str, status: str = "success",
         error_message: str | None = None) -> None:
    """Best-effort observability write. A logging failure must never destroy an
    already-completed LLM result, so errors are reported and swallowed."""
    if pool is None:
        return
    try:
        with pool.acquire() as connection:
            connection.cursor().execute(
                """
                insert into tool_log (thread_id, turn_seq, call_type, inputs, outputs,
                                      latency_ms, model_name, status, error_message)
                values (:t, 1, :c, :i, :o, :l, :m, :s, :e)
                """,
                t=thread_id, c=call_type, i=json.dumps(inputs),
                o=json.dumps(outputs) if outputs is not None else None,
                l=round(latency_ms, 1), m=model_name, s=status,
                e=error_message[:4000] if error_message else None,
            )
            connection.commit()
    except Exception as exc:  # noqa: BLE001 - observability must not break the call
        print(f"warning: tool_log write failed ({exc})", flush=True)


class OllamaBackend:
    """Local model via the Ollama native API (no key; nothing leaves the host)."""

    def __init__(self, model: str, base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url.rstrip("/")

    def _chat(self, system: str, user: str, schema: dict | None) -> tuple[str, int, int]:
        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "think": False,
            "options": {"temperature": 0.2, "num_ctx": 16384},
        }
        if schema is not None:
            payload["format"] = schema
        response = httpx.post(
            f"{self.base_url}/api/chat", json=payload, timeout=OLLAMA_TIMEOUT_S
        )
        response.raise_for_status()
        data = response.json()
        text = data["message"]["content"]
        # Defensive: some thinking models leak their reasoning inline.
        if "</think>" in text:
            text = text.split("</think>", 1)[1].strip()
        return text, int(data.get("prompt_eval_count", 0)), int(data.get("eval_count", 0))

    def parse(self, system: str, user: str) -> ParsedResume:
        text, _, _ = self._chat(system, user, ParsedResume.model_json_schema())
        return ParsedResume.model_validate_json(text)

    def complete(self, system: str, user: str) -> tuple[str, int, int]:
        return self._chat(system, user, None)


class AnthropicBackend:
    """Claude API via the official SDK. Requires ANTHROPIC_API_KEY."""

    def __init__(self, client=None):
        import anthropic

        self.model = ANTHROPIC_MODEL
        self.client = client or anthropic.Anthropic()

    def parse(self, system: str, user: str) -> ParsedResume:
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=PARSE_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=ParsedResume,
        )
        parsed = response.parsed_output
        if parsed is None:
            # parsed_output is None when the model refused or was cut short.
            raise RuntimeError(
                f"resume parse returned no structured output "
                f"(stop_reason={getattr(response, 'stop_reason', 'unknown')})"
            )
        return parsed

    def complete(self, system: str, user: str) -> tuple[str, int, int]:
        with self.client.messages.stream(
            model=self.model,
            max_tokens=NARRATE_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
        ) as stream:
            response = stream.get_final_message()
        if response.stop_reason in ("refusal", "max_tokens"):
            # A refusal or truncation must not pass as a finished story.
            raise RuntimeError(f"narration stopped early ({response.stop_reason})")
        from anthropic.types import TextBlock

        text = "".join(
            block.text for block in response.content if isinstance(block, TextBlock)
        )
        return text, response.usage.input_tokens, response.usage.output_tokens


def default_backend():
    """Resolve the backend from settings (``llm_backend``: ollama | anthropic)."""
    settings = get_settings()
    if settings.llm_backend == "anthropic":
        return AnthropicBackend()
    return OllamaBackend(model=settings.ollama_model, base_url=settings.ollama_base_url)


def parse_resume(
    resume_text: str,
    pool=None,
    thread_id: str = "resume-intake",
    backend=None,
) -> ParsedResume:
    """Extract the profile schema from raw resume text (call_type llm_parse)."""
    backend = backend or default_backend()
    inputs = {"resume_chars": len(resume_text)}
    started = time.monotonic()
    try:
        parsed = backend.parse(PARSE_SYSTEM, resume_text)
    except Exception as exc:
        _log(pool, "llm_parse", thread_id, inputs, None,
             (time.monotonic() - started) * 1000, backend.model,
             status="failure", error_message=str(exc))
        raise
    _log(pool, "llm_parse", thread_id, inputs,
         {"current_title": parsed.current_title,
          "n_experiences": len(parsed.experiences),
          "education_level": parsed.education_level},
         (time.monotonic() - started) * 1000, backend.model)
    return parsed


def narrate_paths(
    profile: ProfileInput,
    match: MatchResult,
    paths: list[Path],
    pool=None,
    thread_id: str = "resume-intake",
    backend=None,
    aspiration_paths: list[Path] | None = None,
    wage_scope: str = "national",
) -> NarrationResult:
    """Turn the decision record into a grounded story (call_type llm_compose)."""
    backend = backend or default_backend()
    context = build_narration_context(
        profile, match, paths, aspiration_paths, wage_scope=wage_scope)
    user_message = (
        "Here is the decision record. Explain it to me as a story:\n\n"
        + json.dumps(context, indent=2)
    )
    inputs = {"n_paths": len(paths), "matched": match.matched.title}
    started = time.monotonic()
    try:
        story, input_tokens, output_tokens = backend.complete(NARRATE_SYSTEM, user_message)
        if not story.strip():
            raise RuntimeError("narration returned an empty story")
    except Exception as exc:
        _log(pool, "llm_compose", thread_id, inputs, None,
             (time.monotonic() - started) * 1000, backend.model,
             status="failure", error_message=str(exc))
        raise
    latency_ms = (time.monotonic() - started) * 1000
    result = NarrationResult(
        story=story,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
    )
    _log(pool, "llm_compose", thread_id, inputs,
         {"story_chars": len(story), "output_tokens": output_tokens},
         latency_ms, backend.model)
    return result
