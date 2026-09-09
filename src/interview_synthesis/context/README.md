# `context_pass` — design notes and trade-offs

Implementation detail for the first-pass context extraction. For what it is and how to run
it, see the [root README](../../../README.md).

This document exists because most of the decisions here traded one real thing away for
another, and the reasoning is not recoverable from the code.

---

## Contents

- [Pipeline shape](#pipeline-shape)
- [Module map](#module-map)
- [Trade-offs](#trade-offs)
- [Known ceiling](#known-ceiling-output-not-input)
- [Extending it](#extending-it)

---

## Pipeline shape

```
                 extract (map)               expert reduce  -> profile + themes
(expert, section) files  ->  UnitExtract  <
                                             section reduce -> questions + themes
                                                       |
                                          assembly (Python) -> first_pass_context.json
```

Three agents, defined in `agents.py`, all `anthropic:claude-opus-5`:

| Agent | Runs | Input | Output |
|---|---|---|---|
| `extract` | one per packed batch of files | raw transcript turns | `UnitExtractBatch` |
| `expert` | one per interviewee | that person's extracts | `ExpertPass` |
| `section` | one per evaluated section | that section's extracts | `SectionPass` |

Both reduces depend only on the map stage, so they run concurrently.

## Module map

| File | Responsibility |
|---|---|
| `sections.py` | Canonical section registry. Stdlib only — `scripts/build_structured.py` imports it, and must stay dependency-free. |
| `corpus.py` | Discovers and parses `structured/`. Turn-level parsing, `normalize()`, sha256 digests. |
| `budget.py` | Token counting, packing, turn-boundary chunking. The scaling logic lives here. |
| `models.py` | Every Pydantic model. Importable by downstream consumers. |
| `prompts.py` | Instruction strings, separate so they can be hashed and diffed. |
| `agents.py` | The three agents and their validators. |
| `pipeline.py` | Stage orchestration, caching, persistence, assembly. |
| `cli.py` | Argument parsing, `.env` loading, credential check, reporting. |

---

## Trade-offs

### 0. What this pass deliberately does not do

It used to emit 22 interviewee themes and 50 section themes. Nothing read them. Not the coding
pass, not the synthesis, not the UI - and they were **62% of the output file** and the single
most expensive stage in the pipeline.

They existed because pass 1 was built before pass 3. Once the framework matrix arrived, the
same job was being done twice, and done better the second time: pass 3's column syntheses read
what people said *against each other* with the cells in front of them, and its case syntheses
read one person across the whole matrix. Pass 1 was guessing at the same conclusions from
extracts, with no matrix to check them against.

So the theme stage was removed outright. What remains is what something downstream actually
reads, plus the interviewee findings:

| Output | Read by |
|---|---|
| Canonical questions + `asked_of` | `coding/codebook.py` (the label space and the answer anchors) |
| Interviewee profiles | `synthesis/matrix.py` (row labels) and the UI; the factual findings about each person |

**The cost:** if you ever want pass 1's output on its own, without running pass 3, it now tells
you who and what-was-asked and stops there. That is the right trade - two passes producing
overlapping interpretations is worse than one producing it well.

### 1. Map-reduce over a single whole-corpus call

The corpus is ~28k measured tokens against a 1M context window. Everything could go in one
call, and that would be cheaper and faster.

**Chose decomposition anyway, for three reasons:**

- **Output budget.** One call producing every artifact has to spread a single `max_tokens`
  across all of it. That is where structured outputs go shallow — fields get stubbed,
  evidence gets dropped, and nothing errors.
- **Input skew.** Expert 1 is 55% of this corpus by word count. A single whole-corpus call
  produces Expert-1-flavoured "themes" and labels them consensus. Per-unit extraction gives
  every interviewee equal footing regardless of how much they talked.
- **It has to keep working.** The corpus is expected to grow past one call. Building the
  split now means there is no rewrite later — only different numbers coming out of the
  budgeter.

**Cost:** ~16 calls instead of 1, and a full uncached run at $3.22 instead of well under a
dollar. Caching makes reruns free, which is what makes this acceptable.

### 1b. Question extraction is its own stage

Deduplicating questions and pairing each with the turn that answered it used to share one call
with theme generation. It became a separate agent (`question`) with its own prompt, cache, and
output type; when themes were dropped it stayed separate, and the reasons still hold.

**Why the split is worth an extra call per section:**

- **Evaluability.** The pairing is the input to the whole coding pass downstream. When it
  shared a call with themes, there was no way to assess it on its own — no isolated output to
  score, and no way to change its prompt without disturbing theme quality. Now there is.
- **Output budget.** Two artifacts competing for one `max_tokens` is where structured output
  goes shallow. Each stage now spends its own.
- **Prompt focus.** Each stage's instructions say "this is the only thing you are doing",
  which measurably tightens both.
- **Independent caches.** Stage digests are per-stage (`prompts.stage_digest`), so editing the
  question prompt re-runs only the question stage. Under a single global digest, any prompt
  edit invalidated everything including the extract stage.

**Cost:** one extra call per section — 7 more on this corpus. **Also added:** a validator that
rejects any `answer_timestamp` not pointing at a real interviewee turn, catching a bad join key
where it is created rather than three stages later.

### 2. Reduces read extracts, not raw transcripts

The reduce stages consume model-produced `UnitExtract` JSON rather than the original text.

**Why:** it decouples reduce input size from transcript *length*. A 50k-word interview
costs the reduce the same as a 2k-word one, because both compress to a ~2k-token extract.
Without this, the reduce stage would inherit the scaling problem the map stage just solved.

**Real cost, and it is the sharpest trade in the design:** anything the extractor misses is
invisible to everything downstream. The section agent cannot notice a nuance that never made
it into an extract. Recall at stage 1 is therefore load-bearing, which is why `UnitExtract`
asks for questions, claims, credential facts, and scale markers separately rather than a
prose summary — enumerated fields resist compression better than free text.

The evidence spine partly compensates: because every extract item carries a verbatim quote
with a source file and timestamp, a reader chasing a claim lands back in the transcript.

### 3. Verbatim quote verification, enforced in code

Every `Evidence` is re-checked against the source file before an agent's output is accepted
(`check_evidence` in `agents.py`). Two rules: the quote must appear verbatim, and a quote
marked `speaker="expert"` must come from an interviewee turn.

**Why the second rule specifically:** the interviewer here is an AI that restates answers
back as fact ("So your top five are: 1) integration, 2) scalability...") and sometimes
proposes numbers before the interviewee confirms them. Those paraphrases are quotable,
fluent, and *wrong* to attribute. Nothing but a structural check catches that reliably.

**Costs accepted:**
- **Retry tokens.** A rejection sends the output back with `ModelRetry`. Budget for it.
- **False rejections are worse than they look.** The transcripts mix curly quotes and
  em-dashes with ASCII, so a naive substring match rejects *correct* quotes and burns the
  retry budget on nothing. `normalize()` (NFKC, quote/dash folding, whitespace collapse,
  casefold) exists entirely for this, and there is a regression test asserting a correct
  smart-punctuation quote passes.
- **It does not verify inference.** A real quote can still be attached to a claim it does
  not support. The check bounds fabrication, not reasoning.

The audit is run twice: as a validator (retryable) and again over the assembled document,
so cached results are re-verified rather than trusted.

### 4. Report background; do not score credibility

`IntervieweeProfile` carries no rating, tier, or assessment. It records role, tenure, org
scale, what each person personally owns, and `platform_experience.relationship` marking each
platform `owns_today` / `operated_previously` / `evaluated_only` / `mentioned_secondhand`.

**Why:** a model-generated "credibility: 4.5/5" manufactures precision the transcripts do
not support, and it launders a judgement the reader should be making. Provenance is a fact
and stays; a verdict is not and goes.

**Cost:** nothing numeric to sort or filter a UI on. `ui_statement` (a factual one-liner)
and `platform_experience.relationship` are the fields to build affordances from instead.

This one earns its keep. Expert 1's ServiceNow experience is at a *former* employer while
his current platform is a custom ITSM; the other two own their platforms today. A single
credibility score would flatten that into "all credible" and quietly make three
non-comparable people look comparable. `relationship` keeps the distinction visible.

### 5. Extract the questions asked; do not synthesize a rubric

`SectionQuestion` records what the interviewer actually asked, not what they should have.

**Why:** a rubric derived from the same transcripts it grades is circular — every question
scores as covered by construction. A genuine rubric needs an external anchor (the client
brief), and there isn't one in this repo.

**Cost:** the output is descriptive. It tells a downstream evaluator what was asked and who
answered; it does not say what a good answer looks like. If a brief ever lands, the natural
addition is a fourth agent mapping brief items onto these extracted questions — the join key
already exists.

Extraction is also faithful to noise: the interviewer's confirmation turns ("Confirmation:
TCO ended up roughly 20–30% higher...") come through as questions, because they were asked.

### 6. Merge questions only when effectively identical

The instruction is to merge two questions **only** when they ask for the same thing, and
otherwise leave them separate.

**Why asymmetric:** over-merging destroys information irrecoverably — two distinct questions
collapsed into one entry cannot be split apart downstream. Under-merging leaves a duplicate
a reader can spot and ignore. When in doubt, keep separate.

A deterministic backstop catches the unambiguous case: `_dedup_validator` rejects any two
`canonical_question`s in a section whose `question_key` (normalized, punctuation-stripped)
matches, so "compare to budget?" and "compare to budget." cannot both survive.

**Observed consequence:** 57% of extracted questions were put to only one interviewee. That
is not a defect — the interviewer's follow-ups genuinely diverged per interview — but it
means a naive reader will over-read `single_interviewee` entries as gaps. They are the
signal that stops an evaluator penalizing someone for a question nobody asked them.

### 7. Arithmetic in Python, judgement in the model

`coverage`, `question_id` numbering, duplicate-asking collapse, digests, and token accounting
are computed in `pipeline.py`. The model is never asked for them.

**Why:** models miscount. Anything derivable from data already in hand is cheaper, exactly
right, and free to recompute — which is what let the duplicate-`asked_of` fix regenerate the
whole document from cache at zero cost.

`_collapse_repeat_askings` is the example worth knowing: the interviewer sometimes re-asks
the same question to the same person after "Can you repeat the question?", which is faithful
to the transcript but breaks the one-entry-per-interviewee contract. Python collapses it,
keeping the more informative asking.

### 8. Tool output over `NativeOutput` / `PromptedOutput`

Pydantic AI's default (tool output) is used deliberately.

**Why:** tool output returns a schema-invalid or validator-rejected response to the model as
a correctable error *in-conversation*. That is the mechanism `ModelRetry` rides on, and the
evidence validator is the most valuable component here. `PromptedOutput` would give a parse
failure and a full re-run instead of a targeted correction. `NativeOutput` offers
grammar-level guarantees that these deeply nested, constraint-heavy schemas do not need —
the failure mode is semantic (fabricated quotes), which no output mode prevents.

### 9. Caching for repeatability, since sampling determinism is unavailable

Opus 5 rejects `temperature` and `top_p`, and runs adaptive thinking. Identical inputs do not
guarantee identical outputs, and pretending otherwise would be false comfort.

**Instead:** hashed inputs (`corpus_digest`, `prompt_digest`, `schema_digest`, git sha) in
`RunMetadata`, and a content-addressed cache keyed on
`sha256(model ‖ prompt_digest ‖ schema_digest ‖ payload)`. An unchanged rerun makes zero API
calls and is byte-identical except for timestamps. Editing one prompt re-runs only the
affected stage.

**Cost:** the cache can hide a stale result if something changes that isn't in the key —
transcript content and prompts are covered, but a `models.py` edit only invalidates via
`schema_digest`, which moves when the *schema* moves, not when a docstring does. `--no-cache`
and `--refresh <stage>` are the escape hatches.

### 10. Partial results by default

Every stage is `asyncio.gather(..., return_exceptions=True)`. A failed unit records a warning,
degrades the run to `status: "partial"`, and is absent from its list.

**Why:** on a large corpus, discarding fifteen good calls because the sixteenth 429'd is
indefensible. Per-stage results are written to `out/stages/` the moment they succeed, so a
late failure never costs earlier work.

**Cost:** a partial document is easy to mistake for a complete one. `status`, `warnings`, and
the `evidence_audit` counts exist to make that loud; `--fail-fast` inverts the default.

### 11. `units_per_call = 4`

The packer caps files per extract call *in addition to* the token budget.

**Why:** input size is not the binding constraint. A call that reads cheaply can still be
asked to write more structured output than `max_tokens` allows, and the model will stub
fields rather than error. This was a real bug — at a 120k budget the packer originally put
all 24 files in one call.

**Cost:** more calls than strictly necessary on small corpora. Raise it if you also raise
`max_tokens`; the two move together.

### 12. Opus 5 for every stage

The map stage is mechanical enough that a cheaper model would plausibly do it.

**Chose not to split** on the principle that model downgrades are a deliberate decision made
with measurements, not a default. The stage-1 extracts are what everything downstream reads
(see trade-off 2), so recall there is the highest-leverage thing in the pipeline. If cost
becomes a concern, stage 1 is the right place to experiment — and the cache means an A/B on
one stage is cheap.

### 13. Sections 01 and 09 are treated asymmetrically

`01-interview-introduction` feeds profiles but is not evaluated as a section;
`09-interview-wrap-up` is dropped everywhere (`sections.py`).

**Why:** 01 looks like pleasantries and is not — it is where every interviewee states their
tenure, prior employers, and prior platforms, which is the entire factual basis for the
credentials output. 09 genuinely is sign-off. Encoded in the registry rather than a prompt so
it is testable.

---

## Known ceiling: output, not input

Input scaling is solved; output is not, and it binds far earlier.

`SectionQuestion.asked_of` carries one entry per interviewee at roughly 300 tokens each, so
section-reduce output grows linearly with roster size:

| Interviewees / section | Est. output tokens | |
|---|---|---|
| 3 (today) | ~6,600 | fits `max_tokens=16000` |
| 25 | ~55,000 | exceeds |
| 100 | ~220,000 | exceeds |

**It breaks at roughly 8–10 interviewees per section** — long before the input budget would
complain, and the hierarchical merge does not rescue it, because the merged result still has
to contain every entry. `SectionTheme.expert_positions` and the `max_length` caps on
`questions` bite around the same point.

Two fixes, cheapest first:

1. **Raise `max_tokens` to 64k and stream.** Opus 5 supports 128k output when streaming.
   Buys roughly 4×. A change to `DEFAULT_SETTINGS` plus switching `_call` to `.stream()`.
2. **Shard the section reduce by interviewee group.** Each call returns questions for its
   group only; merge `asked_of` lists in Python keyed on `question_key` (which already
   exists in `agents.py`). This removes roster size from the output equation entirely and is
   the version that actually scales.

Below ~8 interviewees per section, neither is worth the machinery.

---

## Extending it

**Adding a section** — add it to `SECTIONS` in `sections.py`, and to `PROFILE_ONLY` or `SKIP`
if it should not be evaluated. Nothing else hardcodes the list.

**Adding interviewees** — nothing to change. The roster is discovered from the filesystem in
`corpus.py`; no schema uses a fixed-arity constraint or a `Literal` roster.

**Changing what is extracted** — edit the model in `models.py`. `Field(description=...)` *is*
the prompt: those strings become the tool schema the model sees, so they carry more weight
than the instructions in `prompts.py`. Changing a schema moves `schema_digest` and
invalidates the cache automatically.

**Consuming the output** — import `models.py` rather than parsing dicts:

```python
from context_pass.models import FirstPassContext

ctx = FirstPassContext.model_validate_json(open("out/first_pass_context.json").read())
ctx.section("06-cost-total-cost-of-ownership").questions
ctx.expert("expert-2").platform_experience
```

**Tests** — `uv run pytest`, 46 tests, zero tokens. `tests/conftest.py` sets
`ALLOW_MODEL_REQUESTS = False` so the suite cannot reach a real model. `TestModel` covers
wiring; `FunctionModel` covers the retry loop, the speaker check, and the normalizer;
`test_budget.py` exercises the fanned-out scaling paths without needing a large corpus.
