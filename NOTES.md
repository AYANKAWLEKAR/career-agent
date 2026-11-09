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
