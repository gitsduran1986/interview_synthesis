# `synthesis` — design notes and trade-offs

Implementation detail for pass 3, the framework matrix and its interpretation. For what it
is and how to run it, see the [root README](../README.md).

This pass implements **stages 6 and 7** of the Gale et al. Framework Method. Passes 1 and 2
had already covered stages 1–5: transcription, familiarisation, framework development (pass
1's canonical questions *are* the analytical framework), and coding.

---

## The division of labour

**Charting is mechanical. Interpretation is the model's job.**

Stage 6 builds the matrix in pure Python — no model call, no cost, deterministic — and cells
carry the interviewee's **verbatim** words. Stage 7 spends the entire model budget on the
reading: agreement, conflict, outliers, patterns.

That split is deliberate. The Framework Method's charting step assumes 15–30 pages per cell,
where summarising is a real reduction. Here the median cell is 38 words. "Summarising" a
38-word answer produces a paraphrase of the same length, costs a call, and inserts a layer
between the reader and what the person actually said. So the chart keeps the words, and the
model does the work only a model can do.

A consequence worth stating: **the canonical quote is *selected*, never written.** Every
quote in the output already existed in a transcript.

---

## The matrix has three levels, and that was a correction

The obvious design is rows = interviewees, columns = the 74 canonical questions. Measured,
that matrix is **58% dense with 44 of 74 columns having a single respondent** — apparently
too sparse to compare.

That sparsity is an artifact. The 74 questions are not 74 codes; they are conversational
threads. Section 06 is typical: `q-06-01` (all three answered) is followed by `q-06-02…05`,
all expert-1, all elaborating the same TCO topic. Treating one conversation as five columns
manufactures four empty ones.

Grouping each question under the nearest preceding question that two or more interviewees
answered — a pure Python heuristic, no model:

| Grain | Columns | Density | Comparable |
|---|---|---|---|
| `section` | 7 | 100% | all 7 |
| **`thread`** | **30** | **93%** | **all 30** (24 all-three, 6 two) |
| `question` | 74 | 56% | 24 of 74 |

**The thread is the comparable unit and what the synthesis runs on.** Sections group threads
for navigation. Questions remain as leaves, so nothing is lost and a reader can still reach
the exact probe that produced a given answer.

This is also closer to what Gale means by a "code": `q-06-01`…`q-06-05` *are* one code,
applied five times.

---

## Trade-offs

### 1. Synthesizing threads and sections, not questions

37 columns get a synthesis (30 threads + 7 sections); the 74 question columns do not. Two
thirds of question columns have one respondent, so a per-question synthesis would spend most
of its calls on columns with nothing to compare — and would invite exactly the fabricated
agreement this pass guards against.

**Cost:** no per-question headline. **Mitigation:** each thread is labelled by its anchor
question, so a question-level reader still gets a synthesis — the one that can actually be
supported.

### 2. Empty cells are materialized, and typed

All 222 question-grain cells exist, including the 94 empty ones. `status` separates a gap in
the *interview* (`not_asked`) from a gap in the *evidence* (`no_answer`).

The distinction needs pass 1: the coding database records only what *was* coded, so it cannot
tell "never asked" from "asked and gave nothing". Pass 1's `asked_of` is the authoritative
record. On this corpus all 94 are `not_asked`, and zero people were asked something and gave
nothing back.

**A blank square in a UI reads as "no opinion". Render the reason.**

### 3. The assent guard — the failure this pass most needed to avoid

Five cells are a bare `"Yes."`, answering a *Confirmation:* question where the interviewer
supplied the substance:

```
q-06-04  "Confirmation: TCO ended up roughly 20-30% higher than expected..."  ->  "Yes."
```

That `"Yes."` is verbatim expert speech and passes every verbatim check. Quoting it as
evidence that TCO ran 20–30% over would attribute the AI interviewer's own words to the
expert. `assent_only` marks such cells unquotable, and the citation validator rejects any
attempt to cite one.

The detector is deliberately narrow — bare agreement only. `"I'd say an 8."` and
`"Probably BMC comes closest."` are short but carry content of their own and stay quotable.
A word-count rule would have wrongly excluded them.

This is a property of *AI-conducted* interviews specifically, and it gets more common with
more of them.

### 4. Citations are checked against the cell, not the file

Pass 1's `check_evidence()` verifies a quote appears in the interviewee's transcript. That is
not enough here: pass 3's claim is "this person said this **about this topic**", and a
file-level check cannot verify the second half — real words filed under the wrong column
would pass.

`check_citations()` verifies the quote is in **the cell it names**, that the cell belongs to
the interviewee it is attributed to, and that the cell is quotable at all. Failures return as
`ModelRetry`.

On the real run: **349 citations, 0 unverifiable, 0 assent or disfluent cells quoted.**

### 5. Comparability is computed, not asked

`comparability` is arithmetic over the roster, so Python derives it. A validator then rejects
any column with fewer than two respondents that reports an agreement or a conflict — the
model is told the rule *and* held to it, because with columns varying from one to three
respondents this is the likeliest fabrication. Zero violations on the real run.

### 6. Findings needed verified quotes handed to them

The findings stage first returned **nothing**. That was a design bug, not a model failure: it
was given only headlines — no cell ids, no quote text — while its schema demanded verbatim
citations. It correctly declined rather than invent one.

The fix is to pass the already-verified canonical quotes into that stage, which may then only
reuse them. It now returns 12 findings. **Worth remembering: a stage that cannot satisfy its
own evidence contract will go quiet rather than fail loudly.**

### 7. Both SQLite and JSON

`out/synthesis.json` is what a UI fetches — one document, no query layer, every object
addressable. The matrix and synthesis tables in `out/coding.db` are what an evaluation agent
or an ad-hoc query uses, and they live in the *same* database as pass 2 because a cell's value
is the join back to `text` → `unit` → `interview`. Both are written from the same in-memory
object, so they cannot drift.

---

## UI addressability

Every object carries a stable id and every reference resolves:

| Object | ID |
|---|---|
| Cell | `cell:{q\|t\|s}:{column_id}:{row_id}` |
| Column synthesis | `syn:{q\|t\|s}:{column_id}` |
| Case synthesis | `syn:case:{row_id}` |
| Finding | `finding:{nn}` |
| Source turn | `text_id` (stable since pass 2) |

`Synthesis` exposes the lookups a UI needs without scanning: `cell(grain, column, row)`,
`synthesis_for(column)`, `columns_in(section)`, `case(row)`, and `grid(grain)` which returns
render-ready column-major cells.

| Screen | Binds to |
|---|---|
| Matrix overview | `grid("section")` or `grid("thread")` + rows/columns |
| Section detail | `ColumnSynthesis(grain="section")` + its threads |
| Thread detail | `ColumnSynthesis(grain="thread")` + cells + `canonical_quote` |
| Cell detail | `MatrixCell` → `SourceRef.text_id` → transcript |
| Interviewee | pass 1 `IntervieweeProfile` + `CaseSynthesis` |
| Findings feed | `Finding[]` |

---

## Scaling

Per-call input is bounded by the column, not the corpus — the largest column prompt is
**2,550 tokens against a 120,000 budget**. Growth adds columns and rows, not bigger prompts.

**The known ceiling is on the output side**, the same shape as pass 1's. A column's synthesis
emits one `Position` per interviewee, so output grows linearly with the roster and will bind
around **8–10 interviewees per column**. Two fixes, neither built yet:

1. Group positions by stance (`stance -> [row_ids]`) instead of one entry per interviewee,
   which removes roster size from the output equation entirely.
2. Shard a column's rows, synthesize each group, and fold — `ColumnSynthesis` is closed under
   merging.

At 1,000 interviews the question matrix is ~74,000 cells: fine in SQLite, too big to ship as
one JSON, so the export would become per-section files.

---

## Known limits

1. **Findings rest on 30 thread columns, and expert-1 is 51% of the corpus.** Any
   cross-cutting pattern is substantially one person's account with two corroborating voices.
   No schema fixes this; the UI should show "2 of 3" beside every claim.
2. **Question ids are positional.** Pass 1's `finalize_questions()` numbers by ordinal, so
   adding a question shifts every later id — and thread ids derive from their anchor question.
   Re-running pass 1 can therefore invalidate saved UI links. A content-derived stable key
   would fix it; `question_key()` already exists in `context_pass/agents.py`.
3. **Thread grouping is a heuristic.** It is inspectable and free, and it produced zero
   single-source columns here, but on messier data it could over-merge and average away a
   real disagreement. The grouping is stored in `matrix_column_question`, so it can be diffed.
4. **Cells are one turn each** under pass 2's timestamp strategy. `code-pass --span-fill`
   would give fuller cells at the price of the extra rows being inference.
5. **Pass 1 used to emit its own themes**, which overlapped with these syntheses. They were
   removed rather than reconciled - this pass reads what people said against each other with the
   cells in front of it, which pass 1 could not do.
