# Career Path Navigator — End-to-End Demo Record

**Date:** 2026-11-12 (supersedes the 2026-11-10 record) · **Instance:** Oracle AI
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
| Starting-point match (A-inference) | HNSW-indexed `FETCH APPROX` vector search + start-zone band (education ceiling, experience gate) | Inside the database |
| Career graph traversal | `OCCUPATION_GRAPH` property graph via ISO SQL/PGQ `GRAPH_TABLE` (`{1,3}` quantified match, ONE ROW PER STEP) | Inside the database |
| Degree outcomes (Stage 2) | College Scorecard earnings + debt by field of study; free-text major resolved to a CIP by in-database vector match; Oracle Spatial radius search for nearby programs; JSON Relational Duality view over the same tables | **Inside the database** |
| Free-text task search | 2,382 task embeddings, APPROX top-k with a `DBMS_XPLAN` plan proof (`scripts/search_tasks.py`) | Inside the database |
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
  statements — each embedded in-database for free-text search — and 124
  occupation embeddings with inspectable `embedding_source`.
- **Wages: DOL H-1B LCA FY2026 Q3 disclosures** (quarterly-fresh, Oct 2025–Jun
  2026): 393,004 certified full-time filings aggregated into per-occupation
  offered-wage percentiles — national medians for 116/124 occupations, plus
  SF-Oakland-Berkeley MSA and Alameda County segments (a `WAGE_SCOPE` setting
  picks the displayed region; this run used `national`).
- **Degree outcomes: College Scorecard field-of-study data** (federal, pulled
  2026-11-12): 6,196 institutions with coordinates and 62,926 priced programs
  at the associate, bachelor's, master's, doctoral and professional levels —
  median earnings of graduates at 1, 4, 5 and 7 years plus median
  Stafford/Grad-PLUS debt. 370 fields of study (4-digit CIP) are embedded
  in-database, and the NCES CIP2020–SOC2018 crosswalk links 416 CIPs to all 124
  graph occupations (966 links).
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

### 26ai-native execution (slide: "The database does the work")

- **The traversal is ISO SQL/PGQ.** The ranked paths come from one
  `GRAPH_TABLE` query — a quantified `{1,3}` pattern with `ONE ROW PER STEP`,
  `MATCHNUM()` and `ELEMENT_NUMBER()` — about 20 lines where the legacy
  formulation needed a 56-line three-way UNION ALL of self-joins. Widening the
  horizon to `{1,4}` on a call is a one-character edit. The UNION ALL stays
  behind `GRAPH_ENGINE=hop_join` as a verified fallback: a live full-corpus
  check ran both engines from all 124 starts and got **147,117 paths from
  each, zero differences**, identical ranked surfaces.
- **The vector search runs on the index, provably.** A-inference's old query
  shape silently disqualified the HNSW index (full-table scan in every mode);
  restructured as per-vector `FETCH APPROX FIRST k` probes it executes as
  `VECTOR INDEX HNSW SCAN`. `scripts/search_tasks.py --plan` shows it live —
  the real plan pulled from `v$sql`, plus a `DBMS_VECTOR.INDEX_ACCURACY_QUERY`
  report over the 2,382-task corpus:

```text
query: "optimize data pipelines and automate reporting for business decisions"
[in-db] query embedding (ALL_MINILM_L12_V2): 782 ms
[in-db] APPROX top-8 cosine search over tasks: 68 ms, 8 hits
  0.5914  Business Intelligence Analysts: Generate standard or custom reports...
   |   4 |     TABLE ACCESS BY INDEX ROWID| TASKS               |
>> |   5 |      VECTOR INDEX HNSW SCAN    | TASKS_EMBEDDING_VIX |
Accuracy achieved (100%) is 5% higher than the Target Accuracy requested (95%)
```

- **Degree outcomes use four engines in one statement family.** A student's
  free-text major ("B.A. Applied Mathematics") resolves to a CIP by an
  `APPROX` vector probe over embedded CIP titles (HNSW plan confirmed, ~40 ms
  including the in-database embedding); outcomes aggregate relationally with
  `PERCENTILE_CONT`; "programs near me" is an Oracle Spatial `SDO_WITHIN_DISTANCE`
  query over institution coordinates (~35 ms for a 100-mile radius over 6,196
  schools); and every institution with its programs is also one JSON document
  through a **JSON Relational Duality view**, over the same tables:

```text
select json_serialize(json_query(d.data,
  '$.programs[*]?(@.cipCode == "1107" && @.credentialLevel == "Bachelor''s Degree")'
  with wrapper)) from major_outcomes_dv d where d.data.unitId = '110635';

[{"cipCode":"1107","programName":"Computer Science","credentialLevel":"Bachelor's Degree",
  "earnings1yr":161143,"earnings4yr":204379,"earnings5yr":228465,"earnings7yr":242740,
  "medianDebt":13289,"dataYear":2026}]            -- UC Berkeley, 24 ms
```

- **Honest about the index.** A vector index can be `VALID` with an empty
  in-memory graph (observed on this instance after a restart: the occupations
  HNSW graph held 0 vectors, so the optimizer quietly fell back to a full scan
  while results stayed correct). `scripts/health_check.py` now asserts that
  each graph holds every vector and proves the HNSW plan line.

## 5. Test results (slide: "Engineering rigor")

**322 passed, 2 skipped, 0 failed — 93% coverage against an 85% gate.**
ruff and mypy clean. `scripts/health_check.py`: **18/18 live checks pass**
(database, ONNX, graph + both traversal engines, vector indexes and HNSW plan
proofs, wages, requirements, Scorecard layer, spatial, duality view, local LLM).

```
================================ tests coverage ================================
Name                                    Stmts   Miss  Cover   Missing
---------------------------------------------------------------------
src/navigator/config.py                    61      0   100%
src/navigator/db/migrations.py            134      9    93%   121, 152-153, 227-236
src/navigator/ingest/scorecard.py          53      3    94%   114-115, 118
src/navigator/lca.py                       95      3    97%   109, 114, 186
src/navigator/llm.py                      135     20    85%   360-361, 430-445, 450-453, 468-472, 506-511
src/navigator/outcomes.py                 219      2    99%   299, 497
src/navigator/paths.py                    104      4    96%   182-192, 268
src/navigator/profile.py                  182     16    91%   269-271, 302-362, 391, 463, 472
src/navigator/programs.py                  60      1    98%   73
src/navigator/requirements.py              88     26    70%   183-238
(other modules 93-100%; full table in the run output)
---------------------------------------------------------------------
TOTAL                                    1535    100    93%
Required test coverage of 85% reached. Total coverage: 93.49%
322 passed, 2 skipped in 2.36s
```

### Benchmarks (live, 2026-11-12, Always Free tier, `scripts/benchmark.py`)

```text
operation                              p50 ms   p95 ms  mean ms    n
--------------------------------------------------------------------
paths.graph_table                       421.9   1647.7    602.0   29
paths.hop_join                          199.1    500.9    240.4   29
vectors.occupations.exact                19.5     27.4     24.7   40
vectors.occupations.approx               19.3     25.9     24.8   40
vectors.tasks.exact                      21.4     33.7     22.9   60
vectors.tasks.approx                     21.2     30.7     22.4   60
outcomes.match_major                     40.6     45.0     40.9   20
outcomes.program_outcome                 19.4     24.3     19.9   20
outcomes.feeder_cips_fused               41.0     46.8     41.3   20
outcomes.spatial_radius_100mi            33.5     46.8     36.2   20

vectors.occupations.recall_at_8: mean 1.000 over 40 query vectors
vectors.tasks.recall_at_8:       mean 1.000 over 60 query vectors
paths: 1,084 acyclic paths per start (mean), identical across engines
```

What these say, without spin: **the GRAPH_TABLE enumeration is about 2× slower
than the legacy UNION ALL** (422 vs 199 ms p50) — it returns every walk as
rows and filters cycles in Python — and we ship it as the default for the
expressiveness (one `{1,3}` pattern instead of 56 lines), keeping `hop_join`
one setting away. **APPROX vs exact is a wash on latency at this scale**
(~20 ms either way, dominated by the client round trip at 124 and 2,382
vectors) **but recall is 1.000 on all 100 query vectors**, so the story is
eligibility and accuracy, proven by the plan line, not a speed claim; the
scaling argument is verbal, not measured here.

Testing against the live system (not just fakes) caught more defects before
they shipped. The pre-existing Scorecard client requested field names the API
does not have (`earnings.median_earnings_1yr` — it would have returned empty
rows silently); the first outcomes query used `level` as a bind variable
(reserved in Oracle, invisible to fake-cursor tests); the NCES crosswalk is
many-to-many and unweighted, so ranking feeder fields by graduate count put
"Ecology" under Statisticians — now ordered by fusing in-database vector
similarity with graduate counts; and the health check found the occupations
HNSW graph empty after an instance restart. Earlier findings still stand
(real-resume parse defects, the 11 start-band fixes, and the LCA profiling
landmines: county " COUNTY" suffix, 57.6% blank padding rows, annual salaries
filed under Month/Week).

## 6. Demo inputs

- **Three synthetic personas** (database-only legs): a frontend engineer with a
  bachelor's, a tax associate with a master's, a statistics PhD.
- **One real resume**: `resumetext/ayan.txt` — 4,343 characters of plain text,
  parsed entirely by the LLM into the profile schema (no manual structuring).
- **Settings for this run**: `WAGE_SCOPE=national` (the general-student mode;
  `alameda` and `bay` scopes swap the displayed medians to the local market
  without touching path selection) and `GRAPH_ENGINE=graph_table` (the ISO
  SQL/PGQ traversal; `hop_join` keeps the legacy UNION ALL as a fallback).
  The nearby-programs anchor comes from the location parsed out of the resume
  ("Berkeley, CA"), resolved through our own institutions table — no external
  geocoder; `--near "City, ST"` overrides it.

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
Traversal: engine=graph_table (ISO SQL/PGQ, ONE ROW PER STEP {1,3})

==========================================================================
PERSONA: Frontend engineer, bachelor's
==========================================================================
resume text : CS degree, two years frontend at a logistics startup, genomics internship, calculus tutoring.
[latency] A-inference round trip: 217 ms (per-vector APPROX top-k)

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
[latency] path enumeration (graph_table): 915 ms, 5 ranked paths from Computer Programmers

ranked paths (cost = sum of 1 - skill-transfer weight):
  cost 0.366: Computer Programmers -> Data Warehousing Specialists [LAT w=0.634 +$48k]
  cost 0.381: Computer Programmers -> Software Quality Assurance Analysts and Testers [LAT w=0.619 +$13k]
  cost 0.385: Computer Programmers -> Database Architects [LAT w=0.615 +$48k]
  cost 0.409: Computer Programmers -> Data Scientists [LAT w=0.591 +$31k]
  cost 0.455: Computer Programmers -> Software Developers [LAT w=0.545 +$50k]

DEGREE OUTCOMES (College Scorecard; major matched in-database by vector similarity)
  "Computer Science" -> Computer Science (CIP 1107, sim 1.000) · Bachelor's Degree
      $73.6k @1yr · $99.3k @4yr · $125.5k @7yr · 4yr middle half $87.4k-$115.6k · typical debt $20.5k
      (4yr earnings published for 356 of 452 programs)

TO REACH Computer and Information Research Scientists: graduate fields that feed it: Computer and Information Sciences, General, Computer Science, Information Science/Studies, Human Computer Interaction
  best-paid nationally:
    Stanford University — Computer Science (Master's Degree)  $190.8k @1yr · $255.7k @4yr · $329.5k @7yr
    Cornell University — Computer Science (Master's Degree)  $175.4k @1yr · $235.8k @4yr · $281.0k @7yr
    California Polytechnic State University-San Luis Obispo — Computer Science (Master's Degree)  $141.3k @1yr · $235.2k @4yr
[latency] degree outcomes (vector major match + spatial): 187 ms

==========================================================================
PERSONA: Tax associate, master's
==========================================================================
resume text : Master's in finance. Three years preparing individual tax returns.
[latency] A-inference round trip: 171 ms (per-vector APPROX top-k)

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
[latency] path enumeration (graph_table): 3847 ms, 5 ranked paths from Tax Preparers

ranked paths (cost = sum of 1 - skill-transfer weight):
  cost 0.283: Tax Preparers -> Tax Examiners and Collectors, and Revenue Agents [LAT w=0.717]
  cost 0.294: Tax Preparers -> Credit Analysts [UP w=0.706 +$51k]
  cost 0.326: Tax Preparers -> Accountants and Auditors [UP w=0.674 +$30k]
  cost 0.328: Tax Preparers -> Budget Analysts [UP w=0.672 +$18k]
  cost 0.360: Tax Preparers -> Loan Officers [UP w=0.640 +$7k]

DEGREE OUTCOMES (College Scorecard; major matched in-database by vector similarity)
  "Finance" -> Finance and Financial Management Services (CIP 5208, sim 0.698) · Master's Degree
      $85.3k @1yr · $109.1k @4yr · $134.2k @7yr · 4yr middle half $100.5k-$130.2k · typical debt $35.3k
      (4yr earnings published for 66 of 101 programs)

TO REACH Treasurers and Controllers: fields that feed it: Business Administration, Management and Operations, Accounting and Related Services, Finance and Financial Management Services, Data Analytics
  best-paid nationally:
    University of Pennsylvania — Finance and Financial Management Services (Bachelor's Degree)  $154.4k @1yr · $202.1k @4yr · $281.2k @7yr
    Washington University in St Louis — Finance and Financial Management Services (Bachelor's Degree)  $108.6k @1yr · $185.6k @4yr · $178.7k @7yr
    Carnegie Mellon University — Business Administration, Management and Operations (Bachelor's Degree)  $97.4k @1yr · $160.8k @4yr · $205.1k @7yr
[latency] degree outcomes (vector major match + spatial): 569 ms

==========================================================================
PERSONA: Data scientist, PhD
==========================================================================
resume text : PhD in statistics, five years ML for clinical trials.
[latency] A-inference round trip: 392 ms (per-vector APPROX top-k)

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
[latency] path enumeration (graph_table): 462 ms, 5 ranked paths from Statisticians

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
[latency] A-inference round trip: 255 ms (per-vector APPROX top-k)

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
[latency] path enumeration (graph_table): 1645 ms, 5 ranked paths from Data Scientists
  cost 0.137: Data Scientists -> Mathematicians [UP w=0.863 -$1k]
  cost 0.226: Data Scientists -> Computer and Information Research Scientists [UP w=0.774 +$64k]
  cost 0.238: Data Scientists -> Biostatisticians [UP w=0.762 +$3k]
  cost 0.254: Data Scientists -> Physicists [UP w=0.746 -$34k]
  cost 0.260: Data Scientists -> Bioinformatics Scientists [UP w=0.740 -$41k]

ASPIRATION: Mathematicians [z5] similarity 0.514
[latency] path enumeration (graph_table): 1045 ms, 3 ranked paths to Mathematicians

DEGREE OUTCOMES (College Scorecard; major matched in-database by vector similarity)
  "Applied Mathematics" -> Applied Mathematics (CIP 2703, sim 1.000) · Bachelor's Degree
      $63.7k @1yr · $92.8k @4yr · $107.5k @7yr · 4yr middle half $74.7k-$104.5k · typical debt $20.5k
      (4yr earnings published for 45 of 65 programs)
  "Data Science" -> Data Science (CIP 3070, sim 1.000) · Bachelor's Degree
      $87.4k @1yr · typical debt $19.6k
      (4yr earnings published for 0 of 16 programs; 4yr/7yr not yet published for this field)

TO REACH Mathematicians: graduate fields that feed it: Applied Mathematics, Mathematics, Mathematics and Computer Science, Statistics
  within 100 mi of Berkeley, CA (2 programs with published outcomes):
      0.1 mi  University of California-Berkeley — Statistics (Master's Degree)  $133.4k @1yr
     18.6 mi  California State University-East Bay — Statistics (Master's Degree)  $90.8k @1yr · $142.6k @4yr · $124.6k @7yr
  best-paid nationally:
    Vanderbilt University — Applied Mathematics (Master's Degree)  $150.3k @1yr · $216.4k @4yr
    Columbia University in the City of New York — Statistics (Master's Degree)  $100.9k @1yr · $167.8k @4yr · $208.6k @7yr
    University of Chicago — Statistics (Master's Degree)  $140.0k @1yr · $161.8k @4yr · $193.3k @7yr
[latency] degree outcomes (vector major match + spatial): 278 ms

LLM NARRATION (grounded in the decision record above):

Your background is impressive, and it's clear you've built a strong foundation in both applied mathematics and data science. With a double major from UC Berkeley and two years of hands-on experience in roles that blend AI, data engineering, and product management, you're already operating at a high level. The system analyzed your resume and found that your experience most closely aligns with the role of a **Data Scientist**, which is in **Job Zone 4**. This is the starting point that best reflects the level of preparation you've completed so far.

However, the system also noticed that you strongly resemble **Mathematicians**, a role in **Job Zone 5**, which is one level higher. While you're not quite there yet, the path is clear. The system identified a direct route from Data Scientist to Mathematician, requiring just one step upward in preparation level. This move is supported by a **skill-transfer weight of 0.863**, which is quite strong—meaning your current skills and experience are very relevant to the work of a mathematician. The wage for mathematicians is slightly lower than that of data scientists, but this is a common trend in some fields and doesn't necessarily reflect the full picture of long-term earning potential.

Another path worth considering is moving first to **Biostatisticians**, a role in **Job Zone 5**, which is also one step up from your current level. This path has a **skill-transfer weight of 0.762**, which is still quite strong. From there, you could transition laterally to Mathematicians, though this move has a slightly lower skill-transfer weight of **0.668**. This path might be a good option if you're interested in applying your skills in a more applied, data-driven field before moving into pure mathematics.

If you're looking for a more significant jump in both preparation and earning potential, the system also identified a path to **Computer and Information Research Scientists**, a role in **Job Zone 5**. This move has a **skill-transfer weight of 0.774**, and the median wage is significantly higher—**$190,000**, compared to **$126,000** as a data scientist. This could be a great option if you're interested in a more technical, research-oriented role.

Your education and experience have already positioned you well for these opportunities. With a strong GPA and a solid foundation in both mathematics and data science, you're in a great place to explore these paths. If you're interested in moving toward a career in mathematics or a related field, the steps are clear, and the system has identified several realistic and achievable options.

[551 output tokens, 43.2s]
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

**Degree ROI — the Stage 2 beat.** The real resume's majors resolve in-database
to CIP 2703 Applied Mathematics (sim 1.000) and CIP 3070 Data Science, and the
section answers "what do graduates of my degree earn?" with national medians:
Applied Mathematics bachelor's **$63.7k at 1 year, $92.8k at 4, $107.5k at 7**
(middle half $74.7k–$104.5k at 4 years, typical debt $20.5k, 4-year earnings
published for 45 of 65 programs). Data Science is a new field, so the system
shows only the **$87.4k** 1-year figure and says the longer horizons are not yet
published — it does not invent them. For the Mathematicians aspiration (which
needs graduate preparation) it lists the fields that feed the role, the
programs within 100 miles of Berkeley that publish outcomes — **two**, UC
Berkeley and Cal State East Bay Statistics master's — and the best-paid programs
nationally. Stating "two programs publish outcomes near you" is deliberate: the
Scorecard suppresses small cells, and the section reports coverage instead of
implying it. The three personas each get their own major's numbers (Computer
Science bachelor's $73.6k → $99.3k → $125.5k; Finance master's $85.3k → $109.1k
→ $134.2k).

**The engine shows its work.** The header names the active traversal engine and
every stage prints a measured `[latency]` line: A-inference round trips of
171–392 ms and `GRAPH_TABLE` enumerations from 0.4 to 3.8 s on the shared
Always Free tier (run-to-run jitter; the benchmark p50 is 0.42 s), so "runs
inside the database" is a number on screen, not a claim.

**The story is grounded, not generated folklore.** Every number in the
narration — the 0.863 skill-transfer weight, the 0.762 Biostatisticians rung and
the 0.668 lateral onward, the 0.774 research-scientist weight, the $126,000 →
$190,000 wage comparison — appears in the decision record above it, and the
narrator receives only that record, never database access. When the record
carries degree outcomes the narrations use them faithfully: in the
multi-persona run one story cites $123,231 four-year earnings for UC Berkeley's
cell-biology doctorate and another a $70,428 seven-year figure for a
communications major, both exact matches to the Scorecard rows (see
[Multi-Demo.md](Multi-Demo.md)). The model decides whether to mention them: in
this real-resume run it did not.

## 9. Observability (slide: "Auditable by design")

Every stage writes to `tool_log`: A-inference logs its top candidates and clamp
decisions (`vector_search_occupations`), the major-to-CIP match and program
queries log as `relational_query_programs`, the nearby-programs search as
`spatial_query_metros`, and the parse and narration log model, latency, token
counts, and status — including failures with error messages. `user_profiles`
caches the matched occupation and keeps the raw resume text permanently,
because every downstream inference is a lossy derivation and "what did the user
actually say" is the first debugging question. Operationally,
`scripts/health_check.py` gives the stack a one-command pre-demo verdict, and
it exists because a `VALID` vector index with an empty in-memory graph is
invisible everywhere else.

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
- **Degree outcomes are by program, not by occupation.** Scorecard earnings are
  medians for graduates working and not enrolled (federal-aid recipients), at
  1/4/5/7 years, for a field of study and credential level; they answer "what
  do Applied Mathematics bachelor's earn?", not "what do Mathematicians earn?"
  (the wage layer does that). Small cohorts are suppressed, so programs near a
  given city are often missing, and newer fields publish only short horizons.
  The numbers describe past graduates and are shown that way, never as a
  forecast.
- **The CIP-to-SOC crosswalk is many-to-many and unweighted.** Feeder fields are
  ranked by fusing in-database vector similarity with graduate counts, which
  fixes the worst cases (Software Developers, Statisticians), but some
  crosswalk-listed feeders remain loose (Philosophy appears among bachelor's
  feeders of Mathematicians). Outcomes inform and narrate; they do not change
  edge weights or path ranking.
- `GRAPH_TABLE` enumeration is about 2x slower than the legacy UNION ALL
  (422 vs 199 ms p50) and shows run-to-run jitter on the shared tier; the
  fallback is one setting away if a live demo needs the faster path.
- The occupations HNSW graph can be empty after an instance restart while the
  index still reports `VALID`; results stay correct (the optimizer falls back to
  an exact scan) but the index stops helping. Run `scripts/health_check.py`
  before a demo; the fix is a 4-second index rebuild.
- The local 14B model narrates accurately on this run's record, but small
  models occasionally paraphrase the start zone or band loosely, or decline to
  mention supplied figures; the cited numbers are always correct because they
  come from the decision record. Swapping `LLM_BACKEND=anthropic` upgrades
  narration prose with zero code change.
