# Data model

What each pass produces, where it's stored, and how the pieces join up. Written for someone
who hasn't read the code — every example below is real output, not illustrative.

The models themselves are the source of truth:

| | |
|---|---|
| Pass 1 | [`src/interview_synthesis/context/models.py`](src/interview_synthesis/context/models.py) |
| Pass 2 | [`codebook.py`](src/interview_synthesis/coding/codebook.py) · [`store.py`](src/interview_synthesis/coding/store.py) (SQL) |
| Pass 3 | [`src/interview_synthesis/synthesis/models.py`](src/interview_synthesis/synthesis/models.py) |

---

## The flow

```
raw/*.docx
   └─ structure ─▶ structured/<section>/<person>.md      transcripts, split by section

                   PASS 1 ─▶ out/first_pass_context.json  who they are, what was asked
                                    │
                                    └─ projected ─▶ out/codebook.json   the label space
                                                          │
                   PASS 2 ─▶ out/coding.db ◀───────────────┘            each answer coded
                                    │  out/coding.jsonl                 (flat + diffable)
                                    ▼
                   PASS 3 ─▶ out/synthesis.json           the matrix + the reading of it
                             out/coding.db (more tables)
                             report.html                  self-contained UI (repo root)
```

Every arrow is a file on disk. A pass reads what the previous one wrote and nothing else, so
any pass can be re-run, inspected, or replaced without touching the others.

---

## Identity

Four id schemes hold the whole thing together. All are **derived**, never allocated, so the
same input always produces the same id.

| Id | Shape | Example | Derived from |
|---|---|---|---|
| `text_id` | 16 hex chars | `8488c38d1dcf5aea` | `sha256(unit_id + turn_index)` |
| `question_id` | `q-<section>-<n>` | `q-02-01` | position in the section |
| `column_id` | `col-<section>-<n>` | `col-02-01` | the thread's anchor question |
| `cell_id` | `cell:<grain>:<column>:<row>` | `cell:t:col-02-01:expert-1` | its coordinates |

`text_id` deliberately avoids both the text and the timestamp. Fix a typo in a transcript and
the row keeps its identity — `text_sha` changes instead, so an edit is *detectable* without
orphaning the coding attached to it.

**`question_id` is the weak one.** It's positional, so inserting a question renumbers every id
after it, and `column_id` and `cell_id` inherit that. Fine within a run; not stable across
re-runs. Making it content-derived is the prerequisite for any saved link or bookmark.

---

## Pass 1 — `out/first_pass_context.json`

Answers two questions: **who are these people**, and **what were they asked**.

```python
class FirstPassContext:
    run: RunMetadata               # model, digests, cost, warnings
    interviewees: list[IntervieweeProfile]
    sections: list[SectionPass]    # each holds that section's questions
    evidence_audit: EvidenceAudit  # every quote, re-checked after assembly
```

### Evidence — the spine

Every factual claim in this pass carries one. It's what makes the output checkable rather than
merely plausible.

```python
class Evidence:
    quote: str            # verbatim, validated against the source file
    speaker: "expert" | "interviewer"
    timestamp: str | None
    expert_slug: str
    section_slug: str
    source_file: str
```

`speaker` matters more than it looks. The interviewer is an AI that restates answers back as
fact, so a quote can be verbatim and still be the *interviewer's* words. A validator rejects
any `speaker="expert"` quote that doesn't fall inside an interviewee turn.

### IntervieweeProfile

Factual background only — no scores, no ratings, no assessment of how credible someone is.

```python
class IntervieweeProfile:
    expert_slug, display_name, role_title, organization
    org_description, tenure, background
    ui_statement: str                       # one line for a UI card
    credentials: list[Credential]           # >= 1, each with Evidence
    platform_experience: list[PlatformExperience]
    scale_markers: list[str]                # "~2M tickets/yr"
    stated_limits: list[Evidence]           # where they flagged their own limits
```

```json
{ "claim": "Serves as Senior Director of IT Infrastructure and Operations.",
  "kind": "current_role",
  "evidence": {
    "quote": "I currently serve as the Senior Director of IT Infrastructure and Operations.",
    "speaker": "expert", "timestamp": "00:00:56", "expert_slug": "expert-1",
    "section_slug": "01-interview-introduction",
    "source_file": "structured/01-interview-introduction/expert-1.md" } }
```

`PlatformExperience.relationship` is `owns_today` | `operated_previously` | `evaluated_only` |
`mentioned_secondhand`. That distinction is load-bearing: one interviewee's ServiceNow
experience is at a *former* employer, and flattening that would make three non-comparable
people look comparable.

### SectionQuestion — the analytical framework

The deduplicated question list. This *is* the framework the rest of the system codes against.

```json
{ "question_id": "q-02-01",
  "canonical_question": "Walk me through how your organization uses the ITSM platform and
                         which modules you have deployed.",
  "asked_of": [
    { "expert_slug": "expert-1",
      "as_asked": "Let's start with your time at your previous employer...",
      "timestamp": "00:01:33", "answer_timestamp": "00:01:53", "answered": "answered" }
  ],
  "coverage": "all_interviewees" }
```

- `canonical_question` — one phrasing standing for the same question however it was worded
- `as_asked` — how it was actually put to *that* person
- `answer_timestamp` — points at the turn that answered it; the join key pass 2 can use
- `coverage` — `all_interviewees` | `subset` | `single_interviewee`, computed in Python from
  the roster, never asked of the model

---

## Pass 2 — `out/codebook.json` + `out/coding.db`

### The codebook — the contract between passes

Pass 2 does **not** read `first_pass_context.json`. It reads a narrow projection of it, so
changes to profiles or evidence can't break the coder.

```python
class Codebook:
    corpus_digest: str                    # refuses to run against a different corpus
    questions: list[CodebookQuestion]     # question_id, section_id, canonical_question
    anchors:   list[Anchor]               # pass 1's own answer pairing
```

The split is deliberate. `questions` is the **label space** and is all the *model* coder ever
sees. `anchors` is pass 1's claim about which turn answered which question, and only the
*timestamp* strategy reads it:

```json
{ "question_id": "q-02-01", "section_id": "02-current-environment",
  "expert_slug": "expert-1", "answer_timestamp": "00:01:53" }
```

Two coding strategies use these differently — `--strategy timestamp` replays the anchors,
`--strategy model` judges each turn from the transcript using only `questions`. See
[the pass 2 notes](src/interview_synthesis/coding/README.md).

### The SQLite store

One file, `out/coding.db`. It's SQLite because the consumer is a downstream agent, and one
`sqlite3 out/coding.db "SELECT ..."` beats loading a 583 KB blob into a context window.

```sql
interview(interview_id PK, display_name, role)
unit(unit_id PK, interview_id→interview, section_id, section_role, sha256)
text(text_id PK, unit_id→unit, interview_id, section_id, turn_index,
     speaker_role, raw_text, text_sha, word_count)      -- every turn, both speakers
question(question_id PK, section_id, canonical_question) -- loaded from the codebook

coding(text_id PK→text, question_id→question, method, is_disfluent,
       confidence, rationale, run_id→coding_run)         -- THE coded response
uncoded(text_id PK→text, reason, run_id)                 -- and why it has no code
coding_run(run_id PK, started_at, strategy, model, corpus_digest,
           codebook_digest, prompt_digest, git_sha, status)
```

**A coded response** — the core row of the system:

```
text_id       bfc9a1f76594ea82
question_id   q-06-09                             → question.canonical_question
method        anchor                              → how this code was decided
is_disfluent  0                                   → process talk? then unquotable
run_id        …                                   → which run produced it
              ↓ joins to text
interview_id  expert-1        unit_id  structured/06-cost-.../expert-1.md
turn_index    17              word_count 25
raw_text      "Yes, there was role separation. We had read-only roles, approvers,
               ITIL roles, admins, and standard users."
```

Three properties make that row trustworthy rather than merely stored:

1. **`coding.text_id` is the primary key** — that's what enforces *one code per turn*
   structurally. Many turns may share a `question_id`, which is how a re-asked question keeps
   all of its answers.
2. **`method` records provenance**: `anchor` (pass 1 said so) · `span` (inferred by
   forward-fill) · `model` (judged by the coding agent) · `manual`. Never conflated, so a
   consumer can weight or filter on it.
3. **Foreign keys do real work.** `question_id` references `question`, so a hallucinated label
   is an `IntegrityError` at insert time, not a silent bad row.

`uncoded.reason` is `no_question_addressed` | `model_declined` | `non_eval_section` |
`unit_failed`. An uncodeable turn is recorded, never dropped — absence is a finding.

`out/coding.jsonl` carries the same rows flat and sorted, so a re-run's changes show up in a
diff. The `.db` is binary and derived, and is gitignored.

---

## Pass 3 — `out/synthesis.json` + more tables

```python
class Synthesis:
    run: RunMetadata
    rows: list[MatrixRow]                 # the people
    columns: list[MatrixColumn]           # the topics, at three grains
    cells: list[MatrixCell]               # what each person said about each topic
    columns_synthesis: list[ColumnSynthesis]   # reading DOWN a column
    cases: list[CaseSynthesis]                 # reading ACROSS a row
    findings: list[Finding]                    # reading the whole matrix
```

### The matrix (built in Python, no model)

Three grains, distinguished by `MatrixColumn.grain`:

| grain | what a column is | count | why |
|---|---|---|---|
| `section` | an interview section | 7 | always comparable; the overview |
| `thread` | a conversational thread | 30 | **what the synthesis runs on** |
| `question` | one canonical question | 76 | the leaf; drill down to the exact probe |

A `thread` groups a question with the follow-ups that only one person answered — see
[how a thread is derived](README.md#the-matrix-has-three-levels). `member_question_ids` records
the grouping, so it can be audited or diffed against an alternative.

```json
{ "cell_id": "cell:t:col-02-01:expert-1", "grain": "thread",
  "column_id": "col-02-01", "row_id": "expert-1",
  "status": "answered",
  "text": "Yeah, I used ServiceNow at my previous stop, Thermo Fisher. It was a v...",
  "word_count": 72,
  "sources": [ { "text_id": "8488c38d1dcf5aea",
                 "unit_id": "structured/02-current-environment/expert-1.md",
                 "turn_index": 1, "word_count": 72 } ],
  "is_disfluent": false, "assent_only": false }
```

- **`text` is verbatim.** Charting is a projection, so nothing here can paraphrase or invent.
- **`status`** is `answered` | `not_asked` | `no_answer` | `disfluent_only`. Empty cells are
  materialized, not skipped — "never asked" and "asked and gave nothing" are different
  findings, and a blank square would read as "no opinion".
- **`assent_only`** marks a cell whose whole content is agreement with the interviewer's own
  restatement (a bare `"Yes."`). Verbatim expert speech, but the substance came from the
  question — so it can never supply a quote.
- **`sources`** is the deep link back: `text_id` → the row in `coding.db` → the file and turn.

### The synthesis (the model's job)

```python
class Citation:                # every claim points at a real cell
    quote: str                 # verbatim, checked against THAT cell
    cell_id: str
    row_id: str

class Position:  row_id, stance, gist, citation
class Agreement: statement, positions (>=2), strength
class Conflict:  statement, positions (>=2), nature, explains_it

class ColumnSynthesis:         # reading DOWN a column
    synthesis_id, grain, column_id
    headline
    canonical_quote: Citation | None    # SELECTED from a cell, never written
    agreements, conflicts, outliers, notable, gaps
    comparability                       # derived in Python from respondent_count

class CaseSynthesis:           # reading ACROSS a row
    row_id, through_line, internal_tensions, distinctive, citations

class Finding:                 # reading the whole matrix
    finding_id, kind, statement, grounded_in, citations, confidence
```

```json
{ "quote": "So it was really scalability, asset visibility, and compliance-grade change control.",
  "cell_id": "cell:s:02-current-environment:expert-2", "row_id": "expert-2" }
```

`Position.gist` is a one-line reading of that person's stance — it's what the UI grid shows in
each cell, so the matrix is scannable without opening anything.

The matrix and synthesis are also written to `out/coding.db` (`matrix_column`,
`matrix_column_question`, `matrix_cell`, `matrix_cell_text`, `synthesis_object`,
`synthesis_citation`) from the same in-memory object, so the JSON and the tables cannot drift.
Two views make them easy to query:

```sql
SELECT raw_text FROM coded_text WHERE canonical_question_id = 'q-06-09';
SELECT expert, text FROM matrix  WHERE grain = 'thread' AND column_id = 'col-06-01';
```

---

## What's guaranteed

These hold on every run, and each has a test:

- **Every quote is verbatim.** Checked against the source before an agent's output is accepted;
  a failure returns to the model as a retry. Pass 3 checks against *the cell*, not just the
  file — real words filed under the wrong topic are rejected.
- **No interviewer speech is ever attributed to an interviewee.**
- **One code per turn**, enforced by a primary key.
- **Every eval-section interviewee turn is in exactly one of `coding` or `uncoded`** — never
  both, never neither.
- **A column with one respondent reports no agreement and no conflict**, enforced by a
  validator, not just a prompt.
- **Charting is deterministic** — same database in, byte-identical cells out.

Run counts and cost live in `run.usage`; unverifiable quotes, if any survive retries, are
recorded in `evidence_audit` rather than silently kept.

---

## Exploring it yourself

**[`example.py`](example.py) is the runnable version of everything below** — it runs the
pipeline, then queries the output exactly as shown here.

```bash
uv run synthesize --dry-run     # what would run, and what it would cost

sqlite3 out/coding.db ".tables"
sqlite3 out/coding.db "SELECT method, count(*) FROM coding GROUP BY 1;"
sqlite3 out/coding.db "SELECT reason, count(*) FROM uncoded GROUP BY 1;"
```

```python
from interview_synthesis.synthesis.models import Synthesis

doc = Synthesis.model_validate_json(open("out/synthesis.json").read())
doc.grid("thread")                                    # render-ready matrix
doc.synthesis_for("col-06-06", "thread").conflicts    # where they disagree
doc.cell("thread", "col-06-06", "expert-2").text      # verbatim words
```

Both passes' models are importable, so a downstream consumer reads typed objects rather than
dicts.
