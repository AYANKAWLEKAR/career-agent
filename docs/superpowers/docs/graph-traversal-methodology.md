# Occupation Graph Methodology

How the career-navigator occupation graph routes a career upward: what the edges
are, how the career-ladder edges are derived from O\*NET skill data, and why the
graph refuses shortcuts.

Numbers are measured from the live QHZ6SNWUPW0Y1ASH instance as of 2026-08-26.
Published artifact version: https://claude.ai/code/artifact/06f8f4ed-c0c8-4f95-880c-35d0935d042f
Sources: [path-surfacing design spec](../specs/2026-08-17-path-surfacing-design.md), NOTES.md.

| | |
|---|---|
| Traversable occupations | 115 |
| `career_ladder` edges | 541 |
| `skill_overlap` edges | 748 |
| `related` edges | 1,275 |
| Graph density | 15.3% |

## 1. The graph: nodes, edges, and one hard invariant

The graph covers three verticals of the O\*NET 30.3 taxonomy — tech (`15-1xxx`,
`15-2xxx`), finance (`13-2xxx`, `11-3031`), and research (`19-xxxx`) — 124
occupations, of which 115 are traversable (the nine "All Other" catch-all codes
carry no job zone and are excluded). Nodes live in `occupations`; edges in
`occupation_edges`; both are wrapped in a native SQL property graph,
`OCCUPATION_GRAPH`, on Oracle AI Database 26ai.

The one rule everything else depends on: **only one-hop edges are ever stored.**
A multi-hop career path is never precomputed — it is assembled at query time by
traversal. That keeps every path explainable edge by edge, and it means tuning an
edge rule re-shapes every path in the system consistently.

| Edge type | Direction | Provenance | Count | Role in traversal |
|---|---|---|---:|---|
| `career_ladder` | Upward, one zone | Derived from skill cosine | 541 | The upward move — §3 |
| `skill_overlap` | Lateral, same zone | Derived from skill cosine | 748 | Sideways pivot; stricter bar |
| `related` | Undirected | O\*NET published pairs | 1,275 | Excluded from routing by default; used for explanation ("these roles are adjacent") and as a fallback when no directed path exists |

## 2. The signal under every edge: centered skill cosine

Each occupation carries an importance-rated vector over 157 skill elements
spanning five O\*NET domains: skills, abilities, knowledge, work activities, and
work styles. Every ordered pair of occupations gets an exact cosine similarity
over those vectors — at this scale (~13,000 gated pairs) no approximate prefilter
is needed.

Two corrections to the naive formula were forced by measurement, not preference:

### Mean-centering, or the graph collapses

Raw importance cosine over this data ranges **0.912–0.997** — every occupation
rates Active Listening, Reading Comprehension, and Critical Thinking highly, so
raw cosine says everything resembles everything. At the original thresholds, 85%
of all candidate pairs became edges, and multi-hop traversal was meaningless
because nearly every destination was one hop away. Subtracting each element's
cross-occupation mean restores discrimination (range **−0.714 to 0.934**): what
remains is how an occupation *differs* from the field, which is the signal a
career move actually turns on.

### Shared-subspace norms, or new occupations vanish

O\*NET has not yet published skill ratings for its eight newest in-scope
occupations — Data Scientists and Financial & Investment Analysts among them —
which carry only 21 work-style ratings against a typical 157. Dividing a
21-element dot product by full-length norms understates their similarity by
roughly `sqrt(21/157)`, scoring a measurement gap as dissimilarity. Norms are
therefore computed over the elements a pair shares. For the 107 fully rated
occupations the shared subspace *is* the full vector, so the two formulations are
numerically identical — the change is inert for 107 occupations and corrective
for 8.

## 3. How a career-ladder edge is formed

Direction does not come from similarity — similarity is symmetric. Direction
comes from the **O\*NET job zone**, a 1–5 scale of required preparation
(education, experience, training). An edge A → B is emitted as `career_ladder`
only when B sits *exactly one zone above* A and the skill evidence clears the
threshold.

Current zone distribution and upward edges:

```
Zone 3 (18 occs)  ──220 edges──▶  Zone 4 (60 occs)  ──321 edges──▶  Zone 5 (37 occs)
Web Developers,                   Software Developers,              Statisticians,
Tax Preparers,                    Accountants,                      Economists,
Chemical Technicians…             Data Scientists…                  Investment Fund Managers…
```

The four gating outcomes:

| Job zone relation | Verdict | Rule |
|---|---|---|
| `B.zone = A.zone + 1` | **Emit `career_ladder`** | centered cosine ≥ 0.30 — a plausible single step up |
| `B.zone = A.zone` | **Emit `skill_overlap`** | cosine ≥ 0.45 — a lateral move must justify itself with stronger skill transfer |
| `B.zone > A.zone + 1` | **Reject** | A jump from zone 3 to 5 is a career plus a degree, not a step |
| `B.zone < A.zone` | **Reject** | The graph never routes a career downward |

**Why the shortcut rejection matters.** Without it, the highest-similarity pair
almost always wins directly and every answer becomes one hop. By refusing zone
jumps, the traversal is *forced* to find the intermediate rung — Web Developers
reach Bioinformatics Scientists only through Data Scientists, and that
intermediate step is the actionable advice.

### Edge weight, and the wage correction

The base weight is the centered skill cosine. Job zone measures required
preparation, not pay, and the two diverge — so a wage penalty scales the weight
down when a transition loses money:

```
wage_ratio = median_wage(B) / median_wage(A)      -- OEWS national

penalty = 1.0                     when ratio >= 1.0
        = 0.5 + 0.5 * ratio       when ratio <  1.0

weight  = centered_cosine * penalty               -- a 20% pay cut -> x0.9
```

This is a **penalty, not a filter** — a pay cut into a growth field can still be
the right move, so a strongly skill-transferable cut can outrank a weakly
transferable raise. Where no median exists for either endpoint, penalty is 1.0:
missing wage data must not masquerade as a pay cut. (OEWS wages are not yet
loaded, so every edge currently carries penalty 1.0 — the machinery is in place
and activates on load, with no re-derivation needed.)

### Thresholds are measured, not guessed

The pair 0.30 / 0.45 comes from a sweep (`scripts/tune_thresholds.py`) that
scores every candidate against four properties of the resulting graph. The
binding constraint is isolation: at lateral 0.50 some occupations lose every
outgoing edge, and an isolated occupation can never begin a path.

| Property | At 0.30 / 0.45 | Why it matters |
|---|---:|---|
| Isolated occupations | 0% | Hard constraint — isolation appears at lateral 0.50 and caps it at 0.45 |
| Density (edges ÷ candidate pairs) | 15.3% | 84% of pairs rejected, so an edge means something |
| Median out-degree | 11 | A usable shortlist of next steps, not a firehose |
| Pairs reachable in ≤ 3 hops | 52.3% | Against a ~69% ceiling imposed by never routing downward |
| Connected pairs already adjacent | 18.8% | Four in five connections genuinely need multiple hops |

## 4. Walking the graph at query time

Traversal cost is `1 − weight`, so the cheapest path is the strongest chain of
skill transfer. The primary mechanism is Oracle's SQL property-graph syntax;
bounded variable-length matches keep paths to a realistic number of moves:

```sql
select * from graph_table (occupation_graph
  match (a is occupations) -[e is occupation_edges]->{1,3} (b is occupations)
  where a.soc_code = '15-1254.00'          -- Web Developers
    and e.edge_type in ('career_ladder', 'skill_overlap')
  columns (a.title as from_title, b.title as to_title, b.job_zone as zone));
```

Because zone jumps were rejected at build time, no query-time guard against
unrealistic leaps is needed — the edge set itself enforces one-rung-at-a-time.
A verified example: `Web Developers (z3) → Data Scientists (z4) → Bioinformatics
Scientists (z5)`. `related` edges are excluded from routing and reserved for
explanation and fallback. A recursive-CTE formulation exists as the degradation
path for instances without `GRAPH_TABLE`.

## 5. Where a person enters the graph

Traversal needs a starting node. The user describes their history in natural
language — effectively a resume — and each experience is embedded separately with
the in-database ONNX model (all-MiniLM-L12-v2, 384 dims, inference inside
Oracle). Every occupation is scored against *all* of those vectors and the
strongest signal wins; experience vectors decay 10% per resume position so the
current role outranks an old internship. A zone clamp then enforces stated
education: if the top semantic match sits below the zone the education implies,
the ranker walks down the top five to the first that qualifies — and if none
does, it accepts the top match and logs `zone_clamp_failed`, because a user whose
stated education outranks every semantic match is a signal worth surfacing, not
hiding.

Division of labour: **embeddings decide where you start; the graph decides where
you can go.** Edge derivation reads rated skill data directly and never depends
on embeddings — a retrieval bug cannot masquerade as a graph bug, or vice versa.

## 6. Known limits, stated plainly

**Zone 5 is terminal.** Never routing downward means the 37 zone-5 occupations
can only reach each other — they are destinations, not starting points with
range. This caps three-hop reachability at ~69% by construction.

**Eight occupations ride on temperament, not skills.** The work-styles-only
occupations (Data Scientists among them) form edges from disposition ratings
alone. It reads well where disposition and domain coincide — Financial &
Investment Analysts → Investment Fund Managers — and looser where they don't.
When O\*NET publishes their skill ratings, a re-run picks them up with no code
change.

**Seniority is invisible.** O\*NET SOC codes do not encode within-role
progression — junior-to-senior inside one occupation is not expressible in this
graph, only moves between occupations.
