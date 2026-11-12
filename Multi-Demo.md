# Career Path Navigator — Multi-Persona Demo Record

**Date:** 2026-11-12 (supersedes the 2026-08-28 record) · **Instance:** Oracle AI Database 26ai 23.26.4.1.0 (`QHZ6SNWUPW0Y1ASH`, Autonomous)
**Companion to:** [DEMO.md](DEMO.md) (single real-resume run) — same pipeline, same format.

Three synthetic UC Berkeley students, each with modest internship experience and a
stated career interest, pushed through the full pipeline: schema → in-database ONNX
embedding → A-inference with the student start-zone band → `GRAPH_TABLE` path surfacing
and aspiration ladders → wage context on every hop → College Scorecard degree outcomes
(major resolved in-database, graduate programs near Berkeley) → an LLM-narrated story
grounded in the decision record. Each persona stresses a different part of the system,
and every number below is from the live run (`scripts/multi_demo.py`).

---

## 1. The three personas (slide: "Inputs")

| | Major | Evidence | Stated interest | Designed to test |
|---|---|---|---|---|
| P1 | Statistics | 2 accounting internships, R/Python coursework | Finance | The aspiration bridge: evidence in one field, interest in an adjacent one |
| P2 | Molecular & Cell Biology | 2 yrs wet-lab research + a finance-club biotech pitch | Healthcare investment banking | Evidence vs. aspiration pointing at *different verticals* |
| P3 | Communications | 2 marketing internships, case-competition lead | Management consulting | The edge of the universe: marketing/consulting SOC codes are outside the ingested scope |

All three: `education_level = "bachelor's (in progress)"` → start band **[3..3]**
(a degree in progress supports a zone-3 start), 1–2 years of experience, three
experiences each with accomplishment-level text (that text is what gets embedded — one
vector per experience, recency-weighted).

## 2. Headline numbers (slide: "What happened")

| | Matched start | Similarity | Band outcome | Aspiration (similarity) | Top surfaced move |
|---|---|---|---|---|---|
| P1 | Accountants and Auditors [z4] | **0.5231** | `ceiling_unmet` | Statisticians [z5] (0.347) | Treasurers and Controllers (w 0.743, +$106k) |
| P2 | Bioinformatics Technicians [z4] | **0.4398** | `ceiling_unmet` | Molecular and Cellular Biologists [z5] (0.639) | Blockchain Engineers (w 0.667, +$41k) |
| P3 | Telecommunications Engineering Specialists [z3] | **0.3503** | experience gate relaxed | Survey Researchers [z5] (0.336) | Computer Network Architects (w 0.623, ±$0k) |

| | Major resolved in-database | Bachelor's earnings (1yr / 4yr / 7yr) | Programs near Berkeley with published outcomes |
|---|---|---|---|
| P1 | Statistics (CIP 2705, sim 1.000) | $67.6k / $87.3k / $113.2k | 5 (UC Berkeley, Golden Gate, USF, Cal State East Bay) |
| P2 | Cell/Cellular Biology (CIP 2604, sim 0.685) | $44.3k / $68.3k / $84.9k | 5 (UC Berkeley, UCSF, SF State, UC Davis) |
| P3 | Business/Corporate Communications (CIP 5205, sim 0.724) | $39.6k / $55.8k / $70.4k | 5 (UC Berkeley, Golden Gate, USF, Cal State East Bay) |

The similarity spread is itself a finding: **0.52 → 0.44 → 0.35** tracks how well each
student's evidence is covered by the ingested universe. P3's low score is the system
honestly reporting weak evidence, not a bug — and its resolved major is the loosest of
the three (0.724), which is the same signal from the other direction.

## 3. Triage log (slide: "What we hit and what held")

Issues encountered while preparing this run, in the order they appeared:

1. **The occupations HNSW graph was empty — found before the demo, not during it.**
   The new `scripts/health_check.py` reported the occupations vector index `VALID`
   but its in-memory graph held 0 vectors (observed after an instance restart), so
   candidate search had silently planned a full scan. Results were still correct;
   the index just was not helping. Rebuilt (4 s), health check 18/18, and the first
   demo pass was discarded and re-run on the healthy index.
2. **P3 is out-of-scope by construction — the system degraded gracefully.**
   Marketing occupations and Management Analysts are outside the ingested SOC
   families (tech 15-x, finance 13-2/11-3031, research 19-x). The match lands on a
   zone-3 telecom role at a visibly low 0.350, and the aspiration is Survey
   Researchers at 0.336. The degree-outcomes section still gives the student real
   numbers for their actual major. The long-term fix is widening scope, which the
   design anticipates.
3. **The student ceiling worked as designed for all three.** Every strong match for
   P1 and P2 sat in zone 4–5, above the [3..3] band; the system flagged
   `ceiling_unmet`, started at the lowest zone available, and kept the strongest
   zone-5 match as the **aspiration** with an explicit ladder.
4. **Degree outcomes are honest about coverage.** Every persona's list says "5
   programs with published outcomes" (the Scorecard suppresses small cohorts) and
   shows only the horizons that exist.
5. **No pipeline defects in the run itself.** All three personas produced correct
   matches, paths, outcomes, and grounded narrations on the first full pass on the
   healthy index.

## 4. Verbatim terminal transcript

Everything below is the unedited output of `uv run python scripts/multi_demo.py`.

```text
==========================================================================
CAREER PATH NAVIGATOR — MULTI-PERSONA DEMO (Berkeley students)
==========================================================================
Database : Oracle AI Database 26ai Enterprise Edition Release 23.26.4.1.0 - Production
Graph    : OCCUPATION_GRAPH (SQL property graph)
ONNX     : ALL_MINILM_L12_V2 (in-database)
Corpus   : 124 occupations, 2564 edges
Wages    : scope=national (H-1B LCA FY2026; display only — penalty is national)
Traversal: engine=graph_table (ISO SQL/PGQ, ONE ROW PER STEP {1,3})

==========================================================================
PERSONA: Statistics major, accounting experience, aiming at finance
==========================================================================
self-description : UC Berkeley statistics major, junior year. Two accounting internships. Interested in moving into finance after graduation — ideally analysis or investment work.
schema           : Accounting Intern | bachelor's (in progress) in Statistics | 1.0 yrs | 3 experiences
  [0] Accounting Intern @ Regional CPA firm (3 mo): Accounting internship at a mid-size firm: prepared journal entries and m
  [1] Audit Intern @ Regional CPA firm (3 mo): Second accounting internship: supported audit fieldwork, sampled transac
  [2] Student @ UC Berkeley (24 mo): Course projects in R and Python: regression analysis, time-series foreca
[latency] A-inference round trip: 243 ms (per-vector APPROX top-k)

start band: zones [3..3] (completed education + experience)
START: Accountants and Auditors [zone 4] similarity 0.5231  (ceiling_unmet: every match sits above the band; starting at the lowest zone available)
candidates considered:
  0.5231  [z4] Accountants and Auditors
  0.4754  [z4] Financial Examiners
  0.3825  [z4] Financial Managers
  0.3798  [z4] Treasurers and Controllers
  0.3769  [z4] Actuaries
  0.3607  [z4] Budget Analysts
  0.3545  [z4] Fraud Examiners, Investigators and Analysts
  0.3474  [z5] Statisticians
  0.3277  [z4] Financial Risk Specialists
  0.3233  [z4] Financial and Investment Analysts
[latency] path enumeration (graph_table): 863 ms, 5 ranked paths from Accountants and Auditors

ranked paths (cost = sum of 1 - skill-transfer weight):
  cost 0.257: Accountants and Auditors -> Treasurers and Controllers [LAT w=0.743 +$106k]
  cost 0.266: Accountants and Auditors -> Financial Examiners [LAT w=0.734 -$6k]
  cost 0.322: Accountants and Auditors -> Credit Analysts [LAT w=0.678 +$21k]
  cost 0.337: Accountants and Auditors -> Financial Managers [LAT w=0.663 +$106k]
  cost 0.372: Accountants and Auditors -> Budget Analysts [LAT w=0.628 -$12k]

ASPIRATION: Statisticians [z5] similarity 0.3474 — resembles it, cannot start there yet
[latency] path enumeration (graph_table): 587 ms, 3 ranked paths to Statisticians
  ladder cost 0.686: Accountants and Auditors -> Actuaries [LAT w=0.604 +$34k] -> Statisticians [UP w=0.710 -$2k]
  ladder cost 0.899: Accountants and Auditors -> Financial Examiners [LAT w=0.734 -$6k] -> Actuaries [LAT w=0.657 +$40k] -> Statisticians [UP w=0.710 -$2k]
  ladder cost 0.924: Accountants and Auditors -> Credit Analysts [LAT w=0.678 +$21k] -> Actuaries [LAT w=0.688 +$13k] -> Statisticians [UP w=0.710 -$2k]

DEGREE OUTCOMES (College Scorecard; major matched in-database by vector similarity)
  "Statistics" -> Statistics (CIP 2705, sim 1.000) · Bachelor's Degree
      $67.6k @1yr · $87.3k @4yr · $113.2k @7yr · 4yr middle half $76.6k-$103.4k · typical debt $17.5k
      (4yr earnings published for 50 of 72 programs)

TO REACH Statisticians: graduate fields that feed it: Management Sciences and Quantitative Methods, Statistics, Data Analytics, Applied Mathematics
  within 100 mi of Berkeley, CA (5 programs with published outcomes):
      0.1 mi  University of California-Berkeley — Statistics (Master's Degree)  $133.4k @1yr
      0.1 mi  University of California-Berkeley — Management Sciences and Quantitative Methods (Master's Degree)  $191.1k @1yr
      9.4 mi  Golden Gate University — Management Sciences and Quantitative Methods (Master's Degree)  $111.3k @1yr
     12.2 mi  University of San Francisco — Management Sciences and Quantitative Methods (Master's Degree)  $75.2k @1yr · $186.7k @4yr
     18.6 mi  California State University-East Bay — Statistics (Master's Degree)  $90.8k @1yr · $142.6k @4yr · $124.6k @7yr
  best-paid nationally:
    Vanderbilt University — Applied Mathematics (Master's Degree)  $150.3k @1yr · $216.4k @4yr
    New York University — Management Sciences and Quantitative Methods (Master's Degree)  $187.4k @1yr · $188.2k @4yr
    University of San Francisco — Management Sciences and Quantitative Methods (Master's Degree)  $75.2k @1yr · $186.7k @4yr
[latency] degree outcomes (vector major match + spatial): 226 ms

LLM NARRATION (grounded in the decision record above):

You're currently in your junior year as a statistics major at UC Berkeley, with two accounting internships under your belt and a clear interest in moving into finance—specifically, analysis or investment work. Based on your experience and education, the system has identified that your most direct starting point is the occupation of "Accountants and Auditors," which aligns with your resume and experience at a similarity score of 0.5231. This is a solid match, but it's not the only one. Your education and experience suggest that you're best positioned to start in job zone 3, which means you're not quite ready to jump into higher-preparation roles just yet.

That said, your long-term goal of becoming a Statistician is something you already resemble, even if you can't start there yet. The system found a few realistic paths that could help you get there. One of the most direct routes involves moving laterally into Actuaries, a role that shares enough skill overlap with your current experience (a similarity of 0.604) to make the transition plausible. From there, you could move up one zone into Statisticians, a step that requires a bit more preparation but is supported by a strong skill transfer (0.71). This path is a bit of a climb, but it's doable and could lead you to a role that aligns with your interest in data-driven analysis.

Another option is to first move into Financial Examiners, a lateral step with a similarity of 0.734. While this role pays slightly less than your current position, it could serve as a stepping stone toward Actuaries and, eventually, Statisticians. Alternatively, you might consider moving into Credit Analysts, a role that offers a higher wage and shares a strong skill overlap with your current experience (0.678). From there, you could again move into Actuaries and then up to Statisticians.

If you're interested in accelerating your path, you might also consider graduate programs in fields like Statistics or Management Sciences and Quantitative Methods. Programs at UC Berkeley and nearby institutions offer strong opportunities, and some of the best-paid graduate programs nationally are in Applied Mathematics and Management Sciences and Quantitative Methods. These programs could help bridge the gap between your current experience and the skills needed for roles like Statisticians or Financial Analysts in investment.

You're already on a strong path, and with the right steps, you can move closer to the kind of work you're interested in.

[517 output tokens, 39.5s]

==========================================================================
PERSONA: Biology major, lab experience, aiming at healthcare investment banking
==========================================================================
self-description : UC Berkeley molecular biology major with two years of wet-lab research experience. Interested in healthcare investment banking — using science background to evaluate biotech companies.
schema           : Undergraduate Research Assistant | bachelor's (in progress) in Molecular and Cell Biology | 2.0 yrs | 3 experiences
  [0] Research Assistant @ UC Berkeley MCB (20 mo): Undergraduate research assistant in a genetics lab: ran PCR and gel elec
  [1] Lab Intern @ Biotech startup (3 mo): Summer lab internship at a biotech startup: assisted with assay developm
  [2] Club Member @ Berkeley finance club (8 mo): Finance club member: built a mock pitch valuing a public biotech company
[latency] A-inference round trip: 401 ms (per-vector APPROX top-k)

start band: zones [3..3] (completed education + experience)
START: Bioinformatics Technicians [zone 4] similarity 0.4398  (ceiling_unmet: every match sits above the band; starting at the lowest zone available)
candidates considered:
  0.6385  [z5] Molecular and Cellular Biologists
  0.6217  [z5] Geneticists
  0.4495  [z5] Biochemists and Biophysicists
  0.4488  [z5] Bioinformatics Scientists
  0.4398  [z4] Bioinformatics Technicians
  0.4309  [z4] Biological Technicians
  0.4048  [z4] Microbiologists
  0.4018  [z4] Social Science Research Assistants
  0.3956  [z5] Medical Scientists, Except Epidemiologists
  0.3558  [z5] Biologists
[latency] path enumeration (graph_table): 435 ms, 5 ranked paths from Bioinformatics Technicians

ranked paths (cost = sum of 1 - skill-transfer weight):
  cost 0.333: Bioinformatics Technicians -> Blockchain Engineers [LAT w=0.667 +$41k]
  cost 0.357: Bioinformatics Technicians -> Data Scientists [LAT w=0.643 +$48k]
  cost 0.442: Bioinformatics Technicians -> Bioinformatics Scientists [UP w=0.558 +$7k]
  cost 0.494: Bioinformatics Technicians -> Data Scientists [LAT w=0.643 +$48k] -> Mathematicians [UP w=0.863 -$1k]
  cost 0.498: Bioinformatics Technicians -> Statisticians [UP w=0.502 +$51k]

ASPIRATION: Molecular and Cellular Biologists [z5] similarity 0.6385 — resembles it, cannot start there yet
[latency] path enumeration (graph_table): 439 ms, 3 ranked paths to Molecular and Cellular Biologists
  ladder cost 0.842: Bioinformatics Technicians -> Bioinformatics Scientists [UP w=0.558 +$7k] -> Molecular and Cellular Biologists [LAT w=0.600 +$0k]
  ladder cost 0.958: Bioinformatics Technicians -> Data Scientists [LAT w=0.643 +$48k] -> Molecular and Cellular Biologists [UP w=0.399 -$41k]
  ladder cost 0.973: Bioinformatics Technicians -> Bioinformatics Scientists [UP w=0.558 +$7k] -> Geneticists [LAT w=0.661 +$0k] -> Molecular and Cellular Biologists [LAT w=0.807 +$0k]

DEGREE OUTCOMES (College Scorecard; major matched in-database by vector similarity)
  "Molecular and Cell Biology" -> Cell/Cellular Biology and Anatomical Sciences (CIP 2604, sim 0.685) · Bachelor's Degree
      $44.3k @1yr · $68.3k @4yr · $84.9k @7yr · 4yr middle half $65.3k-$73.1k · typical debt $17.7k
      (4yr earnings published for 34 of 45 programs)

TO REACH Molecular and Cellular Biologists: graduate fields that feed it: Biology, General, Biological and Biomedical Sciences, Other, Cell/Cellular Biology and Anatomical Sciences, Biochemistry, Biophysics and Molecular Biology
  within 100 mi of Berkeley, CA (5 programs with published outcomes):
      0.1 mi  University of California-Berkeley — Cell/Cellular Biology and Anatomical Sciences (Doctoral Degree)  $65.5k @1yr · $123.2k @4yr · $156.8k @7yr
     13.0 mi  University of California-San Francisco — Biology, General (Doctoral Degree)  $98.9k @1yr · $97.0k @4yr
     13.0 mi  University of California-San Francisco — Biochemistry, Biophysics and Molecular Biology (Doctoral Degree)  $124.1k @1yr
     15.7 mi  San Francisco State University — Biology, General (Master's Degree)  $68.0k @1yr · $128.1k @4yr
     53.9 mi  University of California-Davis — Biochemistry, Biophysics and Molecular Biology (Doctoral Degree)  $113.1k @1yr · $108.5k @4yr
  best-paid nationally:
    Duke University — Cell/Cellular Biology and Anatomical Sciences (Master's Degree)  $299.9k @4yr
    Johns Hopkins University — Biology, General (Master's Degree)  $166.8k @1yr · $243.9k @4yr
    Massachusetts Institute of Technology — Biology, General (Doctoral Degree)  $102.6k @1yr · $189.1k @4yr · $159.7k @7yr
[latency] degree outcomes (vector major match + spatial): 210 ms

LLM NARRATION (grounded in the decision record above):

You're currently in a strong position as a UC Berkeley molecular biology major with two years of hands-on wet-lab research experience. Your background in genetics, assay development, and even a basic foray into financial modeling shows a unique blend of scientific and analytical skills. But right now, your experience and education align most closely with the role of a **Bioinformatics Technician**, which is in **Job Zone 4**—a category that typically requires a bachelor’s degree and some experience. The system found that while you have the potential to match with higher-level roles like **Molecular and Cellular Biologists** (Job Zone 5), your current experience and education suggest that starting at Zone 4 is the most realistic and supported path.

That said, the system identified a few clear and realistic paths forward. One of the strongest options is to move **up one zone** from **Bioinformatics Technician** to **Bioinformatics Scientist**. This step is supported by a **skill-transfer weight of 0.558**, which means there’s a solid overlap in the skills required for both roles. As a Bioinformatics Scientist, you’d likely earn a median wage of **$85,322**, compared to **$78,000** as a Technician—a **$7,322** increase. From there, you could make a **lateral move** to **Molecular and Cellular Biologists**, where your skills in genetics and data analysis would align well, with a **skill-transfer weight of 0.6**. This path would take you from your current starting point to the role you most resemble, with a total “cost” of **0.842**, meaning it’s a relatively smooth transition.

Another option is to move **laterally** to **Data Scientists**, a role that pays significantly more—**$126,000** on average. This step has a **skill-transfer weight of 0.643**, which is strong enough to justify the move. However, from there, moving up to **Molecular and Cellular Biologists** would be a bit trickier, as the skill overlap drops to **0.399**, and the wage would actually decrease slightly. Still, it’s a viable path if you’re interested in data science and want to pivot from there.

If you’re drawn to the idea of **Molecular and Cellular Biologists**, you might also consider a **PhD** in a related field, like **Cell/Cellular Biology and Anatomical Sciences**, which is offered at UC Berkeley and other nearby institutions. Graduates of such programs often see median earnings of **$123,231** four years after graduation, though this path would require a significant time and financial investment.

Ultimately, you’re standing on a strong foundation, and the system has mapped out several realistic and achievable paths forward—whether you want to climb the ladder in science, pivot into data, or explore the financial world of healthcare investment. The key is to take one step at a time, building on the skills you already have.

[634 output tokens, 72.9s]

==========================================================================
PERSONA: Communications major, marketing experience, aiming at consulting
==========================================================================
self-description : UC Berkeley communications major. Marketing internships at two startups. Interested in management consulting after graduation.
schema           : Marketing Intern | bachelor's (in progress) in Communications | 1.0 yrs | 3 experiences
  [0] Marketing Intern @ Consumer startup (4 mo): Marketing internship at a consumer app startup: ran A/B tests on campaig
  [1] Marketing Intern @ E-commerce startup (3 mo): Social media marketing internship: planned content calendars, tracked au
  [2] Team Lead @ Berkeley consulting club (6 mo): Case competition team: analyzed a retailer's declining sales, surveyed s
[latency] A-inference round trip: 232 ms (per-vector APPROX top-k)

start band: zones [3..3] (completed education + experience)
START: Telecommunications Engineering Specialists [zone 3] similarity 0.3503  (experience gate relaxed)
candidates considered:
  0.3503  [z3] Telecommunications Engineering Specialists
  0.3364  [z5] Survey Researchers
  0.3336  [z4] Financial and Investment Analysts
  0.2860  [z5] Molecular and Cellular Biologists
  0.2664  [z5] Industrial-Organizational Psychologists
  0.2604  [z4] Loan Officers
  0.2594  [z4] Blockchain Engineers
  0.2576  [z4] Computer Network Architects
  0.2570  [z5] Operations Research Analysts
  0.2521  [z4] Information Technology Project Managers
[latency] path enumeration (graph_table): 759 ms, 5 ranked paths from Telecommunications Engineering Specialists

ranked paths (cost = sum of 1 - skill-transfer weight):
  cost 0.377: Telecommunications Engineering Specialists -> Computer Network Architects [UP w=0.623 +$0k]
  cost 0.507: Telecommunications Engineering Specialists -> Computer Systems Engineers/Architects [LAT w=0.493 -$28k]
  cost 0.532: Telecommunications Engineering Specialists -> Web and Digital Interface Designers [UP w=0.468 -$22k]
  cost 0.573: Telecommunications Engineering Specialists -> Computer Network Support Specialists [UP w=0.427 -$57k]
  cost 0.604: Telecommunications Engineering Specialists -> Computer User Support Specialists [LAT w=0.396 -$76k]

ASPIRATION: Survey Researchers [z5] similarity 0.3364 — resembles it, cannot start there yet
[latency] path enumeration (graph_table): 516 ms, 3 ranked paths to Survey Researchers
  ladder cost 1.071: Telecommunications Engineering Specialists -> Web and Digital Interface Designers [UP w=0.468 -$22k] -> Survey Researchers [UP w=0.462 -$46k]
  ladder cost 1.385: Telecommunications Engineering Specialists -> Information Technology Project Managers [UP w=0.317 -$28k] -> Survey Researchers [UP w=0.298 -$40k]
  ladder cost 1.462: Telecommunications Engineering Specialists -> Web and Digital Interface Designers [UP w=0.468 -$22k] -> Sociologists [UP w=0.436 -$45k] -> Survey Researchers [LAT w=0.635 -$1k]

DEGREE OUTCOMES (College Scorecard; major matched in-database by vector similarity)
  "Communications" -> Business/Corporate Communications (CIP 5205, sim 0.724) · Bachelor's Degree
      $39.6k @1yr · $55.8k @4yr · $70.4k @7yr · 4yr middle half $55.8k-$69.2k · typical debt $25.0k
      (4yr earnings published for 25 of 29 programs)

TO REACH Survey Researchers: graduate fields that feed it: Management Sciences and Quantitative Methods, Educational Assessment, Evaluation, and Research, Marketing, Statistics
  within 100 mi of Berkeley, CA (5 programs with published outcomes):
      0.1 mi  University of California-Berkeley — Statistics (Master's Degree)  $133.4k @1yr
      0.1 mi  University of California-Berkeley — Management Sciences and Quantitative Methods (Master's Degree)  $191.1k @1yr
      9.4 mi  Golden Gate University — Management Sciences and Quantitative Methods (Master's Degree)  $111.3k @1yr
     12.2 mi  University of San Francisco — Management Sciences and Quantitative Methods (Master's Degree)  $75.2k @1yr · $186.7k @4yr
     18.6 mi  California State University-East Bay — Statistics (Master's Degree)  $90.8k @1yr · $142.6k @4yr · $124.6k @7yr
  best-paid nationally:
    University of Pennsylvania — Marketing (Master's Degree)  $169.0k @1yr · $255.4k @4yr · $274.1k @7yr
    New York University — Management Sciences and Quantitative Methods (Master's Degree)  $187.4k @1yr · $188.2k @4yr
    University of San Francisco — Management Sciences and Quantitative Methods (Master's Degree)  $75.2k @1yr · $186.7k @4yr
[latency] degree outcomes (vector major match + spatial): 482 ms

LLM NARRATION (grounded in the decision record above):

You're currently a UC Berkeley communications major with about a year of marketing internship experience under your belt, and you're interested in management consulting after graduation. Based on your resume and the skills you've developed—like running A/B tests, analyzing engagement metrics, and working on case competitions—the system identified that you most closely resemble someone in the field of **Telecommunications Engineering Specialists**, which is in **Job Zone 3**. This is the starting point for your career journey, based on your completed education and experience so far.

Now, let’s talk about where you might go from here. One path that stands out is moving toward **Survey Researchers**, which is in **Job Zone 5**—a higher level of preparation and typically requiring more advanced skills or education. While you don’t quite match that role yet, the system found a few possible routes to get there. One of the most direct paths involves first moving to **Web and Digital Interface Designers** in **Job Zone 4**, which is a one-zone jump up from your starting point. This move has a **skill-transfer weight of 0.468**, which is a moderate match, and from there, you could move to **Survey Researchers** with a similar weight. This path has a **total cost of 1.071**, which means it’s a reasonable but not guaranteed route, as it depends on how well your skills align with the new roles.

Another option is to go from **Telecommunications Engineering Specialists** to **Information Technology Project Managers** in **Job Zone 4**, which is also a one-zone jump. This move has a **skill-transfer weight of 0.317**, which is a bit weaker, but still plausible. From there, you could move to **Survey Researchers**, though that second step has a lower weight of **0.298**, which means it might be a bit of a stretch.

If you're interested in management consulting, you might also consider roles like **Computer Network Architects** or **Web and Digital Interface Designers**, which are one-zone jumps from your starting point and have higher skill-transfer weights. These could be more immediate and practical steps that align with your current experience and interests.

If you're thinking about graduate school, your communications major has a **7-year median earnings of $70,428**, and there are several graduate programs in nearby areas, like **Statistics** or **Management Sciences and Quantitative Methods**, that could help you build the skills needed for roles like **Survey Researchers** or even management consulting. Some of these programs, like those at **University of San Francisco** or **University of Pennsylvania**, have strong earnings outcomes for graduates.

So, where do you go from here? You're starting in **Job Zone 3**, and you have a few clear paths upward. Whether you take a step toward **Web and Digital Interface Designers**, or aim for **Survey Researchers** through a two-step journey, the key is to build on the skills you already have and consider how graduate education might help you reach your long-term goals.

[628 output tokens, 123.9s]
```

## 5. Persona-by-persona reading (slide-by-slide talking points)

**P1 — the aspiration bridge works, now with a degree-ROI close.** The match followed
the evidence, not the stated interest: two accounting internships → Accountants and
Auditors at 0.523, with an all-finance candidate list behind it. The surfaced paths
ARE the answer to "how do I get into finance from here": Treasurers and Controllers
(+$106k), Financial Examiners, Credit Analysts (+$21k). The ladder to the Statisticians
aspiration runs through Actuaries (+$34k). The new section adds the student's own
degree numbers — Statistics bachelor's $67.6k at 1 year rising to $113.2k at 7 — and
the graduate programs within 100 miles that feed Statisticians, led by UC Berkeley's
Statistics and Management Sciences and Quantitative Methods master's.

**P2 — strong aspiration, honest tension.** Wet-lab evidence dominated (Molecular and
Cellular Biologists 0.639, the strongest raw match), but a degree in progress keeps
the start at Bioinformatics Technicians [z4] with the biologist role as the
aspiration and a two-hop ladder through Bioinformatics Scientists. The finance-club
pitch is too small a signal to pull toward finance, and no `career_ladder` edge
bridges research to investment banking — the system does not invent a bridge the
data says is not a single step. The degree section prices the biology path itself:
the UC Berkeley Cell/Cellular Biology doctorate publishes $123.2k at 4 years and
$156.8k at 7.

**P3 — graceful degradation at the boundary.** Weak-evidence signal (0.350), the
experience gate visibly relaxed, and paths into network/web roles that carry negative
wage deltas (the penalty at work). What this persona really demonstrates is that
similarity works as a **confidence signal** — a UI should treat sub-0.4 matches as "we
may not cover your field yet." The Scorecard numbers (Communications bachelor's $39.6k
→ $70.4k over seven years) are the persona's most reliable output.

## 6. Narration quality notes (honest caveats)

The local 14B model (qwen3:14b via Ollama) stayed numerically grounded in all three
stories — every similarity, weight, wage, and Scorecard figure it cites appears in the
decision record, and all three narrations used the degree-outcomes data (e.g. $123,231
four-year earnings for the UC Berkeley cell-biology doctorate, $70,428 seven-year
earnings for communications). Wobbles worth knowing before a live demo:

- P1's story says the student is "best positioned to start in job zone 3" while the
  start it names is a zone-4 role; the record has both (band [3..3], start z4 via
  `ceiling_unmet`), and the paraphrase blurs them.
- P2's story closes with "this path would require a significant time and financial
  investment" — a reasonable generic caution that is not in the record.
- P3's story groups the University of Pennsylvania with "graduate programs in nearby
  areas"; Penn came from the *best-paid nationally* list, not the near-Berkeley list.

All three are narration-layer wobbles, not pipeline errors — the numbers underneath are
correct, and `LLM_BACKEND=anthropic` upgrades the storyteller with zero code change.

## 7. Run metadata

| | P1 | P2 | P3 |
|---|---|---|---|
| Narration length | 517 tokens | 634 tokens | 628 tokens |
| Narration latency | 39.5s | 72.9s | 123.9s |
| A-inference round trip | 243 ms | 401 ms | 232 ms |
| Path enumeration (GRAPH_TABLE) | 863 ms | 435 ms | 759 ms |
| Degree outcomes (vector + spatial) | 226 ms | 210 ms | 482 ms |
| Model | qwen3:14b (local, Ollama) | — | — |

Engine: Oracle AI Database 26ai · `OCCUPATION_GRAPH` property graph (`GRAPH_TABLE`) ·
`ALL_MINILM_L12_V2` ONNX in-database · 124 occupations, 2,564 edges · 6,196
institutions / 62,926 priced programs (College Scorecard) · wages: H-1B LCA FY2026
(national scope). Profiles were created, matched, logged to `tool_log`, and removed
after the run.
