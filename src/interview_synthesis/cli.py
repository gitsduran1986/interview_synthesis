"""One command: interview documents in, browsable synthesis out.

    uv run synthesize                 # everything, then tells you to open the UI
    uv run synthesize --dry-run       # the plan and the cost, spending nothing

Each pass is still its own entry point (`context-pass`, `code-pass`, `synth-pass`) and can
be run, cached, and evaluated on its own. This just runs them in order and reports as it
goes, which is what the upload-and-watch experience needs underneath it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import webbrowser
from pathlib import Path

from interview_synthesis import paths


STAGES = [
    ("Structuring transcripts", "structure"),
    ("Reading interviews", "context"),
    ("Coding answers", "coding"),
    ("Building matrix & synthesizing", "synthesis"),
]


def _run_structure() -> int:
    """The .docx -> markdown split. Stdlib only, no model."""
    import contextlib
    import io

    from interview_synthesis import structure

    with contextlib.redirect_stdout(io.StringIO()):
        structure.main()
    return 0


def _cost(path: Path, key: str = "total_cost_usd") -> float:
    try:
        data = json.loads(path.read_text())
        return float(data.get("run", {}).get(key) or 0.0)
    except Exception:  # noqa: BLE001 - a missing or partial artifact just reports nothing
        return 0.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="synthesize",
        description="Run every pass: transcripts -> profiles -> coding -> matrix -> synthesis.",
    )
    parser.add_argument("--raw", type=Path, default=paths.raw_dir())
    parser.add_argument("--dry-run", action="store_true", help="Plan and price it, spend nothing.")
    parser.add_argument("--skip-structure", action="store_true")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--report", type=Path, default=paths.out_dir() / "report.html")
    parser.add_argument("--no-open", action="store_true", help="Build the page but don't open it.")
    args = parser.parse_args(argv)

    documents = sorted(args.raw.glob("*.docx"))
    if not documents:
        print(f"no .docx files in {args.raw}", file=sys.stderr)
        return 2
    print(f"{len(documents)} document(s) in {args.raw}\n")

    from interview_synthesis.coding import cli as coding_cli
    from interview_synthesis.context import cli as context_cli
    from interview_synthesis.synthesis import cli as synthesis_cli

    passthrough = ["--dry-run"] if args.dry_run else []
    if args.no_cache:
        passthrough.append("--no-cache")

    started = time.monotonic()
    for label, key in STAGES:
        print(f"── {label} " + "─" * max(0, 52 - len(label)))
        if key == "structure":
            # Structuring is free, local and deterministic, so it runs even on a dry run -
            # otherwise the later stages have nothing to plan against and the call plan is
            # a guess rather than a measurement.
            if args.skip_structure:
                print("   skipped\n")
                continue
            _run_structure()
            files = len(list((paths.structured_dir()).glob("*/*.md")))
            print(f"   {files} section files\n")
            continue

        entry = {"context": context_cli, "coding": coding_cli, "synthesis": synthesis_cli}[key]
        code = entry.main(passthrough)
        print()
        if code == 0:
            continue
        if args.dry_run:
            # A later pass plans against the previous pass's output. In a fresh workspace
            # that does not exist yet, so it cannot be measured - which is a fact about the
            # workspace, not a failure of the run.
            print("   cannot plan yet - needs the previous pass to have run\n")
            continue
        print(f"{label} failed (exit {code})", file=sys.stderr)
        return code

    if args.dry_run:
        print("dry run: nothing was written and nothing was spent.")
        return 0

    # The deliverable: one self-contained page, no server and no sibling files.
    from interview_synthesis.synthesis import report

    built = report.build(
        paths.out_dir() / "synthesis.json", paths.ui_template(), args.report
    )

    spent = _cost(paths.out_dir() / "first_pass_context.json") + _cost(paths.out_dir() / "synthesis.json")
    print("─" * 60)
    print(f"done in {time.monotonic() - started:.0f}s · ${spent:.2f} spent")
    print(
        f"\n   {built['path']}  "
        f"({built['bytes'] // 1024}KB · {built['interviewees']} interviewees · "
        f"{built['findings']} findings)"
    )
    if not args.no_open:
        webbrowser.open(built["path"].resolve().as_uri())
        print("   opened in your browser")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
