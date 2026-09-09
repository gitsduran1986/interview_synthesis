# Interview Synthesis

**Expert interview transcripts go in. A cited, queryable synthesis comes out — where people
agree, where they conflict, and every claim traceable to the exact words someone said.**

Built on three ITSM platform interviews (ServiceNow, BMC Helix, Freshservice), but nothing in
it is specific to those.

---

## The problem

Reading three interviews and working out where people actually agree takes hours. At a
hundred interviews you can't do it by hand at all.

The obvious shortcut — hand the transcripts to an LLM and ask for a summary — produces
something fluent, confident, and unverifiable. You get a paragraph that sounds right and no
way to check it. For research that informs a real decision, that's worse than nothing.

So the goal isn't "summarize the interviews." It's:

- Every claim traces to a **verbatim quote** from a **named person** in a **specific place**
- **Agreement and conflict are found, not asserted**
- It still works when there are 300 interviews instead of 3
- Each step can be **checked independently**, because a pipeline you can't evaluate is a
  pipeline you can't trust

---

## The inspiration: the Framework Method

I didn't invent a process. I used the [Framework
Method](https://pmc.ncbi.nlm.nih.gov/articles/PMC3848812/) (Gale et al., 2013) — a
well-established approach for analyzing qualitative interview data, widely used in health
research.

Its seven stages end in a **matrix**: rows are people, columns are topics, and each cell holds
what that person said about that topic. You read *down* a column to compare people, and
*across* a row to understand one person.

I picked it for three reasons:

1. It's **designed for teams**, so every stage has a defined handoff — which maps cleanly onto
   separate pipeline steps.
2. It keeps the analysis **anchored to the source**. The paper is explicit that charted cells
   should carry references back to real quotes.
3. It treats **empty cells as findings**. A gap in the data is information, not something to
   paper over.

The mapping — the paper's stages on the left, what I built on the right:

| Framework Method stage | Here |
|---|---|
| 1. Transcription | `.docx` → structured markdown, split by section |
| 2. Familiarisation | Pass 1 reads every turn |
| 3. Coding | Pass 2 labels each answer with the question it addresses |
| 4. Develop the analytical framework | Pass 1 derives the canonical questions — **this is the framework** |
| 5. Apply the framework | Pass 2, applied to every turn |
| 6. **Chart into the matrix** | Pass 3, part one — pure Python, no model |
| 7. **Interpret** | Pass 3, part two — where the model earns its keep |

I departed from the paper in one place, at stage 6. That's in the trade-offs below.

---

## Assumptions

These three shaped almost every decision. If any were different, I'd have built something
else.

### 1. I have no control over the interview process

Transcripts arrive after the fact. I can't change what was asked, can't ask a follow-up, can't
rerun anything.

This matters more than it sounds, because the interviewer here is an **AI**, and it behaves in
ways a human interviewer wouldn't:

- It **drills into whoever gives it material.** One expert answered 65 questions; the other two
  answered 33 and 30. That's not a bug to fix — it's the shape of the data.
- It **restates answers back as fact** — *"So your top five are: 1) integration, 2)
  scalability…"* Those are the interviewer's words. Quote them as the expert's and you've
  fabricated evidence while being technically verbatim.
- It **asks confirmation questions**, so some answers are a bare `"Yes."` where all the
  substance came from the question.

The system has to be robust to all of that, because I can't ask for cleaner input.

### 2. Context length is the binding constraint, and it grows two ways

More interviews, and longer answers within each. Either can blow past what fits in one model
call, so nothing may assume "it all fits."

Today's corpus is 9,449 words and would fit in a single call easily. **I never let it.** Every
stage asks a budgeter how to split its work, so the same code runs at 3 interviews and at
3,000 — only the numbers coming out of the budgeter change.

### 3. The only shared structure is sections containing question/answer turns

That's it. Different people, companies, platforms, and questions asked in different words. No
shared schema, no shared question list.

So the question list can't be hardcoded. It has to be **derived from the data** and matched
across interviews wherever two people were effectively asked the same thing.

---

## How it works

```
   raw/*.docx
       │  build_structured.py — split into sections, keep every turn + speaker
       ▼
   structured/<section>/<person>.md
       │
       ├─▶ PASS 1  context-pass    Who are these people? What was asked?
       │      • profiles: role, tenure, scale, what they actually own
       │      • canonical questions: 74, deduplicated across the 3 interviews
       │      • themes per person and per section
       │
       ├─▶ PASS 2  code-pass       Which question does each answer address?
       │      • every interviewee turn labelled with a question id
       │      • stored in SQLite, one row per turn
       │
       └─▶ PASS 3  synth-pass      The matrix, and what it shows
              • chart:     rows × columns × cells — pure Python, $0
              • interpret: agreement, conflict, outliers, findings
       ▼
   out/synthesis.json  +  matrix tables in out/coding.db  →  the UI
```

### Pass 1 — who and what

- **Profiles.** Factual only — role, tenure, org scale, and crucially whether each platform is
  something they *run today*, *ran at a previous employer*, or *only evaluated*. No
  credibility scores; the reader judges.
- **The canonical question list.** 74 questions, deduplicated so "How did TCO compare to
  budget?" and "How would you characterize total cost of ownership vs. expectations?" become
  one. **This is the analytical framework** — stage 4 of the paper.
- **Themes**, per person and per section.

283 of 283 quotes verified verbatim against the transcripts.

### Pass 2 — coding

Labels each interviewee turn with the question it answers. 128 turns coded, one code per turn;
many turns can share a question.

Stored in SQLite because the consumer is a downstream agent, and one
`sqlite3 out/coding.db "SELECT ..."` beats loading a 300KB blob into a context window.

### Pass 3 — the matrix

**Charting is pure Python.** No model, no cost, byte-identical every run. Cells hold the
person's **verbatim** words.

**Interpretation is the model's job** — reading down columns for agreement and conflict, across
rows for how one person's account hangs together, and over the whole matrix for cross-cutting
findings.

367 of 367 quotes verified.

### The matrix has three levels

The obvious design is rows = people, columns = the 74 questions. Measured, that matrix is
**58% empty**, with 44 of 74 columns having a single respondent. Apparently too sparse to
compare anything.

That's an artifact. The 74 questions aren't 74 topics — they're conversational threads:

```
q-06-01  "How did TCO compare to budget?"          ← all three answered
q-06-02  "…and versus the alternatives?"           ← expert-1 only
q-06-03  "…versus what you'd budgeted?"            ← expert-1 only
q-06-04  "Confirmation: so 20-30% over?"           ← expert-1 only
q-06-05  "What was the biggest contributor?"       ← expert-1 only
```

That's **one topic**, drilled into with one person. Counting it as five columns manufactures
four empty ones. Grouping each question under the nearest preceding question that two or more
people answered — a simple Python rule, no model:

| Level | Columns | Filled | Comparable |
|---|---|---|---|
| Section | 7 | 100% | all 7 |
| **Thread** | **30** | **93%** | **all 30** |
| Question | 74 | 56% | 24 of 74 |

**Threads are what the synthesis runs on.** Sections group them for navigation. Questions stay
as leaves so you can always click through to the exact probe.

### What comes out

From 3 interviews and 9,449 words:

| | |
|---|---|
| Canonical questions | 74, across 7 sections |
| Matrix | 3 people × 30 topics, 93% filled |
| Agreements found | 76 |
| Conflicts found | 24 |
| Cross-cutting findings | 12 |
| Citations | **650, all verified verbatim** |
| Cost | ~$13 end to end |
| Tests | 115, none of which spend a token |

A finding it produced:

> **Company size, not vendor quality, is the organising variable.** The same five criteria
> appear in all three evaluations and simply reorder — the enterprise buyer leads with
> integration and scalability, the mid-market buyers lead with cost.

And a conflict, with the reason it exists:

> **Whether the platform decision belonged to a broader transformation programme.**
> Expert-1 says it became one; expert-2 explicitly denies that framing.
> *Why:* expert-1 describes a multi-site global manufacturer where commonality forced a global
> programme; the others ran deliberately IT-led projects.

That second one is the point. It doesn't just say "they disagree" — it says what the
disagreement is *about*, and why both people are right in their own context.

---

## Decision points and trade-offs

### 1. Complex vs. simple to explain

**I chose explainable almost every time.**

The clearest case is the thread grouping above. A model could cluster those questions, and on
messy data it would probably do a bit better. But then the answer to "why is this question in
this column?" becomes "the model decided," and nobody can check it.

The Python rule — *attach each question to the nearest preceding question that two or more
people answered* — fits in one sentence, is inspectable, is free, and produced zero
single-source columns. Better trade than a slightly better clustering nobody can audit.

| Chose | Over | Why |
|---|---|---|
| Python rule for grouping questions | A clustering model | One sentence to explain, free, inspectable |
| Verbatim cells | Model-written cell summaries | At 38 words a "summary" is just a paraphrase — it adds a layer between reader and source |
| SQLite | A vector store | You can read the query and predict the result |
| One code per turn | Multi-label with confidences | Simple table, simple evaluation |

**Where I accepted complexity:** the three-level matrix. Two levels would be simpler to
explain, but the data genuinely has three, and flattening it throws away either comparability
or precision.

### 2. Deterministic wherever possible

**Rule of thumb: if it's arithmetic or a lookup, Python does it. The model only does judgment.**

Not the model's job here:

- Charting the matrix — pure projection, $0, byte-identical every run
- Whether a column is comparable — that's counting respondents
- Question numbering, ids, coverage, token accounting, digests
- Collapsing a re-asked question — deduplication against a defined rule

Not purity for its own sake. Every job moved into Python is a job that can't hallucinate, costs
nothing, and runs the same way twice.

**A correction I had to make.** I initially described the coding step as "deterministic"
because it's a database join. That was wrong — the thing it joins was produced by a model in
pass 1. It's a *faithful replay of a model decision*, not a model-free result. The fix wasn't
to reword the docs; it was to make the step that produced that decision its own evaluable
stage, so the judgment happens somewhere you can inspect it and the replay is just a replay.

**What you can't have:** determinism from the models themselves. Opus 5 rejects `temperature`
and runs adaptive thinking, so identical inputs don't guarantee identical outputs. Instead
there's content-addressed caching — an unchanged rerun makes zero API calls and produces
byte-identical output, and every input is hashed into the run metadata.

### 3. Building for evaluation

**Every separable job is its own stage, with its own prompt, cache, and output type.**

This became a hard rule after a specific problem. Pass 1 originally did two things in one call:
deduplicate questions, *and* generate section themes. That meant:

- No way to assess the deduplication on its own — no isolated output to score
- No way to tune its prompt without disturbing theme quality
- Two artifacts competing for one output budget, which is where structured output quietly goes
  shallow and starts stubbing fields

Splitting them cost one extra call per section and bought all three back. There are now **8
model stages** across the three passes, each independently cacheable and checkable.

The supporting machinery:

- **Verbatim validators.** Every quote is checked against the source before it's accepted; a
  failure returns to the model as a retry. 650 citations, zero unverified.
- **Cell-level checking in pass 3.** Not just "did they say this" but "did they say this *about
  this topic*" — real words filed under the wrong column get rejected.
- **115 tests that spend no tokens.** `ALLOW_MODEL_REQUESTS = False` means the suite *cannot*
  reach a real model. Fake models drive the retry loop, the speaker check, and the scaling
  paths.
- **Per-stage caching.** When I fixed one prompt late in the build, only that stage re-ran —
  $0.47 instead of $5.38.

**The catch this saved me from:** pass 1 anchors a bare `"Yes."` as the answer to *"Confirmation:
TCO ended up 20-30% higher?"*. That `"Yes."` is genuinely the expert's verbatim word — it passes
every verbatim check. But the substance came from the interviewer. Publishing it as evidence
would be a fabrication that looks perfectly cited. Pass 3 marks those cells unquotable and a
validator enforces it.

### 4. Larger contexts and scalability

**The rule: no prompt may grow with the size of the corpus.**

Every stage works on a bounded unit, and a budgeter decides how many units fit in a call:

| Stage | Bounded by | Largest prompt today |
|---|---|---|
| Extract | One (person × section) file | 4.9k tokens |
| Question / theme | One section's extracts | ~8k tokens |
| Coding | One section's transcripts | 4.9k tokens |
| Synthesis | One column | 2.6k tokens |

Against a 120,000-token budget. Adding interviews adds **calls, not bigger calls**.

- A file too large for one call splits on **turn boundaries**, never mid-turn — splitting inside
  a turn would sever a question from its answer.
- Reduce stages read model-produced extracts, not raw transcripts, so their input grows with the
  *number* of interviews rather than their length.
- Output size, not input, is usually the real limit. Calls are capped on item count too, because
  a call that reads cheaply can still be asked to write more than fits.
- Tests build a synthetic 500-interview corpus and assert the prompt stays the same size.

**The ceiling I know about and haven't fixed:** a column's synthesis emits one position per
person, so its *output* grows with the roster and will bind at roughly 8–10 people per column.
The fix is to group positions by stance rather than by person. Documented, not built — at three
people it would be machinery for its own sake.

---

## The UI — first stab

**The run builds the page.** `uv run synthesize` ends by writing `out/report.html` — one
self-contained file with the synthesis inlined, no server, no sibling assets — and opening it
in your browser. You can mail that file to someone who doesn't have the repo and it works.

```bash
uv run synthesize              # ... → out/report.html, opens automatically
uv run synthesize --no-open    # build it, don't open it
```

`ui/index.html` is the source of truth for the markup and behaviour, and doubles as the dev
page: it loads `ui/data.js` beside it so you can iterate on the CSS without rebuilding. The
report builder (`synthesis/report.py`) swaps that one `<script src>` for the data inlined.

Only what the page renders goes into the report — it reads thread-grain cells and nothing
else, so question-grain rows are left out. That's 584KB down to 359KB, with every cited cell
still resolvable and the citation count unchanged.

### The screens

**1. The matrix** — the home screen, and the thing the whole pipeline exists to produce.

```
                    expert-1        expert-2        expert-3
                    Sr Dir IT       Dir ITSM        Head of IT Ops
                    $40B pharma     mid biopharma   mid renewables
 ── Current environment ─────────────────────────────────────────
 Modules deployed   ████ 72w        ████ 63w        ███ 48w        ✓ all
 Drivers to adopt   ████ 91w        ████ 88w        ███ 51w        ⚡ conflict
 ── Cost & TCO ──────────────────────────────────────────────────
 TCO vs budget      ████ 118w       ███ 44w         ██ 31w         ✓ all
 Per-user pricing   ████ 96w        ████ 71w        —  not asked    ◐ 2 of 3
 Renewal trend      ██ 12w          ███ 39w         ███ 34w        ⚡ conflict
```

Cells are sized by how much was said. Empty cells say **why** they're empty — "not asked" is
different from "asked and dodged," and a blank square would let a reader assume "no opinion."
The right margin flags where there's a conflict worth reading.

**2. Column view** — click a column. This is where most of the value is.

```
 Renewal pricing trend                          3 of 3 answered

 "We're two renewals in, increases have been around 6 to 8
  percent each time, which is a bit more than I'd like."
                                        — expert-3  ⟶ transcript

 ⚡ CONFLICT — evaluative
 What counts as an acceptable annual uplift.
   expert-1  3-5%, negotiated down     "somewhere around 3-5%"
   expert-2  4-6%, "manageable"        "in the four to six percent range"
   expert-3  6-8%, "more than I'd like"
 Why: enterprise scale buys negotiating leverage the mid-market lacks.

 ✓ AGREEMENT — all three absorbed increases without escalating
```

Canonical quote at the top. Conflicts before agreements, because conflicts are what you can't
get from reading one interview alone. Every quote links back to the turn it came from.

**3. Person view** — one person's whole account, and where they contradict themselves.

**4. Findings** — the 12 cross-cutting readings, each showing the columns it rests on.

**5. Transcript** — the original document with coded turns highlighted, so you can always get
back to the source.

### The experience it's building toward

```
   ┌────────────────────────────────────┐
   │   Drop interview documents here    │
   │        .docx  .txt  .vtt           │
   └────────────────────────────────────┘

   ✓ 3 documents · 291 turns · 9,449 words

   Structuring transcripts        ████████████  done
   Reading interviews             ████████████  done   $7.59
   Coding answers                 ████████████  done   $0.00
   Building matrix                ████████████  done   $0.00
   Finding agreement & conflict   ██████░░░░░░  running

   650 quotes verified · 0 unverified
```

Upload, watch it run with the cost visible as it goes, land in the matrix.

`uv run synthesize` already does the middle part — it runs all four stages in order and prints
this as it goes. What's missing is the upload and the auto-open: today you drop the documents
in `raw/` and open the UI yourself.

---

## Roadmap

### 1. Integration with the interviewer

Right now this is strictly downstream: transcripts arrive, I analyze them. The biggest gains
come from closing that loop.

**a. Feedback loop.** The pipeline already knows things the interviewer could use *during* the
next interview:

- Which questions have thin coverage (44 of 74 were asked of one person)
- Where existing interviewees conflict — the highest-value thing to put to the next person
- Which topics are single-source and need corroboration

Feeding that back turns each interview into a targeted instrument rather than a fixed script.
The interviewer stops re-asking what's settled and starts probing what's contested.

**b. More deductive synthesis, and better synthesis context.** Today the framework is derived
bottom-up from whatever happened to be asked — purely inductive, because there was no research
brief. With the interviewer integrated, the framework can be **agreed up front**: here are the
questions this study must answer, here's what a good answer looks like. Synthesis then becomes
partly deductive — measuring coverage against a known frame — which also makes it evaluable
against something other than itself.

That's also the fix for framework drift: right now a new batch of interviews re-derives the
questions and the columns shift. A **frozen, versioned codebook** that new interviews are coded
*against* is what makes comparison over time possible at all.

### 2. Evals

The architecture is built for this — separable stages, typed outputs, a zero-token test harness
— but the eval sets don't exist yet.

- **Gold-standard sets** per stage: a human-coded subset for the coder, human-identified
  agreements and conflicts for the synthesis.
- **Agreement metrics.** Pass 2 can already run two independent strategies — a replay of pass
  1's pairing, and a fresh model judgment — and compare them. They agreed on 100% of 124
  overlapping turns. That's a template for the other stages.
- **Adversarial cases**: a fabricated quote, a real quote filed under the wrong topic, a
  single-source column that invites a false consensus. Some are already tests; they should be
  scored, not just asserted.
- **Cost/quality curves.** Would Haiku code as well for a fifth of the price? Nobody knows yet,
  and the caching makes that A/B cheap.

### 3. Interact with the data to produce documents and slides

The matrix is a good analysis surface, but nobody presents a matrix. What people want is a deck
or a memo with the quotes already in it.

- Select columns and findings → generate a slide with the canonical quotes attached
- "Write the cost section of the report" → drafted from cited cells, every claim linked
- Export a conflict as a slide showing both sides and the reason
- The citations make this safe: generated documents carry links back to the source turn, so a
  reviewer can check any line

This is the step that turns research infrastructure into something someone uses on a Tuesday.

### 4. Full scalability audit and improvements

I've designed for scale and tested the paths, but haven't *run* at scale.

- **Run 100+ interviews** and find what actually breaks, rather than what I predict will
- **Fix the known output ceiling** — group positions by stance instead of per-person, removing
  roster size from the output equation
- **Streaming and batch**: the Batch API halves the cost of everything here, and none of it is
  latency-sensitive
- **Cheaper models for mechanical stages** — coding is constrained classification over a closed
  label set, a good small-model job
- **UI at scale**: nobody scans a 1,000-row grid. Above ~20 interviews the entry point becomes
  "the 10 topics where people disagree most," and rows become *segments* (company size,
  regulatory posture) rather than individuals

---

## Running it

One command runs everything:

```bash
export ANTHROPIC_API_KEY=sk-ant-...     # or put it in .env

uv run synthesize                       # .docx → … → out/report.html, opens in your browser
uv run synthesize --dry-run             # the plan and the cost, spending nothing
```

```
3 document(s) in raw/

── Structuring transcripts ─────────────────────────────
   27 section files
── Reading interviews ──────────────────────────────────
   status: complete · 283/283 citations verified · $7.59
── Coding answers ──────────────────────────────────────
   128 turns coded · $0.00
── Building matrix & synthesizing ──────────────────────
   37 columns · 76 agreements · 24 conflicts · $5.38

done in 17s · $12.97 spent

   out/report.html  (359KB · 3 interviewees · 12 findings)
   opened in your browser
```

Each pass is also its own command, so you can run, cache, and evaluate them separately:

```bash
uv run context-pass    # pass 1 → out/first_pass_context.json
uv run code-pass       # pass 2 → out/coding.db
uv run synth-pass      # pass 3 → out/synthesis.json
uv run pytest          # 115 tests, no tokens spent
```

Every pass takes `--dry-run`. Results are cached per stage, so a rerun after a prompt change
re-runs only what that change affected — the last full rerun cost $0.00.

<details>
<summary><code>ModuleNotFoundError</code> on any of these commands?</summary>

The editable install writes a `.pth` file that can end up inert — repeated
`uv sync --reinstall-package` calls duplicate its lines until Python stops applying it, and
then nothing imports. Repair it with:

```bash
uv pip install -e .
```

That rewrites the path file cleanly and survives later `uv sync` / `uv run` calls. If you'd
rather not touch the install at all, every command also works as a module, which bypasses the
console scripts entirely:

```bash
uv run python -m orchestrator.cli
```

`pytest` is immune either way — the root `conftest.py` puts the repo on the path itself.
</details>

## Repo map

```
raw/                     original .docx transcripts
structured/              transcripts split by section, one file per person
scripts/                 .docx → markdown
context_pass/            pass 1 — profiles, canonical questions, themes
coding/                  pass 2 — answer → question coding, SQLite
synthesis/               pass 3 — framework matrix + interpretation
orchestrator/            the one-command runner
ui/                      the page source, and the dev version of it
out/                     all generated artifacts, including report.html
tests/                   115 tests, none spend a token
```

Each pass has its own design notes covering the decisions and what each one cost:

| | |
|---|---|
| Pass 1 | [`context_pass/README.md`](context_pass/README.md) |
| Pass 2 | [`coding/README.md`](coding/README.md) |
| Pass 3 | [`synthesis/README.md`](synthesis/README.md) |
