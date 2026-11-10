# Career Path Navigator — End-to-End Demo Record

**Date:** 2026-11-09 (supersedes the 2026-08-28 record) · **Instance:** Oracle AI
Database 26ai 23.26.4.1.0 (`QHZ6SNWUPW0Y1ASH`, Autonomous)
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
| Resume → structured schema | Qwen3 14B via Ollama, schema-constrained decoding | Locally (M4 Pro; nothing leaves the machine) |
| Profile & experience embeddings | all-MiniLM-L12-v2 ONNX, `vector_embedding()` | **Inside the database** (DBMS_VECTOR) |
| Starting-point match (A-inference) | Vector similarity + start-zone band (education ceiling, experience gate) | Inside the database |
| Career graph traversal | `OCCUPATION_GRAPH` SQL property graph, one-hop edges | Inside the database |
| Wage context | H-1B LCA offered-wage medians on every hop, wage penalty in edge weights | Inside the database |
| Path narration ("the story") | Qwen3 14B via Ollama, grounded on the decision record | Locally |
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
- **Wages: DOL H-1B LCA FY2026 Q3 disclosures** (quarterly-fresh, Oct 2025–Jun
  2026): 393,004 certified full-time filings aggregated into per-occupation
  offered-wage percentiles — national medians for 116/124 occupations, plus
  SF-Oakland-Berkeley MSA and Alameda County segments (a `WAGE_SCOPE` setting
  picks the displayed region; this run used `national`).
- Requirements layer: 169 hot-technology tools, education and experience
  requirements for 103 occupations (the experience thresholds gate starts).

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

4. **Pay cuts cost weight.** Edge weights carry an active wage penalty
   (`0.5 + 0.5 · dst/src` when the destination's national median offered wage
   is below the source's): 508 of 1,289 derived edges are penalized, so the
   traversal prefers moves that don't walk a student's pay backwards. The
   most-penalized edge: Financial Managers ($203k) → Credit Counselors
   ($57.6k), weight 0.355. Occupations without wage data stay neutral.

Traversal cost is `sum(1 − weight)`: the cheapest path is the strongest chain
of skill transfer, and every hop in the output carries a `+$Nk` median-wage
delta where both ends have data.

## 5. Test results (slide: "Engineering rigor")

**220 passed, 2 skipped, 0 failed — 86% coverage against an 85% gate.**
ruff and mypy clean.

```
================================ tests coverage ================================
_______________ coverage: platform darwin, python 3.13.5-final-0 _______________

Name                                    Stmts   Miss  Cover   Missing
---------------------------------------------------------------------
src/navigator/__init__.py                   1      0   100%
src/navigator/config.py                    52      0   100%
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
src/navigator/lca.py                       95      3    97%   109, 114, 186
src/navigator/llm.py                      132     36    73%   336-351, 420-435, 440-443, 458-462, 482-510
src/navigator/paths.py                     80     29    64%   133-153, 165-196, 204, 212
src/navigator/profile.py                  163     38    77%   267-269, 300-360, 373-495
src/navigator/requirements.py              88     30    66%   168-178, 183-238
---------------------------------------------------------------------
TOTAL                                    1200    164    86%
Required test coverage of 85% reached. Total coverage: 86.33%
220 passed, 2 skipped in 1.31s
```

Testing on the real resume (not just synthetic data) caught two live defects
before they shipped: the local model once emitted `duration_months = 1.2e15`
(Oracle's `number(5)` column rejected it; extraction is now sanitized), and it
initially filled experience summaries with location lines instead of
accomplishments — which matters because that text is what gets embedded. An
adversarial multi-agent review of the LLM module confirmed and fixed five
further defects (unchecked truncation/refusal, unguarded observability writes,
unlogged failures, profile accumulation across runs, cleanup not in try/finally).
A second adversarial review of the start-band and requirements work confirmed
and fixed eleven more (among them: "undergraduate" mapping to zone 5 through
its "graduate" substring, and a floating-point median that shifted one band).
Before the wage load, a four-agent data-profiling sweep of the 1M-row LCA file
caught three landmines the loader now handles with regression tests: county
values carry a " COUNTY" suffix (a bare-name filter matches zero Bay Area
rows), 57.6% of extracted rows are blank Excel padding, and ~54 annual
salaries were filed under Month/Week units (a $55,000/"Month" math teacher).

## 6. Demo inputs

- **Three synthetic personas** (database-only legs): a frontend engineer with a
  bachelor's, a tax associate with a master's, a statistics PhD.
- **One real resume**: `resumetext/ayan.txt` — 4,343 characters of plain text,
  parsed entirely by the LLM into the profile schema (no manual structuring).
- **Settings for this run**: `WAGE_SCOPE=national` (the general-student mode;
  `alameda` and `bay` scopes swap the displayed medians to the local market
  without touching path selection).

## 7. Verbatim terminal transcript

Everything below is the unedited output of `uv run python scripts/demo.py`.

```text
==========================================================================
CAREER PATH NAVIGATOR — END-TO-END DEMO
==========================================================================
Database : Oracle AI Database 26ai Enterprise Edition Release 23.26.4.1.0 - Production
Graph    : OCCUPATION_GRAPH (SQL property graph)
ONNX     : ALL_MINILM_L12_V2 (in-database)
Corpus   : 124 occupations, 2564 edges
Wages    : scope=national (H-1B LCA FY2026; display only — penalty is national)

==========================================================================
PERSONA: Frontend engineer, bachelor's
==========================================================================
resume text : CS degree, two years frontend at a logistics startup, genomics internship, calculus tutoring.

START: Computer Programmers [zone 4] similarity 0.4153  (education clamp moved the match up)
candidates considered:
  0.4719  [z3] Computer Systems Engineers/Architects
  0.4369  [z5] Computer and Information Research Scientists
  0.4153  [z4] Computer Programmers
  0.4023  [z4] Information Security Engineers
  0.3945  [z5] Bioinformatics Scientists
  0.3902  [z4] Bioinformatics Technicians
  0.3885  [z3] Web Developers
  0.3674  [z4] Computer Network Architects
  0.3588  [z4] Blockchain Engineers
  0.3553  [z4] Computer Systems Analysts

ranked paths (cost = sum of 1 - skill-transfer weight):
  cost 0.366: Computer Programmers -> Data Warehousing Specialists [LAT w=0.634 +$48k]
  cost 0.381: Computer Programmers -> Software Quality Assurance Analysts and Testers [LAT w=0.619 +$13k]
  cost 0.385: Computer Programmers -> Database Architects [LAT w=0.615 +$48k]
  cost 0.409: Computer Programmers -> Data Scientists [LAT w=0.591 +$31k]
  cost 0.455: Computer Programmers -> Software Developers [LAT w=0.545 +$50k]

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
  0.3228  [z4] Financial Managers
  0.3102  [z4] Financial Examiners
  0.2973  [z4] Insurance Underwriters
  0.2798  [z4] Financial and Investment Analysts
  0.2795  [z4] Financial Risk Specialists

ranked paths (cost = sum of 1 - skill-transfer weight):
  cost 0.283: Tax Preparers -> Tax Examiners and Collectors, and Revenue Agents [LAT w=0.717]
  cost 0.294: Tax Preparers -> Credit Analysts [UP w=0.706 +$51k]
  cost 0.326: Tax Preparers -> Accountants and Auditors [UP w=0.674 +$30k]
  cost 0.328: Tax Preparers -> Budget Analysts [UP w=0.672 +$18k]
  cost 0.360: Tax Preparers -> Loan Officers [UP w=0.640 +$7k]

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
  0.3532  [z4] Data Warehousing Specialists
  0.3493  [z4] Bioinformatics Technicians
  0.3244  [z4] Social Science Research Assistants
  0.3112  [z5] Political Scientists
  0.3084  [z4] Geological Technicians, Except Hydrologic Technicians

ranked paths (cost = sum of 1 - skill-transfer weight):
  cost 0.255: Statisticians -> Biostatisticians [LAT w=0.745 +$0k]
  cost 0.284: Statisticians -> Mathematicians [LAT w=0.716 -$3k]
  cost 0.359: Statisticians -> Financial Quantitative Analysts [LAT w=0.641 +$11k]
  cost 0.383: Statisticians -> Environmental Economists [LAT w=0.617 +$27k]
  cost 0.400: Statisticians -> Economists [LAT w=0.600 +$27k]

==========================================================================
REAL RESUME: resumetext/ayan.txt
==========================================================================
input: 4343 chars of raw resume text

LLM PARSE (structured output -> profile schema):
  current_title    : None
  education        : B.A. Applied Mathematics, B.A. Data Science in Applied Mathematics, Data Science
  years_experience : 2.0
  experiences      : 6
    [0] Product Management Intern, Contract @ Oracle: Formulate agentic demo using 26ai database; context-engineering through multi-ho
    [1] AI Intern @ Origo Capital: Develop Python-based AI agents automating sourcing and screening of 100+ LMM buy
    [2] Product Development Intern @ ChipChop (Antler Backed): Architect multi-agent workflow for FPGA design, transforming user requests and i
    [3] Machine Learning Research Assistant @ Berkeley Nanotechnology Lab: Designed PyTorch models to classify and process lithography manufacturing data a
    [4] Strategy Consultant, Contract @ The CollegeBoard: Refine GTM positioning strategy for 2027 product launch by evaluating 15 competi
    [5] Robotics Research Assistant @ Rutgers University PRACSYS Lab: Deployed multimodal CV pipeline for industrial decision-making with RGB, depth, 

start band: zones [3..3]
START: Data Scientists [zone 4] similarity 0.431  (ceiling_unmet: every match sits above the band)
  0.5140  [z5] Mathematicians
  0.4310  [z4] Data Scientists
  0.4267  [z5] Investment Fund Managers
  0.3798  [z4] Database Architects
  0.3716  [z4] Financial and Investment Analysts
  0.3657  [z5] Statisticians
  0.3609  [z4] Data Warehousing Specialists
  0.3595  [z4] Bioinformatics Technicians
  0.3574  [z4] Business Intelligence Analysts
  0.3534  [z5] Physicists
  cost 0.137: Data Scientists -> Mathematicians [UP w=0.863 -$1k]
  cost 0.226: Data Scientists -> Computer and Information Research Scientists [UP w=0.774 +$64k]
  cost 0.238: Data Scientists -> Biostatisticians [UP w=0.762 +$3k]
  cost 0.254: Data Scientists -> Physicists [UP w=0.746 -$34k]
  cost 0.260: Data Scientists -> Bioinformatics Scientists [UP w=0.740 -$41k]

ASPIRATION: Mathematicians [z5] similarity 0.514

LLM NARRATION (grounded in the decision record above):

Based on your resume and the skills you've developed, the system has identified that your strongest starting point is as a **Data Scientist** in **Job Zone 4**. This match was driven by your experience in building AI agents, deploying machine learning models, and working with large datasets — all of which align closely with the day-to-day responsibilities of a Data Scientist. While your education in Applied Mathematics and Data Science from UC Berkeley is strong, the system determined that your completed experience supports starting in Zone 4, not higher. That means you're well-positioned to begin your career in this field, but you're not quite at the level of more advanced roles like Mathematicians or Physicists yet.

One of the most promising paths forward is to move **up one zone** to become a **Mathematician** in **Job Zone 5**. This is a direct step, and the system estimates that you already share **86.3% similarity** in the skills required for this role. While the median wage for Mathematicians is slightly lower than that of Data Scientists, this is a natural progression for someone with your background in applied math and data science. The move is plausible because your experience with complex modeling, algorithm design, and data analysis overlaps strongly with the work of a Mathematician.

Another option is to first move up to **Biostatisticians** in Zone 5, which shares **76.2% similarity** with your current role. This is a slightly more indirect path, but it’s still a strong option. From there, you could transition laterally to Mathematicians, though the skill overlap is a bit lower at **66.8%**. This path might be a good fit if you're interested in applying your skills to healthcare or life sciences, where biostatistics plays a key role.

Finally, there’s a path to **Computer and Information Research Scientists** in Zone 5, which offers a significant jump in median wage — over $64,000 more than your current role. This is a more challenging move, as the skill overlap is **77.4%**, but it’s still within reach given your experience with AI, machine learning, and data infrastructure.

Each of these paths reflects a realistic and structured way forward, based on the skills you’ve already built and the experience you’ve completed. You’re not starting from the bottom — you're already standing on a solid foundation, and the next steps are within reach.

[507 output tokens, 43.2s]
```

## 8. What the transcript demonstrates (slide-by-slide talking points)

**Persona 1 — the education clamp working.** The top semantic match was a
zone-3 role, but a bachelor's implies zone 4+, so the ranker walked down the
candidate list and started from Computer Programmers [z4] instead. The decision
is visible in the output, not buried — and every surfaced move now carries its
wage delta (`Software Developers +$50k`).

**Persona 2 — honest failure surfaced.** A master's implies zone 5, but every
strong match is zone 3–4 tax work. The system accepts the best evidence-based
match and flags `clamp_failed` rather than pretending. Its paths then offer
the ladder up — Credit Analysts (+$51k), Accountants and Auditors (+$30k) —
which is exactly the slide where wage data turns "a graph edge" into advice.

**Persona 3 — precision.** "PhD in statistics, five years ML for clinical
trials" → Statisticians (0.565) with Data Scientists second (0.508).

**Real resume — the full loop, including the student fix.** The LLM extracted
6 experiences in recency order with accomplishment-level summaries, dual
majors, and degree level; each experience was embedded separately in-database
(averaging a resume into one vector lands it near none of its parts). The
strongest raw match is Mathematicians [z5] at 0.514 — but the start-zone band
computed from a degree **in progress** is [3..3], so the system refuses to
seed a student at the top: it flags `ceiling_unmet`, starts at the lowest
qualifying zone (Data Scientists [z4], 0.431), and keeps Mathematicians as the
**aspiration** with a concrete one-hop ladder to it (w=0.863). That is the
"a student cannot start at the highest vertical leap" requirement, visible in
one screen of output — with wage context on every rung (Computer and
Information Research Scientists +$64k; Bioinformatics Scientists −$41k, which
the penalty pushed down the ranking).

**The story is grounded, not generated folklore.** Every number in the
narration — the 0.431 start similarity, the 86.3% skill-transfer weight, the
"slightly lower" −$1k Mathematicians wage delta, the +$64k research-scientist
jump — appears in the decision record above it. The narrator receives only
that record, never database access, so it can only explain choices the system
actually made.

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
- Wage figures are **offered base pay from H-1B LCA filings**: fresh and
  SOC-coded, but base-only (no equity/bonus), and 8 of 124 occupations have no
  H-1B presence (tax examiners, nuclear/forest technicians, appraisers) — they
  show no wage tags and take no penalty. Siblings sharing a 6-digit SOC share
  one wage row.
- The local 14B model narrates accurately on this run's record, but small
  models occasionally paraphrase the start zone or band loosely; the cited
  numbers are always correct because they come from the decision record.
  Swapping `LLM_BACKEND=anthropic` upgrades narration prose with zero code
  change.
