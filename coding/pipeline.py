"""Orchestration for pass 2: plan calls, code, persist.

Work is planned by section: the questions for one section are shared across a small batch of
that section's transcripts, so per-call context is bounded by section size and never by the
corpus. Growth shows up as more calls, not bigger calls.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from context_pass.budget import TokenCounter, pack
from context_pass.corpus import Corpus, Unit
from context_pass.runner import Stage, cache_key, git_sha
from context_pass import runner
from context_pass.sections import PROFILE_ONLY, TITLES

from coding import coder, store, timestamp as ts
from coding.codebook import Codebook


class Config(BaseModel):
    strategy: str = "timestamp"      # 'timestamp' (join pass 1's anchors) or 'model'
    span_fill: bool = False          # forward-fill unanchored turns; inference, off by default
    model: str = coder.DEFAULT_MODEL
    max_input_tokens: int = 120_000
    units_per_call: int = 3
    concurrency: int = 4
    use_cache: bool = True
    refresh: set[str] = set()
    fail_fast: bool = False
    out_dir: Path = Path("out")
    only_experts: list[str] = []
    only_sections: list[str] = []

    model_config = {"arbitrary_types_allowed": True}


@dataclass
class Plan:
    """Work grouped by section, then batched under the token budget."""

    batches: list[tuple[str, list[Unit]]] = field(default_factory=list)

    @property
    def calls(self) -> int:
        return len(self.batches)

    @property
    def units(self) -> int:
        return sum(len(b) for _, b in self.batches)


def plan_calls(
    corpus: Corpus, codebook: Codebook, counter: TokenCounter, cfg: Config
) -> Plan:
    """One batch = several transcripts from ONE section, sharing that section's questions."""
    plan = Plan()
    for section_id in codebook.sections():
        if cfg.only_sections and section_id not in cfg.only_sections:
            continue
        if section_id in PROFILE_ONLY:
            continue
        units = [
            u
            for u in corpus.for_section(section_id)
            if not cfg.only_experts or u.expert_slug in cfg.only_experts
        ]
        if not units:
            continue
        for batch in pack(
            units,
            lambda u: counter.count(coder.render_unit(u)),
            cfg.max_input_tokens,
            max_items=cfg.units_per_call,
        ):
            plan.batches.append((section_id, batch))
    return plan


def _digests(codebook: Codebook) -> tuple[str, str]:
    return coder.prompt_digest(), codebook.digest()


def run_timestamp(
    conn: sqlite3.Connection, corpus: Corpus, codebook: Codebook, cfg: Config, run_id: str
) -> dict:
    """Join pass 1's anchors to turns. No model calls.

    Every anchor that fails to resolve is reported rather than dropped: an unresolvable
    anchor means a question whose answer would otherwise vanish from the database without
    anyone noticing.
    """
    coded = uncoded = disfluent = 0
    warnings: list[str] = []

    for unit in corpus.units:
        if unit.section_slug in PROFILE_ONLY:
            continue
        if cfg.only_experts and unit.expert_slug not in cfg.only_experts:
            continue
        if cfg.only_sections and unit.section_slug not in cfg.only_sections:
            continue

        result = ts.code_unit(unit, codebook, _text_id_of, span_fill=cfg.span_fill)
        store.clear_unit_codings(conn, unit.rel_path)
        disfluent_ids = set(result.disfluent_codings)
        store.insert_codings(
            conn,
            [
                (c.text_id, c.question_id, c.method,
                 1 if c.text_id in disfluent_ids else 0, None, c.rationale, run_id)
                for c in result.codings
            ],
        )
        store.insert_uncoded(conn, [(u.text_id, u.reason, run_id) for u in result.uncoded])
        coded += len(result.codings)
        uncoded += len(result.uncoded)
        disfluent += len(disfluent_ids)

        for path, stamp, qid in result.unresolved_anchors:
            warnings.append(
                f"{path}: pass 1 anchored {qid} at {stamp}, but no turn has that timestamp - "
                f"that question has no coded answer here"
            )
        for path, stamp in result.duplicate_timestamps:
            warnings.append(
                f"{path}: two interviewee turns share timestamp {stamp}; the join key is "
                f"ambiguous and those anchors were skipped"
            )
    conn.commit()
    return {"coded": coded, "uncoded": uncoded, "disfluent": disfluent, "warnings": warnings}


async def run(
    conn: sqlite3.Connection,
    corpus: Corpus,
    codebook: Codebook,
    agent,
    counter: TokenCounter,
    cfg: Config,
) -> dict:
    """Code every eligible turn and write the results. Returns a run summary."""
    started = time.monotonic()
    plan = plan_calls(corpus, codebook, counter, cfg)
    stage = Stage("coding")
    deps = coder.CoderDeps(codebook=codebook, units={u.rel_path: u for u in corpus.units})
    semaphore = asyncio.Semaphore(cfg.concurrency)

    run_id = cache_key(cfg, codebook.digest(), coder.prompt_digest(), cfg.strategy)[:16]
    store.record_run(
        conn,
        (
            run_id,
            datetime.now(timezone.utc).isoformat(),
            cfg.strategy,
            cfg.model if cfg.strategy == "model" else "none (replays pass 1 question stage)",
            corpus.digest(),
            codebook.digest(),
            coder.prompt_digest(),
            git_sha(),
            "complete",
        ),
    )
    conn.commit()

    if cfg.strategy == "timestamp":
        joined = run_timestamp(conn, corpus, codebook, cfg, run_id)
        _mark_profile_only(conn, corpus, run_id)
        status = "complete"
        conn.execute("UPDATE coding_run SET status = ? WHERE run_id = ?", (status, run_id))
        conn.commit()
        return {
            "run_id": run_id, "strategy": "timestamp", "status": status,
            "calls": 0, "cached_calls": 0, "input_tokens": 0, "output_tokens": 0,
            "cost_usd": 0.0, "coded": joined["coded"],
            "uncoded": joined["uncoded"] + _profile_only_count(corpus),
            "disfluent": joined["disfluent"], "warnings": joined["warnings"],
            "wall_clock_s": round(time.monotonic() - started, 2),
        }

    async def one(section_id: str, units: list[Unit], index: int):
        prompt = coder.build_prompt(
            section_id, TITLES.get(section_id, section_id), codebook, units
        )
        async with semaphore:
            return units, await runner.call(
                agent,
                prompt,
                deps,
                coder.CodingBatch,
                cfg=cfg,
                stage=stage,
                unit_id=f"{section_id}-{index:03d}",
                digests=_digests(codebook),
            )

    results = await asyncio.gather(
        *(one(s, b, i) for i, (s, b) in enumerate(plan.batches))
    )

    coded = uncoded = 0
    for units, batch in results:
        by_id = {u.rel_path: u for u in units}
        if batch is None:
            # The whole batch failed; record every expert turn as uncoded rather than
            # letting the rows silently vanish.
            for unit in units:
                store.clear_unit_codings(conn, unit.rel_path)
                rows = [
                    (_text_id(unit, i), "unit_failed", run_id)
                    for i, t in enumerate(unit.turns)
                    if t.is_expert
                ]
                store.insert_uncoded(conn, rows)
                uncoded += len(rows)
            continue

        returned = {u.unit_id for u in batch.units}
        for unit in units:
            store.clear_unit_codings(conn, unit.rel_path)
            if unit.rel_path not in returned:
                rows = [
                    (_text_id(unit, i), "unit_failed", run_id)
                    for i, t in enumerate(unit.turns)
                    if t.is_expert
                ]
                store.insert_uncoded(conn, rows)
                uncoded += len(rows)

        for unit_codings in batch.units:
            unit = by_id.get(unit_codings.unit_id)
            if unit is None:
                continue
            seen: set[int] = set()
            coding_rows, uncoded_rows = [], []
            for c in unit_codings.codings:
                if c.turn_index in seen:
                    continue
                seen.add(c.turn_index)
                tid = _text_id(unit, c.turn_index)
                if c.question_id is None:
                    uncoded_rows.append((tid, "no_question_addressed", run_id))
                else:
                    coding_rows.append(
                        (tid, c.question_id, "model",
                         1 if ts.is_disfluent(unit.turns[c.turn_index].text) else 0,
                         c.confidence, c.rationale, run_id)
                    )
            # Any expert turn the model simply didn't mention is recorded, not lost.
            for i, turn in enumerate(unit.turns):
                if turn.is_expert and i not in seen:
                    uncoded_rows.append((_text_id(unit, i), "model_declined", run_id))
            store.insert_codings(conn, coding_rows)
            store.insert_uncoded(conn, uncoded_rows)
            coded += len(coding_rows)
            uncoded += len(uncoded_rows)
        conn.commit()

    profile_rows = _mark_profile_only(conn, corpus, run_id)

    status = "partial" if stage.warnings else "complete"
    conn.execute("UPDATE coding_run SET status = ? WHERE run_id = ?", (status, run_id))
    conn.commit()

    return {
        "run_id": run_id,
        "strategy": cfg.strategy,
        "status": status,
        "calls": stage.usage.calls,
        "cached_calls": stage.usage.cached_calls,
        "input_tokens": stage.usage.input_tokens,
        "output_tokens": stage.usage.output_tokens,
        "cost_usd": stage.usage.cost_usd,
        "coded": coded,
        "uncoded": uncoded + profile_rows,
        "warnings": stage.warnings,
        "wall_clock_s": round(time.monotonic() - started, 2),
    }


def _text_id(unit: Unit, turn_index: int) -> str:
    return _text_id_of(unit.rel_path, turn_index)


def _text_id_of(unit_id: str, turn_index: int) -> str:
    from coding.ingest import text_id

    return text_id(unit_id, turn_index)


def _profile_only_count(corpus: Corpus) -> int:
    return sum(
        1
        for unit in corpus.units
        if unit.section_slug in PROFILE_ONLY
        for t in unit.turns
        if t.is_expert
    )


def _mark_profile_only(conn: sqlite3.Connection, corpus: Corpus, run_id: str) -> int:
    """Profile-only sections are stored but deliberately never coded."""
    rows = [
        (_text_id(unit, i), "non_eval_section", run_id)
        for unit in corpus.units
        if unit.section_slug in PROFILE_ONLY
        for i, t in enumerate(unit.turns)
        if t.is_expert
    ]
    store.insert_uncoded(conn, rows)
    conn.commit()
    return len(rows)
