# interview_synthesis

Expert interview transcripts (ITSM platform evaluation), a structured split of them, and a
first-pass context extraction over that split.

## Layout

```
raw/                     original .docx transcripts, untouched
structured/              generated markdown, one dir per interview section
  INDEX.md               interviewee + section map
  01-interview-introduction/
    expert-1.md
    expert-2.md
    expert-3.md
  02-current-environment/
  ... through 09-interview-wrap-up/
scripts/build_structured.py   regenerates structured/ from raw/
context_pass/                 first-pass context extraction (Pydantic AI)
out/first_pass_context.json   the extraction output
```

Sections are section-first rather than interview-first so an evaluation agent can read one
directory and see every interviewee's answers to that section together:

```
structured/04-competitive-comparison/*.md
```

Each file carries frontmatter (`expert`, `role`, `platform`, `section`, `section_slug`,
`source`) and the full Q/A turns with timestamps, so answers stay paired with the
interviewer's question.

Section names are canonical across interviews. The one heading that varies by interviewee —
"Current ServiceNow / BMC Helix / Freshservice Environment" — is normalized to
`02-current-environment`, with the platform recorded in frontmatter.

### Regenerating the structured split

```
python3 scripts/build_structured.py
```

`structured/` is rebuilt from scratch each run; edit the script, not the output. The section
registry lives in `context_pass/sections.py` and is shared with the context pass.

---

## First-pass context extraction

`context_pass/` reads `structured/` and produces one JSON document holding the context a
per-section evaluation agent shouldn't have to re-derive on every run:

1. **Interviewee context** — factual background and credentials: role, tenure, organization
   scale, what they personally own, which platforms are first-hand vs. second-hand, plus a
   one-line `ui_statement` for a card. Reported, never scored — no ratings or verdicts, so a
   reader forms their own view.
2. **Themes by interviewee** — what each person returns to across their interview.
3. **Themes per section, across interviewees** — including where they disagree.
4. **Questions per section** — the questions the interviewer actually asked, merged across
   interviews only where they are effectively the same question. A question put to just one
   person stays its own entry: that absence tells the evaluator not to penalize someone for
   a question nobody asked them.

Every claim carries an `Evidence` record (verbatim quote, speaker, timestamp, source file).

### Running it

```bash
export ANTHROPIC_API_KEY=sk-ant-...

uv run context-pass --dry-run          # plan and measure, spend nothing
uv run context-pass                    # full run -> out/first_pass_context.json
uv run context-pass --only-expert expert-3    # smallest slice, for a cheap first look
```

Useful flags: `--max-input-tokens`, `--units-per-call`, `--concurrency`, `--no-cache`,
`--refresh {extract,expert,section,all}`, `--fail-fast`, `--strict-evidence`.

### How it works

Map-reduce with an explicit token budget, so it does not assume the corpus fits in one call:

```
                 extract (map)              expert reduce -> profile + themes
(expert, section) files  ->  UnitExtract  <
                                            section reduce -> questions + themes
                                                     |
                                        assembly (Python) -> first_pass_context.json
```

* **Stage 1 (map)** extracts questions, claims, and credential facts from each
  `(expert, section)` file. The budgeter packs whole files into calls up to the token
  budget and an item cap — input budget alone isn't enough, because a call that reads
  cheaply can still be asked to write more structured output than `max_tokens` allows.
* **A file too large for one call** is split on *turn* boundaries, never mid-turn, so a
  question is never severed from its answer. The parts are extracted separately and folded
  back together.
* **Stages 2a/2b (reduce)** read the extracts rather than raw transcripts, so their input
  grows with the *number* of units rather than their length. If even that overflows, the
  reduce runs in batches and folds its own partial results with the same agent — the output
  types are closed under merging.
* **Assembly is pure Python.** Question ids, `coverage`, digests, and token accounting are
  computed, never asked of a model.

### Evidence verification

Every quote is checked against the source file before an agent's output is accepted:

1. It must appear **verbatim** in the file it cites.
2. A quote marked `speaker="expert"` must come from an interviewee turn.

Rule 2 matters because the interviewer here is an AI that restates answers back as fact
("So your top five are: 1) integration, 2) scalability..."). Without the check, those
paraphrases get mined as expert claims. Comparison is Unicode-normalized — the transcripts
mix curly quotes and em-dashes with ASCII, and a naive match would reject correct quotes.

Failures come back to the model as a `ModelRetry` naming the offending quotes. Anything
still unverified after retries is recorded in `evidence_audit` rather than silently kept;
`--strict-evidence` makes that a non-zero exit.

### Repeatability

Sampling determinism isn't available — Opus 5 rejects `temperature` and runs adaptive
thinking. Instead the run records hashed inputs (`corpus_digest`, `prompt_digest`,
`schema_digest`, git sha) and caches per-call results keyed on those hashes, so an unchanged
rerun makes zero API calls and editing one prompt re-runs only the affected stage. Per-stage
results are written to `out/stages/` as they succeed, so a late failure never costs earlier
work. A failed unit degrades the run to `status: "partial"` with a warning rather than
discarding everything.

### Tests

```
uv run pytest
```

`tests/conftest.py` sets `ALLOW_MODEL_REQUESTS = False`, so the suite cannot reach a real
model or spend money. Fake models (`TestModel`, `FunctionModel`) cover the retry loop, the
speaker check, the normalizer, chunking and packing, partial failure, and a full end-to-end
run.

### Consuming the output

`context_pass/models.py` is importable, so a downstream evaluator reads the JSON back as
typed objects instead of dicts:

```python
from context_pass.models import FirstPassContext

ctx = FirstPassContext.model_validate_json(open("out/first_pass_context.json").read())
section = ctx.section("06-cost-total-cost-of-ownership")
for question in section.questions:
    print(question.canonical_question, question.coverage)
```
