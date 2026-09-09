"""Pydantic models for the first-pass context extraction.

This pass answers two questions and nothing else: **who are these people**, and **what were
they asked**. Interpretation - themes, agreement, conflict - belongs to pass 3, which does it
against the matrix where the evidence sits. Nothing here is kept unless something downstream
reads it.

Two kinds of model live here:

* **Agent output types** — what a model is asked to produce. Their `Field(description=...)`
  strings become the tool schema the model sees, so they are prompt text, not comments.
* **Assembly types** — built in Python from agent outputs plus measured facts. Never handed
  to a model, because nothing here should be guessed (token counts, digests, arithmetic).

Nothing is fixed-arity: interviewee and section counts are discovered at runtime, so no
`Literal` roster and no `min_length` tied to how many people were interviewed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"

Answered = Literal["answered", "partial", "evaded", "no_answer"]
Position = Literal["agrees", "partially_agrees", "disagrees", "not_addressed"]

# ─────────────────────────── traceability spine ───────────────────────────


class Evidence(BaseModel):
    """A verbatim anchor back to the transcript. Every claim carries one."""

    quote: str = Field(
        min_length=12,
        max_length=600,
        description=(
            "A span copied VERBATIM from the transcript, character for character. Do not "
            "paraphrase, tidy, translate punctuation, or join text across turns. It is "
            "checked against the source file and a mismatch is rejected."
        ),
    )
    speaker: Literal["expert", "interviewer"] = Field(
        description=(
            "Who actually said these words. The interviewer is an AI that often restates "
            "an answer back as fact; such a restatement is NOT the interviewee speaking. "
            "Use 'expert' only for the interviewee's own words."
        ),
    )
    timestamp: str | None = Field(
        default=None,
        pattern=r"^\d{2}:\d{2}:\d{2}$",
        description="The [HH:MM:SS] shown on the turn this quote came from.",
    )
    expert_slug: str = Field(description="Interviewee id, e.g. 'expert-2'.")
    section_slug: str = Field(description="Section id, e.g. '06-cost-total-cost-of-ownership'.")
    source_file: str = Field(description="Repo-relative path exactly as given in the input header.")


# ─────────────────────────── interviewee context ───────────────────────────


class Credential(BaseModel):
    """One factual, sourced fact about the interviewee's background."""

    claim: str = Field(
        max_length=300,
        description=(
            "A single background fact, stated plainly, e.g. 'Ran a ServiceNow environment "
            "at a CDMO for six years'. Report what was said; do not characterize it."
        ),
    )
    kind: Literal[
        "current_role",
        "prior_role",
        "tenure",
        "scope_of_ownership",
        "org_scale",
        "regulatory_context",
        "team_size",
    ]
    evidence: Evidence


class PlatformExperience(BaseModel):
    """A platform the interviewee spoke about, and how close they were to it."""

    platform: str = Field(max_length=80, description="Product name, e.g. 'BMC Helix'.")
    relationship: Literal[
        "owns_today",
        "operated_previously",
        "evaluated_only",
        "mentioned_secondhand",
    ] = Field(
        description=(
            "How direct their exposure is. 'owns_today' only if they run it now. Use "
            "'operated_previously' for a prior employer or a platform they have since "
            "left, and 'mentioned_secondhand' for a platform they only heard about or "
            "quoted figures for. This is a provenance fact, not a judgement of quality."
        ),
    )
    period: str | None = Field(
        default=None, max_length=120, description="Dates or duration, if stated. Else null."
    )
    evidence: Evidence


class IntervieweeProfile(BaseModel):
    """Factual background and credentials. Reported, never scored.

    The reader forms their own view of how much weight to give this person, so this model
    contains no rating, tier, score, or assessment of any kind.
    """

    expert_slug: str
    display_name: str | None = Field(
        default=None, description="First name if they introduce themselves. Else null."
    )
    role_title: str = Field(max_length=200, description="Job title as stated.")
    organization: str | None = Field(
        default=None, max_length=200, description="Employer as stated, or null if not named."
    )
    org_description: str = Field(
        max_length=400,
        description="Size, sector, and regulatory posture of the organization, as stated.",
    )
    tenure: str = Field(max_length=200, description="How long in role / at the company, as stated.")
    background: str = Field(
        max_length=1500,
        description=(
            "Factual recap of their career and platform history in a few sentences. Report "
            "only. Do not assess how credible, senior, or well-qualified they are, and do "
            "not editorialize about the strength of their experience."
        ),
    )
    ui_statement: str = Field(
        max_length=200,
        description=(
            "One line for a UI card: role, organization type, and what they own. Factual "
            "and neutral — a caption, not an endorsement or an evaluation."
        ),
    )
    credentials: list[Credential] = Field(
        min_length=1,
        max_length=12,
        description="Sourced background facts, each with a verbatim quote. At least one - a "
        "profile with nothing sourced is not a profile, and with themes gone this is what "
        "keeps the interviewee findings anchored to the transcript.",
    )
    platform_experience: list[PlatformExperience] = Field(default_factory=list, max_length=12)
    scale_markers: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Concrete magnitudes they cited, e.g. '~2M tickets/yr', '2,200 employees'.",
    )
    stated_limits: list[Evidence] = Field(
        default_factory=list,
        max_length=6,
        description=(
            "Quotes where the interviewee flagged a limit on their OWN knowledge — hedging, "
            "guessing, or declining to answer ('from my prior experience', \"I'm blanking on\"). "
            "Report their words. Draw no conclusion from them."
        ),
    )


# ─────────────────────────── questions ───────────────────────────


class AskedQuestion(BaseModel):
    """A question the interviewer put to one interviewee, as extracted from one file."""

    text: str = Field(
        max_length=400,
        description=(
            "The question as actually asked, lightly trimmed of filler. Only questions the "
            "interviewer really asked — never one you think should have been asked."
        ),
    )
    timestamp: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}:\d{2}$")
    answer_timestamp: str | None = Field(
        default=None,
        pattern=r"^\d{2}:\d{2}:\d{2}$",
        description="Timestamp of the interviewee's answering turn, so a reader can jump to it.",
    )
    answered: Answered = Field(
        description=(
            "Whether the interviewee actually answered it: 'answered' fully, 'partial' in "
            "part, 'evaded' if they changed course, 'no_answer' if nothing came back."
        ),
    )


class QuestionAsking(BaseModel):
    """One interviewee's side of a deduplicated question."""

    expert_slug: str
    as_asked: str = Field(max_length=400, description="The phrasing put to THIS interviewee.")
    timestamp: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}:\d{2}$")
    answer_timestamp: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}:\d{2}$")
    answered: Answered


class SectionQuestion(BaseModel):
    """A question asked in this section, merged across interviews where it is the same question."""

    question_id: str = Field(
        pattern=r"^q-\d{2}-\d{2}$", description="q-<section number>-<index>, e.g. 'q-06-01'."
    )
    canonical_question: str = Field(
        max_length=400,
        description=(
            "One phrasing standing for this question. Merge two questions into one entry "
            "ONLY when they ask for effectively the same thing, however differently worded. "
            "Questions that are merely related, or that narrow a follow-up onto a different "
            "point, stay as separate entries — do not over-merge."
        ),
    )
    asked_of: list[QuestionAsking] = Field(
        min_length=1,
        description=(
            "One entry per interviewee who was ACTUALLY asked this. Never invent an entry "
            "for someone who was not asked — their absence here is the signal that they "
            "were not asked, and it matters downstream."
        ),
    )
    # Set in Python from the roster; see pipeline.finalize_questions.
    coverage: Literal["all_interviewees", "subset", "single_interviewee"] = Field(
        default="subset", description="Derived after the fact. Leave as-is."
    )


# ─────────────────────────── agent output types ───────────────────────────


class Claim(BaseModel):
    statement: str = Field(max_length=500, description="One substantive point the interviewee made.")
    evidence: Evidence


class UnitExtract(BaseModel):
    """Stage 1 output: everything drawn from one (expert, section) file."""

    expert_slug: str
    section_slug: str
    questions: list[AskedQuestion] = Field(default_factory=list, max_length=30)
    claims: list[Claim] = Field(default_factory=list, max_length=25)
    credential_facts: list[Credential] = Field(default_factory=list, max_length=10)
    scale_markers: list[str] = Field(default_factory=list, max_length=10)
    chunk_index: int = 0
    chunk_count: int = 1


class UnitExtractBatch(BaseModel):
    """Stage 1 wraps its output so several units can be extracted in one call."""

    extracts: list[UnitExtract] = Field(
        min_length=1, description="Exactly one entry per source file given to you, in order."
    )


class ExpertPass(BaseModel):
    """Output of the expert stage: one interviewee's factual profile."""

    profile: IntervieweeProfile


class SectionQuestions(BaseModel):
    """Output of the question stage: deduplicated questions and their answering turns.

    Its own agent, and its own output type, so the deduplication and answer-pairing work can
    be evaluated on its own rather than sharing a call (and an output budget) with theme
    generation.
    """

    section_slug: str = ""
    questions: list[SectionQuestion] = Field(default_factory=list, max_length=25)


class SectionPass(BaseModel):
    """One section's deduplicated questions, assembled in Python."""

    section_slug: str
    title: str = ""
    questions: list[SectionQuestion] = Field(default_factory=list, max_length=25)


# ─────────────────────────── assembly (never model output) ───────────────────────────


class SourceFile(BaseModel):
    path: str
    sha256: str
    words: int
    expert_slug: str
    section_slug: str


class StageUsage(BaseModel):
    stage: str
    calls: int = 0
    cached_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None


class UnverifiedQuote(BaseModel):
    location: str = Field(description="Where in the output it sits, e.g. 'expert-1.themes[2]'.")
    quote: str
    source_file: str
    reason: Literal["not_found", "wrong_speaker", "file_missing"]


class EvidenceAudit(BaseModel):
    total: int = 0
    verified: int = 0
    unverified: list[UnverifiedQuote] = Field(default_factory=list)


class RunMetadata(BaseModel):
    schema_version: str = SCHEMA_VERSION
    status: Literal["complete", "partial"] = "complete"
    generated_at: datetime
    model: str
    model_settings: dict[str, Any] = Field(default_factory=dict)
    max_input_tokens: int = 0
    corpus_digest: str = ""
    prompt_digest: str = ""
    schema_digest: str = ""
    git_sha: str | None = None
    roster: list[str] = Field(default_factory=list)
    sources: list[SourceFile] = Field(default_factory=list)
    call_plan: dict[str, int] = Field(
        default_factory=dict, description="Planned calls per stage, from the budgeter."
    )
    usage: list[StageUsage] = Field(default_factory=list)
    total_cost_usd: float | None = None
    wall_clock_s: float = 0.0
    warnings: list[str] = Field(default_factory=list)


class FirstPassContext(BaseModel):
    """Root document. `out/first_pass_context.json` is this, serialized."""

    run: RunMetadata
    interviewees: list[IntervieweeProfile] = Field(default_factory=list)
    sections: list[SectionPass] = Field(default_factory=list)
    evidence_audit: EvidenceAudit = Field(default_factory=EvidenceAudit)

    def section(self, slug: str) -> SectionPass | None:
        return next((s for s in self.sections if s.section_slug == slug), None)

    def expert(self, slug: str) -> IntervieweeProfile | None:
        return next((e for e in self.interviewees if e.expert_slug == slug), None)

