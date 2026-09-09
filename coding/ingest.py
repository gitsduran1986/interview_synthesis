"""Corpus -> database rows.

Populates `interview`, `unit`, and `text` with facts taken straight from the transcripts.
Nothing here calls a model; this is the immutable spine the coding overlay hangs off.

Interviewer turns are stored too, flagged by `speaker_role` and never coded. They give the
coder the preceding question as context and let a reviewer audit a label against what was
actually asked, without depending on the first pass recording the question wording.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from context_pass.corpus import Corpus, Unit, normalize
from context_pass.sections import PROFILE_ONLY, SKIP

from coding import store

COMMIT_EVERY = 200  # units; keeps a large ingest from fsyncing per unit


def text_id(unit_id: str, turn_index: int) -> str:
    """Stable row identity.

    Deliberately not derived from a timestamp, and deliberately not from the text itself:
    a fixed typo would otherwise orphan the row's coding. Position within a file is stable
    under edits to wording, and `text_sha` carries the content signal separately.
    """
    return hashlib.sha256(f"{unit_id}\x1f{turn_index}".encode()).hexdigest()[:16]


def section_role(section_slug: str) -> str:
    """'eval' sections get coded; profile-only ones are stored but never coded."""
    if section_slug in SKIP:
        raise ValueError(f"{section_slug} is a skipped section and should not be ingested")
    return "profile_only" if section_slug in PROFILE_ONLY else "eval"


def unit_rows(unit: Unit) -> Iterator[tuple]:
    """One row per turn, both speakers, in file order."""
    for index, turn in enumerate(unit.turns):
        body = turn.text
        yield (
            text_id(unit.rel_path, index),
            unit.rel_path,
            unit.expert_slug,
            unit.section_slug,
            index,
            "expert" if turn.is_expert else "interviewer",
            body,
            hashlib.sha256(normalize(body).encode()).hexdigest(),
            len(body.split()),
        )


def ingest(conn: sqlite3.Connection, corpus: Corpus) -> dict[str, int]:
    """Load interviews, units, and text. Idempotent: re-running replaces rows in place."""
    store.upsert_interviews(
        conn,
        (
            (
                slug,
                corpus.display_name(slug),
                next(
                    (u.frontmatter.get("role") for u in corpus.for_expert(slug)),
                    None,
                ),
            )
            for slug in corpus.experts
        ),
    )

    counts = {"units": 0, "texts": 0, "expert_turns": 0}
    for position, unit in enumerate(corpus.units, start=1):
        store.upsert_unit(
            conn,
            (
                unit.rel_path,
                unit.expert_slug,
                unit.section_slug,
                section_role(unit.section_slug),
                unit.sha256,
            ),
        )
        rows = list(unit_rows(unit))
        store.upsert_texts(conn, rows)
        counts["units"] += 1
        counts["texts"] += len(rows)
        counts["expert_turns"] += sum(1 for r in rows if r[5] == "expert")
        if position % COMMIT_EVERY == 0:
            conn.commit()
    conn.commit()
    return counts


def load_codebook(conn: sqlite3.Connection, questions) -> int:
    store.replace_questions(
        conn, ((q.question_id, q.section_id, q.canonical_question) for q in questions)
    )
    conn.commit()
    return len(questions)


def build(db_path: Path, corpus: Corpus, questions) -> sqlite3.Connection:
    conn = store.connect(db_path)
    store.ensure_schema(conn)
    load_codebook(conn, questions)
    ingest(conn, corpus)
    return conn
