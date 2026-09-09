# interview_synthesis

Expert interview transcripts (ITSM platform evaluation), a structured split of them, and a
first-pass context extraction over that split.

```
raw/                          original .docx transcripts, untouched
structured/                   generated markdown, one dir per interview section
scripts/build_structured.py   regenerates structured/ from raw/
context_pass/                 first-pass context extraction (Pydantic AI)
out/first_pass_context.json   the extraction output
```

---

## 1. Structured transcripts

`structured/` holds one directory per interview section, one markdown file per interviewee
inside it:

```
structured/
  INDEX.md                       interviewee + section map
  01-interview-introduction/
    expert-1.md
    expert-2.md
    expert-3.md
  02-current-environment/
  ... through 09-interview-wrap-up/
```

Section-first rather than interview-first, so an evaluation agent can read one directory and
see every interviewee's answers to that section together:

```
structured/04-competitive-comparison/*.md
```

Each file carries frontmatter (`expert`, `role`, `platform`, `section`, `section_slug`,
`source`) and the full Q/A turns with timestamps, so answers stay paired with the question
that prompted them.

Section names are canonical across interviews. The one heading that varies by interviewee —
"Current ServiceNow / BMC Helix / Freshservice Environment" — is normalized to
`02-current-environment`, with the platform recorded in frontmatter.

**Regenerating:**

```bash
python3 scripts/build_structured.py
```

`structured/` is rebuilt from scratch each run; edit the script, not the output. The section
registry lives in `context_pass/sections.py` and is shared with the context pass.

---

## 2. First-pass context extraction

`context_pass/` reads `structured/` and produces **one JSON document** holding the context a
per-section evaluation agent shouldn't have to re-derive on every run:

| Output | What it is |
|---|---|
| **Interviewee context** | Factual background and credentials — role, tenure, org scale, what each person personally owns, and whether each platform is first-hand or second-hand. Plus a one-line `ui_statement` for a card. Reported, never scored. |
| **Themes by interviewee** | What each person returns to across their own interview. |
| **Themes per section** | What the interviewees collectively say in each section, including where they disagree. |
| **Questions per section** | The questions the interviewer actually asked, merged across interviews only where they are effectively the same question. |

Every claim carries verbatim evidence — quote, speaker, timestamp, and source file — and
each quote is verified against the transcript before it is accepted.

### Running it

```bash
export ANTHROPIC_API_KEY=sk-ant-...      # or put it in .env (gitignored)

uv run context-pass --dry-run            # plan and measure, spend nothing
uv run context-pass                      # full run -> out/first_pass_context.json
uv run context-pass --only-expert expert-3    # smallest slice, for a cheap first look
```

Other flags: `--max-input-tokens`, `--units-per-call`, `--concurrency`, `--no-cache`,
`--refresh {extract,expert,section,all}`, `--fail-fast`, `--strict-evidence`.

Results are cached per call, so an unchanged rerun makes **zero API calls**. The current run
covers 24 files across 3 interviewees and 7 evaluated sections in 16 calls, with 269 of 269
citations verified.

### Shape

Map-reduce with an explicit token budget, so it keeps working when the corpus outgrows a
single model call:

```
                 extract (map)               expert reduce  -> profile + themes
(expert, section) files  ->  UnitExtract  <
                                             section reduce -> questions + themes
                                                       |
                                          assembly (Python) -> first_pass_context.json
```

Transcript files are packed into calls against a measured token budget; a file too large for
one call splits on *turn* boundaries so a question is never severed from its answer; the
reduce stages read extracts rather than raw text, and fold hierarchically if even those
overflow. Question ids, coverage, digests, and token accounting are computed in Python, never
asked of a model.

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

### Tests

```bash
uv run pytest
```

46 tests, zero tokens spent — `tests/conftest.py` sets `ALLOW_MODEL_REQUESTS = False`, so the
suite cannot reach a real model.

> **Design notes and trade-offs: [`context_pass/README.md`](context_pass/README.md)** — why
> the pipeline is decomposed the way it is, what each decision cost, why credibility is
> reported rather than scored, why questions are extracted rather than synthesized, and the
> known output-side scaling ceiling (~8–10 interviewees per section) with two fixes for it.
