"""SQLite storage for coded text.

SQLite because it is stdlib (no new dependency), and because the consumer is a downstream
evaluation agent: one `sqlite3 out/coding.db "SELECT ..."` beats loading a JSON blob into
context. The foreign keys and CHECK constraints are the point, not decoration — they make a
hallucinated question id a database error rather than a silent bad row.

The database is derived and rebuildable from `structured/` + the codebook, so it is never
the system of record. `coding.jsonl` is the committed artifact.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS interview (
    interview_id  TEXT PRIMARY KEY,
    display_name  TEXT,
    role          TEXT
);

CREATE TABLE IF NOT EXISTS unit (
    unit_id       TEXT PRIMARY KEY,
    interview_id  TEXT NOT NULL REFERENCES interview(interview_id) ON DELETE CASCADE,
    section_id    TEXT NOT NULL,
    section_role  TEXT NOT NULL CHECK (section_role IN ('eval','profile_only')),
    sha256        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS question (
    question_id        TEXT PRIMARY KEY,
    section_id         TEXT NOT NULL,
    canonical_question TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS text (
    text_id       TEXT PRIMARY KEY,
    unit_id       TEXT NOT NULL REFERENCES unit(unit_id) ON DELETE CASCADE,
    interview_id  TEXT NOT NULL REFERENCES interview(interview_id) ON DELETE CASCADE,
    section_id    TEXT NOT NULL,
    turn_index    INTEGER NOT NULL,
    speaker_role  TEXT NOT NULL CHECK (speaker_role IN ('expert','interviewer')),
    raw_text      TEXT NOT NULL,
    text_sha      TEXT NOT NULL,
    word_count    INTEGER NOT NULL,
    UNIQUE(unit_id, turn_index)
);

CREATE TABLE IF NOT EXISTS coding_run (
    run_id           TEXT PRIMARY KEY,
    started_at       TEXT NOT NULL,
    strategy         TEXT NOT NULL DEFAULT 'model',
    model            TEXT NOT NULL,
    corpus_digest    TEXT NOT NULL,
    codebook_digest  TEXT NOT NULL,
    prompt_digest    TEXT NOT NULL,
    git_sha          TEXT,
    status           TEXT NOT NULL CHECK (status IN ('complete','partial'))
);

-- One row per coded turn. text_id is the primary key, which is what enforces
-- "each turn is coded uniquely"; many rows may share a question_id.
CREATE TABLE IF NOT EXISTS coding (
    text_id      TEXT PRIMARY KEY REFERENCES text(text_id) ON DELETE CASCADE,
    question_id  TEXT NOT NULL REFERENCES question(question_id),
    -- How this row was decided. 'anchor' = pass 1 said so; 'span' = inferred by
    -- forward-fill; 'model' = judged by the coding agent. Never conflate them.
    method       TEXT NOT NULL DEFAULT 'model'
                 CHECK (method IN ('anchor','span','model','manual')),
    is_disfluent INTEGER NOT NULL DEFAULT 0,
    confidence   REAL,
    rationale    TEXT,
    run_id       TEXT NOT NULL REFERENCES coding_run(run_id)
);

CREATE TABLE IF NOT EXISTS uncoded (
    text_id  TEXT PRIMARY KEY REFERENCES text(text_id) ON DELETE CASCADE,
    reason   TEXT NOT NULL CHECK (reason IN
               ('no_question_addressed','model_declined','non_eval_section','unit_failed')),
    run_id   TEXT NOT NULL REFERENCES coding_run(run_id)
);

CREATE INDEX IF NOT EXISTS ix_coding_question  ON coding(question_id);
CREATE INDEX IF NOT EXISTS ix_coding_method    ON coding(method);
CREATE INDEX IF NOT EXISTS ix_text_unit        ON text(unit_id, turn_index);
CREATE INDEX IF NOT EXISTS ix_text_interview   ON text(interview_id, section_id);
CREATE INDEX IF NOT EXISTS ix_text_role        ON text(speaker_role);
CREATE INDEX IF NOT EXISTS ix_unit_interview   ON unit(interview_id, section_id);

CREATE VIEW IF NOT EXISTS coded_text AS
SELECT t.text_id,
       t.interview_id AS expert,
       t.raw_text,
       c.question_id  AS canonical_question_id,
       t.section_id
FROM text t
JOIN coding c USING (text_id);
"""

# The five columns the deliverable promises, in order.
EXPORT_COLUMNS = ("text_id", "expert", "raw_text", "canonical_question_id", "section_id")


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        # WAL + NORMAL keeps large ingests from fsyncing per transaction.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.commit()


# These are UPSERTs, not INSERT OR REPLACE, and that distinction matters. REPLACE is a
# DELETE followed by an INSERT, so with foreign keys on it destroys any row in a later pass's
# tables that points here - re-running pass 2 after pass 3 has written its matrix would fail.
# ON CONFLICT DO UPDATE keeps the row's identity and just refreshes its columns.


def replace_questions(conn: sqlite3.Connection, rows: Iterable[tuple[str, str, str]]) -> None:
    """Load the codebook, updating rows in place."""
    conn.executemany(
        "INSERT INTO question (question_id, section_id, canonical_question) VALUES (?, ?, ?) "
        "ON CONFLICT(question_id) DO UPDATE SET "
        "section_id=excluded.section_id, canonical_question=excluded.canonical_question",
        rows,
    )


def upsert_interviews(conn: sqlite3.Connection, rows: Iterable[tuple[str, str | None, str | None]]) -> None:
    conn.executemany(
        "INSERT INTO interview (interview_id, display_name, role) VALUES (?, ?, ?) "
        "ON CONFLICT(interview_id) DO UPDATE SET "
        "display_name=excluded.display_name, role=excluded.role",
        rows,
    )


def upsert_unit(conn: sqlite3.Connection, row: tuple[str, str, str, str, str]) -> None:
    conn.execute(
        "INSERT INTO unit (unit_id, interview_id, section_id, section_role, sha256) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(unit_id) DO UPDATE SET "
        "interview_id=excluded.interview_id, section_id=excluded.section_id, "
        "section_role=excluded.section_role, sha256=excluded.sha256",
        row,
    )


def upsert_texts(conn: sqlite3.Connection, rows: Iterable[tuple[Any, ...]]) -> None:
    conn.executemany(
        "INSERT INTO text (text_id, unit_id, interview_id, section_id, turn_index, "
        "speaker_role, raw_text, text_sha, word_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(text_id) DO UPDATE SET unit_id=excluded.unit_id, "
        "interview_id=excluded.interview_id, section_id=excluded.section_id, "
        "turn_index=excluded.turn_index, speaker_role=excluded.speaker_role, "
        "raw_text=excluded.raw_text, text_sha=excluded.text_sha, "
        "word_count=excluded.word_count",
        rows,
    )


def record_run(conn: sqlite3.Connection, row: tuple[Any, ...]) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO coding_run (run_id, started_at, strategy, model, "
        "corpus_digest, codebook_digest, prompt_digest, git_sha, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        row,
    )


def clear_unit_codings(conn: sqlite3.Connection, unit_id: str) -> None:
    """Drop this unit's derived rows so a re-run replaces rather than duplicates."""
    conn.execute(
        "DELETE FROM coding WHERE text_id IN (SELECT text_id FROM text WHERE unit_id = ?)",
        (unit_id,),
    )
    conn.execute(
        "DELETE FROM uncoded WHERE text_id IN (SELECT text_id FROM text WHERE unit_id = ?)",
        (unit_id,),
    )


def insert_codings(conn: sqlite3.Connection, rows: Iterable[tuple[Any, ...]]) -> None:
    """rows: (text_id, question_id, method, is_disfluent, confidence, rationale, run_id)"""
    conn.executemany(
        "INSERT OR REPLACE INTO coding (text_id, question_id, method, is_disfluent, "
        "confidence, rationale, run_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


def insert_uncoded(conn: sqlite3.Connection, rows: Iterable[tuple[str, str, str]]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO uncoded (text_id, reason, run_id) VALUES (?, ?, ?)", rows
    )


def unit_sha(conn: sqlite3.Connection, unit_id: str) -> str | None:
    row = conn.execute("SELECT sha256 FROM unit WHERE unit_id = ?", (unit_id,)).fetchone()
    return row["sha256"] if row else None


def iter_coded(conn: sqlite3.Connection) -> Iterator[dict[str, Any]]:
    """Stream the deliverable rows, ordered so the export is stable across runs."""
    cursor = conn.execute(
        f"SELECT {', '.join(EXPORT_COLUMNS)} FROM coded_text ORDER BY text_id"
    )
    for row in cursor:
        yield {k: row[k] for k in EXPORT_COLUMNS}


def export_jsonl(conn: sqlite3.Connection, path: Path) -> int:
    """Write the flat five-column artifact. Sorted, so re-runs produce identical bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w") as handle:
        for row in iter_coded(conn):
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            count += 1
    return count


def stats(conn: sqlite3.Connection) -> dict[str, Any]:
    def scalar(sql: str) -> int:
        return conn.execute(sql).fetchone()[0]

    return {
        "interviews": scalar("SELECT count(*) FROM interview"),
        "units": scalar("SELECT count(*) FROM unit"),
        "questions": scalar("SELECT count(*) FROM question"),
        "text_rows": scalar("SELECT count(*) FROM text"),
        "expert_turns": scalar("SELECT count(*) FROM text WHERE speaker_role='expert'"),
        "coded": scalar("SELECT count(*) FROM coding"),
        "uncoded": scalar("SELECT count(*) FROM uncoded"),
        "questions_with_no_text": scalar(
            "SELECT count(*) FROM question q WHERE NOT EXISTS "
            "(SELECT 1 FROM coding WHERE question_id = q.question_id)"
        ),
        "disfluent_codings": scalar("SELECT count(*) FROM coding WHERE is_disfluent = 1"),
    }
