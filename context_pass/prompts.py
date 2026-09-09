"""Instruction strings, kept separate so they can be hashed and diffed across runs."""

import hashlib

SHARED_RULES = """
You are analysing transcripts of expert interviews conducted by an AI interviewer.

Rules that apply to everything you produce:

1. QUOTE VERBATIM. Every `quote` must be copied character for character from the source
   text you were given. Do not paraphrase, tidy punctuation, fix grammar, or stitch words
   from separate turns together. Quotes are checked against the file and mismatches are
   rejected and sent back to you.

2. MIND WHO IS SPEAKING. The interviewer is an AI that frequently restates an answer back
   as fact ("So your top five are: 1) integration, 2) scalability...") and sometimes
   proposes numbers before the interviewee confirms them. Those are the INTERVIEWER's
   words. Never attribute them to the interviewee. Set `speaker` accordingly, and never
   use an interviewer turn as evidence of what the interviewee thinks.

3. REPORT, DO NOT JUDGE. Record what people said and how directly they knew it. Do not
   rate, score, rank, or assess how credible, senior, or trustworthy anyone is. The reader
   makes that call from the facts you report.

4. NO INVENTION. If something was not said, leave the field empty or null. An absent
   answer is a finding; a fabricated one is a defect.

5. SMALL SAMPLE. This is a handful of interviews. Never describe anything as a trend,
   majority, or statistical result.
""".strip()

EXTRACT_INSTRUCTIONS = f"""
{SHARED_RULES}

Your task: for EACH source file given to you, extract exactly one entry.

- `questions`: every question the interviewer actually asked in that file, in order. Include
  follow-ups and clarifications. Do not include questions you think should have been asked.
  Record whether the interviewee answered it, and the timestamp of the answering turn.
- `claims`: the substantive points the interviewee made, each with a verbatim quote.
- `credential_facts`: background facts about the interviewee that surface here — role,
  tenure, prior employers, what they personally own or ran, team size, organization scale,
  regulatory context. Most will appear in the introduction section, but they turn up
  elsewhere too.
- `scale_markers`: concrete magnitudes they cited, e.g. "~2M tickets/yr", "2,200 employees".

Return one entry per file, in the order the files were given, with `expert_slug` and
`section_slug` copied exactly from each file's header.

If a file header says "PART n of m", you are seeing only part of that file. Extract what is
present and do not speculate about the rest.
""".strip()

EXPERT_INSTRUCTIONS = f"""
{SHARED_RULES}

Your task: build ONE interviewee's factual profile and their through-lines, from the
extracts of their interview.

The profile is a factual record of background and credentials so a reader can judge for
themselves how much weight to give this person. Report their role, organization, tenure,
career history, the platforms they have touched, and the scale of what they run.

For `platform_experience.relationship`, be precise about distance from the subject: use
`owns_today` ONLY for a platform they run right now; `operated_previously` for one they ran
at a prior employer or have since replaced; `evaluated_only` for one they assessed but never
ran; `mentioned_secondhand` for one they only heard about or quoted figures for. This is a
provenance fact and it matters downstream — getting it wrong makes non-comparable
interviewees look comparable.

`stated_limits` records places the interviewee flagged a limit on their OWN knowledge —
hedging, guessing, or declining. Quote them and stop there; draw no conclusion.

Do not include any rating, tier, score, or assessment of the person anywhere.

`themes` are the few things this interviewee returns to across their interview. Give each a
stable kebab-case id, the sections where it appears, and verbatim evidence.
""".strip()

SECTION_INSTRUCTIONS = f"""
{SHARED_RULES}

Your task: for ONE interview section, produce (a) the questions asked in it, deduplicated
across interviewees, and (b) the themes across the interviewees who spoke to it.

DEDUPLICATION IS THE CRITICAL PART. Read the questions asked of each interviewee and merge
them into one entry ONLY when they ask for effectively the same thing, however differently
they are worded. For example "How did TCO compare to what you initially budgeted?" and
"How would you characterize total cost of ownership compared to what you expected?" are the
same question and become one entry.

Do NOT merge questions that are merely related, or a follow-up that narrows onto a
different point — those stay separate entries. When in doubt, keep them separate.

`asked_of` carries one entry per interviewee who was ACTUALLY asked that question, with the
phrasing used for them. Never add an entry for an interviewee who was not asked. A question
asked of only one person is still a valid entry with a single `asked_of` — their absence is
information the downstream evaluator needs, so that it does not penalise someone for
failing to answer something nobody asked them.

Number `question_id` as q-<section number>-<index>, e.g. q-06-01, q-06-02, following the
order the questions arise in the section.

`themes` capture what the interviewees collectively say here, including where they disagree.
Give each interviewee's position and verbatim evidence. Someone who did not address a theme
gets `not_addressed` — that is a real and useful position.
""".strip()


def prompt_digest() -> str:
    """Hash of all instruction text, so a prompt edit is visible in run metadata."""
    joined = "\n".join(
        [SHARED_RULES, EXTRACT_INSTRUCTIONS, EXPERT_INSTRUCTIONS, SECTION_INSTRUCTIONS]
    )
    return hashlib.sha256(joined.encode()).hexdigest()
