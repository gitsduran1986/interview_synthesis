"""Shared agent-call machinery: caching, persistence, usage, non-fatal failures.

Lifted out of `pipeline.py` so every pass in this repo uses one cache implementation
rather than a copy each. A pass supplies its own config (anything satisfying `RunnerConfig`)
and its own `schema_digest`, so caches from different passes never collide even when the
prompt text happens to match.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel
from pydantic_ai import Agent, capture_run_messages

from interview_synthesis.context.models import StageUsage


@runtime_checkable
class RunnerConfig(Protocol):
    """What `call()` needs from a pass's config object."""

    model: str
    out_dir: Path
    use_cache: bool
    refresh: set[str]
    fail_fast: bool


class Stage:
    """Per-stage bookkeeping: usage, cache hits, and non-fatal failures."""

    def __init__(self, name: str) -> None:
        self.usage = StageUsage(stage=name)
        self.warnings: list[str] = []

    def record(self, result: Any) -> None:
        usage = result.usage
        self.usage.calls += 1
        self.usage.input_tokens += usage.input_tokens or 0
        self.usage.output_tokens += usage.output_tokens or 0
        if usage.cost is not None:
            self.usage.cost_usd = (self.usage.cost_usd or 0.0) + float(usage.cost)


def cache_key(cfg: RunnerConfig, payload: str, *digests: str) -> str:
    """Content address for one call.

    Every input that can change the answer goes in: the model, the caller's prompt and
    schema digests, and the payload itself. Anything omitted here is something a rerun
    will silently serve stale.
    """
    material = " ".join([cfg.model, *digests, payload])
    return hashlib.sha256(material.encode()).hexdigest()[:32]


def write_model(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=2))


def git_sha() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return None


async def call(
    agent: Agent,
    prompt: str,
    deps: Any,
    output_type: type[BaseModel],
    *,
    cfg: RunnerConfig,
    stage: Stage,
    unit_id: str,
    digests: tuple[str, ...] = (),
) -> BaseModel | None:
    """One agent call, with cache, persistence, and non-fatal failure handling.

    Returns None when the call failed and `fail_fast` is off — the caller records the
    absence rather than the whole run collapsing.
    """
    stage_name = stage.usage.stage
    key = cache_key(cfg, prompt, *digests)
    cache_path = cfg.out_dir / ".cache" / stage_name / f"{key}.json"
    stage_path = cfg.out_dir / "stages" / stage_name / f"{unit_id}.json"

    if cfg.use_cache and stage_name not in cfg.refresh and cache_path.exists():
        stage.usage.cached_calls += 1
        result = output_type.model_validate_json(cache_path.read_text())
        write_model(stage_path, result)
        return result

    messages: list[Any] = []
    try:
        with capture_run_messages() as messages:
            result = await agent.run(prompt, deps=deps)
    except Exception as exc:  # noqa: BLE001 - one bad unit must not sink the whole run
        if cfg.fail_fast:
            raise
        stage.warnings.append(f"{stage_name}/{unit_id} failed: {type(exc).__name__}: {exc}")
        failure = cfg.out_dir / "stages" / stage_name / f"{unit_id}.failure.json"
        failure.parent.mkdir(parents=True, exist_ok=True)
        failure.write_text(
            json.dumps(
                {
                    "error": f"{type(exc).__name__}: {exc}",
                    "messages": [str(m) for m in messages],
                },
                indent=2,
            )
        )
        return None

    stage.record(result)
    write_model(cache_path, result.output)
    write_model(stage_path, result.output)
    return result.output
