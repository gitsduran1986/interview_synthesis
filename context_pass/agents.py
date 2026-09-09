"""Pydantic AI agents and the validators that police their output.

The evidence validator is the load-bearing piece: it re-reads the source file and rejects
any quote that was not actually said, or was said by the interviewer and attributed to the
interviewee. Fabricated or misattributed quotes are the failure mode that would silently
poison everything downstream, so they are caught here rather than trusted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models.anthropic import AnthropicModelSettings

from context_pass import prompts
from context_pass.corpus import Corpus, normalize
from context_pass.models import (
    Evidence,
    ExpertPass,
    SectionPass,
    UnitExtractBatch,
)

DEFAULT_MODEL = "anthropic:claude-opus-5"

# No temperature/top_p and no thinking budget: both are rejected by Opus 5, which runs
# adaptive thinking by default. Determinism comes from input caching, not sampling.
DEFAULT_SETTINGS = AnthropicModelSettings(max_tokens=16000)


@dataclass
class Deps:
    """What a validator needs to check the model's work against the real transcripts."""

    corpus: Corpus


# ─────────────────────────── evidence checking ───────────────────────────


def walk_evidence(value: Any, path: str = "") -> list[tuple[str, Evidence]]:
    """Every Evidence anywhere in a model tree, with a readable path to each."""
    found: list[tuple[str, Evidence]] = []
    if isinstance(value, Evidence):
        found.append((path or "evidence", value))
    elif isinstance(value, BaseModel):
        for name, child in value:
            found.extend(walk_evidence(child, f"{path}.{name}" if path else name))
    elif isinstance(value, (list, tuple)):
        for i, child in enumerate(value):
            found.extend(walk_evidence(child, f"{path}[{i}]"))
    elif isinstance(value, dict):
        for key, child in value.items():
            found.extend(walk_evidence(child, f"{path}.{key}" if path else str(key)))
    return found


def check_evidence(corpus: Corpus, output: Any) -> list[str]:
    """Return one problem string per bad citation. Empty means everything checks out."""
    problems: list[str] = []
    for path, ev in walk_evidence(output):
        unit = corpus.by_path(ev.source_file)
        if unit is None:
            problems.append(
                f"{path}: source_file {ev.source_file!r} does not exist. Use the exact "
                f"path from the file header."
            )
            continue
        needle = normalize(ev.quote)
        if needle not in normalize(unit.body):
            problems.append(
                f"{path}: this quote does not appear in {ev.source_file}. Copy the words "
                f"exactly as written: {ev.quote[:80]!r}"
            )
        elif ev.speaker == "expert" and needle not in normalize(unit.expert_text()):
            problems.append(
                f"{path}: this quote is from an AI Interviewer turn but is attributed to "
                f"the interviewee. Either set speaker='interviewer' or quote the "
                f"interviewee's own words instead: {ev.quote[:80]!r}"
            )
    return problems


def _evidence_validator(ctx: RunContext[Deps], output: Any) -> Any:
    problems = check_evidence(ctx.deps.corpus, output)
    if problems:
        raise ModelRetry(
            "Some citations could not be verified against the transcripts. Fix each one, "
            "copying text character for character:\n- " + "\n- ".join(problems[:12])
        )
    return output


def question_key(text: str) -> str:
    """Comparison key for question dedup.

    Punctuation is dropped as well as case and smart quotes: "...compare to budget?" and
    "...compare to budget." are the same question asked twice, not two questions.
    """
    return "".join(ch for ch in normalize(text) if ch.isalnum() or ch.isspace()).strip()


def _dedup_validator(ctx: RunContext[Deps], output: SectionPass) -> SectionPass:
    """Catch questions the model should have merged but didn't.

    Where to merge is a judgement call the model makes, but two questions whose text
    reduces to the same key are unambiguously the same question.
    """
    seen: dict[str, str] = {}
    clashes: list[str] = []
    for question in output.questions:
        key = question_key(question.canonical_question)
        if key in seen:
            clashes.append(f"{seen[key]!r} and {question.canonical_question!r}")
        else:
            seen[key] = question.canonical_question
    if clashes:
        raise ModelRetry(
            "These are the same question and must be merged into a single entry, "
            "combining their `asked_of` lists:\n- " + "\n- ".join(clashes)
        )
    return output


# ─────────────────────────── agents ───────────────────────────


def build_agents(
    model: str = DEFAULT_MODEL, settings: AnthropicModelSettings | None = None
) -> dict[str, Agent]:
    """The three agents. Built by a factory so tests can swap the model cheaply."""
    settings = settings or DEFAULT_SETTINGS
    common: dict[str, Any] = {
        "model": model,
        "deps_type": Deps,
        "model_settings": settings,
        "retries": 2,
        # Resolve the provider lazily: the agents must be constructible without
        # credentials so `--dry-run` and the offline test suite work.
        "defer_model_check": True,
    }

    extract = Agent(
        output_type=UnitExtractBatch,
        instructions=prompts.EXTRACT_INSTRUCTIONS,
        name="extract",
        **common,
    )
    expert = Agent(
        output_type=ExpertPass,
        instructions=prompts.EXPERT_INSTRUCTIONS,
        name="expert",
        **common,
    )
    section = Agent(
        output_type=SectionPass,
        instructions=prompts.SECTION_INSTRUCTIONS,
        name="section",
        **common,
    )

    for agent in (extract, expert, section):
        agent.output_validator(_evidence_validator)
    section.output_validator(_dedup_validator)

    return {"extract": extract, "expert": expert, "section": section}
