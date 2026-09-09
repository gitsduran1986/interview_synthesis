"""Synthesis agents and the validators that keep their claims anchored.

The citation validator is stricter than pass 1's `check_evidence()` in a way that matters
here: a quote must appear in the CELL it cites, not merely somewhere in that interviewee's
transcript. Pass 3's whole claim is "this person said this *about this topic*", and a
file-level check cannot verify the second half.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models.anthropic import AnthropicModelSettings

from context_pass.corpus import normalize

from synthesis import prompts
from synthesis.models import (
    CaseSynthesisOut,
    Citation,
    ColumnSynthesisBatch,
    FindingsOut,
    MatrixCell,
)

DEFAULT_MODEL = "anthropic:claude-opus-5"
# No temperature/top_p/budget_tokens: Opus 5 rejects all three.
DEFAULT_SETTINGS = AnthropicModelSettings(max_tokens=16000)


@dataclass
class SynthDeps:
    """Cells indexed by id, so a validator can check a quote against the cell it names."""

    cells: dict[str, MatrixCell] = field(default_factory=dict)


def walk_citations(value: Any, path: str = "") -> list[tuple[str, Citation]]:
    found: list[tuple[str, Citation]] = []
    if isinstance(value, Citation):
        found.append((path or "citation", value))
    elif isinstance(value, BaseModel):
        for name, child in value:
            found.extend(walk_citations(child, f"{path}.{name}" if path else name))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            found.extend(walk_citations(child, f"{path}[{index}]"))
    elif isinstance(value, dict):
        for key, child in value.items():
            found.extend(walk_citations(child, f"{path}.{key}" if path else str(key)))
    return found


def check_citations(cells: dict[str, MatrixCell], output: Any) -> list[str]:
    """Every quote must be verbatim, from the cell it names, and from a quotable cell."""
    problems: list[str] = []
    for path, citation in walk_citations(output):
        cell = cells.get(citation.cell_id)
        if cell is None:
            problems.append(
                f"{path}: cell_id {citation.cell_id!r} does not exist. Copy it exactly "
                f"from the cell header."
            )
            continue
        if cell.row_id != citation.row_id:
            problems.append(
                f"{path}: cell {citation.cell_id} belongs to {cell.row_id}, not "
                f"{citation.row_id}. Attribute the words to whoever actually said them."
            )
        if normalize(citation.quote) not in normalize(cell.text):
            problems.append(
                f"{path}: this quote is not in cell {citation.cell_id}. Copy it verbatim "
                f"from that cell's text: {citation.quote[:70]!r}"
            )
        elif not cell.quotable:
            reason = (
                "is only assent to the interviewer's restatement"
                if cell.assent_only
                else "is process talk, not an answer"
            )
            problems.append(
                f"{path}: cell {citation.cell_id} {reason}, so it cannot be quoted as "
                f"evidence. The substance came from the interviewer, not the interviewee. "
                f"Quote a cell with content of its own, or use null."
            )
    return problems


def _citation_validator(ctx: RunContext[SynthDeps], output: Any) -> Any:
    problems = check_citations(ctx.deps.cells, output)
    if problems:
        raise ModelRetry(
            "These citations could not be verified against the matrix. Fix each one:\n- "
            + "\n- ".join(problems[:12])
        )
    return output


def _comparability_validator(
    ctx: RunContext[SynthDeps], output: ColumnSynthesisBatch
) -> ColumnSynthesisBatch:
    """A column with one respondent cannot have agreement or conflict.

    The model is told this, but with a corpus where columns vary from one to three
    respondents it is the likeliest fabrication, so it is also enforced.
    """
    problems: list[str] = []
    for column in output.columns:
        rows = {
            cell.row_id
            for cell in ctx.deps.cells.values()
            if cell.column_id == column.column_id and cell.filled
        }
        if len(rows) >= 2:
            continue
        if column.agreements or column.conflicts:
            problems.append(
                f"{column.column_id}: only {len(rows)} interviewee has content here, so "
                f"there is nothing to agree or disagree with. Return empty agreements and "
                f"conflicts and put what the single account gives you in headline/notable."
            )
    if problems:
        raise ModelRetry("\n- ".join(["Fix these columns:", *problems]))
    return output


def build_agents(
    model: str = DEFAULT_MODEL, settings: AnthropicModelSettings | None = None
) -> dict[str, Agent]:
    settings = settings or DEFAULT_SETTINGS
    common: dict[str, Any] = {
        "model": model,
        "deps_type": SynthDeps,
        "model_settings": settings,
        "retries": 2,
        "defer_model_check": True,
    }

    column = Agent(
        output_type=ColumnSynthesisBatch,
        instructions=prompts.COLUMN_INSTRUCTIONS,
        name="column",
        **common,
    )
    case = Agent(
        output_type=CaseSynthesisOut,
        instructions=prompts.CASE_INSTRUCTIONS,
        name="case",
        **common,
    )
    findings = Agent(
        output_type=FindingsOut,
        instructions=prompts.FINDINGS_INSTRUCTIONS,
        name="findings",
        **common,
    )

    for agent in (column, case, findings):
        agent.output_validator(_citation_validator)
    column.output_validator(_comparability_validator)

    return {"column": column, "case": case, "findings": findings}
