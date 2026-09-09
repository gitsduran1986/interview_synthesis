"""Command-line entrypoint for pass 2.

    uv run code-pass --dry-run      # plan and measure, spend nothing
    uv run code-pass                # code everything -> out/coding.db + out/coding.jsonl
    uv run code-pass --stats        # report on an existing database
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from interview_synthesis import paths

from interview_synthesis import corpus as corpus_mod
from interview_synthesis.budget import make_counter
from interview_synthesis.context.cli import load_dotenv

from interview_synthesis.coding import codebook as codebook_mod
from interview_synthesis.coding import coder
from interview_synthesis.coding import ingest
from interview_synthesis.coding import pipeline
from interview_synthesis.coding import store



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="code-pass",
        description="Code interviewee text against the canonical questions from pass 1.",
    )
    parser.add_argument("--structured", type=Path, default=paths.structured_dir())
    parser.add_argument("--first-pass", type=Path, default=paths.out_dir() / "first_pass_context.json")
    parser.add_argument("--db", type=Path, default=paths.out_dir() / "coding.db")
    parser.add_argument("--codebook", type=Path, default=paths.out_dir() / "codebook.json")
    parser.add_argument("--export", type=Path, default=paths.out_dir() / "coding.jsonl")
    parser.add_argument(
        "--strategy",
        choices=["timestamp", "model"],
        default="timestamp",
        help="timestamp: join pass 1's answer anchors (free, deterministic, but replays "
        "pass 1's coding). model: judge each turn independently.",
    )
    parser.add_argument(
        "--span-fill",
        action="store_true",
        help="timestamp only: unanchored turns inherit the preceding anchor's question. "
        "Raises coverage to 100%% but those rows are inference, marked method='span'.",
    )
    parser.add_argument("--model", default=coder.DEFAULT_MODEL)
    parser.add_argument("--max-input-tokens", type=int, default=120_000)
    parser.add_argument(
        "--units-per-call",
        type=int,
        default=3,
        help="Transcripts per call, so structured output stays inside max_tokens.",
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--only-expert", action="append", default=[], dest="only_experts")
    parser.add_argument("--only-section", action="append", default=[], dest="only_sections")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--refresh", action="store_true", help="Ignore cached codings.")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument(
        "--allow-stale",
        action="store_true",
        help="Run even if the codebook was built from a different corpus.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--stats", action="store_true", help="Report on the existing DB and exit.")
    return parser


def _print_stats(conn) -> None:
    s = store.stats(conn)
    for key, value in s.items():
        print(f"  {key:<24} {value}")
    rows = conn.execute("SELECT method, count(*) c FROM coding GROUP BY 1 ORDER BY c DESC").fetchall()
    if rows:
        print("\n  coded by method:")
        for r in rows:
            print(f"    {r['method']:<10} {r['c']}")
    rows = conn.execute(
        "SELECT question_id, count(*) c FROM coding GROUP BY 1 ORDER BY c DESC LIMIT 5"
    ).fetchall()
    if rows:
        print("\n  most-coded questions:")
        for r in rows:
            print(f"    {r['question_id']}  {r['c']}")
    rows = conn.execute(
        "SELECT reason, count(*) c FROM uncoded GROUP BY 1 ORDER BY c DESC"
    ).fetchall()
    if rows:
        print("\n  uncoded by reason:")
        for r in rows:
            print(f"    {r['reason']:<24} {r['c']}")
    rows = conn.execute(
        "SELECT interview_id, count(*) c FROM coding JOIN text USING(text_id) "
        "GROUP BY 1 ORDER BY 1"
    ).fetchall()
    if rows:
        total = sum(r["c"] for r in rows)
        print("\n  coded turns per interviewee:")
        for r in rows:
            print(f"    {r['interview_id']:<12} {r['c']:>4}  ({r['c']/total:.0%})")
    row = conn.execute(
        "SELECT count(*) n, avg(word_count) a, max(word_count) m FROM text "
        "WHERE speaker_role='expert'"
    ).fetchone()
    print(f"\n  turn size: n={row['n']} mean={row['a']:.0f}w max={row['m']}w")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv(paths.env_file())

    if args.stats:
        if not args.db.exists():
            print(f"no database at {args.db}", file=sys.stderr)
            return 2
        _print_stats(store.connect(args.db))
        return 0

    if not args.first_pass.exists():
        print(
            f"no first pass at {args.first_pass}. Run `uv run context-pass` first.",
            file=sys.stderr,
        )
        return 2

    codebook = codebook_mod.build(args.first_pass)
    args.codebook.parent.mkdir(parents=True, exist_ok=True)
    args.codebook.write_text(codebook.model_dump_json(indent=2))

    corpus = corpus_mod.load(args.structured)
    if codebook.corpus_digest != corpus.digest() and not args.allow_stale:
        print(
            "codebook was built from a different corpus than structured/ holds now.\n"
            "Re-run `uv run context-pass`, or pass --allow-stale to proceed anyway.",
            file=sys.stderr,
        )
        return 2

    counter = make_counter(args.model.split(":")[-1], prefer_api=not args.dry_run)
    cfg = pipeline.Config(
        strategy=args.strategy,
        span_fill=args.span_fill,
        model=args.model,
        max_input_tokens=args.max_input_tokens,
        units_per_call=args.units_per_call,
        concurrency=args.concurrency,
        use_cache=not args.no_cache,
        refresh={"coding"} if args.refresh else set(),
        fail_fast=args.fail_fast,
        out_dir=args.db.parent,
        only_experts=args.only_experts,
        only_sections=args.only_sections,
    )

    plan = pipeline.plan_calls(corpus, codebook, counter, cfg)
    largest = max(
        (
            counter.count(
                coder.build_prompt(section, section, codebook, units)
            )
            for section, units in plan.batches
        ),
        default=0,
    )
    kind = "measured" if counter.exact else "estimated"
    print(f"strategy:  {args.strategy}" + ("  (+span-fill)" if args.span_fill else ""))
    print(f"codebook:  {len(codebook.questions)} questions across {len(codebook.sections())} sections")
    print(f"corpus:    {plan.units} transcripts, {len(corpus.experts)} interviewees")
    if args.strategy == "timestamp":
        print(f"anchors:   {len(codebook.anchors)} from pass 1")
        print("call plan: 0 model calls here - replays pass 1's question stage")
    else:
        print(f"call plan: {plan.calls} coding calls")
        print(f"largest prompt: {largest:,} tokens ({kind}) of {args.max_input_tokens:,} budget")

    if args.dry_run:
        print("\ndry run: nothing written.")
        return 0

    if args.strategy == "model" and not (
        os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    ):
        print(
            f"No Anthropic credentials found. Set ANTHROPIC_API_KEY or put it in "
            f"{paths.env_file()}. Use --dry-run to plan without one.",
            file=sys.stderr,
        )
        return 2

    conn = ingest.build(args.db, corpus, codebook.questions)
    agent = coder.build_agent(args.model) if args.strategy == "model" else None
    summary = asyncio.run(pipeline.run(conn, corpus, codebook, agent, counter, cfg))

    exported = store.export_jsonl(conn, args.export)

    print(f"\nstatus:    {summary['status']}")
    if summary["strategy"] == "model":
        print(
            f"  coding   {summary['calls']} calls ({summary['cached_calls']} cached), "
            f"{summary['input_tokens']:,} in / {summary['output_tokens']:,} out"
        )
    if summary["cost_usd"] is not None:
        print(f"cost:      ${summary['cost_usd']:.2f}")
    print(f"coded:     {summary['coded']} turns")
    if summary.get("disfluent"):
        print(
            f"  of which {summary['disfluent']} are disfluency turns "
            f"(\"can you repeat the question?\") - filter with is_disfluent=0"
        )
    print(f"uncoded:   {summary['uncoded']} turns")
    for warning in summary["warnings"]:
        print(f"warning:   {warning}", file=sys.stderr)
    print(f"wrote:     {args.db}  ({exported} rows -> {args.export})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
