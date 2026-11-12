# Environment notes

- Python 3.14 fallback was needed (2026-08-03): `oracleagentmemory` 26.6.0 publishes wheels
  only for cp310–cp313, and the local `uv` (0.8.0) offers only a 3.14 beta interpreter.
  Project pinned to Python 3.13 per the setup spec's fallback rule. `oracledb` 4.0.2 does
  publish cp314 wheels, so revisit once `oracleagentmemory` ships a cp314 wheel.
- `CREATE PROPERTY GRAPH` outcome on the live instance: not yet attempted (no database
  provisioned in this phase). The migration runner appends the result here when
  008_property_graph.sql runs against a real instance.
- `oracleagentmemory` documented API vs installed wheel: no divergence checked yet; the
  package is declared but unused in this phase.
- Schema deviation: `occupation_skills.level` is named `level_value` because `LEVEL` is
  an Oracle reserved word (the CONNECT BY pseudocolumn) and cannot be an unquoted
  column name.
- [2026-08-24 07:23 UTC] property graph: CREATE PROPERTY GRAPH failed (ORA-06550: line 4, column 37:
PL/SQL: ORA-00942: table or view does not exist
ORA-06550: line 4, column 3:
PL/SQL: SQL Statement ignored
Help: https://docs.oracle.com/error-help/db/ora-06550/)
- [2026-08-24 07:25 UTC] property graph: CREATE PROPERTY GRAPH succeeded

## O*NET 30.3 Oracle distribution: corrected by `scripts/fix_onet_sql.py`

The vendor's Oracle build cannot be executed against Oracle as published. Rather than
absorbing that in the loader — which cost every constraint in the staging schema —
the distribution is rewritten into a corrected copy that any Oracle client can run,
and the loader executes that DDL verbatim.

Two genuine defects, each corrected only where the data proves it necessary:

1. **Empty string versus NULL.** 2,419 `content_model_reference` rows ship `''` for a
   column the same file declares `NOT NULL`. Oracle stores `''` as NULL, so the file
   contradicts itself and the inserts raise ORA-01400. This is a MySQL-ism in a file
   labelled for Oracle — there `''` satisfies `NOT NULL`. Those values are rewritten
   as explicit `NULL` and `NOT NULL` is dropped from **that one column**;
   `content_model_reference.description` is the only column in the distribution that
   needs it.
2. **Foreign key against a non-unique column.** `gwas_to_iwas` and
   `gwas_to_iwas_to_dwas` reference `content_model_reference(element_name)`, which
   carries no unique constraint, so Oracle rejects the whole table with ORA-02270.
   Those **2 of 95** foreign keys are dropped; the other **93 are preserved**, so the
   staging schema keeps its referential integrity.

Two further constructs are valid Oracle but break a naive parser, so they are handled
in `navigator.ingest.onet_sql` rather than "fixed":

* `&` is escaped by splitting a literal and concatenating it
  (`'ABB Optimize IT Predict &'||' Control'`), to dodge SQL*Plus substitution. The
  corrected files carry `SET DEFINE OFF` so the concatenation is unnecessary.
* Dates are written `TO_DATE('2023-08-01', 'yyyy-mm-dd')`, which a comma-splitting
  parser reads as two values.

Because foreign keys are enforced again, load order matters. Files are grouped into
dependency levels and loaded level by level, parallel within a level. Table creation
runs sequentially first: adding a foreign key locks the referenced table, and
concurrent `CREATE TABLE` statements against a shared parent (nearly every table
references `content_model_reference` or `occupation_data`) collide with ORA-00054.

Loaded result: 45 staging tables, 1,104,314 rows, 93 foreign keys, 11 primary keys.

## Graph density measurement (design §4.3, §10)

The design flagged that compression toward job zones 4-5 might starve `career_ladder`
edges. Measured on the real data, the opposite happened, and for a different reason.

Zone distribution is 3:18, 4:60, 5:37 (9 unzoned), which is enough spread. The problem
is the similarity measure: **raw importance cosine over all gated pairs ranges
min 0.912 / median 0.971 / max 0.997**. Both thresholds (0.65 ladder, 0.75 lateral)
sit far below the observed minimum, so 85.2% of all gated pairs become edges — 7,222
of a possible 8,478. A graph that dense makes multi-hop traversal meaningless, since
almost every destination is already one hop away.

Cause: O*NET importance vectors are dominated by elements every occupation rates
highly (Active Listening, Reading Comprehension, Critical Thinking), so raw cosine is
uniformly near 1 and carries little discriminative signal.

Mean-centering each element by its cross-occupation average restores the spread:
**min -0.714 / p25 -0.256 / median -0.034 / p95 0.571 / max 0.934**.

**Resolution: centered cosine is now the default**, and the thresholds are retuned to
match. This is a deviation from §4.2's formula, recorded here because the spec still
describes raw importance. `scripts/build_graph.py --raw-cosine` reproduces the
original behaviour for comparison.

### Retuned thresholds: 0.30 ladder / 0.45 lateral

Carrying the old 0.65/0.75 onto the centered scale swings to the opposite extreme —
118 edges, 1.4% density — because those numbers were chosen against a distribution
that topped out at 1.0. `scripts/tune_thresholds.py` sweeps candidates and scores each
against isolation, out-degree, three-hop reachability, and the share of connected
pairs that are already adjacent. Selected values and why:

| Property | Value | Reasoning |
|---|---|---|
| Isolated occupations | 0% | Hard constraint. At lateral 0.50 isolation appears (3.7%, rising to 13% by 0.60); an occupation with no outgoing edge can never start a path. This caps lateral at 0.45. |
| Density | 15.6% | 84% of candidate pairs rejected, so an edge means something. |
| Median out-degree | 9 | A usable shortlist of next steps rather than 30. |
| Reachable within 3 hops | 39.5% | Against a **ceiling of 69.2%** — never routing downward means zone 5 can only reach zone 5. |
| Already 1 hop | 25.2% | Three quarters of connected pairs need 2-3 hops, which is what makes multi-hop surfacing worth doing. |

Ladder stays below lateral per §4.3: a sideways move that does not raise your level
has to justify itself with stronger skill transfer. Result: 1,130 derived edges plus
1,275 `related`.

Spot check — from Software Developers the top lateral moves are Data Warehousing
Specialists, Database Administrators, Software QA Analysts, Database Architects,
Computer Programmers, and Information Security Analysts.

### Work styles: closing the gap for O*NET's newest occupations

17 of 124 in-scope occupations had no rating in any of the four domains the spec
uses, so they got no skill vector and no derived edges. Nine are "All Other" catch-all
SOC codes with no job zone either; those stay out. The other eight are real and
prominent, and are exactly O*NET's newest additions: Data Scientists, Financial and
Investment Analysts, Financial Risk Specialists, Penetration Testers, Digital
Forensics Analysts, Blockchain Engineers, Web and Digital Interface Designers,
Hydrologic Technicians.

All eight do carry Work Styles ratings, so `work_style` was added as a fifth element
type (migration `009_work_style_element_type.sql`). Two details made this less
straightforward than adding a table to the union:

**Work styles use a different scale.** The other four domains are rated IM
(Importance, 1-5) and LV (Level, 0-7). Work styles are rated **WI ("Work Styles
Impact", -3..3)** and DR -- and DR is not in `scales_reference` at all. WI is mapped
onto importance; there is no level equivalent. Filtering on IM/LV, as the original
transform did, would have silently inserted zero work-style rows.

**Full-length norms scored the data gap as dissimilarity.** With work styles added,
a fully rated occupation has 157 elements and a work-styles-only occupation has 21.
The dot product spans only the 21 shared elements, so dividing by a 157-element norm
understates similarity by roughly `sqrt(21/157)`. Measured: for work-styles-only
pairs the centered cosine reached only p90 = 0.177 / p99 = 0.48 under full-length
norms -- almost nothing cleared the thresholds, and Data Scientists got 4 edges.
Normalizing over the shared subspace instead lifts that to p90 = 0.559 / p99 = 0.834.

Section 4.2 explicitly specifies full-length norms, so this is a deliberate
departure, and a measurement justifies it: for pairs whose element sets match -- every
one of the 107 fully rated occupations -- both formulations give an identical mean
cosine of -0.008, because the shared subspace *is* the full vector. The change is
therefore inert for 107 occupations and corrective for 8.

**Thresholds did not move.** Re-sweeping with work styles included leaves 0.30/0.45
the best pick: isolation is still 0% and still appears at lateral 0.50. Coverage
improved -- three-hop reachability 39.5% -> 52.3%, median out-degree 9 -> 11, and the
already-adjacent share fell 25.2% -> 18.8%, so more connections genuinely need
multiple hops.

Result: **115 traversable occupations** (tech 36, finance 19, research 60), zero rated
occupations isolated, 1,289 derived edges plus 1,275 `related`.

**Caveat worth carrying forward.** For those eight occupations similarity is computed
over work styles alone, which measures disposition (analytical, persistent,
independent) rather than skill transfer. It reads well where disposition and domain
coincide -- Financial and Investment Analysts routes to Investment Fund Managers,
Financial Risk Specialists, and Treasurers and Controllers -- but it is looser
elsewhere: Data Scientists routes to Physicists and Geoscientists, which share a
temperament more than a skill set. When O*NET publishes skill ratings for these
occupations they should be re-derived, and no code change will be needed.

## ONNX embeddings and A-inference (sections 3, 7.1) — implemented

The in-database model is loaded: `ALL_MINILM_L12_V2` (Oracle's augmented
all-MiniLM-L12-v2, tokenizer and pooling inside the ONNX graph, 384 dims). The
documented Object Storage PAR in older blog posts is dead; the current link is
resolved from the 26ai Vector Search guide's redirect and shipped up as a BLOB by
`scripts/load_onnx_model.py` (`--force` to reload). `scripts/embed_occupations.py`
composes `occupations.embedding_source` per section 3 (task order from raw
`task_ratings` IM because curated `tasks.importance` is unpopulated; no posting text
yet) and generates all 124 embeddings in one set-based statement.

`navigator/profile.py` is the entry point: `create_profile` embeds the profile and
each experience separately in-database; `infer_start` scores every occupation
against ALL those vectors, applies the 7.1 zone clamp, caches the match on the
profile row, and logs top-5 candidates to `tool_log`. Three findings from smoke
testing, all now encoded in the module:

1. **"All Other" catch-alls must be excluded from A-inference.** Their generic
   descriptions are semantic attractors, and they have no zone and no edges — a
   match strands the user with no paths. Filter: `job_zone is not null`.
2. **The composite profile vector reintroduced the blurred mean.** Concatenating
   title + majors + all experiences matched a three-experience resume to
   Biological Technicians — above every occupation any single signal pointed at.
   The profile vector now carries only current title + education; experiences are
   matched by their own vectors.
3. **Recency weighting is required, not optional.** Unweighted, a genomics
   internship outranked the current frontend job. Experience vectors decay 10%
   per resume position (floor 0.5) — `RECENCY_DECAY`/`RECENCY_FLOOR`, tunable.

Smoke results: frontend bachelor's -> Computer Systems Engineers/Architects family
(zone-clamped up from z3); tax master's -> Tax Preparers with `zone_clamp_failed`
logged (correct: stated education outranks every semantic match, spec says accept
and flag); statistics PhD -> Statisticians, Data Scientists second at 0.51.

## Start-zone band and aspiration mode (student-overplacement fix)

The section 7.1 zone clamp was a floor with no ceiling: education implied a
MINIMUM starting zone, nothing implied a maximum, and "bachelor's (in
progress)" mapped to the bachelor's tier. Students therefore seeded into
zone-5 occupations — measured live: a biology undergraduate started at
Molecular and Cellular Biologists [z5], a communications student was clamped
UP to Survey Researchers [z5]. Zone 5 is terminal (the graph never routes
downward), so the product's core audience received the one output with no
ladder in it.

The fix, in `profile.py`:

- **Start band.** `infer_start_band` derives [floor, ceiling] from COMPLETED
  preparation: a degree in progress supports the tier below it
  (`education_in_progress`, migration 013, set explicitly by the LLM parse and
  by text markers like "expected"/"candidate"/"junior year"); four or more
  years of experience buys one tier back, mirroring O*NET's own zone-5
  definition ("graduate degree OR extensive experience").
- **Selection.** `pick_start` picks the strongest candidate inside the band,
  falls back below the floor (flagged `zone_clamp_failed`, unchanged
  semantics), relaxes the experience gate before failing (flagged), and when
  every candidate sits above the ceiling starts at the LOWEST zone available
  (flagged `ceiling_unmet`) rather than the highest-similarity one.
- **Experience gate.** The RW-derived per-occupation thresholds (requirements
  loader, spec 2026-08-28 — now implemented: 169 tools, 12 RL + 11 RW
  vocabulary entries, 103 occupations with education and experience
  requirements) gate the start: unknown years or a missing threshold passes,
  because missing data must not masquerade as ineligibility.
- **Aspirations.** Matches strictly above the actual start's zone become
  aspirations — destinations, not entry points — and `surface_paths_to` builds
  the ladder from the chosen start to the top aspiration. Measured after the
  fix: the biology student starts at Bioinformatics Technicians [z4] and gets
  a 2-hop ladder to Molecular and Cellular Biologists [z5]; the statistics
  student starts at Accountants and Auditors with Statisticians [z5] as the
  aspiration.

Candidate pool widened 5 -> 10 so the band has lower-zone candidates to choose
from. RW bands are also the honest answer to "seniority": SOC codes cannot
express within-occupation progression, but an occupation whose median
incumbent needed 4+ prior years is a senior role regardless of zone, and the
gate now enforces exactly that.

## 2026-11-09: Stage 1 — H-1B LCA wages activate the edge wage penalty

Wage source decision: BLS OEWS was rejected (May 2024 release = ~18 months
stale); the DOL OFLC H-1B LCA quarterly disclosure workbook replaced it.
FY2026 Q3 is cumulative (Oct 2025-Jun 2026), carries 6-digit SOC codes and
employer-offered base wages, and is published quarterly. Caveats accepted
and recorded: base pay only, certified filing != hire, coverage skews to
visa-sponsoring professional employers (which matches the demo verticals).

Pipeline: `scripts/fetch_lca.py` downloads the 240 MB workbook ->
slim 12-column CSV -> `scripts/load_lca_wages.py` aggregates per-SOC
percentiles into `metro_wages` (national rows with `metro_id is null` feed
the penalty; SF-Oakland-Berkeley MSA rows, CBSA 41860, are additive).

Measured on FY2026 Q3 (1,032,735 raw rows):
- 57.6% of rows are Excel used-range padding (595,239); real records 437,496.
- Filters: Certified + full-time keeps 393,435; wage parse yield is 100%;
  372 rows (0.095%) blow the [20k, 1.5M] sanity window, all annual figures
  filed under sub-annual units; a FROM > $25k guard on Month/Week/Bi-Weekly
  drops ~54 more mis-filed rows (Hour exempt: $240-356/hr physician comp is
  real).
- `WORKSITE_COUNTY` values carry a " COUNTY" suffix; a bare-name match finds
  ZERO Bay Area rows. Normalization fixed it (23,079 MSA rows); the loader
  now asserts the bay segment is non-empty.
- Coverage at min_count=5: 116/124 occupations have national wages; the 8
  without (tax examiners, nuclear/forest/hydro technicians, appraisers) have
  no H-1B presence and stay penalty-neutral by design.
- SOC6 collisions: 124 occupations collapse to 85 six-digit codes, so
  specializations sharing a code share a wage row (Financial Managers and
  Treasurers both read 11-3031: $203,007 median).

Effect on the graph (edge counts unchanged — the penalty reweights, never
gates): 508/1,289 derived edges now carry a pay-cut penalty; mean
career_ladder weight 0.4819 -> 0.4670, skill_overlap 0.5903 -> 0.5660.
Most-penalized edge: Financial Managers ($203k) -> Credit Counselors
($57.6k), weight 0.355. Hops now surface `median_wage_from/to/wage_delta`
in the narration context (keys absent when either end is unpriced, so small
models cannot read null as "zero pay") and `+$Nk` tags in the demo output.

### Wage scope setting (same day)

`WAGE_SCOPE` (national | bay | alameda, default national) scopes the
DISPLAYED wages only — hop tags and narration context — via per-occupation
fallback to national where a region is thin. The edge penalty always uses
national rows so the graph serves students in general, not just Berkeley.
Loaded segments: 116 national / 75 bay / 63 alameda occupation rows.
Measured contrast: Software Developers $145,018 national vs $187,574
Alameda (+29%); Financial and Investment Analysts -10% locally. Same hop,
two stories: Computer Programmers -> Software Developers is +$50k
nationally, +$80k in Alameda County.

## 2026-11-10: Phase 1+2 — the 26ai-native showcase

### GRAPH_TABLE is the primary traversal

`paths.py` now runs the ISO SQL/PGQ form by default (`GRAPH_ENGINE=graph_table`):
one quantified `{1,3}` match with ONE ROW PER STEP, MATCHNUM() and
ELEMENT_NUMBER() — per-hop edge type/weight/zone preserved, which the old
docstring claimed GRAPH_TABLE could not do (true before 23.7; false now).
This build (23.26.4.1.0) rejects ISO path modes (MATCH ACYCLIC/TRAIL fail to
parse), so the match runs in WALK mode and Python drops walks with repeated
vertices. Both engines share one routing filter
(`edge_type in ('career_ladder','skill_overlap')`, equivalent to the old
`source like 'derived_%'` — live-verified bijection) so they cannot diverge.
Live full-corpus equivalence: 124 starts, 147,117 unique paths per engine,
zero diff, identical ranked surfaces; hop-join 20.6s total vs graph_table
47.4s (~0.17s vs ~0.38s per start). The UNION ALL stays one env var away.

### A-inference is HNSW-index-eligible

The old candidate query (MAX of vector_distance arithmetic under GROUP BY)
ran TABLE ACCESS STORAGE FULL in every mode. It is now one
`FETCH APPROX FIRST k` probe per query vector (profile + each experience),
combined in Python by max((1-dist)*wt). Constraints discovered live: ANY
WHERE clause flips the plan back to a full scan, so the job_zone/null filters
moved to Python with k padded by the 9 null-zone occupations; a bound :k is
fine. Plan now shows VECTOR INDEX HNSW SCAN. Per-vector top-k union provably
contains the global top-N (weights constant per vector preserve distance
order). Same candidates/similarities as before to 4dp on all demo personas.

### Task corpus embedded; APPROX proven honestly

`scripts/embed_tasks.py`: 2,382 task CLOBs embedded in-database in 41.5s
(vector_embedding takes the CLOB directly), then TASKS_EMBEDDING_VIX dropped
and rebuilt in 10.9s — mandatory, since 007 created it over all-NULL rows.
`scripts/search_tasks.py "<free text>" --plan`: in-DB query embedding, APPROX
top-k over tasks (68 ms), occupation titles resolved after (a join would
disqualify the index), real plan pulled from v$sql with the HNSW line
highlighted, and DBMS_VECTOR.INDEX_ACCURACY_QUERY (100% at target 95).
Demos print a Traversal header and measured `[latency]` lines.

Review notes: shipped via two parallel TDD lanes + three adversarial review
lenses (zero blocking findings). The review caught `cursor.callTimeout` being
a silent no-op (call timeout lives on the Connection in python-oracledb) —
fixed here and in the two older scripts that had the same dead line.
Suite: 242 passed, 2 skipped, 91% coverage; ruff + mypy clean.

## 2026-11-12: Stage 2 — College Scorecard degree outcomes

### What shipped

* `scripts/fetch_scorecard.py` -> JSONL (6,196 schools, 215,131 raw programs, ~7 min,
  62 pages of ~2.7 MB); `scripts/load_scorecard.py` loads it idempotently (full
  replace): `institutions` (+ SDO_GEOMETRY point), `programs` (62,926 priced rows
  at associate/bachelor's/master's/doctoral/professional levels), `program_occupations`
  (966 CIP->occupation links, all 124 occupations reachable), `cip_codes` (370 CIPs,
  titles embedded in-DB, HNSW index created AFTER the embeddings).
* Migration `014_scorecard_outcomes.sql`: 5/7-year earnings, loan payment and award
  columns on `programs`; `cip_codes`; and the `major_outcomes_dv` JSON Relational
  Duality view (institutions + programs). Duality DDL worked first try on 26ai
  23.26.4.1.0 with `@insert @update` on both tables.
* `navigator/programs.py` (pure transforms, tested) and `navigator/outcomes.py`
  (major->CIP vector match, program medians via PERCENTILE_CONT, feeder fields,
  city anchor from our own institutions table, spatial radius search, best-paid
  list, report formatter, narration context). Narration gains an optional
  `degree_outcomes` block and a system-prompt rule: cite only published figures,
  say they are past graduates' medians, never estimate an absent horizon.
* Demos print a DEGREE OUTCOMES section per persona; the real resume also gets graduate
  programs near its parsed location. Outcome queries log to `tool_log`
  (`relational_query_programs`, `spatial_query_metros`).
* `scripts/health_check.py` (18 live probes) and `scripts/benchmark.py`.

### Findings worth remembering

* The pre-existing Scorecard client asked for field names that do not exist
  (`earnings.median_earnings_1yr`, `debt.median_debt`); the real paths are
  `earnings.{1,4,5,7}_yr.overall_median_earnings` and
  `debt.staff_grad_plus.all.eval_inst.median`. Fixed with a regression test that pins
  the live names. `school.operating=1` filters closed schools.
* Scorecard suppresses small cells: of 215k raw programs only 62.9k publish anything
  we store, and graduate programs near a given city are often absent (Mathematicians'
  feeder fields have 2 programs with published outcomes within 100 miles of
  Berkeley). Output says "N programs with published outcomes" rather than implying
  coverage. New fields (Data Science, CIP 3070) publish 1-year earnings only.
* The NCES crosswalk is many-to-many and unweighted. Ranking feeder fields by graduate
  count alone is misleading ("Ecology" under Statisticians); similarity alone drops
  Computer Science for Software Developers. `feeder_cips` fuses in-database vector
  similarity (occupation embedding vs CIP title embedding) with graduate counts by
  reciprocal rank. 
* `level` is a reserved word and cannot be a bind name (ORA-01745) — fake-cursor tests
  cannot catch this; live runs did.
* **HNSW graph can be empty while the index is VALID.** After the instance restart,
  `OCCUPATIONS_EMBEDDING_VIX` showed NUM_VECTORS=0 in `v$vector_graph_index` and every
  occupations probe planned a full table scan (correct results, no index). Fix:
  drop + recreate the index (4 s). `health_check.py` now asserts each graph holds all
  its vectors. Run it before every demo day.
* Benchmarks (p50): GRAPH_TABLE enumeration 422 ms vs hop-join 199 ms (2.1x slower,
  identical path sets); APPROX vs exact vector search ~20 ms both at 124 and 2,382
  vectors (client round trip dominates) with recall@8 = 1.000 over 100 query vectors;
  outcome queries 19-41 ms.
* Earnings are federal-aid recipients' medians for graduates working and not enrolled,
  by program (CIP + credential level), not by occupation.
