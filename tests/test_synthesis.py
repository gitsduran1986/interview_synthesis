"""Pass 3: framework matrix (stage 6) and its synthesis (stage 7). No tokens spent."""

import json
from pathlib import Path

import pytest
from pydantic_ai import ModelRetry

from coding import store as coding_store
from synthesis import agents as agents_mod
from synthesis import matrix, pipeline
from synthesis.models import (
    Citation,
    ColumnSynthesis,
    ColumnSynthesisBatch,
    Synthesis,
    cell_id,
)

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "out/coding.db"
FIRST_PASS = ROOT / "out/first_pass_context.json"

pytestmark = pytest.mark.skipif(
    not (DB.exists() and FIRST_PASS.exists()), reason="needs pass 1 and pass 2 output"
)


@pytest.fixture(scope="module")
def charted():
    conn = coding_store.connect(DB)
    first_pass = json.loads(FIRST_PASS.read_text())
    rows, columns, cells = matrix.chart(conn, first_pass)
    return rows, columns, cells


# --------------------------- stage 6: charting ---------------------------


def test_every_column_row_pair_has_exactly_one_cell(charted):
    rows, columns, cells = charted
    assert len(cells) == len(columns) * len(rows)
    assert len({c.cell_id for c in cells}) == len(cells)


def test_threads_make_the_matrix_comparable(charted):
    """The reason the thread grain exists: questions alone leave 44 columns uncomparable."""
    rows, columns, cells = charted
    threads = [c for c in columns if c.grain == "thread"]
    questions = [c for c in columns if c.grain == "question"]

    single_threads = [c for c in threads if c.respondent_count == 1]
    single_questions = [c for c in questions if c.respondent_count == 1]

    assert not single_threads, "threads should have no single-source columns"
    assert len(single_questions) > 20, "questions do, which is why threads exist"
    assert all(c.comparable for c in threads)


def test_every_question_belongs_to_exactly_one_thread(charted):
    _, columns, _ = charted
    members = [
        q for c in columns if c.grain == "thread" for q in c.member_question_ids
    ]
    questions = {c.column_id for c in columns if c.grain == "question"}
    assert sorted(members) == sorted(questions)
    assert len(members) == len(set(members)), "a question must not be in two threads"


def test_thread_anchor_is_its_first_member(charted):
    _, columns, _ = charted
    for column in (c for c in columns if c.grain == "thread"):
        assert column.anchor_question_id == column.member_question_ids[0]
        assert column.column_id == f"col-{column.anchor_question_id.removeprefix('q-')}"


def test_cells_are_verbatim_and_never_interviewer_speech(charted):
    """Charting is a projection - it must not be able to invent or paraphrase."""
    conn = coding_store.connect(DB)
    _, _, cells = charted
    interviewer = {
        r["raw_text"]
        for r in conn.execute("SELECT raw_text FROM text WHERE speaker_role='interviewer'")
    }
    for cell in cells:
        if not cell.filled:
            continue
        for source in cell.sources:
            row = conn.execute(
                "SELECT raw_text, speaker_role FROM text WHERE text_id=?", (source.text_id,)
            ).fetchone()
            assert row["speaker_role"] == "expert"
            assert row["raw_text"] in cell.text
        assert cell.text not in interviewer


def test_empty_cells_are_materialized_with_a_reason(charted):
    rows, columns, cells = charted
    empty = [c for c in cells if not c.filled]
    assert empty, "a sparse grain should have empty cells"
    assert all(c.status in {"not_asked", "no_answer", "disfluent_only"} for c in empty)
    assert all(c.text == "" for c in empty if c.status in {"not_asked", "no_answer"})


def test_charting_is_deterministic(charted):
    conn = coding_store.connect(DB)
    first_pass = json.loads(FIRST_PASS.read_text())
    again = matrix.chart(conn, first_pass)
    before = [c.model_dump_json() for c in charted[2]]
    after = [c.model_dump_json() for c in again[2]]
    assert before == after


# --------------------------- the assent guard ---------------------------


def test_assent_detection_is_narrow():
    """Bare agreement is not quotable; a short answer with content of its own is."""
    assert matrix.is_assent("Yes.")
    assert matrix.is_assent("Yeah, well said.")
    assert matrix.is_assent("Yes, agreed.")
    assert matrix.is_assent("Go ahead.")

    assert not matrix.is_assent("I'd say an 8.")
    assert not matrix.is_assent("Probably BMC comes closest.")
    assert not matrix.is_assent("Customization, scalability, and integration.")
    assert not matrix.is_assent("Yes, there was role separation across ITIL roles.")


def test_assent_cells_are_flagged_unquotable(charted):
    _, _, cells = charted
    assent = [c for c in cells if c.assent_only and c.grain == "question"]
    assert assent, "this corpus has confirmation cells"
    for cell in assent:
        assert not cell.quotable
        assert len(cell.text.split()) <= 4


# --------------------------- citation validator ---------------------------


def _cells_by_id(cells):
    return {c.cell_id: c for c in cells}


def _quotable(cells):
    return next(c for c in cells if c.quotable and len(c.text.split()) > 12)


def test_verbatim_quote_from_the_right_cell_passes(charted):
    _, _, cells = charted
    cell = _quotable(cells)
    citation = Citation(quote=cell.text[:60], cell_id=cell.cell_id, row_id=cell.row_id)
    assert agents_mod.check_citations(_cells_by_id(cells), citation) == []


def test_fabricated_quote_is_rejected(charted):
    _, _, cells = charted
    cell = _quotable(cells)
    citation = Citation(
        quote="nobody in this corpus ever said this", cell_id=cell.cell_id, row_id=cell.row_id
    )
    problems = agents_mod.check_citations(_cells_by_id(cells), citation)
    assert problems and "not in cell" in problems[0]


def test_quote_from_a_different_cell_is_rejected(charted):
    """Stricter than pass 1: real words filed under the wrong topic must not pass."""
    _, _, cells = charted
    source = _quotable(cells)
    other = next(
        c for c in cells
        if c.quotable and c.cell_id != source.cell_id and c.row_id == source.row_id
    )
    citation = Citation(quote=source.text[:50], cell_id=other.cell_id, row_id=other.row_id)
    problems = agents_mod.check_citations(_cells_by_id(cells), citation)
    assert problems and "not in cell" in problems[0]


def test_quote_attributed_to_the_wrong_interviewee_is_rejected(charted):
    _, _, cells = charted
    cell = _quotable(cells)
    wrong = "expert-2" if cell.row_id != "expert-2" else "expert-3"
    citation = Citation(quote=cell.text[:50], cell_id=cell.cell_id, row_id=wrong)
    problems = agents_mod.check_citations(_cells_by_id(cells), citation)
    assert problems and "belongs to" in problems[0]


def test_assent_cell_cannot_be_quoted(charted):
    """The signature failure this pass must not commit: 'Yes.' as evidence."""
    _, _, cells = charted
    cell = next(c for c in cells if c.assent_only)
    citation = Citation(quote=cell.text, cell_id=cell.cell_id, row_id=cell.row_id)
    problems = agents_mod.check_citations(_cells_by_id(cells), citation)
    assert problems and "assent" in problems[0]


def test_smart_punctuation_quote_is_accepted(charted):
    _, _, cells = charted
    cell = next(
        (c for c in cells if c.quotable and any(ch in c.text for ch in "'’—")), None
    )
    if cell is None:
        pytest.skip("no smart punctuation in a quotable cell")
    citation = Citation(quote=cell.text[:60], cell_id=cell.cell_id, row_id=cell.row_id)
    assert agents_mod.check_citations(_cells_by_id(cells), citation) == []


# --------------------------- comparability guard ---------------------------


def test_single_source_column_cannot_report_agreement(charted):
    _, columns, cells = charted
    single = next(c for c in columns if c.grain == "question" and c.respondent_count == 1)
    deps = agents_mod.SynthDeps(cells=_cells_by_id(cells))
    batch = ColumnSynthesisBatch(
        columns=[
            ColumnSynthesis(
                column_id=single.column_id,
                headline="they agreed",
                agreements=[
                    {
                        "statement": "invented consensus",
                        "strength": "explicit",
                        "positions": [
                            {"row_id": "expert-1", "stance": "supports", "gist": "a"},
                            {"row_id": "expert-2", "stance": "supports", "gist": "b"},
                        ],
                    }
                ],
            )
        ]
    )

    class Ctx:
        pass

    ctx = Ctx()
    ctx.deps = deps
    with pytest.raises(ModelRetry, match="nothing to agree"):
        agents_mod._comparability_validator(ctx, batch)


def test_comparability_is_derived_not_asked(charted):
    rows, columns, _ = charted
    threads = [c for c in columns if c.grain == "thread"]
    assert matrix.comparability(3, len(rows)) == "all_rows"
    assert matrix.comparability(2, len(rows)) == "partial"
    assert matrix.comparability(1, len(rows)) == "single_source"
    assert matrix.comparability(0, len(rows)) == "none"
    assert sum(1 for c in threads if c.respondent_count == 3) == 24


# --------------------------- UI addressability ---------------------------


def test_ids_are_stable_and_resolvable(charted):
    rows, columns, cells = charted
    doc = Synthesis(
        run={"generated_at": "2026-01-01T00:00:00Z", "model": "test"},
        rows=rows, columns=columns, cells=cells,
    )
    for column in columns:
        for row in rows:
            found = doc.cell(column.grain, column.column_id, row.row_id)
            assert found is not None
            assert found.cell_id == cell_id(column.grain, column.column_id, row.row_id)


def test_grid_is_render_ready(charted):
    rows, columns, cells = charted
    doc = Synthesis(
        run={"generated_at": "2026-01-01T00:00:00Z", "model": "test"},
        rows=rows, columns=columns, cells=cells,
    )
    grid = doc.grid("thread")
    assert len(grid) == sum(1 for c in columns if c.grain == "thread")
    assert all(len(r) == len(rows) for r in grid)


def test_columns_nest_under_sections(charted):
    _, columns, cells = charted
    doc = Synthesis(
        run={"generated_at": "2026-01-01T00:00:00Z", "model": "test"},
        rows=[], columns=columns, cells=cells,
    )
    for section in (c for c in columns if c.grain == "section"):
        threads = doc.columns_in(section.column_id, "thread")
        assert threads, f"{section.column_id} should contain threads"
        assert all(t.section_id == section.column_id for t in threads)


def test_document_round_trips(charted):
    rows, columns, cells = charted
    doc = Synthesis(
        run={"generated_at": "2026-01-01T00:00:00Z", "model": "test"},
        rows=rows, columns=columns, cells=cells,
    )
    again = Synthesis.model_validate_json(doc.model_dump_json())
    assert len(again.cells) == len(cells)


# --------------------------- prompt bounds ---------------------------


def test_column_prompt_is_bounded_by_the_column(charted, counter):
    rows, columns, cells = charted
    threads = [c for c in columns if c.grain == "thread"]
    sizes = [counter.count(pipeline.render_column(c, cells, rows)) for c in threads]
    assert max(sizes) < 6000, "a column prompt must stay small regardless of corpus size"
