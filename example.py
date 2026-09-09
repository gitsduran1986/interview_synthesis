"""End-to-end example: transcripts in, synthesis out, then read the results.

Run it:

    uv run python example.py --dry-run     # show the plan and the cost, spend nothing
    uv run python example.py               # run it for real

What you need first:

    1. Interview transcripts as .docx in ./raw/   (this repo ships three)
    2. An Anthropic API key, either exported as ANTHROPIC_API_KEY
       or written to a .env file in this directory as ANTHROPIC_API_KEY=sk-ant-...

Everything is cached, so running this twice costs nothing the second time. A first run on
this repo's three interviews is roughly $9.

The point of this file is the second half: once the pipeline has run, the output is a set of
typed objects you can query, not a wall of prose. Read PART 2 for that.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import sys

from interview_synthesis import paths, structure
from interview_synthesis.coding import cli as coding_pass
from interview_synthesis.context import cli as context_pass
from interview_synthesis.context.models import FirstPassContext
from interview_synthesis.synthesis import cli as synthesis_pass
from interview_synthesis.synthesis.models import Synthesis


def check_prerequisites(dry_run: bool) -> str | None:
    """Return a problem to report, or None if we're good to go."""
    if not paths.raw_dir().is_dir() or not list(paths.raw_dir().glob("*.docx")):
        return (
            f"No .docx transcripts found in {paths.raw_dir()}\n"
            f"Put interview transcripts there, then run this again."
        )
    if dry_run:
        return None

    import os

    # A .env in the working directory is read by every entry point.
    if not (os.environ.get("ANTHROPIC_API_KEY") or paths.env_file().exists()):
        return (
            "No Anthropic API key found.\n"
            "  export ANTHROPIC_API_KEY=sk-ant-...\n"
            f"or write it to {paths.env_file()} as ANTHROPIC_API_KEY=sk-ant-...\n"
            "\nOr run with --dry-run to see the plan without spending anything."
        )
    return None


# ─────────────────────────── PART 1: run the pipeline ───────────────────────────


def run_pipeline(dry_run: bool) -> bool:
    """Four stages. Each writes to disk before the next reads it."""
    flags = ["--dry-run"] if dry_run else []

    print("\n[1/4] Splitting transcripts into sections")
    # Stdlib only - no model, no cost. Safe to run every time. It logs a line per file,
    # which is noise here; the count below is the part that matters.
    with contextlib.redirect_stdout(io.StringIO()):
        structure.main()
    print(f"      {len(list(paths.structured_dir().glob('*/*.md')))} section files")

    stages = [
        ("[2/4] Reading interviews - who they are, and what was asked", context_pass),
        ("[3/4] Coding - which question does each answer address", coding_pass),
        ("[4/4] Building the matrix, then interpreting it", synthesis_pass),
    ]
    for label, stage in stages:
        print(f"\n{label}")
        if stage.main(flags) == 0:
            continue
        if dry_run:
            # A later stage plans against the previous stage's output. In a fresh
            # workspace that does not exist yet, so it cannot be measured - a fact about
            # the workspace, not a failure. It prints its own note to stderr above.
            print("      (can't plan yet - needs the previous stage to have run for real)")
            continue
        return False

    return True


# ─────────────────────────── PART 2: read the results ───────────────────────────


def show_results() -> None:
    """The output is typed objects. This is what consuming it looks like."""
    context = FirstPassContext.model_validate_json(
        (paths.out_dir() / "first_pass_context.json").read_text()
    )
    synthesis = Synthesis.model_validate_json(
        (paths.out_dir() / "synthesis.json").read_text()
    )

    print("\n" + "=" * 74)
    print("WHO WAS INTERVIEWED")
    print("=" * 74)
    for person in context.interviewees:
        print(f"\n  {person.display_name or person.expert_slug} - {person.role_title}")
        print(f"    {person.ui_statement}")
        # Whether they run a platform today or ran it at a former employer is a fact,
        # and it decides whether two people are actually comparable.
        for platform in person.platform_experience[:3]:
            print(f"    · {platform.platform}: {platform.relationship}")

    print("\n" + "=" * 74)
    print("THE MATRIX")
    print("=" * 74)
    threads = [c for c in synthesis.columns if c.grain == "thread"]
    filled = [c for c in synthesis.cells if c.grain == "thread" and c.filled]
    print(f"\n  {len(synthesis.rows)} people x {len(threads)} topics, "
          f"{len(filled)}/{len(threads) * len(synthesis.rows)} cells filled")

    print("\n" + "=" * 74)
    print("WHERE THEY DISAGREE")
    print("=" * 74)
    conflicts = [
        (s, c) for s in synthesis.columns_synthesis if s.grain == "thread"
        for c in s.conflicts
    ]
    for column_synthesis, conflict in conflicts[:2]:
        column = synthesis.column(column_synthesis.column_id, "thread")
        print(f"\n  {column.label[:68] if column else column_synthesis.column_id}")
        print(f"  {conflict.statement[:150]}")
        for position in conflict.positions:
            print(f"    {position.row_id}: {position.gist[:60]}")
        if conflict.explains_it:
            print(f"    why: {conflict.explains_it[:90]}")
    print(f"\n  ({len(conflicts)} conflicts in total)")

    print("\n" + "=" * 74)
    print("FINDINGS")
    print("=" * 74)
    for finding in synthesis.findings[:3]:
        print(f"\n  [{finding.kind}/{finding.confidence}] {finding.statement[:160]}")

    print("\n" + "=" * 74)
    print("EVERY CLAIM TRACES BACK")
    print("=" * 74)
    quoted = next(
        (s for s in synthesis.columns_synthesis if s.canonical_quote and s.grain == "thread"),
        None,
    )
    if quoted:
        quote = quoted.canonical_quote
        print(f'\n  "{quote.quote[:110]}"')
        print(f"    - {quote.row_id}")
        # The citation names a cell; the cell names the transcript turn it came from.
        cell = next((c for c in synthesis.cells if c.cell_id == quote.cell_id), None)
        if cell and cell.sources:
            source = cell.sources[0]
            print(f"    from {source.unit_id}, turn {source.turn_index}")
    audit = context.evidence_audit
    print(f"\n  pass 1: {audit.verified}/{audit.total} quotes verified verbatim")

    report = paths.out_dir() / "report.html"
    if report.exists():
        print(f"\n  Open the full report:  {report}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show the plan and the cost without calling a model.",
    )
    parser.add_argument(
        "--results-only", action="store_true",
        help="Skip the pipeline and just read whatever is already in out/.",
    )
    args = parser.parse_args(argv)

    print(f"Workspace: {paths.workspace()}")

    if not args.results_only:
        problem = check_prerequisites(args.dry_run)
        if problem:
            print(f"\n{problem}", file=sys.stderr)
            return 2
        if not run_pipeline(args.dry_run):
            print("\nA stage failed. See the output above.", file=sys.stderr)
            return 1

    if args.dry_run:
        print("\nDry run: nothing was written and nothing was spent.")
        return 0

    if not (paths.out_dir() / "synthesis.json").exists():
        print(
            f"\nNothing to read yet in {paths.out_dir()}. Run without --results-only first.",
            file=sys.stderr,
        )
        return 2

    show_results()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
