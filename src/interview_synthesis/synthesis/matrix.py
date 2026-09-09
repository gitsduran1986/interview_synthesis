"""Stage 6 - charting the coded data into the framework matrix.

Pure Python. No model call, no cost, deterministic: the same database in produces
byte-identical cells out. Cells carry the interviewee's own words, so nothing here can
paraphrase, soften, or invent.

Empty cells are materialized rather than skipped. The Framework Method treats them as
findings in their own right, and `status` separates a gap in the *interview* (`not_asked`)
from a gap in the *evidence* (`no_answer`).
"""

from __future__ import annotations

import re
import sqlite3

from interview_synthesis.sections import TITLES

from interview_synthesis.synthesis.models import (
    MatrixCell,
    MatrixColumn,
    MatrixRow,
    SourceRef,
    cell_id,
)

REASK_PREFIXES = ("restated:", "re-ask:", "confirmation:")

# A cell whose whole content is assent: verbatim expert speech, but the substance came from
# the interviewer's restatement, so quoting it attributes the interviewer's words to the
# expert. Deliberately narrow - "I'd say an 8." and "Probably BMC comes closest." carry
# content of their own and must stay quotable.
ASSENT = re.compile(
    r"^(yes|yeah|yep|sure|ok|okay|correct|right|agreed|absolutely|exactly|true|no)"
    r"(\s*[,.!-]?\s*(that'?s right|agreed|well said|correct|exactly|i agree|makes sense))?"
    r"[.!]?$"
    r"|^go ahead[.!]?$",
    re.I,
)


def is_assent(text: str) -> bool:
    """True when a turn adds nothing beyond agreeing with what was just put to it."""
    stripped = " ".join(text.split())
    return bool(ASSENT.match(stripped.strip()))


def build_rows(conn: sqlite3.Connection, profiles: dict[str, dict]) -> list[MatrixRow]:
    """Cases, in stable order, labelled from pass 1's interviewee profiles."""
    rows = []
    for record in conn.execute("SELECT * FROM interview ORDER BY interview_id"):
        profile = profiles.get(record["interview_id"], {})
        rows.append(
            MatrixRow(
                row_id=record["interview_id"],
                display_name=profile.get("display_name") or record["display_name"],
                ui_statement=profile.get("ui_statement", ""),
                organization=profile.get("organization"),
                role_title=profile.get("role_title", record["role"] or ""),
            )
        )
    return rows


def derive_threads(conn: sqlite3.Connection) -> list[tuple[str, str, list[str]]]:
    """Group questions into conversational threads. Pure heuristic, no model.

    A question that two or more interviewees answered opens a thread; the drill-downs that
    follow it in the same section belong to it. This is what makes the matrix comparable:
    the interviewer's follow-ups to one person are part of that topic, not topics of their
    own. Returns (thread_id, section_id, [anchor_question_id, *members]).
    """
    respondents = _respondents(conn, "question")
    threads: dict[str, list[str]] = {}
    thread_section: dict[str, str] = {}
    anchor: dict[str, str] = {}

    for record in conn.execute(
        "SELECT question_id, section_id FROM question ORDER BY section_id, question_id"
    ):
        qid, section_id = record["question_id"], record["section_id"]
        if respondents.get(qid, 0) >= 2 or section_id not in anchor:
            # Opens a thread: either genuinely comparable, or the first question in the
            # section with nothing preceding it to attach to.
            anchor[section_id] = qid
            threads[qid] = [qid]
            thread_section[qid] = section_id
        else:
            threads[anchor[section_id]].append(qid)

    return [
        (f"col-{aid.removeprefix('q-')}", thread_section[aid], members)
        for aid, members in threads.items()
    ]


def _respondents(conn: sqlite3.Connection, grain: str) -> dict[str, int]:
    column = "c.question_id" if grain == "question" else "t.section_id"
    return {
        r["col"]: r["n"]
        for r in conn.execute(
            f"SELECT {column} AS col, count(DISTINCT t.interview_id) n "
            "FROM coding c JOIN text t USING(text_id) GROUP BY 1"
        )
    }


def build_columns(conn: sqlite3.Connection) -> list[MatrixColumn]:
    """Codes at both grains: questions, and the sections they nest under."""
    columns: list[MatrixColumn] = []

    counts = _respondents(conn, "section")
    sections = [
        r["section_id"]
        for r in conn.execute("SELECT DISTINCT section_id FROM question ORDER BY section_id")
    ]
    for order, section_id in enumerate(sections):
        n = counts.get(section_id, 0)
        columns.append(
            MatrixColumn(
                column_id=section_id,
                grain="section",
                label=TITLES.get(section_id, section_id),
                section_id=section_id,
                order=order,
                respondent_count=n,
                comparable=n >= 2,
            )
        )

    threads = derive_threads(conn)
    labels = {
        r["question_id"]: r["canonical_question"]
        for r in conn.execute("SELECT question_id, canonical_question FROM question")
    }
    per_section = {}
    for thread_id, section_id, members in threads:
        order = per_section.get(section_id, 0)
        per_section[section_id] = order + 1
        who = set()
        for member in members:
            who |= {
                r["interview_id"]
                for r in conn.execute(
                    "SELECT DISTINCT t.interview_id FROM coding c JOIN text t USING(text_id) "
                    "WHERE c.question_id = ?",
                    (member,),
                )
            }
        columns.append(
            MatrixColumn(
                column_id=thread_id,
                grain="thread",
                label=labels[members[0]],
                section_id=section_id,
                order=order,
                respondent_count=len(who),
                comparable=len(who) >= 2,
                anchor_question_id=members[0],
                member_question_ids=members,
            )
        )

    counts = _respondents(conn, "question")
    per_section: dict[str, int] = {}
    for record in conn.execute(
        "SELECT question_id, section_id, canonical_question FROM question "
        "ORDER BY section_id, question_id"
    ):
        section_id = record["section_id"]
        order = per_section.get(section_id, 0)
        per_section[section_id] = order + 1
        n = counts.get(record["question_id"], 0)
        columns.append(
            MatrixColumn(
                column_id=record["question_id"],
                grain="question",
                label=record["canonical_question"],
                section_id=section_id,
                order=order,
                respondent_count=n,
                comparable=n >= 2,
                is_reask=record["canonical_question"].strip().lower().startswith(REASK_PREFIXES),
            )
        )
    return columns


def _cell_sources(
    conn: sqlite3.Connection,
    grain: str,
    column_id: str,
    row_id: str,
    members: list[str] | None = None,
):
    """The coded turns behind one cell, in transcript order."""
    select = (
        "SELECT t.text_id, t.unit_id, t.turn_index, t.raw_text, t.word_count, c.is_disfluent "
        "FROM coding c JOIN text t USING(text_id) WHERE "
    )
    order = " ORDER BY t.unit_id, t.turn_index"
    if grain == "thread":
        placeholders = ",".join("?" * len(members or []))
        return conn.execute(
            f"{select} c.question_id IN ({placeholders}) AND t.interview_id = ?{order}",
            (*(members or []), row_id),
        ).fetchall()
    where = "c.question_id = ?" if grain == "question" else "t.section_id = ?"
    return conn.execute(f"{select}{where} AND t.interview_id = ?{order}", (column_id, row_id)).fetchall()


def asked_index(first_pass: dict) -> set[tuple[str, str]]:
    """(question_id, expert_slug) pairs the interviewer actually put to someone.

    Pass 2's database records only what WAS coded, so it cannot tell "never asked" from
    "asked and gave nothing". Pass 1's `asked_of` is the authoritative record of who was
    asked, and that distinction is the whole reason empty cells are worth materializing.
    """
    return {
        (question["question_id"], asking["expert_slug"])
        for section in first_pass.get("sections", [])
        for question in section.get("questions", [])
        for asking in question.get("asked_of", [])
    }


def _was_asked(
    conn: sqlite3.Connection,
    grain: str,
    column_id: str,
    row_id: str,
    asked: set[tuple[str, str]],
    members: list[str] | None = None,
) -> bool:
    if grain == "question":
        return (column_id, row_id) in asked
    if grain == "thread":
        return any((member, row_id) in asked for member in (members or []))
    # At section grain, taking part in the section is the equivalent signal.
    return bool(
        conn.execute(
            "SELECT 1 FROM text WHERE section_id = ? AND interview_id = ? "
            "AND speaker_role = 'expert' LIMIT 1",
            (column_id, row_id),
        ).fetchone()
    )


def build_cells(
    conn: sqlite3.Connection,
    rows: list[MatrixRow],
    columns: list[MatrixColumn],
    asked: set[tuple[str, str]] | None = None,
) -> list[MatrixCell]:
    """Every (column, row) pair, filled or not."""
    cells: list[MatrixCell] = []
    for column in columns:
        for row in rows:
            sources = _cell_sources(
                conn, column.grain, column.column_id, row.row_id, column.member_question_ids
            )
            identifier = cell_id(column.grain, column.column_id, row.row_id)

            if not sources:
                was_asked = _was_asked(
                    conn, column.grain, column.column_id, row.row_id,
                    asked or set(), column.member_question_ids,
                )
                cells.append(
                    MatrixCell(
                        cell_id=identifier,
                        grain=column.grain,
                        column_id=column.column_id,
                        row_id=row.row_id,
                        status="no_answer" if was_asked else "not_asked",
                    )
                )
                continue

            all_disfluent = all(s["is_disfluent"] for s in sources)
            all_assent = all(is_assent(s["raw_text"]) for s in sources)
            cells.append(
                MatrixCell(
                    cell_id=identifier,
                    grain=column.grain,
                    column_id=column.column_id,
                    row_id=row.row_id,
                    status="disfluent_only" if all_disfluent else "answered",
                    text="\n\n".join(s["raw_text"] for s in sources),
                    word_count=sum(s["word_count"] for s in sources),
                    sources=[
                        SourceRef(
                            text_id=s["text_id"],
                            unit_id=s["unit_id"],
                            turn_index=s["turn_index"],
                            word_count=s["word_count"],
                        )
                        for s in sources
                    ],
                    is_disfluent=all_disfluent,
                    assent_only=all_assent and not all_disfluent,
                )
            )
    return cells


def comparability(respondent_count: int, row_count: int) -> str:
    """Derived in Python, never asked of the model - it is arithmetic."""
    if respondent_count <= 0:
        return "none"
    if respondent_count == 1:
        return "single_source"
    return "all_rows" if respondent_count >= row_count else "partial"


def chart(conn: sqlite3.Connection, first_pass: dict):
    """Stage 6 in one call."""
    profiles = {p["expert_slug"]: p for p in first_pass.get("interviewees", [])}
    rows = build_rows(conn, profiles)
    columns = build_columns(conn)
    cells = build_cells(conn, rows, columns, asked_index(first_pass))
    return rows, columns, cells
