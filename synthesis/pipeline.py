"""Orchestration for pass 3.

    chart (Python, $0) ─┬─→ column synthesis (thread + section grains)  ─┐
                        └─→ case synthesis   (one per interviewee)       ─┴─→ findings

Column and case synthesis depend only on the chart, so they run concurrently. Findings runs
last because it reads the other two.

Per-call input is bounded by the column, not the corpus: a thread column carries at most a
few hundred words per interviewee. Growth adds columns and rows, not bigger prompts.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from context_pass import runner
from context_pass.budget import TokenCounter, pack
from context_pass.runner import Stage, git_sha
from context_pass.sections import TITLES

from synthesis import matrix, prompts
from synthesis.agents import SynthDeps
from synthesis.models import (
    CaseSynthesisOut,
    ColumnSynthesis,
    ColumnSynthesisBatch,
    Finding,
    FindingsOut,
    MatrixCell,
    MatrixColumn,
    MatrixRow,
    RunMetadata,
    StageUsage,
    Synthesis,
    case_synthesis_id,
    column_synthesis_id,
)

# Grains the model synthesizes. `question` stays as a leaf in the matrix but is not
# synthesized: two thirds of question columns have a single respondent, so a per-question
# synthesis would spend most of its calls on columns with nothing to compare.
SYNTHESIZED_GRAINS = ("thread", "section")


class Config(BaseModel):
    model: str = "anthropic:claude-opus-5"
    max_input_tokens: int = 120_000
    columns_per_call: int = 4
    concurrency: int = 4
    use_cache: bool = True
    refresh: set[str] = set()
    fail_fast: bool = False
    out_dir: Path = Path("out")
    only_sections: list[str] = []
    grains: list[str] = list(SYNTHESIZED_GRAINS)

    model_config = {"arbitrary_types_allowed": True}


def render_column(column: MatrixColumn, cells: list[MatrixCell], rows: list[MatrixRow]) -> str:
    """One column as the model sees it: the header, then each case's verbatim words."""
    by_row = {c.row_id: c for c in cells if c.column_id == column.column_id}
    filled = [c for c in by_row.values() if c.filled]
    lines = [
        f"### column_id: {column.column_id}   ({column.grain})",
        f"### topic: {column.label}",
        f"### interviewees with content here: {len(filled)} of {len(rows)}",
    ]
    if column.member_question_ids:
        lines.append(f"### questions in this thread: {', '.join(column.member_question_ids)}")
    lines.append("")
    for row in rows:
        cell = by_row.get(row.row_id)
        if cell is None or not cell.filled:
            status = cell.status if cell else "not_asked"
            lines.append(f"[{row.row_id}] — no content ({status})\n")
            continue
        note = ""
        if cell.assent_only:
            note = "  (assent to the interviewer's restatement only — NOT quotable)"
        elif cell.is_disfluent:
            note = "  (process talk only — NOT quotable)"
        lines.append(f"cell_id: {cell.cell_id}{note}")
        lines.append(f"[{row.row_id}] {cell.text}\n")
    return "\n".join(lines)


def render_case(row: MatrixRow, columns: list[MatrixColumn], cells: list[MatrixCell]) -> str:
    """One case's whole row, in section order, so the model can read across it."""
    by_id = {c.column_id: c for c in columns}
    mine = [c for c in cells if c.row_id == row.row_id and c.filled]
    mine.sort(key=lambda c: (by_id[c.column_id].section_id, by_id[c.column_id].order))
    lines = [f"### interviewee: {row.row_id} — {row.ui_statement}", ""]
    current = None
    for cell in mine:
        column = by_id[cell.column_id]
        if column.section_id != current:
            current = column.section_id
            lines.append(f"\n## {TITLES.get(current, current)}")
        note = "  (assent only — NOT quotable)" if cell.assent_only else ""
        lines.append(f"\ncell_id: {cell.cell_id}  topic: {column.label}{note}")
        lines.append(cell.text)
    return "\n".join(lines)


async def run_columns(
    agents, columns, cells, rows, counter: TokenCounter, cfg: Config, deps: SynthDeps
):
    stage = Stage("column")
    semaphore = asyncio.Semaphore(cfg.concurrency)
    targets = [
        c
        for c in columns
        if c.grain in cfg.grains
        and (not cfg.only_sections or c.section_id in cfg.only_sections)
    ]
    rendered = {c.column_id: render_column(c, cells, rows) for c in targets}
    batches = pack(
        targets,
        lambda c: counter.count(rendered[c.column_id]),
        cfg.max_input_tokens,
        max_items=cfg.columns_per_call,
    )

    async def one(batch: list[MatrixColumn], index: int):
        prompt = (
            f"Synthesize each of the {len(batch)} columns below. Return one entry per "
            f"column, with column_id copied exactly.\n\n"
            + "\n\n---\n\n".join(rendered[c.column_id] for c in batch)
        )
        async with semaphore:
            return batch, await runner.call(
                agents["column"],
                prompt,
                deps,
                ColumnSynthesisBatch,
                cfg=cfg,
                stage=stage,
                unit_id=f"batch-{index:03d}",
                digests=(prompts.stage_digest("column"),),
            )

    results = await asyncio.gather(*(one(b, i) for i, b in enumerate(batches)))
    out: list[ColumnSynthesis] = []
    by_id = {c.column_id: c for c in columns}
    for batch, result in results:
        if result is None:
            continue
        for synthesis in result.columns:
            column = by_id.get(synthesis.column_id)
            if column is None:
                continue
            synthesis.grain = column.grain
            synthesis.synthesis_id = column_synthesis_id(column.grain, column.column_id)
            synthesis.comparability = matrix.comparability(
                column.respondent_count, len(rows)
            )
            out.append(synthesis)
    return out, stage


async def run_cases(agents, rows, columns, cells, cfg: Config, deps: SynthDeps):
    stage = Stage("case")
    semaphore = asyncio.Semaphore(cfg.concurrency)
    threads = [c for c in columns if c.grain == "thread"]

    async def one(row: MatrixRow):
        prompt = (
            "Read across this interviewee's whole row and synthesize them as a case.\n\n"
            + render_case(row, threads, [c for c in cells if c.grain == "thread"])
        )
        async with semaphore:
            result = await runner.call(
                agents["case"],
                prompt,
                deps,
                CaseSynthesisOut,
                cfg=cfg,
                stage=stage,
                unit_id=row.row_id,
                digests=(prompts.stage_digest("case"),),
            )
        if result is None:
            return None
        result.case.row_id = row.row_id
        result.case.synthesis_id = case_synthesis_id(row.row_id)
        return result.case

    results = await asyncio.gather(*(one(r) for r in rows))
    return [r for r in results if r is not None], stage


async def run_findings(agents, columns_synthesis, cases, cfg: Config, deps: SynthDeps):
    stage = Stage("findings")
    # The already-verified canonical quotes are included verbatim with their cell_ids.
    # Without them this stage has nothing it is allowed to cite - it sees only headlines -
    # and correctly returns nothing rather than fabricate a quote.
    def render(synthesis) -> str:
        lines = [f"{synthesis.synthesis_id} [{synthesis.comparability}] {synthesis.headline}"]
        if synthesis.canonical_quote:
            quote = synthesis.canonical_quote
            lines.append(
                f'  quotable — cell_id: {quote.cell_id}  row_id: {quote.row_id}\n'
                f'    "{quote.quote}"'
            )
        for conflict in synthesis.conflicts:
            lines.append(f"  conflict: {conflict.statement}")
        for agreement in synthesis.agreements:
            lines.append(f"  agreement: {agreement.statement}")
        return "\n".join(lines)

    payload = "\n\n".join(
        [
            "## Column syntheses",
            *(render(s) for s in columns_synthesis),
            "## Case syntheses",
            *(
                f"{c.synthesis_id}: {c.through_line}"
                + ("\n  tensions: " + "; ".join(t.statement for t in c.internal_tensions)
                   if c.internal_tensions else "")
                + ("\n  distinctive: " + "; ".join(c.distinctive) if c.distinctive else "")
                for c in cases
            ),
        ]
    )
    result = await runner.call(
        agents["findings"],
        "Read the whole matrix below and state the findings that only appear at this "
        "level.\n\n" + payload,
        deps,
        FindingsOut,
        cfg=cfg,
        stage=stage,
        unit_id="all",
        digests=(prompts.stage_digest("findings"),),
    )
    findings: list[Finding] = result.findings if result else []
    for index, finding in enumerate(findings, start=1):
        finding.finding_id = f"finding:{index:02d}"
    return findings, stage


async def run(
    conn: sqlite3.Connection,
    first_pass: dict,
    agents,
    counter: TokenCounter,
    cfg: Config,
    *,
    coding_digests: tuple[str, str] = ("", ""),
) -> Synthesis:
    started = time.monotonic()

    rows, columns, cells = matrix.chart(conn, first_pass)
    deps = SynthDeps(cells={c.cell_id: c for c in cells})

    (columns_synthesis, column_stage), (cases, case_stage) = await asyncio.gather(
        run_columns(agents, columns, cells, rows, counter, cfg, deps),
        run_cases(agents, rows, columns, cells, cfg, deps),
    )
    findings, findings_stage = await run_findings(
        agents, columns_synthesis, cases, cfg, deps
    )

    stages = [column_stage, case_stage, findings_stage]
    warnings = [w for s in stages for w in s.warnings]
    costs = [s.usage.cost_usd for s in stages if s.usage.cost_usd is not None]

    return Synthesis(
        run=RunMetadata(
            generated_at=datetime.now(timezone.utc),
            model=cfg.model,
            corpus_digest=coding_digests[0],
            codebook_digest=coding_digests[1],
            prompt_digests=prompts.prompt_digests(),
            git_sha=git_sha(),
            usage=[
                StageUsage(**s.usage.model_dump(include=set(StageUsage.model_fields)))
                for s in stages
            ],
            total_cost_usd=sum(costs) if costs else None,
            wall_clock_s=round(time.monotonic() - started, 2),
            warnings=warnings,
            status="partial" if warnings else "complete",
            counts={
                "rows": len(rows),
                "columns": len(columns),
                "cells": len(cells),
                "filled_cells": sum(1 for c in cells if c.filled),
                "columns_synthesized": len(columns_synthesis),
                "cases": len(cases),
                "findings": len(findings),
            },
        ),
        rows=rows,
        columns=columns,
        cells=cells,
        columns_synthesis=columns_synthesis,
        cases=cases,
        findings=findings,
    )
