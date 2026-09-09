"""SQLite tables for the matrix and its synthesis.

Written into the SAME database as pass 2, deliberately: the whole value of a cell is the
join back to `text` -> `unit` -> `interview`, and a separate file would force an ATTACH on
every query. Keeping them together also means `cell_text.text_id REFERENCES text(text_id)`
turns a dangling source pointer into an IntegrityError instead of a silent bad row.

The JSON document is what a UI fetches; these tables are what an evaluation agent or an
ad-hoc "show me every column where they disagree" query uses. Both are written from the same
in-memory `Synthesis`, so they cannot drift.
"""

from __future__ import annotations

import sqlite3

from interview_synthesis.synthesis.models import Synthesis

DDL = """
CREATE TABLE IF NOT EXISTS matrix_column (
    column_id     TEXT NOT NULL,
    grain         TEXT NOT NULL CHECK (grain IN ('question','thread','section')),
    label         TEXT NOT NULL,
    section_id    TEXT NOT NULL,
    ord           INTEGER NOT NULL DEFAULT 0,
    respondents   INTEGER NOT NULL DEFAULT 0,
    comparable    INTEGER NOT NULL DEFAULT 0,
    anchor_question_id TEXT,
    PRIMARY KEY (grain, column_id)
);

CREATE TABLE IF NOT EXISTS matrix_column_question (
    column_id   TEXT NOT NULL,
    question_id TEXT NOT NULL REFERENCES question(question_id),
    PRIMARY KEY (column_id, question_id)
);

CREATE TABLE IF NOT EXISTS matrix_cell (
    cell_id      TEXT PRIMARY KEY,
    grain        TEXT NOT NULL,
    column_id    TEXT NOT NULL,
    row_id       TEXT NOT NULL REFERENCES interview(interview_id),
    status       TEXT NOT NULL,
    text         TEXT NOT NULL DEFAULT '',
    word_count   INTEGER NOT NULL DEFAULT 0,
    is_disfluent INTEGER NOT NULL DEFAULT 0,
    assent_only  INTEGER NOT NULL DEFAULT 0,
    UNIQUE (grain, column_id, row_id)
);

CREATE TABLE IF NOT EXISTS matrix_cell_text (
    cell_id TEXT NOT NULL REFERENCES matrix_cell(cell_id) ON DELETE CASCADE,
    text_id TEXT NOT NULL REFERENCES text(text_id),
    PRIMARY KEY (cell_id, text_id)
);

CREATE TABLE IF NOT EXISTS synthesis_object (
    synthesis_id  TEXT PRIMARY KEY,
    kind          TEXT NOT NULL CHECK (kind IN ('column','case','finding')),
    grain         TEXT,
    subject_id    TEXT NOT NULL,
    comparability TEXT,
    headline      TEXT NOT NULL DEFAULT '',
    body          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS synthesis_citation (
    synthesis_id TEXT NOT NULL REFERENCES synthesis_object(synthesis_id) ON DELETE CASCADE,
    cell_id      TEXT NOT NULL REFERENCES matrix_cell(cell_id),
    row_id       TEXT NOT NULL,
    quote        TEXT NOT NULL,
    is_canonical INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_cell_column   ON matrix_cell(grain, column_id);
CREATE INDEX IF NOT EXISTS ix_cell_row      ON matrix_cell(row_id);
CREATE INDEX IF NOT EXISTS ix_syn_subject   ON synthesis_object(kind, subject_id);
CREATE INDEX IF NOT EXISTS ix_citation_cell ON synthesis_citation(cell_id);

-- The matrix as a UI or an agent wants to read it.
CREATE VIEW IF NOT EXISTS matrix AS
SELECT c.grain, c.column_id, col.label AS topic, col.section_id,
       c.row_id AS expert, c.status, c.word_count, c.text,
       col.respondents, col.comparable
FROM matrix_cell c
JOIN matrix_column col ON col.column_id = c.column_id AND col.grain = c.grain;
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.commit()


def write(conn: sqlite3.Connection, doc: Synthesis) -> dict[str, int]:
    """Replace the matrix and synthesis tables from one document. Idempotent."""
    ensure_schema(conn)
    for table in (
        "synthesis_citation", "synthesis_object", "matrix_cell_text",
        "matrix_cell", "matrix_column_question", "matrix_column",
    ):
        conn.execute(f"DELETE FROM {table}")

    conn.executemany(
        "INSERT INTO matrix_column (column_id, grain, label, section_id, ord, respondents, "
        "comparable, anchor_question_id) VALUES (?,?,?,?,?,?,?,?)",
        [
            (c.column_id, c.grain, c.label, c.section_id, c.order, c.respondent_count,
             int(c.comparable), c.anchor_question_id)
            for c in doc.columns
        ],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO matrix_column_question (column_id, question_id) VALUES (?,?)",
        [(c.column_id, q) for c in doc.columns for q in c.member_question_ids],
    )
    conn.executemany(
        "INSERT INTO matrix_cell (cell_id, grain, column_id, row_id, status, text, "
        "word_count, is_disfluent, assent_only) VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (c.cell_id, c.grain, c.column_id, c.row_id, c.status, c.text, c.word_count,
             int(c.is_disfluent), int(c.assent_only))
            for c in doc.cells
        ],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO matrix_cell_text (cell_id, text_id) VALUES (?,?)",
        [(c.cell_id, s.text_id) for c in doc.cells for s in c.sources],
    )

    objects, citations = [], []
    for synthesis in doc.columns_synthesis:
        objects.append(
            (synthesis.synthesis_id, "column", synthesis.grain, synthesis.column_id,
             synthesis.comparability, synthesis.headline, synthesis.model_dump_json())
        )
        if synthesis.canonical_quote:
            q = synthesis.canonical_quote
            citations.append((synthesis.synthesis_id, q.cell_id, q.row_id, q.quote, 1))
    for case in doc.cases:
        objects.append(
            (case.synthesis_id, "case", None, case.row_id, None,
             case.through_line, case.model_dump_json())
        )
    for finding in doc.findings:
        objects.append(
            (finding.finding_id, "finding", None, finding.kind, finding.confidence,
             finding.statement, finding.model_dump_json())
        )

    conn.executemany(
        "INSERT INTO synthesis_object (synthesis_id, kind, grain, subject_id, "
        "comparability, headline, body) VALUES (?,?,?,?,?,?,?)",
        objects,
    )
    conn.executemany(
        "INSERT INTO synthesis_citation (synthesis_id, cell_id, row_id, quote, is_canonical) "
        "VALUES (?,?,?,?,?)",
        citations,
    )
    conn.commit()
    return {
        "columns": len(doc.columns),
        "cells": len(doc.cells),
        "synthesis_objects": len(objects),
        "citations": len(citations),
    }
