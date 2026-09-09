"""Command-line entrypoint for pass 3.

    uv run synth-pass --dry-run    # plan and measure, spend nothing
    uv run synth-pass              # -> out/synthesis.json
    uv run synth-pass --stats      # report on an existing synthesis
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path

from interview_synthesis import paths

from interview_synthesis.coding import store as coding_store
from interview_synthesis.budget import make_counter
from interview_synthesis.context.cli import load_dotenv

from interview_synthesis.synthesis import agents as agents_mod
from interview_synthesis.synthesis import report as synth_report
from interview_synthesis.synthesis import store as synth_store
from interview_synthesis.synthesis import matrix
from interview_synthesis.synthesis import pipeline
from interview_synthesis.synthesis.models import Synthesis



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="synth-pass",
        description="Chart the coded interviews into a framework matrix and synthesize it.",
    )
    parser.add_argument("--db", type=Path, default=paths.out_dir() / "coding.db")
    parser.add_argument("--first-pass", type=Path, default=paths.out_dir() / "first_pass_context.json")
    parser.add_argument("--out", type=Path, default=paths.out_dir() / "synthesis.json")
    parser.add_argument("--model", default=agents_mod.DEFAULT_MODEL)
    parser.add_argument("--max-input-tokens", type=int, default=120_000)
    parser.add_argument("--columns-per-call", type=int, default=4)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--only-section", action="append", default=[], dest="only_sections")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument(
        "--refresh", action="append", default=[],
        choices=["column", "case", "findings", "all"],
    )
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--stats", action="store_true")
    return parser


def _stats(path: Path) -> int:
    if not path.exists():
        print(f"no synthesis at {path}", file=sys.stderr)
        return 2
    doc = Synthesis.model_validate_json(path.read_text())
    for key, value in doc.run.counts.items():
        print(f"  {key:<22} {value}")
    print("\n  columns by grain:")
    for grain, n in Counter(c.grain for c in doc.columns).most_common():
        cells = [c for c in doc.cells if c.grain == grain]
        filled = sum(1 for c in cells if c.filled)
        print(f"    {grain:<10} {n:>3} cols  {filled:>3}/{len(cells):<3} filled")
    print("\n  synthesis by comparability:")
    for k, n in Counter(s.comparability for s in doc.columns_synthesis).most_common():
        print(f"    {k:<16} {n}")
    print(
        f"\n  agreements {sum(len(s.agreements) for s in doc.columns_synthesis)}"
        f"   conflicts {sum(len(s.conflicts) for s in doc.columns_synthesis)}"
        f"   canonical quotes {sum(1 for s in doc.columns_synthesis if s.canonical_quote)}"
    )
    print(f"  findings {len(doc.findings)}   cases {len(doc.cases)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv(paths.env_file())

    if args.stats:
        return _stats(args.out)

    for path, what in ((args.db, "coding database"), (args.first_pass, "first pass")):
        if not path.exists():
            print(f"no {what} at {path}. Run the earlier passes first.", file=sys.stderr)
            return 2

    conn = coding_store.connect(args.db)
    first_pass = json.loads(args.first_pass.read_text())
    rows, columns, cells = matrix.chart(conn, first_pass)

    cfg = pipeline.Config(
        model=args.model,
        max_input_tokens=args.max_input_tokens,
        columns_per_call=args.columns_per_call,
        concurrency=args.concurrency,
        use_cache=not args.no_cache,
        refresh=(
            {"column", "case", "findings"} if "all" in args.refresh else set(args.refresh)
        ),
        fail_fast=args.fail_fast,
        out_dir=args.out.parent,
        only_sections=args.only_sections,
    )

    counter = make_counter(args.model.split(":")[-1], prefer_api=not args.dry_run)
    targets = [
        c for c in columns
        if c.grain in cfg.grains
        and (not cfg.only_sections or c.section_id in cfg.only_sections)
    ]
    rendered = [pipeline.render_column(c, cells, rows) for c in targets]
    batches = max(1, -(-len(targets) // cfg.columns_per_call))
    largest = max((counter.count(r) for r in rendered), default=0)

    print("matrix:")
    for grain in ("section", "thread", "question"):
        cols = [c for c in columns if c.grain == grain]
        cs = [c for c in cells if c.grain == grain]
        filled = sum(1 for c in cs if c.filled)
        mark = "  <- synthesized" if grain in cfg.grains else ""
        print(
            f"  {grain:<9} {len(cols):>3} columns  {filled:>3}/{len(cs):<3} cells filled "
            f"({filled/len(cs):.0%}){mark}"
        )
    print(f"\ncall plan: {batches} column + {len(rows)} case + 1 findings "
          f"= {batches + len(rows) + 1} calls")
    print(f"largest column prompt: {largest:,} tokens of {args.max_input_tokens:,} budget")

    if args.dry_run:
        print("\ndry run: nothing written.")
        return 0

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        print(f"No Anthropic credentials. Set ANTHROPIC_API_KEY or use {paths.env_file()}.",
              file=sys.stderr)
        return 2

    run = conn.execute(
        "SELECT corpus_digest, codebook_digest FROM coding_run ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    doc = asyncio.run(
        pipeline.run(
            conn, first_pass, agents_mod.build_agents(args.model), counter, cfg,
            coding_digests=(run["corpus_digest"], run["codebook_digest"]) if run else ("", ""),
        )
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = doc.model_dump_json(indent=2)
    args.out.write_text(payload)
    written = synth_store.write(conn, doc)


    print(f"\nstatus:    {doc.run.status}")
    for usage in doc.run.usage:
        print(f"  {usage.stage:<9} {usage.calls} calls ({usage.cached_calls} cached), "
              f"{usage.input_tokens:,} in / {usage.output_tokens:,} out")
    if doc.run.total_cost_usd is not None:
        print(f"cost:      ${doc.run.total_cost_usd:.2f}")
    print(f"columns synthesized: {len(doc.columns_synthesis)}  "
          f"agreements {sum(len(s.agreements) for s in doc.columns_synthesis)}  "
          f"conflicts {sum(len(s.conflicts) for s in doc.columns_synthesis)}")
    print(f"cases {len(doc.cases)}   findings {len(doc.findings)}")
    for warning in doc.run.warnings:
        print(f"warning:   {warning}", file=sys.stderr)
    print(f"wrote:     {args.out}")
    print(f"           {args.db}  ({written['cells']} cells, "
          f"{written['synthesis_objects']} synthesis objects)")
    built = synth_report.build(args.out, paths.ui_template(), paths.out_dir() / "report.html")
    print(f"           {built['path']}  ({built['bytes'] // 1024}KB, self-contained)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
