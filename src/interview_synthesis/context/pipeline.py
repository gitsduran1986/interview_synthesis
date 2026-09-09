"""Stage orchestration: extract (map) -> expert/section reduce -> assemble.

Design notes that matter when the corpus grows:

* Every stage asks the budgeter how to split its work, so nothing assumes the corpus fits
  in one call.
* Reduce stages read model-produced extracts, not raw transcripts, so their input grows
  with the NUMBER of units rather than their length. When even that overflows, the reduce
  runs in batches and folds its own partial results with the same agent.
* Units are cached and persisted individually, so a failure late in a long run never costs
  the work already done.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel
from pydantic_ai import Agent

from interview_synthesis.context import prompts
from interview_synthesis import runner
from interview_synthesis.context.agents import Deps, check_evidence, walk_evidence
from interview_synthesis.budget import (
    Chunk,
    TokenCounter,
    plan_extract_calls,
    plan_reduce_rounds,
)
from interview_synthesis.corpus import Corpus
from interview_synthesis.context.models import (
    EvidenceAudit,
    SectionQuestions,
    ExpertPass,
    FirstPassContext,
    QuestionAsking,
    RunMetadata,
    SectionPass,
    SectionQuestion,
    SourceFile,
    UnitExtract,
    UnitExtractBatch,
    UnverifiedQuote,
)
from interview_synthesis.runner import Stage
from interview_synthesis.sections import TITLES, eval_sections, section_number


class Config(BaseModel):
    model: str = "anthropic:claude-opus-5"
    max_input_tokens: int = 120_000
    units_per_call: int = 4
    concurrency: int = 4
    use_cache: bool = True
    refresh: set[str] = set()
    fail_fast: bool = False
    out_dir: Path = Path("out")
    only_experts: list[str] = []
    only_sections: list[str] = []

    model_config = {"arbitrary_types_allowed": True}


def _schema_digest() -> str:
    return hashlib.sha256(
        json.dumps(FirstPassContext.model_json_schema(), sort_keys=True).encode()
    ).hexdigest()


def _digests(stage_name: str) -> tuple[str, str]:
    """Per-stage, so editing one stage's prompt does not invalidate the others' caches."""
    return prompts.stage_digest(stage_name), _schema_digest()


async def _call(
    agent: Agent,
    prompt: str,
    deps: Deps,
    output_type: type[BaseModel],
    *,
    cfg: Config,
    stage: Stage,
    unit_id: str,
) -> BaseModel | None:
    return await runner.call(
        agent, prompt, deps, output_type,
        cfg=cfg, stage=stage, unit_id=unit_id, digests=_digests(stage.usage.stage),
    )


# --------------------------- stage 1: extract ---------------------------


def _render_batch(chunks: list[Chunk]) -> str:
    files = "\n\n---\n\n".join(c.render() for c in chunks)
    return f"Extract one entry for each of the {len(chunks)} source files below.\n\n{files}"


def _merge_chunk_extracts(extracts: list[UnitExtract]) -> list[UnitExtract]:
    """Fold chunks of the same file back into a single extract."""
    merged: dict[tuple[str, str], UnitExtract] = {}
    for extract in extracts:
        key = (extract.expert_slug, extract.section_slug)
        if key not in merged:
            copy = extract.model_copy(deep=True)
            copy.chunk_index, copy.chunk_count = 0, 1
            merged[key] = copy
            continue
        target = merged[key]
        target.questions.extend(extract.questions)
        target.claims.extend(extract.claims)
        target.credential_facts.extend(extract.credential_facts)
        target.scale_markers.extend(
            m for m in extract.scale_markers if m not in target.scale_markers
        )
    return list(merged.values())


async def run_extract(
    agents: dict[str, Agent],
    corpus: Corpus,
    counter: TokenCounter,
    cfg: Config,
    deps: Deps,
) -> tuple[list[UnitExtract], Stage, int]:
    units = [
        u
        for u in corpus.units
        if (not cfg.only_experts or u.expert_slug in cfg.only_experts)
        and (not cfg.only_sections or u.section_slug in cfg.only_sections)
    ]
    plan = plan_extract_calls(
        units, counter, cfg.max_input_tokens, max_units_per_call=cfg.units_per_call
    )
    stage = Stage("extract")
    semaphore = asyncio.Semaphore(cfg.concurrency)

    async def one(batch: list[Chunk], index: int) -> UnitExtractBatch | None:
        async with semaphore:
            return await _call(
                agents["extract"],
                _render_batch(batch),
                deps,
                UnitExtractBatch,
                cfg=cfg,
                stage=stage,
                unit_id=f"batch-{index:03d}",
            )

    results = await asyncio.gather(*(one(b, i) for i, b in enumerate(plan)))
    extracts = [e for r in results if r is not None for e in r.extracts]
    return _merge_chunk_extracts(extracts), stage, len(plan)


# --------------------------- reduce ---------------------------


def _extract_payload(extract: UnitExtract) -> str:
    return extract.model_dump_json(indent=1, exclude={"chunk_index", "chunk_count"})


async def _reduce(
    agent: Agent,
    payloads: list[str],
    header: str,
    output_type: type[BaseModel],
    *,
    counter: TokenCounter,
    cfg: Config,
    stage: Stage,
    deps: Deps,
    unit_id: str,
) -> BaseModel | None:
    """Run a reduce, folding in rounds when the inputs exceed one call's budget."""
    rounds = plan_reduce_rounds(payloads, counter, cfg.max_input_tokens)

    if len(rounds) == 1:
        return await _call(
            agent,
            f"{header}\n\n" + "\n\n---\n\n".join(rounds[0]),
            deps,
            output_type,
            cfg=cfg,
            stage=stage,
            unit_id=unit_id,
        )

    partials: list[BaseModel] = []
    for index, batch in enumerate(rounds):
        part = await _call(
            agent,
            f"{header}\n\nThis is part {index + 1} of {len(rounds)} of the input.\n\n"
            + "\n\n---\n\n".join(batch),
            deps,
            output_type,
            cfg=cfg,
            stage=stage,
            unit_id=f"{unit_id}-part-{index:02d}",
        )
        if part is not None:
            partials.append(part)

    if not partials:
        return None
    if len(partials) == 1:
        return partials[0]

    # The reduce output types are closed under merging, so the same agent folds its own
    # partial results and no second agent definition is needed.
    return await _reduce(
        agent,
        [p.model_dump_json(indent=1) for p in partials],
        f"{header}\n\nBelow are partial results over disjoint slices of the same input. "
        "Merge them into one consistent result, deduplicating as instructed.",
        output_type,
        counter=counter,
        cfg=cfg,
        stage=stage,
        deps=deps,
        unit_id=f"{unit_id}-merge",
    )


async def run_experts(
    agents: dict[str, Agent],
    corpus: Corpus,
    extracts: list[UnitExtract],
    counter: TokenCounter,
    cfg: Config,
    deps: Deps,
) -> tuple[dict[str, ExpertPass], Stage]:
    stage = Stage("expert")
    semaphore = asyncio.Semaphore(cfg.concurrency)
    roster = [e for e in corpus.experts if not cfg.only_experts or e in cfg.only_experts]

    async def one(expert: str) -> tuple[str, ExpertPass | None]:
        mine = [e for e in extracts if e.expert_slug == expert]
        if not mine:
            return expert, None
        async with semaphore:
            result = await _reduce(
                agents["expert"],
                [_extract_payload(e) for e in mine],
                f"Build the profile and themes for interviewee '{expert}' "
                f"({corpus.display_name(expert)}) from these extracts of their interview.",
                ExpertPass,
                counter=counter,
                cfg=cfg,
                stage=stage,
                deps=deps,
                unit_id=expert,
            )
        return expert, result

    results = await asyncio.gather(*(one(e) for e in roster))
    return {e: r for e, r in results if r is not None}, stage


def _section_slugs(corpus: Corpus, cfg: Config) -> list[str]:
    return [
        s
        for s in eval_sections()
        if s in corpus.sections and (not cfg.only_sections or s in cfg.only_sections)
    ]


async def _run_section_stage(
    agents: dict[str, Agent],
    corpus: Corpus,
    extracts: list[UnitExtract],
    counter: TokenCounter,
    cfg: Config,
    deps: Deps,
    *,
    stage_name: str,
    output_type: type[BaseModel],
    header: str,
) -> tuple[dict[str, BaseModel], Stage]:
    """One reduce per section, for a single artifact.

    The question stage and the theme stage share this shape but are separate calls with
    separate agents, prompts, caches, and outputs. Keeping them apart means each can be
    evaluated on its own, and neither spends the other's output budget.
    """
    stage = Stage(stage_name)
    semaphore = asyncio.Semaphore(cfg.concurrency)

    async def one(slug: str) -> tuple[str, BaseModel | None]:
        mine = [e for e in extracts if e.section_slug == slug]
        if not mine:
            return slug, None
        async with semaphore:
            result = await _reduce(
                agents[stage_name],
                [_extract_payload(e) for e in mine],
                header.format(slug=slug, title=TITLES.get(slug, slug),
                              number=section_number(slug)),
                output_type,
                counter=counter,
                cfg=cfg,
                stage=stage,
                deps=deps,
                unit_id=slug,
            )
        return slug, result

    results = await asyncio.gather(*(one(s) for s in _section_slugs(corpus, cfg)))
    return {slug: r for slug, r in results if r is not None}, stage


async def run_questions(agents, corpus, extracts, counter, cfg, deps):
    return await _run_section_stage(
        agents, corpus, extracts, counter, cfg, deps,
        stage_name="question",
        output_type=SectionQuestions,
        header=(
            "Section '{slug}' ({title}). Below are the extracts for each interviewee in "
            "this section. Deduplicate the questions across them and point each asking at "
            "the turn that answered it. Number question ids q-{number}-NN."
        ),
    )


# --------------------------- assembly ---------------------------


def _collapse_repeat_askings(question: SectionQuestion) -> None:
    """Keep one `asked_of` entry per interviewee.

    The interviewer sometimes re-asks the same question to the same person (after "Can
    you repeat the question?"), which produces two entries for one interviewee. That is
    faithful to the transcript but breaks the one-entry-per-interviewee contract the
    downstream evaluator relies on, so the entries are collapsed here, keeping the most
    informative one: an answered asking beats an unanswered one, and an entry carrying an
    answer timestamp beats one without.
    """

    def informativeness(asking: QuestionAsking) -> tuple[int, int]:
        answered = 1 if asking.answered in {"answered", "partial"} else 0
        return answered, 1 if asking.answer_timestamp else 0

    best: dict[str, QuestionAsking] = {}
    for asking in question.asked_of:
        current = best.get(asking.expert_slug)
        if current is None or informativeness(asking) > informativeness(current):
            best[asking.expert_slug] = asking
    # Preserve the order the interviewees first appeared in.
    order = list(dict.fromkeys(a.expert_slug for a in question.asked_of))
    question.asked_of = [best[slug] for slug in order]


def finalize_questions(section: SectionPass, roster_size: int) -> None:
    """Renumber question ids and derive coverage in Python.

    Coverage is arithmetic over the roster, so it is computed rather than asked of the
    model. A question asked of only one person stays a first-class entry: that is what
    tells the downstream evaluator not to penalise an interviewee for failing to answer
    something nobody put to them.
    """
    number = section_number(section.section_slug)
    for index, question in enumerate(section.questions, start=1):
        question.question_id = f"q-{number}-{index:02d}"
        _collapse_repeat_askings(question)
        asked = len({a.expert_slug for a in question.asked_of})
        if asked >= roster_size:
            question.coverage = "all_interviewees"
        elif asked <= 1:
            question.coverage = "single_interviewee"
        else:
            question.coverage = "subset"


def audit_evidence(corpus: Corpus, context: FirstPassContext) -> EvidenceAudit:
    """Re-check every citation in the assembled document, cached results included."""
    audit = EvidenceAudit()
    for scope in (context.interviewees, context.sections):
        for path, evidence in walk_evidence(scope):
            audit.total += 1
            problems = check_evidence(corpus, evidence)
            if not problems:
                audit.verified += 1
                continue
            if corpus.by_path(evidence.source_file) is None:
                reason = "file_missing"
            elif "AI Interviewer" in problems[0]:
                reason = "wrong_speaker"
            else:
                reason = "not_found"
            audit.unverified.append(
                UnverifiedQuote(
                    location=path,
                    quote=evidence.quote[:200],
                    source_file=evidence.source_file,
                    reason=reason,
                )
            )
    return audit


async def run(
    corpus: Corpus,
    agents: dict[str, Agent],
    counter: TokenCounter,
    cfg: Config,
) -> FirstPassContext:
    started = time.monotonic()
    deps = Deps(corpus=corpus)

    extracts, extract_stage, planned_calls = await run_extract(agents, corpus, counter, cfg, deps)

    # Both reduces depend only on stage 1, so they run concurrently. They stay separate stages
    # rather than one call per section: question deduplication and answer pairing is its own
    # job and has to be evaluable on its own.
    (
        (expert_passes, expert_stage),
        (question_results, question_stage),
    ) = await asyncio.gather(
        run_experts(agents, corpus, extracts, counter, cfg, deps),
        run_questions(agents, corpus, extracts, counter, cfg, deps),
    )

    sections = [
        SectionPass(
            section_slug=slug,
            title=TITLES.get(slug, slug),
            questions=question_results[slug].questions,
        )
        for slug in _section_slugs(corpus, cfg)
        if slug in question_results
    ]

    roster_size = len(cfg.only_experts or corpus.experts)
    for section in sections:
        finalize_questions(section, roster_size)

    stages = [extract_stage, expert_stage, question_stage]
    warnings = [w for s in stages for w in s.warnings]
    costs = [s.usage.cost_usd for s in stages if s.usage.cost_usd is not None]

    context = FirstPassContext(
        run=RunMetadata(
            generated_at=datetime.now(timezone.utc),
            model=cfg.model,
            model_settings={"max_tokens": 16000, "thinking": "adaptive (model default)"},
            max_input_tokens=cfg.max_input_tokens,
            corpus_digest=corpus.digest(),
            prompt_digest=prompts.prompt_digest(),
            schema_digest=_schema_digest(),
            git_sha=runner.git_sha(),
            roster=corpus.experts,
            sources=[
                SourceFile(
                    path=u.rel_path,
                    sha256=u.sha256,
                    words=u.words,
                    expert_slug=u.expert_slug,
                    section_slug=u.section_slug,
                )
                for u in corpus.units
            ],
            call_plan={
                "extract": planned_calls,
                "expert": expert_stage.usage.calls + expert_stage.usage.cached_calls,
                "question": question_stage.usage.calls + question_stage.usage.cached_calls,
            },
            usage=[s.usage for s in stages],
            total_cost_usd=sum(costs) if costs else None,
            wall_clock_s=round(time.monotonic() - started, 2),
            warnings=warnings,
            status="partial" if warnings else "complete",
        ),
        interviewees=[p.profile for p in expert_passes.values()],
        sections=sections,
    )
    context.evidence_audit = audit_evidence(corpus, context)
    if context.evidence_audit.unverified:
        context.run.warnings.append(
            f"{len(context.evidence_audit.unverified)} citations could not be verified; "
            "see evidence_audit."
        )
    return context
