"""Command-line entrypoint.

    uv run context-pass --dry-run        # plan and measure, spend nothing
    uv run context-pass                  # full run
    uv run context-pass --only-expert expert-3
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from context_pass import corpus as corpus_mod
from context_pass.agents import DEFAULT_MODEL, build_agents
from context_pass.budget import make_counter, plan_extract_calls
from context_pass.pipeline import Config, run
from context_pass.sections import eval_sections

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path) -> None:
    """Read KEY=value lines from .env, without overriding a real environment variable.

    Keeps credentials in a gitignored file rather than in shell history or a command line.
    """
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="context-pass",
        description="First-pass context extraction over structured interview transcripts.",
    )
    parser.add_argument("--structured", type=Path, default=ROOT / "structured")
    parser.add_argument("--out", type=Path, default=ROOT / "out")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--max-input-tokens",
        type=int,
        default=120_000,
        help="Per-call input budget. Work is split to fit it.",
    )
    parser.add_argument(
        "--units-per-call",
        type=int,
        default=4,
        help="Cap on transcript files per extract call, so output stays within max_tokens.",
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--only-expert", action="append", default=[], dest="only_experts")
    parser.add_argument("--only-section", action="append", default=[], dest="only_sections")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument(
        "--refresh",
        action="append",
        default=[],
        choices=["extract", "expert", "question", "theme", "all"],
        help="Ignore cached results for a stage.",
    )
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument(
        "--strict-evidence",
        action="store_true",
        help="Exit non-zero if any citation could not be verified.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Measure the corpus and print the call plan without calling the model.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv(ROOT / ".env")

    if not args.dry_run and not (
        os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    ):
        print(
            "No Anthropic credentials found. Set ANTHROPIC_API_KEY in the environment or "
            f"put it in {ROOT / '.env'} (gitignored). Use --dry-run to plan without one.",
            file=sys.stderr,
        )
        return 2

    corpus = corpus_mod.load(args.structured)
    counter = make_counter(args.model.split(":")[-1], prefer_api=not args.dry_run)

    units = [
        u
        for u in corpus.units
        if (not args.only_experts or u.expert_slug in args.only_experts)
        and (not args.only_sections or u.section_slug in args.only_sections)
    ]
    plan = plan_extract_calls(
        units, counter, args.max_input_tokens, max_units_per_call=args.units_per_call
    )
    sections = [
        s
        for s in eval_sections()
        if s in corpus.sections and (not args.only_sections or s in args.only_sections)
    ]
    experts = [e for e in corpus.experts if not args.only_experts or e in args.only_experts]
    total_tokens = sum(counter.count(c.render()) for batch in plan for c in batch)

    kind = "measured" if counter.exact else "estimated"
    print(f"corpus:    {len(units)} files, {len(experts)} interviewees, {len(sections)} sections")
    print(f"input:     {total_tokens:,} tokens ({kind})")
    print(
        f"call plan: {len(plan)} extract + {len(experts)} expert + {len(sections)} question "
        f"+ {len(sections)} theme = {len(plan) + len(experts) + 2 * len(sections)} calls"
    )

    if args.dry_run:
        print("\ndry run: no model calls made.")
        return 0

    cfg = Config(
        model=args.model,
        max_input_tokens=args.max_input_tokens,
        units_per_call=args.units_per_call,
        concurrency=args.concurrency,
        use_cache=not args.no_cache,
        refresh=(
            {"extract", "expert", "question", "theme"}
            if "all" in args.refresh
            else set(args.refresh)
        ),
        fail_fast=args.fail_fast,
        out_dir=args.out,
        only_experts=args.only_experts,
        only_sections=args.only_sections,
    )
    agents = build_agents(args.model)
    context = asyncio.run(run(corpus, agents, counter, cfg))

    destination = args.out / "first_pass_context.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(context.model_dump_json(indent=2))

    audit = context.evidence_audit
    print(f"\nstatus:    {context.run.status}")
    print(f"evidence:  {audit.verified}/{audit.total} citations verified")
    for stage in context.run.usage:
        print(
            f"  {stage.stage:<8} {stage.calls} calls ({stage.cached_calls} cached), "
            f"{stage.input_tokens:,} in / {stage.output_tokens:,} out"
        )
    if context.run.total_cost_usd is not None:
        print(f"cost:      ${context.run.total_cost_usd:.2f}")
    for warning in context.run.warnings:
        print(f"warning:   {warning}", file=sys.stderr)
    print(f"wrote:     {destination}")

    if args.strict_evidence and audit.unverified:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
