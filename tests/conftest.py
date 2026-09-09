"""Shared fixtures.

`ALLOW_MODEL_REQUESTS = False` is set at import time so no test can reach a real model,
including one written later in a hurry.
"""

from pathlib import Path

import pydantic_ai.models
import pytest

pydantic_ai.models.ALLOW_MODEL_REQUESTS = False

from interview_synthesis import corpus as corpus_mod  # noqa: E402
from interview_synthesis.budget import HeuristicTokenCounter  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def corpus():
    return corpus_mod.load(ROOT / "structured")


@pytest.fixture(scope="session")
def counter():
    return HeuristicTokenCounter()


@pytest.fixture(scope="session")
def real_quotes(corpus):
    """A verbatim expert quote and a verbatim interviewer quote from the same file."""
    unit = corpus.by_path("structured/02-current-environment/expert-1.md")
    expert = next(t.text for t in unit.turns if t.is_expert and len(t.text) > 120)
    interviewer = next(t.text for t in unit.turns if not t.is_expert and len(t.text) > 120)
    return {"unit": unit, "expert": expert[:120], "interviewer": interviewer[:120]}
