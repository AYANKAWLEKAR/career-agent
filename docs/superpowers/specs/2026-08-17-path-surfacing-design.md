# Path Surfacing Design

**Date:** 2026-08-17
**Status:** Approved approach, pending implementation plan
**Scope:** Given a user profile and a user-named target occupation B, surface ordered
sequences of intermediary occupations leading from the user's inferred current position A to B.

---

## 1. Scope

### In scope

- Ingest O\*NET into the Autonomous Database (occupations, job zones, tasks, skills, related occupations).
- Enrich occupation embeddings with aggregated job-posting text from Greenhouse and Ashby.
- Derive a directed occupation graph supporting multi-hop traversal.
- Extract typed **requirements** (tools, skills, education, experience, certifications) from
  O\*NET structured files and from posting prose via `llm_parse` at ingest.
- A user profile schema whose held attributes map into the same requirement vocabulary.
- Infer starting occupation A from profile text by vector search.
- Compute per-occupation requirement **coverage** and an explicit **gap list** for a profile.
- Return ranked A→B paths, scored by `bridge_value` — whether each intermediary is
  reachable now *and* teaches what the target still demands.

Requirement matching exists to select intermediary jobs. Telling a user which degree to
enroll in remains out of scope; identifying that a target demands a credential they lack is
in scope, because that is what makes an intermediary necessary.

### Explicitly out of scope

These are deferred, not cancelled. The schema already accommodates them.

| Deferred | Reason |
|---|---|
| Education programs (`relational_query_programs`, College Scorecard) | Scope is intermediary jobs, not education advice. Also blocked on the CIP↔SOC crosswalk. |
| Credentials (CareerOneStop) | Not needed to surface intermediary jobs; blocked on registration. |
| Spatial / metro queries | Geography does not affect which occupations bridge A and B. |
| Wage-based path ranking (BLS) | Optional tiebreaker only. Design supports adding it without topology changes. |

### Blocking prerequisite

**The current instance is Oracle 19c and cannot run this design.** Probing confirmed
`VECTOR(384, FLOAT32)` fails with ORA-00907 and native `JSON` fails with ORA-00902.
This design requires 23ai for: the `VECTOR` datatype, `VECTOR_EMBEDDING`, vector indexes,
`FETCH APPROX FIRST`, and `CREATE PROPERTY GRAPH`. A 23ai instance must be provisioned
before any of this applies.

---

## 2. Data ingested and how it is used

### O\*NET — primary source, already downloaded

| File | Target table | Role in path surfacing |
|---|---|---|
| `Occupation Data.txt` | `occupations` | Node identity; description is embedding input |
| `Job Zones.txt` | `occupations.job_zone` | **Supplies edge direction.** Without it there is no graph. |
| `Task Statements.txt` | `tasks` | Embedding enrichment; how people describe daily work |
| `Skills.txt`, `Abilities.txt`, `Knowledge.txt`, `Work Activities.txt` | `occupation_skills` | Exact transition cost between occupations |
| `Content Model Reference.txt` | `skill_elements` | Element names and types |
| `Related Occupations.txt` | `occupation_edges` (`related`) | Seed adjacency, undirected ground truth |
| `Technology Skills.txt` | `occupation_requirements` (`tool`) | **Named tools per occupation — "Python", "R", "Tableau". Ground-truth requirements, no extraction needed.** |
| `Tools Used.txt` | `occupation_requirements` (`tool`) | Physical/technical tools |
| `Education, Training, and Experience.txt` | `occupation_requirements` (`education_level`, `experience`) | Education-level distribution per occupation |

`Technology Skills.txt` is the most underused file in the release for this purpose. It is
SOC-keyed, already normalized to named commodity software titles, and requires no LLM to
parse — it supplies the `tool` requirement type outright. Confirm these filenames when the
archive is unzipped; the release's exact naming should be verified rather than assumed.

`job_zone` is load-bearing and single-sourced. If it is missing or null for an occupation,
that occupation can participate in `related` edges but cannot receive `career_ladder`
edges, and is therefore unreachable as an intermediary. Ingest must report null job-zone
counts as a first-class metric.

### Job postings — Greenhouse and Ashby, dual purpose

Both APIs are public and unauthenticated (verified HTTP 200):

- `https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`
- `https://api.ashbyhq.com/posting-api/job-board/{name}`

Postings serve two ends: aggregated text feeds occupation embedding input, and the
requirements section is parsed into typed requirement rows. **No `job_postings` table
exists** — postings are absorbed as staged payloads, never retrievable as entities.

Binding a posting to an occupation is inference, not ground truth: postings carry free-text
titles, not SOC codes. Procedure — embed the posting title plus description, vector-search
`occupations.embedding`, take the nearest match. Two different thresholds apply to that
match, and the gap between them is deliberate:

| Use | Floor | Why |
|---|---|---|
| Embedding enrichment | 0.60 | Text is averaged into a large composite document; a marginal posting adds slight noise and little harm |
| Requirement attribution | **0.75** | Creates discrete claims — "this occupation requires Python." A misbound posting injects a false requirement that propagates into gap lists and path scoring |

Postings clearing 0.60 but not 0.75 contribute to embeddings only. Postings below 0.60 are
discarded. Both counts are reported by ingest, never silently dropped.

Requires widening the `staging_raw.source` check constraint to admit `'greenhouse'` and
`'ashby'`.

The purpose is vocabulary coverage. O\*NET says "develop applications using programming
languages"; postings say "React, TypeScript, Kubernetes." User profiles read like postings,
not like O\*NET, so enrichment closes a real vocabulary gap in A-inference.

### Ingest ordering

Order is forced by dependency:

```
1. occupations + job_zones      (nodes must exist)
2. skill_elements               (referenced by occupation_skills)
3. tasks, occupation_skills     (embedding input + transition cost)
4. related edges                (needs occupation_id resolution)
5. job postings → staging       (independent, can run anytime after 1)
6. build embedding_source text  (needs 1, 3, 5)
7. generate embeddings          (needs 6)
8. create vector index          (007_indexes.sql — after load, before step 9)
9. derive career_ladder / skill_overlap edges  (needs 7 and 8)
```

Note that `007_indexes.sql` lands at step 8, not last. Its own header warns against running
it before bulk load, which is still respected — but edge derivation in step 9 uses
`FETCH APPROX FIRST`, which requires the vector index to exist. So the index is built after
data is loaded and embedded, and before edges are derived.

---

## 3. Embedding enrichment

A single vector built from the formal description alone underperforms on profile text.
Embeddings are computed from a composite document.

### New column

```sql
alter table occupations add (embedding_source clob);
```

Making the embedded text an inspectable column rather than an inline expression is
deliberate: when retrieval returns something surprising, you need to read exactly what was
embedded. This is the single highest-value debugging affordance in the system.

### Composition

`embedding_source` is assembled per occupation as:

```
title
+ description
+ top 10 task statements by importance
+ top 15 element names by importance (15 total across skill, knowledge, ability,
  and work_activity types combined — not 15 of each)
+ up to 2000 chars of aggregated matched posting text
```

Truncation matters: all-MiniLM-L12-v2 truncates at 512 tokens, so unbounded concatenation
silently discards the tail. Ordering by importance ensures the most diagnostic content
survives truncation. The posting text goes last precisely because it is the least
trustworthy component.

### Generation

One set-based statement, per the phase plan:

```sql
update occupations
set embedding = vector_embedding(ALL_MINILM_L12_V2 using embedding_source as data)
where embedding_source is not null;
```

The ONNX model is loaded once via `DBMS_VECTOR.LOAD_ONNX_MODEL` from Object Storage.
`all-MiniLM-L12-v2` outputs 384 dimensions, matching the column declaration.

---

## 4. Graph construction

Three edge types with distinct provenance recorded in `occupation_edges.source`.
**Only one-hop edges are ever inserted.** Multi-hop is computed at query time.

### 4.1 `related` — direct load

Loaded verbatim from `Related Occupations.txt`, one row per published pair.
`weight` = normalized relatedness index. `source = 'onet_related'`.

These are undirected similarity and are **excluded from path traversal** by default. They
exist for explanation ("these roles are adjacent") and as a fallback when no directed path
is found.

### 4.2 Candidate generation — two-stage

Comparing all occupation pairs on exact skill overlap is O(n²) over ~1,000 occupations with
~120 skill rows each, which produces a very large intermediate join. Instead, narrow first
with the vector index, then compute exact overlap only on survivors.

**Stage 1 — approximate neighbors via vector index:**

```sql
create table edge_candidates as
select a.occupation_id as src_id, k.occupation_id as dst_id, k.dist
from occupations a,
     lateral (
       select b.occupation_id,
              vector_distance(a.embedding, b.embedding, cosine) as dist
       from occupations b
       where b.occupation_id <> a.occupation_id
       order by dist
       fetch approx first 25 rows only
     ) k;
```

~25,000 candidate pairs instead of ~1,000,000.

**Stage 2 — exact weighted skill cosine on candidates only:**

```sql
with norms as (
  select occupation_id, sqrt(sum(power(importance, 2))) as nrm
  from occupation_skills
  where importance is not null
  group by occupation_id
),
dots as (
  select c.src_id, c.dst_id, sum(sa.importance * sb.importance) as dot
  from edge_candidates c
  join occupation_skills sa on sa.occupation_id = c.src_id
  join occupation_skills sb on sb.occupation_id = c.dst_id
                           and sb.element_id  = sa.element_id
  group by c.src_id, c.dst_id
)
select d.src_id, d.dst_id, d.dot / (na.nrm * nb.nrm) as skill_cosine
from dots d
join norms na on na.occupation_id = d.src_id
join norms nb on nb.occupation_id = d.dst_id;
```

Norms are computed over each occupation's full skill vector, not just shared elements —
otherwise the result is not cosine similarity.

### 4.3 `career_ladder` and `skill_overlap` — derived, directional

Direction comes from job zone. Emit A→B when `skill_cosine` clears a threshold and:

| Job zone relation | Edge type | Threshold | Meaning |
|---|---|---|---|
| `B.job_zone = A.job_zone + 1` | `career_ladder` | ≥ 0.65 | Step up one level |
| `B.job_zone = A.job_zone` | `skill_overlap` | ≥ 0.75 | Lateral pivot |
| `B.job_zone > A.job_zone + 1` | *none* | — | Not a single career step |
| `B.job_zone < A.job_zone` | *none* | — | Never route downward |

Two decisions worth stating plainly. Zone jumps greater than one are rejected because a
single move from zone 2 to zone 5 is not a career step — it is a career plus a degree, and
the traversal should be forced to find the intermediate rungs instead. That rejection is
what *creates* multi-hop paths; without it the graph would be full of shortcuts and every
answer would be one hop. Lateral moves carry a higher threshold (0.75 vs 0.65) because a
sideways move that does not raise your level has to justify itself with stronger skill
transfer, or it is just noise in the path.

`weight` = `skill_cosine`. Higher weight means a cheaper, more plausible transition.
Traversal cost is therefore `1 - weight`.

Thresholds are configuration, not constants. They will need tuning against the demo
personas, and the derivation step must be re-runnable idempotently — delete all edges where
`source` starts with `derived_`, then re-insert.

---

## 5. Multi-hop path surfacing

Two mechanisms, serving different purposes.

### 5.1 Ranked weighted paths — recursive CTE (primary)

This is the workhorse. It returns ordered intermediaries, hop count, and total cost in one
statement, and it runs on any Oracle version.

```sql
with path_search (current_id, depth, total_cost, route) as (
  select occupation_id, 0, 0, '>' || occupation_id || '>'
  from occupations
  where occupation_id = :start_id
  union all
  select e.dst_occupation_id,
         p.depth + 1,
         p.total_cost + (1 - nvl(e.weight, 0)),
         p.route || e.dst_occupation_id || '>'
  from path_search p
  join occupation_edges e on e.src_occupation_id = p.current_id
  where p.depth < :max_hops
    and e.edge_type in ('career_ladder', 'skill_overlap')
    and instr(p.route, '>' || e.dst_occupation_id || '>') = 0
)
select route, depth, total_cost
from path_search
where current_id = :target_id
order by total_cost, depth
fetch first :k rows only;
```

The cycle guard delimits the id on **both** sides. Checking `instr(route, '>' || id)` alone
would let occupation 12 falsely match inside 123, silently pruning valid paths.

`max_hops` is bounded at 4. Candidate generation caps out-degree at 25 before thresholding,
so worst-case depth-4 exploration is 25⁴ ≈ 390,000 partial paths. Real out-degree will be
substantially lower once the 0.65/0.75 thresholds and the job-zone rules prune, but the
bound must hold for the worst case: depth 5 would be ≈ 9.8 million and must not be allowed.
Measure actual mean out-degree after the first edge build and revisit `max_hops` then.

### 5.2 Existence and enumeration — `GRAPH_TABLE` (secondary)

The property graph from `008_property_graph.sql` answers "is B reachable from A at all"
efficiently, and is the intended showcase of the platform:

```sql
select * from graph_table (
  occupation_graph
  match (a is occupations where a.occupation_id = :start_id)
        -[e is occupation_edges where e.edge_type in ('career_ladder','skill_overlap')]->{1,4}
        (b is occupations where b.occupation_id = :target_id)
  one row per match
  columns (a.soc_code as from_soc, b.soc_code as to_soc)
);
```

`GRAPH_TABLE` is used for reachability; the CTE is used for ranked paths with recoverable
intermediaries and summed cost. Both are logged to `tool_log` as `graph_traversal_routes`.

### 5.3 Degradation

When no directed path exists within 4 hops, do not fail. Fall back in order: relax to
include `related` edges; then report the closest reachable occupation to B by embedding
distance, stating plainly that no route was found. Returning "no path" with an explanation
beats returning nothing.

---

## 6. Profile and requirement schema

### 6.1 Profile tables

New migration `009_user_profile.sql`.

```sql
create table user_profiles (
  profile_id            number generated always as identity primary key,
  user_ref              varchar2(200) not null,
  raw_text              clob not null,
  -- structured extraction, produced by llm_parse
  current_title         varchar2(300),
  years_experience      number(4,1),
  education_level       varchar2(50),
  inferred_job_zone     number constraint user_profiles_zone_ck
                        check (inferred_job_zone between 1 and 5),
  location_text         varchar2(200),
  -- matching
  embedding             vector(384, float32),
  matched_occupation_id number
                        constraint user_profiles_matched_fk
                        references occupations (occupation_id),
  match_similarity      number(6,4),
  created_at            timestamp with time zone default systimestamp not null,
  updated_at            timestamp with time zone default systimestamp not null
);

create table profile_requirements (
  profile_requirement_id number generated always as identity primary key,
  profile_id       number not null
                   constraint profile_reqs_profile_fk
                   references user_profiles (profile_id) on delete cascade,
  requirement_id   number
                   constraint profile_reqs_item_fk
                   references requirement_items (requirement_id),
  raw_text         varchar2(300) not null,
  requirement_type varchar2(30),
  ordinal_value    number,
  evidence         varchar2(500),
  source           varchar2(30) constraint profile_reqs_source_ck
                   check (source in ('stated', 'inferred_from_title', 'inferred_from_text')),
  created_at       timestamp with time zone default systimestamp not null
);
```

`raw_text` on `user_profiles` is retained permanently. Every downstream inference — the
embedding, the matched occupation, the extracted requirements — is a lossy derivation, and
when a path looks wrong the first question is always what the user actually said.

`profile_requirements.requirement_id` is **nullable** while `raw_text` is **not null**. A
user will state things with no vocabulary entry ("bedside manner"). Requiring the mapping
would force either discarding those or inventing bad matches; this shape keeps the unmapped
text for later reconciliation, and unmapped rows are simply excluded from coverage math.

`matched_occupation_id` and `match_similarity` cache the A-inference so it is not recomputed
per query, and so a wrong A is visible and correctable rather than buried in an ephemeral
call.

### 6.2 Requirement vocabulary

New migration `010_requirements.sql`. One canonical vocabulary is shared by occupations and
profiles, which is what turns matching into relational algebra rather than a similarity
computation.

```sql
create table requirement_items (
  requirement_id   number generated always as identity primary key,
  requirement_type varchar2(30) not null
                   constraint requirement_items_type_ck
                   check (requirement_type in
                     ('tool', 'skill', 'education_level', 'education_field',
                      'experience', 'certification')),
  canonical_text   varchar2(300) not null,
  normalized_key   varchar2(200) not null,
  -- education_level: 1-5 per the job-zone table in 7.1
  -- experience:      years
  -- null for tool, skill, certification, education_field
  ordinal_value    number,
  embedding        vector(384, float32),
  created_at       timestamp with time zone default systimestamp not null,
  updated_at       timestamp with time zone default systimestamp not null,
  constraint requirement_items_uq unique (requirement_type, normalized_key)
);

create table occupation_requirements (
  occupation_requirement_id number generated always as identity primary key,
  occupation_id     number not null
                    constraint occ_reqs_occupation_fk
                    references occupations (occupation_id) on delete cascade,
  requirement_id    number not null
                    constraint occ_reqs_item_fk
                    references requirement_items (requirement_id) on delete cascade,
  importance        number(5,2),
  frequency_pct     number(5,2),
  -- rows sharing a non-null group are alternatives: satisfying ANY satisfies the group
  alternative_group number,
  source            varchar2(50) not null,
  created_at        timestamp with time zone default systimestamp not null,
  constraint occ_reqs_pair_uq unique (occupation_id, requirement_id)
);
```

Four decisions that matter.

**Education splits into two types.** `education_level` is ordinal and compared numerically;
`education_field` is a fuzzy set compared by embedding. "Bachelor's in Data Science" is two
requirements, not one, because the level and the field fail independently — someone with a
master's in English satisfies the level and not the field.

**`alternative_group` is required, not a nicety.** "Bachelor's in Data Science, Computer
Science, or **related fields**" is three `education_field` rows where satisfying one
satisfies all. Without grouping, a Data Science major would score 1/3 on a requirement they
fully meet. Null group means the requirement is conjunctive.

**Unique on `(occupation_id, requirement_id)`, not including `source`.** When both O\*NET and
postings assert Python, that is one requirement with merged evidence, not two. Merge happens
at load: `source` records the primary provenance, `importance` comes from O\*NET where
available, `frequency_pct` from posting counts.

**`skill_elements` is not replaced.** The two vocabularies serve different masters and both
stay: `occupation_skills`/`skill_elements` holds O\*NET's abstract rated elements and drives
**edge derivation** (complete coverage, numerically rated, every occupation); `requirement_items`
holds concrete named things and drives **user matching** (nameable, gap-able, actionable).
Collapsing them would either make edges unreliable or make gap lists unspeakable.

### 6.3 Canonicalization

Mapping an extracted string to a vocabulary entry is the one place vectors and keywords are
both needed, which is why this uses a **hybrid index** rather than either alone. `R` is a
single character that embeds meaninglessly but keyword-matches perfectly; "experience
leading cross-functional teams" is the reverse. A hybrid index over
`requirement_items.canonical_text` with `embedding` as the vector parameter covers both arms,
mirroring the `credentials_hybrid_vix` pattern already in `007_indexes.sql`.

Procedure per extracted string: hybrid-search the vocabulary; accept the top hit above 0.80
combined score; otherwise insert a new `requirement_items` row. The vocabulary therefore
grows monotonically and self-organizes, at the cost of needing periodic synonym review.

### 6.4 Coverage and gap

Matching is a join. Alternatives collapse via `max` within group, then coverage is
importance-weighted across groups:

```sql
with req as (
  select r.requirement_id, r.requirement_type, r.canonical_text, r.embedding,
         r.ordinal_value as required_ordinal,
         nvl(orq.importance, 1) as importance,
         nvl(orq.alternative_group, -orq.requirement_id) as grp
  from occupation_requirements orq
  join requirement_items r on r.requirement_id = orq.requirement_id
  where orq.occupation_id = :occupation_id
),
held as (
  select pr.requirement_id, pr.ordinal_value as held_ordinal, ri.embedding
  from profile_requirements pr
  join requirement_items ri on ri.requirement_id = pr.requirement_id
  where pr.profile_id = :profile_id
),
-- exact credit, with ordinal sufficiency for level/experience types
exact as (
  select req.requirement_id, req.grp, req.importance, req.canonical_text,
         case
           when h.requirement_id is null then 0
           when req.required_ordinal is not null
                and nvl(h.held_ordinal, 0) < req.required_ordinal then 0
           else 1
         end as credit
  from req
  left join held h on h.requirement_id = req.requirement_id
),
-- soft credit: an unmet item may still be semantically covered (Approach B fallback)
soft as (
  select e.requirement_id, e.grp, e.importance, e.canonical_text,
         greatest(e.credit,
           nvl((select max(1 - vector_distance(req.embedding, h.embedding, cosine))
                from held h
                where 1 - vector_distance(req.embedding, h.embedding, cosine) >= 0.75), 0)
         ) as credit
  from exact e
  join req on req.requirement_id = e.requirement_id
),
grouped as (
  select grp, max(importance) as importance, max(credit) as credit
  from soft group by grp
)
select sum(importance * credit) / sum(importance) as coverage
from grouped;
```

The gap list is the same query stopping earlier — `soft` rows where `credit = 0`, which
yields literal rows naming what is missing. That crispness is the entire reason for
Approach A: "you are missing R and Python" is a database result, not a heuristic.

Soft matches earn partial credit equal to their cosine, never a full 1. A near-synonym is
evidence, not proof.

---

## 7. Profile → path data flow

```
user free text
  → llm_parse            → current_title, years_experience, education_level, held items
  → hybrid canonicalize  → profile_requirements (requirement_id or null)
  → VECTOR_EMBEDDING     → user_profiles.embedding
  → vector search        → matched_occupation_id (A), match_similarity
  → coverage/gap on B    → unmet(user, B)              [§6.4]
  → recursive CTE        → candidate A→B paths by total_cost
  → bridge_value rescore → final ranking               [§7.2]
  → tool_log + saved_plans.route
```

Note the ordering: the gap against B is computed **before** paths are ranked, because
`bridge_value` needs `unmet(user, B)` as an input. A path cannot be scored until the system
knows what the target still demands.

### 7.1 A-inference

```sql
select o.occupation_id, o.soc_code, o.title,
       1 - vector_distance(o.embedding, :profile_vec, cosine) as similarity
from occupations o
order by vector_distance(o.embedding, :profile_vec, cosine)
fetch approx first 5 rows only;
```

Top 5 are retrieved but only the best becomes A, per the chosen design. The rest are logged
to `tool_log` so a bad match is diagnosable, and they are what a future "did you mean"
affordance would use.

`education_level` maps to `inferred_job_zone` on this fixed table, mirroring O\*NET's own
job-zone definitions:

| `education_level` | `inferred_job_zone` |
|---|---|
| none / some high school | 1 |
| high school diploma or GED | 2 |
| some college, certificate, or associate's | 3 |
| bachelor's | 4 |
| graduate or professional degree | 5 |

The clamp is explicit rather than implied: if the top-ranked occupation's `job_zone` is
below `inferred_job_zone`, walk down the top-5 candidates and take the first whose
`job_zone >= inferred_job_zone`. If none qualifies, accept the top match anyway but record
`match_similarity` alongside a `zone_clamp_failed` note in `tool_log` — the user's stated
education outranking every semantic match is a signal worth seeing, not one worth hiding.

### 7.2 `bridge_value` rescoring

Graph cost measures whether a transition is plausible *in general*. It says nothing about
whether it is plausible **for this user**, or whether it makes progress toward B. Each
intermediary M on a candidate path is scored on two independent criteria:

```
unmet(user, B) = gap list from §6.4 against the target

reachability(M) = coverage(user, M)                              -- can I get M now?
transfer(M, B)  = |req(M) ∩ unmet(user, B)| / |unmet(user, B)|   -- does M teach what B needs?

bridge_value(M) = reachability(M) × transfer(M, B)
```

The product, not the sum, is deliberate. A job I could get tomorrow that teaches nothing B
requires is a dead end; a job that teaches everything B requires but that I cannot get is
not a step at all. Either factor near zero should kill the candidate, and only multiplication
has that property.

Path-level score:

```
path_bridge(path) = mean over intermediaries M of bridge_value(M)
final_score(path) = w1 * (1 / (1 + total_cost)) + w2 * path_bridge
```

Starting weights `w1 = 0.4`, `w2 = 0.6` — deliberately inverted from graph-cost dominance,
because requirement evidence is concrete where edge weight is a similarity heuristic. Both
tune against the demo personas.

`transfer` is computed with a join against the same `occupation_requirements` rows used for
coverage, so no new retrieval path is needed:

```sql
select count(distinct orq.requirement_id) / :unmet_count as transfer
from occupation_requirements orq
where orq.occupation_id = :candidate_id
  and orq.requirement_id in (select column_value from table(:unmet_ids));
```

This is the step that makes two users targeting the same B receive different routes, and it
is the difference between a graph query and a career navigator.

### 7.3 Persistence

`saved_plans.route` holds the ordered occupation list as JSON. `retrieval_evidence` records
each surfaced intermediary with `source_type = 'route_node'` and its score, with
`used_in_plan` set when the user confirms — which is what makes retrieval quality
measurable later.

---

## 8. Error handling

| Failure | Handling |
|---|---|
| Occupation has null `job_zone` | Excluded from derived edges; counted and reported by ingest |
| Posting matches no occupation above 0.60 | Discarded, counted; never force-assigned |
| Posting scores 0.60–0.75 | Contributes to embeddings only; no requirements attributed |
| Extracted string matches no vocabulary entry above 0.80 | New `requirement_items` row inserted; flagged for synonym review |
| Profile item unmappable to vocabulary | Stored with null `requirement_id`, excluded from coverage math, never silently dropped |
| Target B has zero requirements on record | `unmet` is empty, `transfer` undefined — fall back to graph cost alone (`w2 = 0`) and say so |
| `unmet(user, B)` is empty because user already qualifies | Report "you already meet the stated requirements" rather than fabricating a path |
| LLM extraction returns malformed output | Posting skipped, staged payload retained, counted; extraction is re-runnable |
| `embedding_source` exceeds model token limit | Truncated at composition time, importance-ordered so the tail is least valuable |
| No A→B path within 4 hops | Relax to `related`, then report nearest reachable with explanation |
| Profile embeds to low similarity everywhere (best < 0.40) | Surface as "could not place your background" rather than guessing A |
| `CREATE PROPERTY GRAPH` unsupported | Already handled: logged to NOTES.md, CTE path is primary anyway |
| Edge derivation re-run | Idempotent — delete `source like 'derived_%'`, re-insert |

---

## 9. Testing

Unit tests, no database:

- Path-cost arithmetic and the both-sided cycle guard, including the 12-vs-123 substring case.
- `embedding_source` composition: ordering by importance, truncation boundary, absent postings.
- Job-zone edge rules as a pure function: each of the four zone relations yields the right edge type or none.
- Coverage math: `alternative_group` collapse (a Data Science major scores 1.0, not 0.33, against "DS, CS, or related"), ordinal insufficiency ("2 years" fails "3+ years" despite an exact item match), importance weighting, unmapped-only profiles.
- `bridge_value` products: either factor at zero yields zero; both at 1.0 yields 1.0.
- Requirement-type routing: `education_level` compares ordinally, `education_field` compares by embedding, `tool` compares exactly.

Integration tests requiring the vocabulary:

- The worked example from §6.4 end to end — required `{bachelor, DS|CS, R, Python}` against a profile of `{bachelor, Data Science, research experience}` must yield coverage reflecting met education and a gap list of exactly `{R, Python}`, with "research experience" recorded as surplus and not penalized.
- Canonicalization of `R` versus `R programming` versus `R (programming language)` collapsing to one vocabulary entry via the hybrid index.
- A posting binding at 0.68 contributing to `embedding_source` while adding zero rows to `occupation_requirements`.

Integration tests against a seeded 23ai instance:

- A hand-built 6-occupation fixture graph with known shortest path; assert the CTE returns it, in order, with the expected cost.
- A deliberately disconnected pair; assert graceful degradation, not an exception.
- Zone-jump rejection: assert a zone 2 → zone 5 pair produces no direct edge and that traversal finds the multi-hop route instead.

The fixture graph is the important one. It is small enough to reason about by hand, which
makes it the only test that can prove the traversal semantics are actually right.

---

## 10. Open questions

1. Demo personas are pending. Every threshold (edge 0.65/0.75, posting bind 0.60/0.75,
   canonicalization 0.80, soft match 0.75) and both scoring weights (0.4/0.6) are starting
   points to be tuned once personas exist.
2. Whether `max_hops` should be 3 rather than 4 depends on observed graph diameter after
   edges are built.
3. 23ai provisioning is unresolved and blocks everything in sections 3 through 7.
4. `tool_log.call_type` has no value for requirement gap analysis. Its check constraint needs
   `requirement_gap_analysis` added, or the operation must be logged under an existing value.
5. Company board tokens for Greenhouse and Ashby are an unspecified curated list. Requirement
   quality depends directly on which employers are sampled — a list skewed to startups will
   overstate tool requirements and understate credential requirements.
6. `requirement_items` grows monotonically with no dedupe pass specified beyond the 0.80
   canonicalization floor. A synonym review step will eventually be needed.
