# 26ai Demo Optimization Plan

**Date:** 2026-11-09
**Status:** Phase 0 complete; Phases 1–5 pending
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
- Test suite: 158 passed, 2 skipped, ~86% coverage; ruff + mypy clean.
- Deliverables: DEMO.md, Multi-Demo.md, methodology doc, two demo scripts.

## Phases

### Phase 0 — Operational recovery (DONE 2026-11-09)
- [x] Restart the stopped Always Free instance (auto-stops after 7 days idle;
      reclaimed if left stopped ~3 months — keep it warm).
- [x] Full integration pass: 16/16 DB health checks, unit suite, both demos.
- [x] Pin `OLLAMA_MODEL=qwen3:14b` (qwen3:8b no longer installed locally).
- [x] Commit and push all outstanding work.

### Phase 1 — Make 26ai features the demo surface (1–2 sessions)
- [ ] Switch `paths.py` to GRAPH_TABLE as the primary traversal (hop-join SQL
      stays as fallback and per-hop detail source) — the marquee 26ai feature
      should be what the demo executes.
- [ ] Switch A-inference candidate search to `FETCH APPROX FIRST` against the
      existing HNSW index.
- [ ] Print latency and row counts in the demos so "runs inside the database"
      is demonstrated, not asserted.

### Phase 2 — Scale the retrieval story (1 session)
- [ ] Populate `tasks.embedding` (~2,382 rows) via in-DB `vector_embedding()`.
- [ ] Optionally embed the full ~1,016-occupation taxonomy for retrieval only
      (graph stays scoped to the 3 verticals).
- [ ] Capture before/after approximate-search latency numbers for the pitch.

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

### Opportunistic / data levers
- [ ] OEWS wage load (activates the already-wired edge wage penalty).
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
