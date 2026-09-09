"""Instructions for the synthesis stages, kept separate so each can be hashed and diffed."""

import hashlib

SHARED_RULES = """
You are synthesizing a framework matrix built from expert interviews. Rows are interviewees,
columns are the questions they were asked, and each cell holds that interviewee's own words,
verbatim.

Your job is SYNTHESIS, not summary. Do not restate what a cell already says - the reader can
see the verbatim text. Your value is in the reading across cells: where interviewees agree,
where they genuinely conflict, who stands apart, and what a reader would otherwise miss.

Rules:

1. QUOTE VERBATIM. Every `quote` must be copied character for character from the cell you
   name in `cell_id`. Do not paraphrase, trim mid-word, tidy punctuation, or merge text from
   two cells. Quotes are checked against the cell and mismatches are rejected.

2. CITE THE RIGHT CELL. `cell_id` and `row_id` must be the cell the words actually came from.
   A quote attributed to the wrong interviewee is worse than no quote.

3. NEVER INVENT AGREEMENT. Agreement and conflict require at least two interviewees who
   actually addressed the point. If only one interviewee has content here, there is nothing
   to compare: return empty `agreements` and `conflicts` and say what the single account
   gives you in `headline` and `notable`.

4. A CONFLICT MUST BE A REAL DISAGREEMENT. Different emphasis is not conflict. Two people
   describing different situations is not conflict unless they make incompatible claims.
   When the difference is explained by their circumstances, say so in `explains_it` and
   consider `nature: "contextual"`.

5. SMALL SAMPLE. This is a handful of interviews. Never describe anything as a trend,
   majority, or statistical result.

6. ABSENCE IS INFORMATION. A question nobody else was asked limits what can be concluded.
   Record that in `gaps` rather than papering over it.
""".strip()

COLUMN_INSTRUCTIONS = f"""
{SHARED_RULES}

Your task: for each column given to you, read DOWN it - across the interviewees - and produce
one synthesis.

- `headline`: what this column tells you, in a sentence or two. The answer a reader wants
  when they ask "what did they say about this?"
- `canonical_quote`: the single best exemplar for this column, SELECTED from one of the
  cells. Choose the quote that most efficiently shows a reader what is going on here -
  concrete, self-contained, and characteristic. Copy it verbatim. Null if no single quote
  does that job.
- `agreements` / `conflicts`: only where two or more interviewees actually addressed the
  point. Give each one's position and a citation.
- `outliers`: an interviewee who stands apart from the others here.
- `notable`: anything else a reader should see - a striking number, an unprompted admission,
  a claim that undercuts an earlier one.
- `gaps`: who was not asked or did not answer, and what that costs the comparison.

Each column's header states how many interviewees have content in it. Respect it: a column
with one respondent gets no agreements and no conflicts.
""".strip()

CASE_INSTRUCTIONS = f"""
{SHARED_RULES}

Your task: read ACROSS one interviewee's row - everything they said, in section order - and
produce one synthesis of them as a case.

- `through_line`: what holds this person's account together. The logic underneath their
  answers, not a list of them.
- `internal_tensions`: places this interviewee contradicts themselves, or where two of their
  own statements sit awkwardly together. Every position in one of these is the SAME person
  at different moments - that is what makes it internal. Cite both sides.
- `distinctive`: what only this case contributes - experience, scale, or a vantage point the
  others do not have.

This reading is what keeps a cell in the context of the whole case, so prefer observations
that need the whole row to see.
""".strip()

FINDINGS_INSTRUCTIONS = f"""
{SHARED_RULES}

Your task: read the whole matrix - the per-column syntheses and the per-case syntheses - and
state the findings that only appear at that level.

A finding must need more than one column or more than one case to see. If it is visible in a
single column, it belongs to that column's synthesis, not here.

- `kind`: `pattern` (recurs across columns), `typology` (cases sort into kinds),
  `tension` (the corpus pulls two ways), `gap` (something the interviews systematically
  failed to establish), `outlier` (a case that resists the pattern).
- `grounded_in`: the synthesis_ids this rests on.
- `confidence`: `well_evidenced` when several interviewees and columns support it;
  `suggestive` when it is a reading rather than a demonstration; `single_source` when one
  interviewee drives it. Be honest - with this few interviews, most findings are suggestive.

You may cite ONLY the quotes shown under "quotable" in the input, reusing their `cell_id`,
`row_id` and quote text exactly as given. They are already verified. Do not write a new
quote and do not cite anything else - if a finding has no suitable quote among them, give it
an empty `citations` list rather than inventing one.

Prefer few, load-bearing findings over many thin ones. Returning nothing is wrong here: the
matrix contains real cross-cutting patterns, and your task is to state them.
""".strip()

STAGE_INSTRUCTIONS = {
    "column": COLUMN_INSTRUCTIONS,
    "case": CASE_INSTRUCTIONS,
    "findings": FINDINGS_INSTRUCTIONS,
}


def stage_digest(stage: str) -> str:
    """Per-stage, so editing one stage's prompt does not invalidate the others' caches."""
    return hashlib.sha256(STAGE_INSTRUCTIONS[stage].encode()).hexdigest()


def prompt_digests() -> dict[str, str]:
    return {name: stage_digest(name) for name in STAGE_INSTRUCTIONS}
