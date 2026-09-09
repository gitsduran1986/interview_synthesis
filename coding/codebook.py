"""The codebook: the label space pass 2 codes against.

This is the ONLY thing the coder takes from the first pass. It is deliberately a narrow
projection rather than an import of `FirstPassContext`: the coder must not depend on
themes, profiles, credentials, or evidence, so changes there cannot break it.

It carries two separable things:

* `questions` — the label space. This is all the *model* coder ever sees.
* `anchors` — the first pass's own view of which turn answered which question, taken from
  `asked_of[].answer_timestamp`. Only the *timestamp* coder reads these.

The split is deliberate. Anchors are pass 1's implicit coding, so a strategy that uses them
is joining rather than judging, and cannot be evaluated independently of pass 1. Keeping
them in a separate field (and out of the model prompt) is what keeps that boundary visible
rather than accidental.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from context_pass.models import FirstPassContext

SCHEMA_VERSION = "1.0"


class CodebookQuestion(BaseModel):
    question_id: str
    section_id: str
    canonical_question: str


class Anchor(BaseModel):
    """Pass 1's claim that one interviewee answered one question at one timestamp."""

    question_id: str
    section_id: str
    expert_slug: str
    answer_timestamp: str


class Codebook(BaseModel):
    """The flat label space, plus enough provenance to detect staleness."""

    schema_version: str = SCHEMA_VERSION
    generated_at: datetime
    source: str
    corpus_digest: str
    questions: list[CodebookQuestion] = Field(default_factory=list)
    anchors: list[Anchor] = Field(
        default_factory=list,
        description="Pass 1's own answer/question pairing. Read only by the timestamp "
        "strategy; never shown to the model coder.",
    )

    @classmethod
    def from_first_pass(cls, ctx: FirstPassContext, source: str) -> "Codebook":
        return cls(
            generated_at=datetime.now(timezone.utc),
            source=source,
            corpus_digest=ctx.run.corpus_digest,
            questions=[
                CodebookQuestion(
                    question_id=q.question_id,
                    section_id=section.section_slug,
                    canonical_question=q.canonical_question,
                )
                for section in ctx.sections
                for q in section.questions
            ],
            anchors=[
                Anchor(
                    question_id=q.question_id,
                    section_id=section.section_slug,
                    expert_slug=a.expert_slug,
                    answer_timestamp=a.answer_timestamp,
                )
                for section in ctx.sections
                for q in section.questions
                for a in q.asked_of
                if a.answer_timestamp
            ],
        )

    def digest(self) -> str:
        """Stable hash of the label space. Changing it invalidates cached codings."""
        material = json.dumps(
            [[q.question_id, q.section_id, q.canonical_question] for q in self.questions],
            sort_keys=True,
        )
        return hashlib.sha256(material.encode()).hexdigest()

    def for_section(self, section_id: str) -> list[CodebookQuestion]:
        return [q for q in self.questions if q.section_id == section_id]

    def sections(self) -> list[str]:
        return sorted({q.section_id for q in self.questions})

    def ids(self) -> set[str]:
        return {q.question_id for q in self.questions}

    def anchors_for(self, expert_slug: str, section_id: str) -> dict[str, str]:
        """timestamp -> question_id for one transcript.

        A timestamp claimed by two questions is dropped rather than resolved arbitrarily;
        the caller reports it. Silently picking one would be a coin flip.
        """
        found: dict[str, str] = {}
        clashing: set[str] = set()
        for anchor in self.anchors:
            if anchor.expert_slug != expert_slug or anchor.section_id != section_id:
                continue
            if anchor.answer_timestamp in found and found[anchor.answer_timestamp] != anchor.question_id:
                clashing.add(anchor.answer_timestamp)
            found[anchor.answer_timestamp] = anchor.question_id
        for timestamp in clashing:
            found.pop(timestamp, None)
        return found


def build(first_pass_path: Path) -> Codebook:
    ctx = FirstPassContext.model_validate_json(first_pass_path.read_text())
    return Codebook.from_first_pass(ctx, source=str(first_pass_path))


def load(path: Path) -> Codebook:
    return Codebook.model_validate_json(path.read_text())
