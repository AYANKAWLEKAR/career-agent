# Requirements Ingestion and Gap Math Design

Populates the §6.2 requirements layer from three already-loaded O\*NET 30.3 staging
tables and implements the coverage/gap computation that §6.4 of the
[path-surfacing design](2026-08-17-path-surfacing-design.md) sketches. After this
lands, "you're missing Tableau" is a query, not an aspiration.

## 1. Scope

**In scope**

- Populate `requirement_items` (the shared vocabulary) and `occupation_requirements`
  from `software_skills`, `education`, and `training_and_experience` — structure
  created by migration `010_requirements.sql`, currently empty.
- A coverage/gap computation from a stored user profile against one occupation's
  requirements, exposed as a Python function.

**Out of scope**

- §6.3 hybrid-index canonicalization of arbitrary user strings. Profile tool
  matching in this phase is exact `normalized_key` match; unmapped
  `profile_requirements` rows are excluded from coverage math per §6.1.
- `llm_parse` extraction wiring (producing `profile_requirements` rows from resumes).
- Posting-derived requirements (Greenhouse/Ashby, §2 of the parent design).
- `education_field` requirements (majors). Only `tool`, `education_level`, and
  `experience` types are populated here.

## 2. Decisions

Four decisions were made explicitly during design review:

1. **Scope: ingestion + gap math** in one feature, so the layer is visible to users
   the moment it lands.
2. **Tools: hot-technology rows only.** O\*NET lists ~180 tools for some occupations
   (Accountants down to "AdSense Tracker"); ingesting all 3,192 distinct tools makes
   gap lists unusable. Only rows with `hot_technology = 'Y'` (3,812 in-scope
   pairs) become requirements. **Recorded consequence:** a resume mentioning a
   non-hot tool earns no coverage credit. Deliberate, and revisitable by widening
   the ingest filter — the loader is idempotent, so re-running with a wider filter
   is the whole migration.
3. **Education: alternatives at ≥10%.** The RL scale is a percentage distribution
   over 12 categories per occupation. Every category holding ≥10% of incumbents
   becomes an `occupation_requirements` row; all such rows for one occupation share
   one `alternative_group`, so satisfying ANY satisfies the requirement
   ("bachelor's OR master's"). `frequency_pct` preserves each category's share.
   This exercises §6.2's alternative-group semantics exactly as designed.
4. **Experience: median band as a single threshold.** The RW scale is a distribution
   over 11 bands, but experience is cumulative — five years satisfies "2–4 years" —
   so alternatives collapse to a threshold anyway. One `experience` requirement per
   occupation: the band at the distribution's 50th percentile, with
   `ordinal_value` = the band's lower bound in years.

## 3. Source data (measured)

| Source | Scale | In-scope coverage | Shape |
|---|---|---|---|
| `software_skills` | flags, no scale | 115 of 115 traversable occupations; 10,226 rows, 3,192 distinct tools, 3,812 hot-tech | (occupation, tool, hot_technology, in_demand) |
| `education` | RL (categories 1–12) | 103 occupations | % of incumbents per education category |
| `training_and_experience` | RW (categories 1–11) | ~103 occupations | % of incumbents per experience band |

The occupations without RL/RW data (the eight work-styles-only occupations among
them) simply get no education/experience requirements — coverage math treats an
absent requirement as not-applicable, never as unmet.

## 4. Ingestion: `scripts/load_requirements.py`

Follows the `build_graph.py` pattern: set-based SQL, idempotent by provenance —
each run deletes `occupation_requirements` rows whose `source` belongs to this
loader and re-inserts, so re-running after a rule change leaves no stale rows.
Vocabulary rows are upserted by the `(requirement_type, normalized_key)` unique key
and never deleted (the vocabulary grows monotonically per §6.3).

### 4.1 Tools

- Vocabulary: distinct hot-tech `workplace_example` values for in-scope occupations.
  `requirement_type='tool'`, `canonical_text` = the name as published,
  `normalized_key` = lowercased, whitespace-collapsed, trimmed. `ordinal_value` null.
- Per occupation: one `occupation_requirements` row per hot-tech (occupation, tool)
  pair. `importance` null, `frequency_pct` null, `alternative_group` null (tools are
  conjunctive evidence, not alternatives), `source='onet_software_hot'`.
- The `in_demand` flag is not stored in this phase; it is recoverable from the raw
  table when a ranking signal is needed.

### 4.2 Education

- Vocabulary: the 12 RL categories from `education_categories` seed 12
  `education_level` rows once. `canonical_text` = the full O\*NET category
  description (so gap output can say "Master's Degree", not "level 5");
  `normalized_key` = `rl-<category>`; `ordinal_value` = the RL→zone mapping below,
  keeping profile comparison on the §7.1 scale:

  | RL category | ordinal_value |
  |---|---|
  | 1 (Less than HS) | 1 |
  | 2 (HS diploma/GED) | 2 |
  | 3–5 (certificate, some college, associate's) | 3 |
  | 6–7 (bachelor's, post-bacc certificate) | 4 |
  | 8–12 (master's through post-doc) | 5 |

- Per occupation: every RL category with `data_value >= 10` becomes a row;
  all rows for one occupation share `alternative_group = 1` (group numbering is
  per-occupation); `frequency_pct` = the category's percentage;
  `source='onet_education_rl'`.

### 4.3 Experience

- Vocabulary: the 11 RW bands seed 11 `experience` rows. `canonical_text` = the
  band description; `normalized_key` = `rw-<category>`; `ordinal_value` = the
  band's lower bound in years:

  | RW band | ordinal_value (years) |
  |---|---|
  | 1 (None) | 0 |
  | 2 (up to 1 month) | 0 |
  | 3 (1–3 months) | 0.083 |
  | 4 (3–6 months) | 0.25 |
  | 5 (6 months–1 year) | 0.5 |
  | 6 (1–2 years) | 1 |
  | 7 (2–4 years) | 2 |
  | 8 (4–6 years) | 4 |
  | 9 (6–8 years) | 6 |
  | 10 (8–10 years) | 8 |
  | 11 (over 10 years) | 10 |

  Bands 1 and 2 both resolve to 0 — either means "no meaningful experience
  required" — and remain distinct vocabulary entries via `normalized_key`.
- Per occupation: one row at the median band — the smallest category whose
  cumulative `data_value` reaches 50% — with `frequency_pct` = that band's own
  percentage, `alternative_group` null, `source='onet_experience_rw'`.

## 5. Gap math: `src/navigator/requirements.py`

One public entry point:

```python
coverage(pool, profile_id, occupation_id) -> CoverageReport
```

```python
@dataclass
class CoverageReport:
    occupation_id: int
    met_tools: list[str]          # canonical_text of matched hot-tech tools
    missing_tools: list[str]      # hot-tech tools with no profile match
    education_satisfied: bool | None   # None when the occupation has no education rows
    education_needed: str | None       # lowest-ordinal alternative's canonical_text when unsatisfied
    experience_satisfied: bool | None
    experience_needed_years: float | None
    coverage_pct: float           # met / (met + missing) over applicable requirements
```

Rules:

- **Tools:** a requirement is met when a `profile_requirements` row for this profile
  has `requirement_id` pointing at the same vocabulary entry. (Mapping profile
  strings to vocabulary entries is §6.3's job; rows with null `requirement_id` are
  excluded from the denominator per §6.1.)
- **Education:** satisfied when `user_profiles.inferred_job_zone >= min(ordinal_value)`
  over the occupation's alternative group — any alternative satisfies the group.
  When unsatisfied, `education_needed` names the lowest-ordinal alternative (the
  cheapest way in).
- **Experience:** satisfied when `user_profiles.years_experience >= ordinal_value`
  of the occupation's single experience requirement.
- **Denominator:** `coverage_pct` counts tools plus (education group: 0 or 1) plus
  (experience: 0 or 1). Absent requirement types are not counted — an occupation
  with no RL data is not "missing education."
- Decision logic (group satisfaction, threshold comparison, denominator assembly)
  lives in pure functions taking plain values, unit-testable without a database;
  the DB function is a thin fetch-then-decide wrapper.

## 6. Error handling

- Loader: any SQL failure aborts the transaction for that section; sections
  (tools/education/experience) commit independently so a partial run is resumable
  by re-running (idempotency makes re-runs safe).
- `coverage()` raises `ValueError` on unknown profile or occupation ids; an
  occupation with zero requirement rows returns a report with empty lists,
  `None` flags, and `coverage_pct = 0.0` — callers distinguish "no data" from
  "nothing met" by the `None`s.

## 7. Testing

- Pure-function units: key normalization, RL→zone mapping, RW band→years table,
  median-band selection over a distribution, alternative-group satisfaction,
  denominator/percentage assembly. These carry the coverage gate.
- Live verification at load time (row counts by type and source; every traversable
  occupation has ≥1 tool requirement; the 12+11 vocabulary seeds present).
- One end-to-end smoke: a stored profile with known tools and education against a
  known occupation, asserting the exact met/missing split.

## 8. Success criteria

- `requirement_items` holds **169 tool entries** (distinct in-scope hot-tech
  tools, measured 2026-08-28), plus exactly 12 education and 11 experience
  entries.
- Every traversable occupation has ≥1 tool requirement; ~103 have education and
  experience requirements.
- `coverage()` on the demo personas returns explainable results — the Tax
  Associate profile shows tax-software gaps against Accountants, not noise.
- Test suite ≥85% coverage, ruff and mypy clean.
