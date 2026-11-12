# 26ai Demo Optimization Plan

**Date:** 2026-11-09
**Status:** Phases 0–2 complete 2026-11-10 (taxonomy-wide embed deferred); Stage 2 (Scorecard outcomes) complete 2026-11-12; 3–5 pending
**Goal:** Align the Career Path Navigator demo with the product scope — an Oracle
Autonomous AI Database 26ai showcase for sales calls: multi-hop traversal,
in-database semantic retrieval (DBMS_VECTOR + ONNX), memory modalities,
observability, and a React/Next.js visual layer.

## Where the project stands

Verified live on the restarted instance (26ai 23.26.4.1.0, 2026-11-09):

- O*NET 30.3 loaded and corrected (45 tables, ~1.1M rows, 13 migrations).
- `OCCUPATION_GRAPH` SQL property graph: 124 occupations (115 traversable),
  2,564 edges (centered-cosine derivation + O*NET related), GRAPH_TABLE queries
  verified.
- In-database ONNX (`ALL_MINILM_L12_V2`, 384-dim) with `vector_embedding()`,
  124 occupation embeddings, 3 HNSW vector indexes.
- Full pipeline working end to end: resume text → LLM parse (Ollama qwen3:14b /
  Anthropic pluggable) → A-inference with the student start-zone band →
  graph path surfacing → aspiration ladders → grounded LLM narration.
- Requirements layer loaded: 169 hot-tech tools, education (RL) and experience
  (RW) requirements for 103 occupations, `coverage()` gap math implemented.
- Test suite: 322 passed, 2 skipped, 93% coverage; ruff + mypy clean; 18/18 live health checks.
- Deliverables: DEMO.md, Multi-Demo.md, methodology doc, two demo scripts.

## Phases

### Phase 0 — Operational recovery (DONE 2026-11-09)
- [x] Restart the stopped Always Free instance (auto-stops after 7 days idle;
      reclaimed if left stopped ~3 months — keep it warm).
- [x] Full integration pass: 16/16 DB health checks, unit suite, both demos.
- [x] Pin `OLLAMA_MODEL=qwen3:14b` (qwen3:8b no longer installed locally).
- [x] Commit and push all outstanding work.

### Phase 1 — Make 26ai features the demo surface (DONE 2026-11-10)
- [x] Switch `paths.py` to GRAPH_TABLE as the primary traversal (hop-join SQL
      stays as fallback behind `GRAPH_ENGINE=hop_join`) — quantified `{1,3}`
      match, ONE ROW PER STEP, MATCHNUM()/ELEMENT_NUMBER(); ISO path modes
      rejected by this build, so WALK mode + Python acyclic filter. Live
      full-corpus equivalence: 124 starts, 147,117 paths per engine, zero diff.
- [x] Make A-inference candidate search index-eligible: restructured into
      per-query-vector `FETCH APPROX FIRST k` blocks (no WHERE — a filter
      flips the plan back to a full scan; k padded by the 9 null-zone rows,
      filtered in Python). `VECTOR INDEX HNSW SCAN` confirmed in the live plan.
- [x] Print latency and row counts in the demos (`[latency]` lines + a
      Traversal header naming the engine).

### Phase 2 — Scale the retrieval story (DONE 2026-11-10, except optional embed)
- [x] Populate `tasks.embedding` (2,382 rows, 41.5s) via in-DB
      `vector_embedding()` over the CLOB directly (`scripts/embed_tasks.py`).
- [ ] Optionally embed the full ~1,016-occupation taxonomy for retrieval only
      (graph stays scoped to the 3 verticals).
- [x] Prove approximate search honestly: `scripts/search_tasks.py --plan`
      pulls the real plan from v$sql (`VECTOR INDEX HNSW SCAN` on
      TASKS_EMBEDDING_VIX, 68 ms APPROX top-8) and prints the
      `DBMS_VECTOR.INDEX_ACCURACY_QUERY` report (100% at target 95).
      TASKS_EMBEDDING_VIX dropped + rebuilt after embedding (10.9s).

### Phase 3 — Gap analysis in the demo (1 session)
- [ ] LLM extracts stated tools from the resume → match into
      `profile_requirements` via `normalized_key`.
- [ ] Surface `coverage()` output in demos and narration
      ("to start as X you're missing: Tableau, SQL").

### Phase 4 — Memory modalities (1–2 sessions)
- [ ] `saved_plans` writeback with its vector column (semantic memory).
- [ ] `retrieval_evidence` rows per inference (episodic memory).
- [ ] Together with `tool_log` (observability), this makes the
      "3 memory modalities" scope bullet true. Evaluate `oracleagentmemory`
      as the wrapper.

### Phase 5 — Visual demo layer (largest; spec first)
- [ ] Next.js front end over a thin API: resume input → parse panel →
      candidate/band decision view → interactive weighted graph →
      story + gap panel → observability drawer reading `tool_log`.
- [ ] Run brainstorm → spec → plan before any code.

### Stage 2 — College Scorecard degree outcomes (DONE 2026-11-12)
- [x] Fetch + load 6,196 institutions / 62,926 priced programs; NCES CIP-SOC
      crosswalk (966 links, all 124 occupations); 370 CIPs embedded in-DB.
- [x] Free-text major -> CIP by vector match; outcomes aggregation; fused feeder
      ranking; spatial nearby-programs; JSON Relational Duality view
      `major_outcomes_dv`; narration grounded on published figures only.
- [x] `scripts/health_check.py` (18 probes) and `scripts/benchmark.py`.
- [ ] Stage 3 (USAJobs postings: spatial + in-DB embedding + hybrid index) —
      needs the USAJobs key; Scorecard data also enables a per-institution
      comparison once the resume parse captures the school.

### Opportunistic / data levers
- [x] ~~OEWS wage load~~ Superseded: the H-1B LCA load (2026-11-09) activated
      the edge wage penalty with quarterly-fresh data.
- [ ] Widen the zone-3 occupation layer (18 today) to reduce `ceiling_unmet`
      frequency for student profiles.
- [ ] Narration backend swap to Anthropic for sales-quality prose when an API
      key is available.

## Known limitations (by design, documented in NOTES.md)
- Zone-5 occupations are terminal (~69% reachability ceiling).
- 8 occupations ride on work-styles-only vectors.
- Consulting/marketing verticals out of scope (comms persona matches ~0.35).
- Small local models occasionally paraphrase the start zone imprecisely in
  narration; the cited decision record is always correct.
