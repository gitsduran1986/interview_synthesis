"""example.py is the front door — if it breaks, a newcomer's first run breaks.

Everything here is dry-run or reads existing output, so no tokens are spent.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "example.py"


def run(*args, cwd=None, env=None):
    import os

    environment = {**os.environ, **(env or {})}
    return subprocess.run(
        [sys.executable, str(EXAMPLE), *args],
        capture_output=True, text=True, timeout=180,
        cwd=str(cwd or ROOT), env=environment,
    )


def test_dry_run_plans_every_stage_without_spending():
    result = run("--dry-run")
    assert result.returncode == 0, result.stderr
    for stage in ("[1/4]", "[2/4]", "[3/4]", "[4/4]"):
        assert stage in result.stdout
    assert "nothing was spent" in result.stdout


def test_missing_transcripts_says_what_to_do(tmp_path):
    """A newcomer with an empty workspace should get an instruction, not a traceback."""
    result = run(env={"INTERVIEW_SYNTHESIS_WORKSPACE": str(tmp_path)})
    assert "No .docx transcripts found" in result.stderr
    assert "Traceback" not in result.stderr


def test_missing_api_key_says_what_to_do(tmp_path):
    """Transcripts present, no credentials — the message must name both ways to fix it."""
    import shutil

    shutil.copytree(ROOT / "raw", tmp_path / "raw")
    env = {"INTERVIEW_SYNTHESIS_WORKSPACE": str(tmp_path), "ANTHROPIC_API_KEY": ""}
    result = run(env=env)
    assert "No Anthropic API key found" in result.stderr
    assert "ANTHROPIC_API_KEY" in result.stderr
    assert "--dry-run" in result.stderr
    assert "Traceback" not in result.stderr


def test_dry_run_works_without_credentials(tmp_path):
    """The whole point of --dry-run: you can see the plan before committing to spend."""
    import shutil

    shutil.copytree(ROOT / "raw", tmp_path / "raw")
    env = {"INTERVIEW_SYNTHESIS_WORKSPACE": str(tmp_path), "ANTHROPIC_API_KEY": ""}
    result = run("--dry-run", env=env)
    assert result.returncode == 0, result.stderr
    assert "section files" in result.stdout
    assert "call plan" in result.stdout


@pytest.mark.skipif(
    not (ROOT / "out/synthesis.json").exists(), reason="needs a completed run"
)
def test_results_only_reads_existing_output():
    result = run("--results-only")
    assert result.returncode == 0, result.stderr
    for heading in ("WHO WAS INTERVIEWED", "THE MATRIX", "WHERE THEY DISAGREE",
                    "FINDINGS", "EVERY CLAIM TRACES BACK"):
        assert heading in result.stdout
    assert "quotes verified verbatim" in result.stdout
