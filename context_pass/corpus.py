"""Load and parse the structured/ transcripts into typed objects.

The roster of interviewees and sections is DISCOVERED from disk, never hardcoded — the
corpus is expected to grow.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from context_pass.sections import SECTIONS, SKIP, TITLES

FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
TURN = re.compile(r"^\*\*(?P<speaker>[^*]+?)\*\*\s*\((?P<ts>\d{2}:\d{2}:\d{2})\)\s*$")

# Quote characters the transcripts mix with their ASCII equivalents.
_PUNCT = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "–": "-", "—": "-", "―": "-", "−": "-",
    "…": "...", " ": " ",
}


def normalize(text: str) -> str:
    """Fold the variations that make verbatim quote matching fail spuriously.

    The transcripts mix curly quotes and em-dashes with ASCII, so a model that copies a
    quote correctly can still fail a naive substring check. Compare normalized forms.
    """
    text = unicodedata.normalize("NFKC", text)
    for bad, good in _PUNCT.items():
        text = text.replace(bad, good)
    return " ".join(text.split()).casefold()


@dataclass(frozen=True)
class Turn:
    """One speaker turn. `is_expert` is False for the AI interviewer."""

    speaker: str
    timestamp: str
    text: str
    is_expert: bool


@dataclass
class Unit:
    """One (expert, section) transcript file — the atomic unit of the pipeline."""

    expert_slug: str
    section_slug: str
    path: Path
    rel_path: str
    frontmatter: dict[str, str]
    turns: list[Turn]
    body: str
    sha256: str

    @property
    def title(self) -> str:
        return TITLES.get(self.section_slug, self.section_slug)

    @property
    def words(self) -> int:
        return len(self.body.split())

    def render(self) -> str:
        """The text handed to a model, with the header it needs to attribute quotes."""
        lines = [
            f"### source_file: {self.rel_path}",
            f"### expert_slug: {self.expert_slug}",
            f"### section_slug: {self.section_slug}  ({self.title})",
            f"### interviewee: {self.frontmatter.get('expert', self.expert_slug)}"
            f" — {self.frontmatter.get('role', 'role not stated')}",
            "",
        ]
        for turn in self.turns:
            who = "EXPERT" if turn.is_expert else "INTERVIEWER"
            lines.append(f"[{turn.timestamp}] {who} ({turn.speaker}):\n{turn.text}\n")
        return "\n".join(lines)

    def expert_text(self) -> str:
        """Only the interviewee's own words — used to police speaker attribution."""
        return "\n".join(t.text for t in self.turns if t.is_expert)


@dataclass
class Corpus:
    root: Path
    units: list[Unit]
    experts: list[str] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.experts = sorted({u.expert_slug for u in self.units})
        known = [slug for slug, _ in SECTIONS]
        found = {u.section_slug for u in self.units}
        # Known sections keep interview order; anything new sorts in after.
        self.sections = [s for s in known if s in found] + sorted(found - set(known))
        self._by_path = {u.rel_path: u for u in self.units}

    def for_expert(self, expert_slug: str) -> list[Unit]:
        order = {s: i for i, s in enumerate(self.sections)}
        return sorted(
            (u for u in self.units if u.expert_slug == expert_slug),
            key=lambda u: order.get(u.section_slug, 999),
        )

    def for_section(self, section_slug: str) -> list[Unit]:
        return sorted(
            (u for u in self.units if u.section_slug == section_slug),
            key=lambda u: u.expert_slug,
        )

    def by_path(self, rel_path: str) -> Unit | None:
        return self._by_path.get(rel_path)

    def digest(self) -> str:
        h = hashlib.sha256()
        for unit in sorted(self.units, key=lambda u: u.rel_path):
            h.update(unit.rel_path.encode())
            h.update(unit.sha256.encode())
        return h.hexdigest()

    def display_name(self, expert_slug: str) -> str:
        units = self.for_expert(expert_slug)
        return units[0].frontmatter.get("expert", expert_slug) if units else expert_slug


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = FRONTMATTER.match(text)
    if not match:
        return {}, text
    data: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            data[key.strip()] = value.strip()
    return data, text[match.end():]


def _parse_turns(body: str) -> list[Turn]:
    turns: list[Turn] = []
    speaker = timestamp = None
    buffer: list[str] = []

    def flush() -> None:
        if speaker is None:
            return
        text = "\n".join(buffer).strip()
        if text:
            turns.append(
                Turn(
                    speaker=speaker,
                    timestamp=timestamp or "",
                    text=text,
                    is_expert=not speaker.lower().startswith("ai interviewer"),
                )
            )

    for line in body.splitlines():
        match = TURN.match(line.strip())
        if match:
            flush()
            speaker = match.group("speaker").strip()
            timestamp = match.group("ts")
            buffer = []
        elif speaker is not None:
            buffer.append(line)
    flush()
    return turns


def load(structured_dir: Path, *, include_skipped: bool = False) -> Corpus:
    """Discover and parse every <section>/<expert>.md under `structured_dir`."""
    units: list[Unit] = []
    for path in sorted(structured_dir.glob("*/*.md")):
        section_slug = path.parent.name
        if not include_skipped and section_slug in SKIP:
            continue
        raw = path.read_text()
        frontmatter, body = _parse_frontmatter(raw)
        turns = _parse_turns(body)
        if not turns:
            continue
        units.append(
            Unit(
                expert_slug=path.stem,
                section_slug=section_slug,
                path=path,
                rel_path=str(path.relative_to(structured_dir.parent)),
                frontmatter=frontmatter,
                turns=turns,
                body=body.strip(),
                sha256=hashlib.sha256(raw.encode()).hexdigest(),
            )
        )
    if not units:
        raise SystemExit(f"no transcript units found under {structured_dir}")
    return Corpus(root=structured_dir, units=units)
