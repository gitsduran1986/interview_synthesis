# `coding` — design notes and trade-offs

Implementation detail for pass 2, text coding. For what it is and how to run it, see the
[root README](../README.md).

---

## Two strategies

`--strategy timestamp` (default) joins pass 1's answer anchors to transcript turns.
`--strategy model` has the coding agent judge each turn from the transcript, seeing only the
label space.

|  | `timestamp` | `model` |
|---|---|---|
| Cost in this pass | **$0** | $0.40 (≈$95 at 1,000 interviews) |
| Runtime | instant | ~40s |
| Reproducible given the same codebook | yes | no |
| Coverage | 128/135 turns (95%) | 128/135 (95%) |
| Where the LLM judgement lives | pass 1's question stage | here |
| Survives pass 1 dropping `answer_timestamp` | no | yes |

**Neither strategy is deterministic end to end.** The timestamp join is a deterministic
*materialization*, but the anchors it replays were produced by an LLM in pass 1's question
stage. Calling it "deterministic coding" would hide where the judgement actually happened.

What makes the join trustworthy is not that it avoids a model — it does not — but that the
step which made the pairing is **its own first-class stage**: separate agent, prompt, cache,
output type, and a validator rejecting any answer timestamp that is not a real interviewee
turn. The quality question is asked where the pairing is made, not where it is copied.

They agree on 100% of the 124 turns both code. See [Risks of the timestamp strategy](#risks-of-the-timestamp-strategy).

---

## Why the model strategy exists

The timestamp join replays pass 1's own answer/question pairing. The model strategy exists so
that pairing can be produced — and checked — without pass 1:

1. **Evaluability.** When pass 2 replays pass 1's pairing, the coding step cannot be assessed
   on its own: pass 1 already did the work, and its errors propagate invisibly.
2. **The dependency is being removed.** Pass 1's answer/question pairing is slated to go. When
   it does, the timestamp join yields nothing and this becomes the only strategy that works.

The boundary is enforced in the codebook's shape. `coding/codebook.py` splits pass 1 into two
fields: `questions` (the label space — `{question_id, section_id, canonical_question}`, **5.4%
of the first pass**) and `anchors` (pass 1's pairing). The model coder is only ever shown
`questions`, and a test asserts no anchor timestamp appears anywhere in its prompt.

### What the comparison showed

Measured against pass 1's implicit coding on the current corpus (a one-off comparison, not a
dependency):

```
overlapping turns            124
agreement on label      124/124 = 100%
disagreements                  0
turns pass 1 coded that pass 2 declined   4
```

Those 4 are all disfluencies — `"Can you repeat the question?"`, `"Sorry, can you repeat the
question?"`. Pass 1 anchored them as the *answers* to their questions, because it models a
re-ask as its own canonical question. Pass 2 declined to code them, so
`SELECT raw_text WHERE canonical_question_id='q-06-07'` returns substance rather than a
request for clarification. The independent pass is the more useful of the two on exactly the
rows where they differ.

---

## Risks of the timestamp strategy

This is the current default. It is the right call for a POC — free, instant, reproducible —
but it carries real exposure, in rough order of how likely each is to bite.

### 1. The judgement moves upstream; it does not disappear

The join replays pass 1's question stage, so *this pass* has no opinion to be wrong about. The
LLM decision still exists — it just lives in pass 1, and that is where it must be evaluated.

This is fine **because the question stage is now first-class**: its own agent and prompt, its
own cache, its own output type (`SectionQuestions`), and a validator that rejects an answer
timestamp not pointing at a real interviewee turn. Before that split it shared a call with
theme generation, and there was no way to assess the pairing on its own.

**Residual risk:** a pass-1 pairing error still becomes a database row here with no local
signal. **Mitigation:** `--strategy model` gives a genuine second opinion for $0.40 across the
whole corpus — cheap enough to re-run whenever pass 1's question prompt changes.

### 2. It depends on a field slated for removal

The moment `answer_timestamp` leaves pass 1, `codebook.anchors` is empty and the join produces
**zero rows** — not an error, just an empty database. `--strategy model` is the fallback, kept
working and tested for exactly this reason.

**Mitigation:** the run reports the anchor count up front (`anchors: 128 from pass 1`), so a
drop to zero is visible rather than silent.

### 3. Disfluency turns get coded as answers — measured, and real

Pass 1 models a re-ask as its own canonical question, so `"Can you repeat the question?"` is
recorded as the answer to the question that prompted it. Four rows in this corpus:

```
q-02-03  "Can you repeat the question?"
q-02-09  "Could you repeat the question?"
q-06-01  "Can you repeat the question?"
q-06-07  "Sorry, can you repeat the question?"
```

A consumer running `SELECT raw_text WHERE canonical_question_id='q-06-07'` gets a request for
clarification instead of the answer. **The model strategy gets these right** — it declined to
code all four. This is the one place the two strategies measurably differ in quality.

**Mitigation:** `is_disfluent = 1` marks them; filter with `WHERE is_disfluent = 0`. The flag is
narrow on purpose — it does *not* catch bare affirmatives, because a lone `"Yes."` is the
complete, correct answer to a confirmation question, and five turns here are exactly that.
Flagging those would tell consumers to discard real answers.

### 4. Coverage is 95%, and the 5% is not all noise

Seven interviewee turns carry no anchor. Four are back-channel, but three are substantive
content (43, 25 and 15 words) that a downstream evaluator will simply never see — including
expert-1's explanation that internal validation effort added 20-30% to cost.

**Mitigation:** `--span-fill` forward-fills from the preceding anchor, reaching 100%. Those rows
are *inference*, recorded as `method='span'` so they are never confused with pass 1's claims.
Off by default because inference should be opted into. Note a bad anchor can then mislabel a
run of turns rather than one.

### 5. `answer_timestamp` is model-produced and unvalidated upstream

Pass 1's `audit_evidence()` verifies quote strings but never checks that an answer timestamp
resolves to a real turn. All 128 resolve today — that is good model behaviour, not an enforced
invariant. A future pass-1 run emitting `00:39:51` instead of `00:39:50` would drop that
question's answer.

**Mitigation:** every unresolvable anchor is reported as a run warning naming the question, so
it fails loudly here. The better fix is a validator in `pipeline.finalize_questions()` where
the timestamp is created — cheap, zero-token, and not yet done.

### 6. The join key is unenforced

`(expert, section, timestamp)` must be unique within a transcript. Zero collisions across all
24 files today, but nothing in pass 1 guarantees it. Two turns in the same second make the
mapping a coin flip.

**Mitigation:** the joiner detects duplicates, skips the ambiguous anchors rather than guessing,
and warns.

### 7. One anchor per (question, interviewee) caps answers at one turn each

You asked for multi-turn answers to be kept — "if expert-1 has 3 separate coded responses to one
question, that's ok". The timestamp join cannot produce that: pass 1 emits exactly one anchor
per question/interviewee pair, so a multi-turn answer collapses to whichever turn was anchored.
`--span-fill` restores the continuation turns; the model strategy does it natively.

### 8. Transcript regeneration breaks the join

Re-exporting the `.docx` files with shifted timestamps invalidates every anchor at once. Row
identity (`text_id`) is safe — it is derived from file and turn position, not from the clock —
but the *anchors* are timestamp-keyed and would all miss.

**Mitigation:** the codebook carries `corpus_digest` and the run refuses to proceed against a
corpus it was not built from, unless `--allow-stale`.

---

## Trade-offs

### 1. Which strategy is the default

`timestamp` is the default today: free, instant, reproducible, and simple enough to reason
about completely (the judgement it replays is evaluated upstream, in pass 1's question stage). `model` costs $0.40 and buys independent evaluability plus survival of pass 1
being pared back. The risks above are the price of the default; they are all either mitigated
or visible in the run output.

If the model strategy becomes the default later and cost bites, the levers in order:
`--model claude-haiku-4-5` (constrained classification over a closed label set is a good Haiku
job), then the Batch API's 50% discount — this stage is not latency-sensitive.

### 2. One code per turn

Enforced at the database level: `coding.text_id` is the primary key. Many rows may share a
`question_id`, so a re-ask producing three coded responses to one question is represented
faithfully rather than collapsed.

Justified by the measured turn-size distribution:

```
p25 14   median 34   p75 56   p90 76   p99 113   max 219 words
>100w: 5 turns (3.4%)   >200w: 1 turn (0.7%)
```

A turn is about a paragraph. At that size it rarely spans two questions, so sentence-level
segmentation would add a splitting step and more rows for precision the data does not need.
**Revisit if p90 climbs past ~150 words** — `--stats` prints this distribution every run.

### 3. SQLite, with JSONL as the committed artifact

SQLite is stdlib, so no new dependency, and its constraints do real work rather than
decorate: `FOREIGN KEY` on `question_id` makes a hallucinated label a database error instead
of a silent bad row, and the `coding` primary key enforces one-code-per-turn structurally.
The consumer is a downstream agent, and one `sqlite3 out/coding.db "SELECT ..."` beats
loading a blob into context.

The DB is binary and derived, so it is gitignored; `coding.jsonl` — the five promised columns,
sorted by `text_id` — is what gets committed and diffed.

Not DuckDB: it wins past ~10M rows and costs a 30MB dependency here. The migration stays open
(DuckDB attaches SQLite directly) so nothing is foreclosed.

### 4. `text_id` from file plus turn position

`sha256(unit_id || turn_index)` — no timestamp, no content hash. A content hash would orphan a
row's coding when a typo is fixed; a timestamp would tie identity to a clock value that is not
meaningful here. Position is stable under wording edits, and `text_sha` carries the content
signal separately so an edit is detectable without changing identity.

### 5. Interviewer turns stored, never coded

Doubles the row count (291 vs 147) and requires a `speaker_role` filter in queries. Bought:
the coder sees the question that preceded an answer, a reviewer can audit a label against what
was actually asked, and the database stops depending on pass 1 recording question wording —
which matters given pass 1 is being pared back.

### 6. Uncoded text is a table, not a NULL

`coding.question_id` stays `NOT NULL` with a real foreign key, and anything uncodeable goes to
`uncoded` with a `reason`. A turn that answers nothing is a finding, not an absence:

```
non_eval_section        12   (section 01 — stored, deliberately not coded)
no_question_addressed    7   (back-channel: "Yeah.", "Go ahead.")
```

Invariant, asserted in tests and verified on real data: every eval-section interviewee turn is
in **exactly one** of `coding` or `uncoded` — never both, never neither.

### 7. Batched by section, which is what makes it scale

A call carries one section's questions plus a few of that section's transcripts. Per-call
context is bounded by section size, never by corpus size:

```
largest prompt   4,874 tokens (measured)  of a 120,000 budget
```

Adding interviews adds *calls*, not context — `tests/test_coding.py` asserts the prompt for a
500-interview corpus is the same size as for 3, which is what stops someone "helpfully" adding
the whole codebook or a corpus summary to the prompt. `units_per_call` caps batch size
independently of tokens, because structured *output* is the binding constraint, not input.

---

## Known limits

1. **6 of 72 canonical questions are re-asks or confirmations** (`"Restated: ..."`,
   `"Re-ask: ..."`, `"Confirmation: ..."`). They are legitimate labels, but a downstream count
   of *distinct questions answered* reads high — ~65 for expert-1 where the substantive figure
   is nearer 59. A `restates` edge in the codebook would fix it; not needed for coding itself.
2. **The corpus is imbalanced** — expert-1 holds 51% of coded turns (65 vs 33 and 30). Any
   per-question cross-expert comparison is dominated by one interviewee. `--stats` reports it.
3. **The label space drifts as the corpus grows.** Pass 1 keeps discovering new canonical
   questions, so coding run N and N+1 are not directly comparable. The real fix is a frozen
   codebook that new interviews are coded *against*, with a review path for genuinely new
   questions. `out/codebook.json` is where that would live.
4. **Confidence is model-reported** and not calibrated. Useful for sorting a review queue,
   not as a probability.

---

## Module map

| File | Responsibility |
|---|---|
| `codebook.py` | `FirstPassContext` → `Codebook` projection; digest; the enforced pass-1 boundary |
| `store.py` | DDL, connection, upserts, `export_jsonl()`, `stats()` |
| `ingest.py` | Corpus → `interview`/`unit`/`text` rows; `text_id()` |
| `coder.py` | Instructions, output models, prompt builder, validator, agent |
| `pipeline.py` | Call planning, orchestration, persistence |
| `cli.py` | `code-pass` entrypoint |

Reused from `context_pass/` rather than rewritten: `corpus.load()` and `Unit`/`Turn` (the only
transcript parser), `normalize()` (so `text_sha` folds identically to the evidence checker),
`budget.pack()` (batching), `runner.call()` (caching, persistence, non-fatal failure),
`sections.*` (which sections are evaluable).

`runner.py` was lifted out of `context_pass/pipeline.py` so both passes share one cache
implementation; the cache key formula is unchanged, verified by pass 1 still hitting all 16
cached calls afterwards.
