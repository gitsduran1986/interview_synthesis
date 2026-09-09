"""Token measurement, packing, and chunking.

Nothing here assumes the corpus fits in one call. Every stage asks the budgeter how to
split its work, so the same code path handles three interviews and three hundred.

Token counts come from the Anthropic API's own counter when credentials are available
(the only way to get true counts — a generic BPE estimate is the wrong tokenizer). A
labelled heuristic is used when there are no credentials, so `--dry-run` and the test
suite work offline.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeVar

from context_pass.corpus import Turn, Unit

T = TypeVar("T")


class TokenCounter(Protocol):
    exact: bool

    def count(self, text: str) -> int: ...


class HeuristicTokenCounter:
    """Offline fallback. Deliberately conservative — it over-estimates rather than under."""

    exact = False

    def __init__(self, chars_per_token: float = 3.2) -> None:
        self.chars_per_token = chars_per_token

    def count(self, text: str) -> int:
        return max(1, int(len(text) / self.chars_per_token) + 1)


class ApiTokenCounter:
    """Real counts via `client.messages.count_tokens`, memoized per content hash."""

    exact = True

    def __init__(self, model: str = "claude-opus-5") -> None:
        from anthropic import Anthropic

        self._client = Anthropic()
        self._model = model
        self._cache: dict[str, int] = {}

    def count(self, text: str) -> int:
        key = hashlib.sha256(text.encode()).hexdigest()
        if key not in self._cache:
            result = self._client.messages.count_tokens(
                model=self._model, messages=[{"role": "user", "content": text}]
            )
            self._cache[key] = result.input_tokens
        return self._cache[key]


def make_counter(model: str = "claude-opus-5", *, prefer_api: bool = True) -> TokenCounter:
    """Real counter when credentials exist, heuristic otherwise. Never raises."""
    if prefer_api and (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        try:
            return ApiTokenCounter(model)
        except Exception:  # missing SDK, bad credentials — fall through to the estimate
            pass
    return HeuristicTokenCounter()


# ─────────────────────────── packing ───────────────────────────


@dataclass
class Chunk:
    """A slice of one unit's turns. `count == 1` means the unit was not split."""

    unit: Unit
    turns: list[Turn]
    index: int = 0
    count: int = 1

    @property
    def is_whole(self) -> bool:
        return self.count == 1

    def render(self) -> str:
        if self.is_whole:
            return self.unit.render()
        header = (
            f"### source_file: {self.unit.rel_path}\n"
            f"### expert_slug: {self.unit.expert_slug}\n"
            f"### section_slug: {self.unit.section_slug}  ({self.unit.title})\n"
            f"### PART {self.index + 1} of {self.count} of this file\n"
        )
        body = "\n".join(
            f"[{t.timestamp}] {'EXPERT' if t.is_expert else 'INTERVIEWER'} ({t.speaker}):\n{t.text}\n"
            for t in self.turns
        )
        return f"{header}\n{body}"


def chunk_unit(unit: Unit, counter: TokenCounter, budget: int, *, overlap: int = 1) -> list[Chunk]:
    """Split one unit on TURN boundaries, never mid-turn.

    Splitting inside a turn would sever a question from its answer, which is exactly the
    relationship this pipeline exists to record. `overlap` repeats trailing turns at the
    head of the next chunk so a Q/A pair straddling a boundary survives in one piece.
    """
    if counter.count(unit.render()) <= budget:
        return [Chunk(unit=unit, turns=list(unit.turns))]

    groups: list[list[Turn]] = []
    current: list[Turn] = []
    current_tokens = 0
    for turn in unit.turns:
        turn_tokens = counter.count(turn.text) + 16  # speaker/timestamp line
        if current and current_tokens + turn_tokens > budget:
            groups.append(current)
            current = current[-overlap:] if overlap else []
            current_tokens = sum(counter.count(t.text) + 16 for t in current)
        current.append(turn)
        current_tokens += turn_tokens
    if current:
        groups.append(current)

    return [
        Chunk(unit=unit, turns=turns, index=i, count=len(groups)) for i, turns in enumerate(groups)
    ]


def pack(
    items: Sequence[T],
    size_of: Callable[[T], int],
    budget: int,
    *,
    max_items: int | None = None,
) -> list[list[T]]:
    """Greedy first-fit in the given order. Order carries meaning, so it is preserved.

    `max_items` caps batch length independently of the token budget. Input size is not the
    only constraint: a call that reads cheaply can still be asked to WRITE more structured
    output than `max_tokens` allows, and the model will silently truncate or stub fields.
    Cap the items so output stays bounded too.

    An item larger than the budget on its own gets a batch to itself — callers that can
    subdivide (see `chunk_unit`) should do so before calling this.
    """
    batches: list[list[T]] = []
    current: list[T] = []
    current_size = 0
    for item in items:
        size = size_of(item)
        too_big = current and current_size + size > budget
        too_many = max_items is not None and len(current) >= max_items
        if too_big or too_many:
            batches.append(current)
            current, current_size = [], 0
        current.append(item)
        current_size += size
    if current:
        batches.append(current)
    return batches


def plan_extract_calls(
    units: Iterable[Unit],
    counter: TokenCounter,
    budget: int,
    *,
    max_units_per_call: int = 4,
) -> list[list[Chunk]]:
    """Stage 1 plan: chunk anything oversized, then pack chunks into calls.

    Chunks of the same unit are never separated across calls, so a split file is always
    extracted with its neighbouring context in view.
    """
    chunks: list[Chunk] = []
    for unit in units:
        chunks.extend(chunk_unit(unit, counter, budget))
    return pack(
        chunks, lambda c: counter.count(c.render()), budget, max_items=max_units_per_call
    )


def plan_reduce_rounds(
    payloads: Sequence[str],
    counter: TokenCounter,
    budget: int,
    *,
    max_items: int | None = None,
) -> list[list[str]]:
    """Reduce plan: one batch if it all fits, otherwise several to be merged afterwards.

    The reduce output types are closed under merging (merging two `SectionPass` lists
    yields a `SectionPass` list), so a multi-batch reduce is folded by re-running the same
    agent over its own partial results — no second agent definition needed.
    """
    return pack(payloads, counter.count, budget, max_items=max_items)
