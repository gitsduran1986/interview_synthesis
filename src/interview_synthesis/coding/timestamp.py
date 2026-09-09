"""The timestamp coding strategy: join pass 1's anchors to transcript turns.

Zero model calls and instant. The join itself is deterministic, but the pipeline is NOT:
the anchors it replays were produced by an LLM in the question stage of pass 1. This is a
faithful, reproducible *materialization* of an earlier model decision, not a
model-free derivation of the answer.

That is only trustworthy because the question stage is its own evaluable step: it has its
own agent, prompt, cache and output type, and a validator that rejects any answer timestamp
that does not point at a real interviewee turn. The quality question belongs there, where
the pairing is made - not here, where it is copied.

Two consequences worth keeping in view, both handled explicitly below rather than hidden:

* An anchor can point at a turn that is not a substantive answer. Pass 1 models a re-ask as
  its own canonical question, so `"Can you repeat the question?"` is legitimately recorded
  as the answer to the question that prompted it. Those rows get `method='anchor'` like any
  other; `flag_disfluent()` marks them so a consumer can filter.
* An anchor can fail to resolve, if pass 1 emitted a timestamp no turn carries. That is
  never silently dropped — it is reported, because the alternative is a question whose
  answer quietly vanishes from the database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from interview_synthesis.corpus import Unit, normalize

from interview_synthesis.coding.codebook import Codebook

# Process talk: turns that are not an answer to anything, in any context.
#
# Deliberately does NOT include bare affirmatives. A lone "Yes." looks like filler but is
# the complete and correct answer to a confirmation question ("Confirmation: TCO ended up
# 20-30% higher - is that accurate?"), and five of this corpus's turns are exactly that.
# Flagging them would tell a consumer to discard real answers.
DISFLUENT = re.compile(
    r"repeat the question"
    r"|give me a minute"
    r"|one moment"
    r"|^(go ahead|sorry)\b[.,!]?$",
    re.I,
)
DISFLUENT_MAX_WORDS = 10


@dataclass
class Coding:
    text_id: str
    question_id: str
    method: str
    rationale: str


@dataclass
class Uncoded:
    text_id: str
    reason: str


@dataclass
class JoinResult:
    codings: list[Coding] = field(default_factory=list)
    uncoded: list[Uncoded] = field(default_factory=list)
    unresolved_anchors: list[tuple[str, str, str]] = field(default_factory=list)
    duplicate_timestamps: list[tuple[str, str]] = field(default_factory=list)
    disfluent_codings: list[str] = field(default_factory=list)


def is_disfluent(text: str) -> bool:
    """Process talk, not content. A hint for consumers; never used to drop a row."""
    if len(text.split()) > DISFLUENT_MAX_WORDS:
        return False
    return bool(DISFLUENT.search(normalize(text)))


def code_unit(
    unit: Unit,
    codebook: Codebook,
    text_id_of,
    *,
    span_fill: bool = False,
) -> JoinResult:
    """Join one transcript's turns to pass 1's anchors.

    With `span_fill`, an interviewee turn with no anchor of its own inherits the question
    from the most recent anchored turn — which turns 95% coverage into 100%, at the price of
    those rows being inference rather than pass 1's claim. They are recorded as
    `method='span'` so the two are never confused.
    """
    result = JoinResult()
    anchors = codebook.anchors_for(unit.expert_slug, unit.section_slug)

    # The join key must be unique within a transcript, or the mapping is a coin flip.
    seen: dict[str, int] = {}
    for index, turn in enumerate(unit.turns):
        if not turn.is_expert:
            continue
        if turn.timestamp in seen:
            result.duplicate_timestamps.append((unit.rel_path, turn.timestamp))
        seen[turn.timestamp] = index

    for timestamp in anchors:
        if timestamp not in seen:
            result.unresolved_anchors.append(
                (unit.rel_path, timestamp, anchors[timestamp])
            )

    current: str | None = None
    for index, turn in enumerate(unit.turns):
        if not turn.is_expert:
            continue
        text_id = text_id_of(unit.rel_path, index)
        question_id = anchors.get(turn.timestamp)

        if question_id is not None:
            current = question_id
            result.codings.append(
                Coding(text_id, question_id, "anchor", "pass 1 anchored this turn")
            )
            if is_disfluent(turn.text):
                result.disfluent_codings.append(text_id)
        elif span_fill and current is not None:
            result.codings.append(
                Coding(text_id, current, "span", "inherited from the preceding anchor")
            )
        else:
            result.uncoded.append(Uncoded(text_id, "no_question_addressed"))

    return result
