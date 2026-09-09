# Interview Synthesis

**Interview transcripts in. A cited, queryable synthesis out — where people agree, where they
conflict, every claim traceable to the exact words someone said.**

Asking an LLM to "summarize these interviews" gives you something fluent, confident, and
unverifiable. This finds agreement and conflict instead of asserting them, and every claim
carries a verbatim quote from a named person.

---

## Method

Built on the [Framework Method](https://pmc.ncbi.nlm.nih.gov/articles/PMC3848812/) (Gale et
al., 2013) — a standard approach in qualitative health research. Its seven stages end in a
**matrix**: rows are people, columns are topics, cells hold what that person said. Read *down*
a column to compare people, *across* a row to understand one.

| Framework Method stage | Here |
|---|---|
| 1. Transcription | `.docx` → markdown, split by section |
| 2. Familiarisation | Pass 1 reads every turn |
| 3. Coding | Pass 2 labels each answer with the question it addresses |
| 4. Develop the analytical framework | Pass 1's canonical questions **are** the framework |
| 5. Apply the framework | Pass 2, every turn |
| 6. **Chart into the matrix** | Pass 3, part one — pure Python, no model |
| 7. **Interpret** | Pass 3, part two — the model's actual job |

---

## Assumptions

1. **No control over the interview.** Transcripts arrive after the fact. The interviewer is an
   AI: it drills into whoever gives it material (one expert answered 65 questions, the others
   33 and 30), restates answers back as fact, and asks confirmation questions that get a bare
   `"Yes."`
2. **Context length is the binding constraint**, and it grows two ways — more interviews, and
   longer answers. Nothing may assume "it all fits."
3. **The only shared structure is sections containing question/answer turns.** No shared
   schema, no shared question list. The framework has to be derived from the data.

---

## Pipeline

```
raw/*.docx ──▶ structured/<section>/<person>.md
                   │
                   ├─▶ PASS 1  context-pass   profiles + the canonical question list
                   ├─▶ PASS 2  code-pass      each answer → the question it addresses
                   └─▶ PASS 3  synth-pass     matrix (Python) → agreement, conflict, findings
                   ▼
              out/report.html
```

Pass 1 answers *who* and *what was asked* — nothing else. Interpretation is pass 3's job,
where the matrix is there to check it against.

### The matrix has three levels

Rows = people, columns = the 76 questions gives a matrix **55% filled**, 45 columns with one
respondent. That's an artifact: the questions are conversational threads, not topics — one TCO
question all three answered, then four follow-ups with one person. Grouping each question under
the nearest preceding one that ≥2 people answered (a Python rule, no model):

| Level | Columns | Filled | Comparable |
|---|---|---|---|
| Section | 7 | 100% | all 7 |
| **Thread** | **30** | **93%** | **all 30** |
| Question | 76 | 55% | 24 of 76 |

Threads are what the synthesis runs on. Questions stay as leaves.

### What comes out

| | |
|---|---|
| Canonical questions | 75 across 7 sections |
| Matrix | 3 people × 30 topics, 93% filled |
| Agreements / conflicts | 75 / 23 |
| Cross-cutting findings | 11 |
| Citations | **438, all verified verbatim** |
| Cost | $8.58 end to end |
| Tests | 119, none spend a token |

> **Conflict:** whether the platform decision belonged to a broader transformation programme.
> Expert-1 says it became one; expert-2 explicitly denies that framing.
> *Why:* expert-1 ran a multi-site global manufacturer where commonality forced a global
> programme; the others ran deliberately IT-led projects.

Not just "they disagree" — what the disagreement is *about*, and why both are right in context.

---

## Decision points and trade-offs

### 1. Complex vs. simple to explain

Chose explainable almost every time.

| Chose | Over | Why |
|---|---|---|
| Python rule for grouping questions | A clustering model | One sentence to explain, free, auditable |
| Verbatim cells | Model-written summaries | At 38 words a "summary" is a paraphrase |
| SQLite | A vector store | You can read the query and predict the result |
| One code per turn | Multi-label with confidences | Simple table, simple evaluation |

Accepted complexity once: the three-level matrix. The data genuinely has three.

### 2. Deterministic wherever possible

If it's arithmetic or a lookup, Python does it. The model only does judgment.

Not the model's job: charting the matrix, deciding whether a column is comparable, question
numbering, ids, coverage, token accounting, digests.

I called the coding step "deterministic" early on and was wrong — it replays a model decision
from pass 1. The fix wasn't rewording the docs, it was making that decision its own evaluable
stage.

### 3. Building for evaluation

Every separable job is its own stage, with its own prompt, cache, and output type — **7 model
stages**, each independently cacheable and checkable.

- Verbatim validators; a failure returns to the model as a retry. 438 citations, zero
  unverified.
- Pass 3 checks quotes against **the cell**, not the file — "did they say this *about this
  topic*."
- 119 tests that cannot reach a real model (`ALLOW_MODEL_REQUESTS = False`).
- Per-stage caching: fixing one prompt late in the build re-ran one stage, $0.47 not $5.38.

**What it caught:** a bare `"Yes."` answering *"Confirmation: TCO ended up 20-30% higher?"* is
verbatim expert speech and passes every check — but the substance came from the interviewer.
Perfectly cited, still a fabrication. Pass 3 marks those cells unquotable.

**And it caught dead weight.** Once question extraction and theme generation were separate
stages, it was obvious nothing read the themes — pass 3 does that job better. Deleting them cut
62% of pass 1's output and its most expensive stage — 234 KB to 88 KB, $7.59 to $3.52.

### 4. Larger contexts and scalability

No prompt may grow with the corpus. Every stage works on a bounded unit; a budgeter decides how
many fit per call.

| Stage | Bounded by | Largest prompt |
|---|---|---|
| Extract | One (person × section) file | 4.9k |
| Question | One section's extracts | ~8k |
| Coding | One section's transcripts | 4.9k |
| Synthesis | One column | 2.6k |

Against 120k. Adding interviews adds **calls, not bigger calls**. Files too large for one call
split on turn boundaries, never mid-turn. Tests build a synthetic 500-interview corpus and
assert prompt size holds.

**Known ceiling, not fixed:** a column's synthesis emits one position per person, so *output*
grows with the roster and binds around 8–10 people per column. Fix is to group positions by
stance.

---

## The UI

```
                     expert-1              expert-2             expert-3
 ── Cost & TCO ───────────────────────────────────────────────────────────────
 TCO vs budget       Ran 20-30% over,      15% over, driven     Within ~5% of
                     all of it internal    by SAP integration   budget; added
                     validation effort     work                 agent seats      ⚡ conflict

 Renewal trend       3-5%, negotiated      4-6%, calls it       6-8%, "more than
                     down at scale         manageable           I'd like"        ⚡ conflict

 Per-user pricing    ~$100/user/mo as      $60 blended vs       ~$40/agent vs
                     an illustration       $160 ServiceNow      $150 ServiceNow  ◐ 2 of 3
```

Each cell is a one-line reading of what that person said — not written for the grid, it's the
`gist` the synthesis already produced. Hover for their actual words. Empty cells say *why*
they're empty. Click a row for the full comparison: canonical quote, conflicts before
agreements, every quote linking to its source turn.

Also: findings feed, per-person view, and the underlying transcript.

`uv run synthesize` writes `out/report.html` — one self-contained file, no server, no upload.

---

## Roadmap

**1. Integration with the interviewer**
- *Feedback loop* — the pipeline already knows which questions have thin coverage, where people
  conflict, and what's single-source. Feed that back and each interview becomes a targeted
  instrument instead of a fixed script.
- *More deductive synthesis* — the framework is currently derived bottom-up because there was no
  brief. Agreed up front, synthesis can measure coverage against a known frame. Also fixes
  framework drift: a frozen, versioned codebook is what makes comparison over time possible.

**2. Evals** — the architecture is built for it; the eval sets don't exist yet. Gold-standard
sets per stage, agreement metrics (pass 2 can already run two independent strategies — they
agreed on 100% of 124 overlapping turns), adversarial cases, cost/quality curves.

**3. Documents and slides** — nobody presents a matrix. Select columns and findings → a slide
with the quotes attached; "write the cost section" → drafted from cited cells. The citations
make it safe.

**4. Scalability audit** — designed for scale, not yet *run* at scale. Run 100+ interviews and
find what actually breaks; fix the output ceiling; Batch API halves the cost; cheaper models for
the mechanical stages; a UI that opens on "the 10 topics where people disagree most" rather than
a 1,000-row grid.

---

## Running it

```bash
export ANTHROPIC_API_KEY=sk-ant-...     # or put it in .env

uv run synthesize                       # everything → out/report.html, opens it
uv run synthesize --dry-run             # the plan and the cost, spending nothing
uv run pytest                           # 119 tests, no tokens
```

Each pass also runs alone (`context-pass`, `code-pass`, `synth-pass`) and takes `--dry-run`.
Results cache per stage, so a rerun after a prompt change re-runs only what it affected.

> The `src` layout is what makes the install reliable. With a flat layout the editable
> install's path entry pointed at the repo root — which `site` already treats as known, since
> the venv lives inside it — so the entry was skipped and the console scripts randomly failed
> to import. `src/` is never the working directory, so the entry always applies.

## Repo map

Standard `src` layout — the package is installable and container-ready. Code ships in the
package; data lives in the working directory.

```
src/interview_synthesis/
  cli.py                 the one-command runner
  structure.py           .docx → sectioned markdown
  corpus.py  sections.py  budget.py  runner.py    shared infrastructure
  paths.py               where the workspace is (cwd, or $INTERVIEW_SYNTHESIS_WORKSPACE)
  context/               pass 1 — profiles + the canonical question list
  coding/                pass 2 — answer → question coding, SQLite
  synthesis/             pass 3 — framework matrix + interpretation
  ui/index.html          the report template, shipped in the wheel

raw/  structured/  out/   the workspace: transcripts in, artifacts out
tests/                    119 tests, none spend a token
```

```bash
uv build                          # wheel + sdist
pip install dist/*.whl            # then run `interview-synthesis` anywhere
```

Design notes and trade-offs per pass:
[pass 1](src/interview_synthesis/context/README.md) ·
[pass 2](src/interview_synthesis/coding/README.md) ·
[pass 3](src/interview_synthesis/synthesis/README.md)
