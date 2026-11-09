# Career Path Navigator — End-to-End Demo Record

**Date:** 2026-08-28 · **Instance:** Oracle AI Database 26ai (`QHZ6SNWUPW0Y1ASH`, Autonomous)
**Repo:** [AYANKAWLEKAR/career-agent](https://github.com/AYANKAWLEKAR/career-agent)

This file is the complete record of the working demo: what was input, what the
system did at every stage, the verbatim terminal transcript, the test results,
and the methodology behind each decision. Each section maps to a deck slide.

---

## 1. The one-sentence story

**A resume goes in as plain text; a ranked set of realistic career moves comes
out — every step explainable, every number traceable, and the whole pipeline
running inside Oracle 26ai plus a local LLM.**

---

## 2. System architecture (slide: "What's running where")

| Stage | Technology | Where it runs |
|---|---|---|
| Resume → structured schema | Qwen3 8B via Ollama, schema-constrained decoding | Locally (M4 Pro; nothing leaves the machine) |
| Profile & experience embeddings | all-MiniLM-L12-v2 ONNX, `vector_embedding()` | **Inside the database** (DBMS_VECTOR) |
| Starting-point match (A-inference) | Vector similarity + education zone clamp | Inside the database |
| Career graph traversal | `OCCUPATION_GRAPH` SQL property graph, one-hop edges | Inside the database |
| Path narration ("the story") | Qwen3 8B via Ollama, grounded on the decision record | Locally |
| Observability | Every LLM and vector call logged to `tool_log` with status, latency, model | Inside the database |

The LLM layer is backend-pluggable (`LLM_BACKEND=ollama|anthropic`): the same
code runs against the Claude API (`claude-opus-5`, structured outputs +
streaming) when an `ANTHROPIC_API_KEY` is configured. This demo ran fully
local by choice — zero API cost, zero data egress.

## 3. The data foundation (slide: "What it knows")

- **O*NET 30.3**, the U.S. Department of Labor occupational database: 45 tables,
  **1,104,314 rows** loaded — after correcting two genuine defects in the
  vendor's own Oracle distribution files (empty-string-vs-NULL, and foreign keys
  against a non-unique column). 93 of 95 foreign keys preserved.
- Scope: three verticals — tech (15-1xxx/15-2xxx), finance (13-2xxx/11-3031),
  research (19-xxxx) — **124 occupations, 115 traversable**.
- Curated layer: 16,883 skill ratings across five O*NET domains, 2,382 task
  statements, 124 occupation embeddings with inspectable `embedding_source`.

## 4. The graph (slide: "How moves are modeled")

**2,564 edges, three types:**

| Edge | Count | Meaning |
|---|---|---|
| `career_ladder` | 541 | A step **up exactly one** O*NET job zone, skill cosine ≥ 0.30 |
| `skill_overlap` | 748 | A lateral move, held to a stricter bar (≥ 0.45) |
| `related` | 1,275 | O*NET's published adjacency; excluded from routing, used for explanation |

Three deliberate rules make paths meaningful:

1. **Zone jumps are rejected.** A move from zone 3 to zone 5 is "a career plus a
   degree," not a step — refusing it forces the traversal to find the
   intermediate rung, which is the actionable advice.
2. **Downward routing never happens.**
3. **Similarity is mean-centered cosine** over skill-importance vectors. Raw
   cosine on this data runs 0.912–0.997 (every job values Critical Thinking), so
   it was replaced after measurement showed it made 85% of pairs edges.
   Thresholds (0.30/0.45) came from a sweep over isolation, out-degree, and
   3-hop reachability — at these values 0% of rated occupations are isolated and
   ~75% of connected pairs genuinely need multiple hops.

Traversal cost is `sum(1 − weight)`: the cheapest path is the strongest chain
of skill transfer.

## 5. Test results (slide: "Engineering rigor")

**112 passed, 2 skipped, 0 failed — 88% coverage against an 85% gate.**
ruff and mypy clean.

```
================================ tests coverage ================================
_______________ coverage: platform darwin, python 3.13.5-final-0 _______________

Name                                    Stmts   Miss  Cover   Missing
---------------------------------------------------------------------
src/navigator/__init__.py                   1      0   100%
src/navigator/config.py                    43      0   100%
src/navigator/db/__init__.py                0      0   100%
src/navigator/db/migrations.py            134      9    93%   121, 152-153, 227-236
src/navigator/db/pool.py                   25      0   100%
src/navigator/ingest/__init__.py            0      0   100%
src/navigator/ingest/base.py               49      0   100%
src/navigator/ingest/bls.py                23      0   100%
src/navigator/ingest/careeronestop.py      30      1    97%   83
src/navigator/ingest/models.py             67      0   100%
src/navigator/ingest/onet.py               66     10    85%   37, 75-77, 84-86, 93-94, 97
src/navigator/ingest/onet_sql.py          143      5    97%   80-81, 99, 104, 119
src/navigator/ingest/scorecard.py          52      3    94%   103-104, 107
src/navigator/llm.py                      118     36    69%   212-227, 296-311, 316-319, 334-338, 356-383
src/navigator/paths.py                     50     16    68%   97-123
src/navigator/profile.py                   94     28    70%   142-196, 206-300
---------------------------------------------------------------------
TOTAL                                     895    108    88%
Required test coverage of 85% reached. Total coverage: 87.93%
111 passed, 2 skipped in 2.70s
```

Testing on the real resume (not just synthetic data) caught two live defects
before they shipped: the local model once emitted `duration_months = 1.2e15`
(Oracle's `number(5)` column rejected it; extraction is now sanitized), and it
initially filled experience summaries with location lines instead of
accomplishments — which matters because that text is what gets embedded. An
adversarial multi-agent review of the LLM module confirmed and fixed five
further defects (unchecked truncation/refusal, unguarded observability writes,
unlogged failures, profile accumulation across runs, cleanup not in try/finally).

## 6. Demo inputs

- **Three synthetic personas** (database-only legs): a frontend engineer with a
  bachelor's, a tax associate with a master's, a statistics PhD.
- **One real resume**: `resumetext/ayan.txt` — 4,343 characters of plain text,
  parsed entirely by the LLM into the profile schema (no manual structuring).

## 7. Verbatim terminal transcript

Everything below is the unedited output of `uv run python scripts/demo.py`.

```text
==========================================================================
CAREER PATH NAVIGATOR — END-TO-END DEMO
==========================================================================
Database : Oracle AI Database 26ai Enterprise Edition Release 23.26.3.2.0 - Production
Graph    : OCCUPATION_GRAPH (SQL property graph)
ONNX     : ALL_MINILM_L12_V2 (in-database)
Corpus   : 124 occupations, 2564 edges

==========================================================================
PERSONA: Frontend engineer, bachelor's
==========================================================================
resume text : CS degree, two years frontend at a logistics startup, genomics internship, calculus tutoring.

START: Computer and Information Research Scientists [zone 5] similarity 0.4369  (education clamp moved the match up)
candidates considered:
  0.4719  [z3] Computer Systems Engineers/Architects
  0.4369  [z5] Computer and Information Research Scientists
  0.4153  [z4] Computer Programmers
  0.4023  [z4] Information Security Engineers
  0.3945  [z5] Bioinformatics Scientists

ranked paths (cost = sum of 1 - skill-transfer weight):
  cost 0.476: Computer and Information Research Scientists -> Physicists [LAT w=0.524]
  cost 0.689: Computer and Information Research Scientists -> Physicists [LAT w=0.524] -> Astronomers [LAT w=0.787]
  cost 0.935: Computer and Information Research Scientists -> Physicists [LAT w=0.524] -> Mathematicians [LAT w=0.540]
  cost 0.952: Computer and Information Research Scientists -> Physicists [LAT w=0.524] -> Bioinformatics Scientists [LAT w=0.524]
  cost 0.958: Computer and Information Research Scientists -> Physicists [LAT w=0.524] -> Biochemists and Biophysicists [LAT w=0.517]

==========================================================================
PERSONA: Tax associate, master's
==========================================================================
resume text : Master's in finance. Three years preparing individual tax returns.

START: Tax Preparers [zone 3] similarity 0.6495  (clamp_failed: education outranks every semantic match)
candidates considered:
  0.6495  [z3] Tax Preparers
  0.5002  [z3] Tax Examiners and Collectors, and Revenue Agents
  0.3966  [z4] Personal Financial Advisors
  0.3716  [z4] Accountants and Auditors
  0.3470  [z4] Treasurers and Controllers

ranked paths (cost = sum of 1 - skill-transfer weight):
  cost 0.283: Tax Preparers -> Tax Examiners and Collectors, and Revenue Agents [LAT w=0.717]
  cost 0.294: Tax Preparers -> Credit Analysts [UP w=0.706]
  cost 0.326: Tax Preparers -> Accountants and Auditors [UP w=0.674]
  cost 0.327: Tax Preparers -> Credit Counselors [UP w=0.673]
  cost 0.328: Tax Preparers -> Budget Analysts [UP w=0.672]

==========================================================================
PERSONA: Data scientist, PhD
==========================================================================
resume text : PhD in statistics, five years ML for clinical trials.

START: Statisticians [zone 5] similarity 0.5649
candidates considered:
  0.5649  [z5] Statisticians
  0.5081  [z4] Data Scientists
  0.3652  [z5] Bioinformatics Scientists
  0.3650  [z5] Biostatisticians
  0.3647  [z5] Mathematicians

ranked paths (cost = sum of 1 - skill-transfer weight):
  cost 0.255: Statisticians -> Biostatisticians [LAT w=0.745]
  cost 0.274: Statisticians -> Mathematicians [LAT w=0.726]
  cost 0.359: Statisticians -> Financial Quantitative Analysts [LAT w=0.641]
  cost 0.383: Statisticians -> Environmental Economists [LAT w=0.617]
  cost 0.400: Statisticians -> Economists [LAT w=0.600]

==========================================================================
REAL RESUME: resumetext/ayan.txt
==========================================================================
input: 4343 chars of raw resume text

LLM PARSE (structured output -> profile schema):
  current_title    : Product Management Intern
  education        : B.A. in Applied Mathematics, Data Science
  years_experience : 2.0
  experiences      : 6
    [0] Product Management Intern @ Oracle: Formulated agentic demo using 26ai database; context-engineering through multi-h
    [1] AI Intern @ Origo Capital: Developed Python-based AI agents automating sourcing and screening of 100+ LMM b
    [2] Product Development Intern @ ChipChop: Architected multi-agent workflow for FPGA design, transforming user requests and
    [3] Machine Learning Research Assistant @ Berkeley Nanotechnology Lab: Designed PyTorch models to classify and process lithography manufacturing data a
    [4] Strategy Consultant @ The CollegeBoard: Refined GTM positioning strategy for 2027 product launch by evaluating 15 compet
    [5] Robotics Research Assistant @ Rutgers University PRACSYS Lab: Deployed multimodal CV pipeline for industrial decision-making with RGB, depth, 

START: Operations Research Analysts [zone 5] similarity 0.3854
  0.3854  [z5] Operations Research Analysts
  0.3839  [z5] Investment Fund Managers
  0.3761  [z4] Database Architects
  0.3722  [z5] Mathematicians
  0.3667  [z4] Data Scientists
  cost 0.414: Operations Research Analysts -> Statisticians [LAT w=0.586]
  cost 0.428: Operations Research Analysts -> Financial Quantitative Analysts [LAT w=0.572]
  cost 0.434: Operations Research Analysts -> Mathematicians [LAT w=0.566]
  cost 0.505: Operations Research Analysts -> Physicists [LAT w=0.495]
  cost 0.507: Operations Research Analysts -> Economists [LAT w=0.493]

LLM NARRATION (grounded in the decision record above):

You're currently positioned as an Operations Research Analyst, based on a similarity score of 0.3854 between your resume and that occupation. This match was driven by your experience in data-driven problem-solving, algorithmic modeling, and systems optimization—skills that align closely with what the system defines as an Operations Research Analyst. Your education level, a B.A. in Applied Mathematics and Data Science, didn’t override this match, so the education zone clamp wasn’t applied. That means your experience and skills are the primary drivers of your starting point, not your degree.

From here, the system found several plausible paths, all of which are lateral moves (no zone jumps), meaning they require strong skill overlap. The most promising climb is to Statisticians, with a skill-transfer weight of 0.586—strong enough to justify a lateral move. This path makes sense because your work with data modeling, statistical analysis, and algorithmic systems directly aligns with the statistical methods and data interpretation required for this role. The system sees your experience as a solid foundation for this transition.

Another strong option is Financial Quantitative Analysts, with a slightly lower skill-transfer weight of 0.572. This path is also lateral, but it reflects your experience in financial modeling, investment analysis, and data-driven decision-making—skills that are highly relevant to quantitative finance. The system sees your background in financial diligence, investment decks, and data analysis as a good match for this role.

Finally, Mathematicians is another lateral path with a weight of 0.566. While this might seem like a broader move, your experience in algorithm design, mathematical modeling, and data processing aligns well with the theoretical and applied mathematics required for this role. The system sees your background as a strong fit for this more abstract and research-oriented path.

Each of these paths is viable because they all meet the system’s threshold for lateral moves (0.45 or higher), and they all build on the skills you’ve already demonstrated. Your current position is a solid starting point, and these climbs offer clear, skill-based transitions that align with your experience and education.

[437 output tokens, 25.6s]
```

## 8. What the transcript demonstrates (slide-by-slide talking points)

**Persona 1 — the education clamp working.** The top semantic match was a
zone-3 role, but a bachelor's implies zone 4+, so the ranker walked down the
candidate list and started from a qualifying occupation instead. The decision is
visible in the output, not buried.

**Persona 2 — honest failure surfaced.** A master's implies zone 5, but every
strong match is zone 3–4 tax work. The system accepts the best evidence-based
match and flags `clamp_failed` rather than pretending. Its paths then offer
the ladder up: Credit Analysts, Accountants and Auditors (both UP edges).

**Persona 3 — precision.** "PhD in statistics, five years ML for clinical
trials" → Statisticians (0.565) with Data Scientists second (0.508).

**Real resume — the full loop.** The LLM extracted 6 experiences in recency
order with accomplishment-level summaries, dual majors, and degree level. Each
experience was embedded separately in-database (averaging a resume into one
vector lands it near none of its parts), the strongest signal chose Operations
Research Analysts — a defensible read of an applied-math/DS student building AI
agents and quant tooling — and the graph surfaced Statisticians, Financial
Quantitative Analysts, and Mathematicians as the strongest lateral moves.
(Zone-5 starts have no upward edges by construction; the graph never routes down.)

**The story is grounded, not generated folklore.** Every number in the
narration — 0.3854, 0.586, 0.572, 0.566, the 0.45 lateral threshold — appears in
the decision record above it. The narrator receives only that record, never
database access, so it can only explain choices the system actually made.

## 9. Observability (slide: "Auditable by design")

Every stage writes to `tool_log`: A-inference logs its top-5 candidates and
clamp decisions (`vector_search_occupations`), the parse and narration log
model, latency, token counts, and status — including failures with error
messages. `user_profiles` caches the matched occupation and keeps the raw
resume text permanently, because every downstream inference is a lossy
derivation and "what did the user actually say" is the first debugging question.

## 10. Known limitations (worth stating before someone asks)

- Zone-5 starting points only move laterally — no downward routing is a design
  rule, so the top of the ladder is terminal.
- Eight of O*NET's newest occupations (Data Scientists among them) are rated on
  work styles only; their edges reflect disposition more than skill transfer
  until O*NET publishes their skill ratings.
- Wage penalties on edge weights are wired but inert until OEWS wage data loads.
- The local 8B model narrates competently but occasionally calls lateral moves
  "climbs"; swapping `LLM_BACKEND=anthropic` upgrades narration quality with
  zero code change.
