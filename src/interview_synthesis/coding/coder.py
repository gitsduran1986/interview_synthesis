"""The coding agent: assign each interviewee turn to a canonical question.

Independent of the first pass's own answer/question pairing — it sees only the codebook
(the label space) and the transcript, and re-derives the assignment. That separation is
what makes the coding step evaluable on its own.

Scaling property to preserve: the prompt is built from ONE section's questions and a small
batch of units from that section. Neither grows with the size of the corpus. Adding
interviews adds calls, not context. `tests/test_coder_fake.py` asserts this.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models.anthropic import AnthropicModelSettings

from interview_synthesis.corpus import Unit

from interview_synthesis.coding.codebook import Codebook

DEFAULT_MODEL = "anthropic:claude-opus-5"
# No temperature/top_p/budget_tokens: Opus 5 rejects all three.
DEFAULT_SETTINGS = AnthropicModelSettings(max_tokens=16000)

INSTRUCTIONS = """
You are coding interview transcripts. For each turn spoken by the INTERVIEWEE, decide which
one question from the supplied list that turn addresses.

Rules:

1. ONE question per turn. Pick the single question the turn most directly addresses. If a
   turn touches two, choose the one it actually answers.

2. Several turns may address the SAME question, and that is expected and correct. An
   interviewee often answers across consecutive turns, and an interviewer sometimes re-asks
   or asks a follow-up on the same point. Do not spread codes around to make the
   distribution look even, and never avoid a question just because you already used it.

3. Use only `question_id` values from the list given for that section. Never invent one.

4. If a turn addresses none of the listed questions, set `question_id` to null. Back-channel
   and process talk ("Yeah.", "Go ahead.", "Can you repeat the question?") normally gets
   null. A null is a real, useful answer — do not force a code onto a turn that has no
   content.

5. Code ONLY the turns marked INTERVIEWEE. Interviewer turns are shown for context so you
   can see what was asked; never emit a coding for them.

6. Judge by what the turn is ABOUT, not by which question happens to sit nearest it in the
   transcript. An interviewee may return to an earlier topic, or answer something asked
   several turns back.

Return one entry per interviewee turn, identified by its `turn_index`, with a confidence
between 0 and 1 and a one-line rationale naming what in the turn drove the choice.
""".strip()


def prompt_digest() -> str:
    return hashlib.sha256(INSTRUCTIONS.encode()).hexdigest()


@dataclass
class CoderDeps:
    """What the validator needs to check the model's answer against reality."""

    codebook: Codebook
    units: dict[str, Unit]


class TurnCoding(BaseModel):
    turn_index: int = Field(description="The [n] index of the interviewee turn being coded.")
    question_id: str | None = Field(
        description="A question_id from the list for this section, or null if the turn "
        "addresses none of them."
    )
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(
        max_length=240, description="One line: what in the turn drove this choice."
    )


class UnitCodings(BaseModel):
    unit_id: str = Field(description="Copied exactly from the transcript header.")
    codings: list[TurnCoding]


class CodingBatch(BaseModel):
    units: list[UnitCodings] = Field(
        min_length=1, description="One entry per transcript given to you."
    )


def render_unit(unit: Unit) -> str:
    """Transcript with turn indices, both speakers, so the model can see the question asked."""
    lines = [f"### unit_id: {unit.rel_path}", f"### interviewee: {unit.expert_slug}", ""]
    for index, turn in enumerate(unit.turns):
        who = "INTERVIEWEE" if turn.is_expert else "interviewer"
        lines.append(f"[{index}] {who}: {turn.text}")
        lines.append("")
    return "\n".join(lines)


def build_prompt(section_id: str, title: str, codebook: Codebook, units: list[Unit]) -> str:
    """Questions for ONE section plus a small batch of that section's transcripts."""
    questions = codebook.for_section(section_id)
    listing = "\n".join(f"  {q.question_id}  {q.canonical_question}" for q in questions)
    transcripts = "\n\n---\n\n".join(render_unit(u) for u in units)
    return (
        f"Section: {section_id} ({title})\n\n"
        f"Questions asked in this section:\n{listing}\n\n"
        f"Code every INTERVIEWEE turn in the {len(units)} transcript(s) below.\n\n"
        f"{transcripts}"
    )


def _validate(ctx: RunContext[CoderDeps], output: CodingBatch) -> CodingBatch:
    """Reject invented question ids, unknown units, and codings on interviewer turns."""
    problems: list[str] = []
    for unit_codings in output.units:
        unit = ctx.deps.units.get(unit_codings.unit_id)
        if unit is None:
            problems.append(
                f"unit_id {unit_codings.unit_id!r} was not given to you; copy it exactly "
                f"from the transcript header."
            )
            continue
        allowed = {q.question_id for q in ctx.deps.codebook.for_section(unit.section_slug)}
        for coding in unit_codings.codings:
            if not 0 <= coding.turn_index < len(unit.turns):
                problems.append(
                    f"{unit.rel_path}: turn_index {coding.turn_index} does not exist."
                )
                continue
            if not unit.turns[coding.turn_index].is_expert:
                problems.append(
                    f"{unit.rel_path}: turn [{coding.turn_index}] is an interviewer turn "
                    f"and must not be coded."
                )
            if coding.question_id is not None and coding.question_id not in allowed:
                problems.append(
                    f"{unit.rel_path}: {coding.question_id!r} is not a question in section "
                    f"{unit.section_slug}. Use one of the listed ids, or null."
                )
    if problems:
        raise ModelRetry(
            "Fix these codings:\n- " + "\n- ".join(problems[:12])
        )
    return output


def build_agent(
    model: str = DEFAULT_MODEL, settings: AnthropicModelSettings | None = None
) -> Agent:
    agent = Agent(
        model,
        output_type=CodingBatch,
        instructions=INSTRUCTIONS,
        deps_type=CoderDeps,
        model_settings=settings or DEFAULT_SETTINGS,
        retries=2,
        name="coder",
        defer_model_check=True,
    )
    agent.output_validator(_validate)
    return agent
