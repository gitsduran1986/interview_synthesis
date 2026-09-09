# Interview Synthesis

**Interview transcripts in. A cited, queryable synthesis out — where people agree, where they
conflict, every claim traceable to the exact words someone said.**

Asking an LLM to "summarize these interviews" gives you something fluent, confident, and
unverifiable. This finds agreement and conflict instead of asserting them, and every claim
carries a verbatim quote from a named person.

**Start here:** [`example.py`](example.py) runs the whole thing and shows what consuming the
output looks like · [`DATA_MODEL.md`](DATA_MODEL.md) describes every artifact it produces.

---

## Modeled After FrameWork Method

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

Four steps. Each writes its output to disk before the next reads it, so any step can be
re-run, inspected, or replaced on its own.

| Step | In | Out | Stored as |
|---|---|---|---|
| **structure** | `raw/*.docx` | sectioned transcripts, every turn + speaker | `structured/<section>/<person>.md` |
| **pass 1** `context-pass` | `structured/` | profiles + the canonical question list | `out/first_pass_context.json` |
| **pass 2** `code-pass` | `structured/` + the codebook | every answer labelled with a question | `out/coding.db` (SQLite) + `out/coding.jsonl` |
| **pass 3** `synth-pass` | `out/coding.db` | the matrix, then agreement/conflict/findings | `out/synthesis.json` + tables in `out/coding.db` → `out/report.html` |

```
raw/*.docx
    │  structure — stdlib only, no model
    ▼
structured/<section>/<person>.md
    │
    ├─▶ PASS 1  who are these people, and what were they asked
    │      out/first_pass_context.json   (117 KB)
    │      └─ projected to out/codebook.json (39 KB) — the only thing pass 2 sees
    │
    ├─▶ PASS 2  which question does each answer address
    │      out/coding.db      one row per turn, one code per answer
    │      out/coding.jsonl   the same rows, flat and diffable
    │
    └─▶ PASS 3  chart the matrix (Python), then interpret it (model)
           out/synthesis.json          (583 KB) the whole document
           out/coding.db               matrix + synthesis tables, queryable
           out/report.html             (356 KB) self-contained, opens anywhere
```

Two supporting directories: `out/stages/` holds every individual call's result so a crash never
costs earlier work, and `out/.cache/` is content-addressed by model, prompt, schema and payload
— which is why an unchanged rerun makes zero API calls.

**→ [`DATA_MODEL.md`](DATA_MODEL.md)** describes every one of these artifacts: the models, the
SQLite schema, the id scheme, and what's guaranteed — with real examples throughout.

Pass 1 answers *who* and *what was asked* — nothing else. Interpretation is pass 3's job, where
the matrix is there to check it against.

### The matrix has three levels

Rows = people, columns = the 76 questions gives a matrix **55% filled**, 45 columns with one
respondent. That looks too sparse to compare anything — but it's an artifact of how the
interviewer behaves, not of what people said.

**How a thread is derived.** One rule, in Python, no model:

> Walk the questions in order within a section. A question **two or more people answered opens
> a thread**. Every question after it that only **one** person answered joins that thread.

It encodes a single observation: this interviewer asks everyone the main question, then drills
into whoever gives it material. So a one-respondent question is almost never a new topic — it's
a follow-up on the one that just opened. A real thread:

```
col-02-03   ANCHOR  q-02-03  3 resp  "Were those goals part of a broader strategic initiative?"
              +     q-02-04  1 resp  "You mentioned GRSD — could you clarify which module?"
              +     q-02-05  1 resp  "What business needs drove the expansion into asset mgmt?"
              +     q-02-06  1 resp  "Was that expansion part of the original scope?"
              +     q-02-07  1 resp  "What makes a custom ITSM the right fit now?"
```

One topic, drilled into with one person. As five columns that's one comparable and four empty.
As a thread it's one column all three answered, with the probes nested inside.

| Level | Columns | Filled | Comparable |
|---|---|---|---|
| Section | 7 | 100% | all 7 |
| **Thread** | **30** | **93%** | **all 30** |
| Question | 76 | 55% | 24 of 76 |

A thread's respondent count is the **union** of everyone who answered any question in it.
Threads are what the synthesis runs on; questions stay as leaves so you can always reach the
exact probe.

**Where it would break:** the rule is ordering-based, not meaning-based. If the interviewer
circled back to an earlier topic after moving on, the question would attach to whichever thread
was open, not the one it belongs to. Didn't happen here; I didn't test for it. The grouping is
stored in `matrix_column_question`, so a model-based version could be diffed against it question
by question — which is how I'd want to evaluate one.

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

### The intermediate data store

Everything between the transcripts and the report lives in one SQLite file, `out/coding.db`.
It's there because the consumer is a downstream agent, and one `sqlite3 out/coding.db "SELECT
..."` beats loading a 583 KB JSON blob into a context window.

| Table | Rows | What it holds |
|---|---|---|
| `text` | 291 | every turn, both speakers, verbatim |
| `question` | 76 | the canonical question list from pass 1 |
| `coding` | 129 | **the coded responses** — one row per answered turn |
| `uncoded` | 18 | turns with no code, and the reason why |
| `matrix_cell` | 339 | the charted matrix, all three grains |
| `synthesis_object` | 51 | column/case/finding syntheses |
| `unit` `interview` `coding_run` `matrix_column` `synthesis_citation` | | provenance and joins |

**A coded response**, which is the core row of the whole system:

```
text_id       bfc9a1f76594ea82
question_id   q-06-09                          → question.canonical_question
interview_id  expert-1                         → interview
section_id    06-cost-total-cost-of-ownership
unit_id       structured/06-cost-.../expert-1.md   → the file it came from
turn_index    17                               → the exact turn in that file
word_count    25
raw_text      "Yes, there was role separation. We had read-only roles, approvers,
               ITIL roles, admins, and standard users."
method        anchor                           → how this code was decided
is_disfluent  0                                → process talk? then unquotable
```

Three things make that row useful rather than just stored:

- **`text_id` is stable** — `sha256(unit_id + turn_index)`, not the text and not a timestamp.
  Fix a typo in a transcript and the row keeps its identity; `text_sha` changes instead, so an
  edit is detectable without orphaning the coding.
- **`method` records how it was decided** (`anchor`, `span`, `model`, `manual`), so a consumer
  can always separate what pass 1 claimed from what was inferred from what a model judged.

Two views sit on top: `coded_text` returns the five columns the deliverable promises, and
`matrix` returns the grid with topic labels joined in.

```sql
SELECT raw_text FROM coded_text WHERE canonical_question_id = 'q-06-09';
SELECT expert, text FROM matrix WHERE grain='thread' AND column_id='col-06-01';
```

`out/coding.jsonl` carries the same coded rows flat, so a re-run's changes are reviewable in a
diff — the `.db` is binary and derived, and is gitignored.

Future Agentic Tools benefit from this data set. Other agentic processes can take advantage of this data for creating slides, reports or other outputs.  

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

**What it caught:** a bare `"Yes."` answering *"So the total cost of ownership ended up roughly
20-30% higher than you expected?"* is verbatim expert speech and passes every check — but the
number came from the interviewer, not the expert. Perfectly cited, still a fabrication. Pass 3
flags those 7 cells unquotable, and a validator rejects any attempt to cite one.

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

### 5. The UI

```
                     expert-1             expert-2             expert-3
 ── Cost & TCO ──────────────────────────────────────────────────────────────
 Licensing           Unanticipated        AIOps and mobile     Only the Project
 surprises           volume of business   Digital Workplace    module, and that
                     users needing test   were separate SKUs   was a later
                     access                                    choice            ⚡ conflict

 Per-vendor cost     not asked            Rates BMC well on    Freshservice a 7,
 ratings                                  both cost and        adequate for
                                          capability           their scale       ◐ 2 of 3

 ── Satisfaction ────────────────────────────────────────────────────────────
 Biggest             Steep learning       Weaknesses are       Automation
 weaknesses          curve, complex       largely external     builder gets
                     resource-hungry      to the product       clunky as
                     implementations                           complexity rises  ⚡ conflict
```

Five decisions, each with a cost:

| Decision | Instead of | Trade |
|---|---|---|
| **Cells carry the synthesis `gist`** | A word count, or the raw answer | You can scan a row and see the disagreement. Costs nothing — the gist already existed — but it is a *reading*, so the words are one hover away, never replaced |
| **Topics as rows, people as columns** | The paper's orientation | 30 topics fit down a page; 30 columns don't. Diverges from the Framework Method's layout, not its logic |
| **Empty cells state *why*** | Leaving them blank | A blank square reads as "no opinion." Costs grid space to say "not asked" |
| **Conflicts before agreements** | Neutral ordering | Conflict is the thing you cannot get from reading one interview. Risks over-weighting disagreement in a corpus that mostly agrees |
| **One self-contained HTML file** | A served app | Opens anywhere, no server, no upload, survives being emailed. 356 KB, and every re-run rewrites the whole thing |

Click a row for the full comparison — canonical quote, conflicts, every quote linking to its
source turn. Plus a findings feed, a per-person view, and the transcript underneath.

`uv run synthesize` writes `out/report.html`.

**What it is not:** a product. It exists to prove the data model is navigable — that every
object is addressable and every claim walks back to a transcript turn. Those ids are stable
*within* a run but not *across* re-runs, which is the first thing roadmap #5 has to fix.

---

## Roadmap

**1. Integration with the larger pipeline**
- *Feedback loop* — the pipeline already knows which questions have thin coverage, where people
  conflict, and what's single-source. Feed that back and each interview becomes a targeted
  instrument instead of a fixed script.
- *More deductive synthesis* — the framework is currently derived bottom-up because there was no
  brief. Agreed up front, synthesis can measure coverage against a known frame. Also fixes
  framework drift: a frozen, versioned codebook is what makes comparison over time possible.
- *What's Important* — No where in this system is there a measure of how does this answer the customers question. 
   Integration and passing context from Initiatial questions to interview to synthesize can create valuable context

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

**5. An output UI that fits the existing UX** — `out/report.html` is a prototype I built to
prove the data model is navigable, not a product. The real version has to live inside whatever
interface people already use, which changes the shape of the problem: the synthesis becomes an
API rather than a document, objects need to survive being embedded in someone else's page, and
the ids have to be URL-stable across re-runs. They are not today — pass 1 numbers questions by
ordinal, so adding one question shifts every id after it and any saved link breaks. Content-
derived ids are the prerequisite for this whole item.

**6. Let experts grade their own synthesis** — show each interviewee how the system represented
them and let them mark it right or wrong. This is the highest-quality eval signal available and
nobody else can produce it: the person who said the words is the only one who knows whether the
reading of them is fair. Concretely: send each expert their own row — the cells, the positions
attributed to them, the quotes chosen — and collect a per-cell judgement.

It also closes a gap I can't otherwise close. Every check in this system verifies that a quote
is *real* and *correctly attributed*. Nothing verifies that the **inference** drawn from it is
sound — a genuine quote can support a claim it doesn't actually make, and no validator will
catch that. Expert grading is the only mechanism here that would.

---

## Running it

```bash
export ANTHROPIC_API_KEY=sk-ant-...     # or put it in .env

uv run synthesize                       # everything → out/report.html, opens it
uv run synthesize --dry-run             # the plan and the cost, spending nothing
uv run pytest                           # 119 tests, no tokens
```

**[`example.py`](example.py)** is the annotated version of the same thing — it imports the
package, runs the four stages, then shows what consuming the typed output looks like. Start
there if you want to use this as a library:

```bash
uv run python example.py --dry-run     # the plan and the cost, spending nothing
uv run python example.py               # run it, then print who was interviewed,
                                       # where they disagree, and the findings
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

example.py                annotated end-to-end run, and how to read the output
DATA_MODEL.md             every artifact: models, SQLite schema, id scheme, guarantees

raw/  structured/  out/   the workspace: transcripts in, artifacts out
tests/                    124 tests, none spend a token
```

```bash
uv build                          # wheel + sdist
pip install dist/*.whl            # then run `interview-synthesis` anywhere
```

**[`example.py`](example.py)** — imports the package, runs the four stages, then queries the
typed output. The fastest way to see what this produces.

**[`DATA_MODEL.md`](DATA_MODEL.md)** — what each pass produces, how it's stored, and how the
pieces join up, with real examples. Read it if you want to consume the output yourself.

Design notes and trade-offs per pass:
[pass 1](src/interview_synthesis/context/README.md) ·
[pass 2](src/interview_synthesis/coding/README.md) ·
[pass 3](src/interview_synthesis/synthesis/README.md)

---

## Where this is

| | |
|---|---|
| **Solid** — defined, tested, and I'd defend the design | The three-pass split · verbatim citation and its validators · the coding step · the framework matrix and how it's charted · caching and cost control · scaling design |
| **Working, but the shape may change** | **The data model** — see below · the thread heuristic (one rule, unvalidated against messy data) · what a "finding" should be · which grains get synthesized · the SQLite schema (fine for one workspace, not for many) |
| **Early — sketched, not solved** | The UI (a prototype, not a product) · evals (architecture is ready, sets don't exist) · anything past ~10 interviewees · integration with the interviewer · turning findings into documents |

**The data model is a work in progress and will change.** [`DATA_MODEL.md`](DATA_MODEL.md)
describes what exists today, and it works — but it was shaped by one corpus of three interviews
and a specific interviewer. Expect it to move.

The reason is worth being explicit about: **agentic approaches evolve as feedback loops close.**
Right now this system runs one direction — transcripts arrive, analysis comes out, nobody tells
it whether the analysis was any good. Every loop on the roadmap changes what the data has to
carry:

- **Interviewer integration** (roadmap #1) means the framework gets agreed up front rather than
  derived per run, which turns the question list from an output into a versioned input.
- **Evals** (#2) need somewhere to put a graded judgement against a stage's output — there is
  no home for that today.
- **Expert grading** (#6) adds a per-cell verdict from the person who said the words, which is
  a new relation the schema doesn't have.

None of those are fields I can usefully add now. They're the shape the model takes once the
loops exist, and guessing at them would be building for an argument I haven't had yet.

The honest summary: **the analysis pipeline is the finished part.** It runs end to end, every
claim is cited and checked, and the design decisions have reasons I can defend. What surrounds
it — how you look at the output, how you know it's good, how it plugs into a larger system — is
sketched well enough to argue about, not built.
