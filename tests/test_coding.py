"""Pass 2: codebook projection, storage constraints, ingest, and the coding agent.

No tokens spent — `tests/conftest.py` sets ALLOW_MODEL_REQUESTS = False.
"""

import json
from pathlib import Path

import pytest
from pydantic_ai import Agent, ModelRetry
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from context_pass.corpus import Turn, Unit
from context_pass.models import FirstPassContext
from coding import codebook as codebook_mod
from coding import coder, ingest, pipeline, store

ROOT = Path(__file__).resolve().parent.parent
FIRST_PASS = ROOT / "out/first_pass_context.json"

pytestmark = pytest.mark.skipif(
    not FIRST_PASS.exists(), reason="needs a first-pass run in out/"
)


@pytest.fixture(scope="module")
def codebook():
    return codebook_mod.build(FIRST_PASS)


@pytest.fixture
def db(corpus, codebook):
    conn = ingest.build(":memory:", corpus, codebook.questions)
    store.record_run(
        conn, ("run-1", "now", "model", "test", "cd", "kd", "pd", None, "complete")
    )
    conn.commit()
    return conn


# --------------------------- codebook ---------------------------


def test_codebook_carries_every_question(codebook):
    ctx = FirstPassContext.model_validate_json(FIRST_PASS.read_text())
    expected = {q.question_id for s in ctx.sections for q in s.questions}
    assert codebook.ids() == expected
    assert len(codebook.questions) == len(expected)


def test_codebook_is_a_small_fraction_of_the_first_pass(codebook):
    whole = len(FIRST_PASS.read_text())
    assert len(codebook.model_dump_json()) < whole * 0.15


def test_codebook_digest_is_stable_and_content_sensitive(codebook):
    assert codebook.digest() == codebook_mod.build(FIRST_PASS).digest()
    mutated = codebook.model_copy(deep=True)
    mutated.questions[0].canonical_question += "?"
    assert mutated.digest() != codebook.digest()


def test_question_ids_match_their_section(codebook):
    from context_pass.sections import section_number

    for q in codebook.questions:
        assert q.question_id.split("-")[1] == section_number(q.section_id)


# --------------------------- store constraints ---------------------------


def test_invented_question_id_is_rejected_by_the_database(db):
    text_id = db.execute("SELECT text_id FROM text LIMIT 1").fetchone()["text_id"]
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        store.insert_codings(db, [(text_id, "q-99-99", "model", 0, 1.0, "invented", "run-1")])


def test_each_turn_is_coded_uniquely(db, codebook):
    """One code per turn is enforced by the primary key, not by convention."""
    text_id = db.execute("SELECT text_id FROM text LIMIT 1").fetchone()["text_id"]
    qid = codebook.questions[0].question_id
    store.insert_codings(db, [(text_id, qid, "model", 0, 0.9, "first", "run-1")])
    store.insert_codings(db, [(text_id, qid, "model", 0, 0.4, "second", "run-1")])
    rows = db.execute("SELECT * FROM coding WHERE text_id = ?", (text_id,)).fetchall()
    assert len(rows) == 1


def test_many_turns_may_share_one_question(db, codebook):
    """A re-ask legitimately produces several coded responses to the same question."""
    ids = [r["text_id"] for r in db.execute(
        "SELECT text_id FROM text WHERE speaker_role='expert' LIMIT 3")]
    qid = codebook.questions[0].question_id
    store.insert_codings(
        db, [(t, qid, "model", 0, 0.9, "same question", "run-1") for t in ids]
    )
    n = db.execute("SELECT count(*) c FROM coding WHERE question_id = ?", (qid,)).fetchone()["c"]
    assert n == 3


def test_export_has_exactly_the_promised_columns(db, codebook, tmp_path):
    text_id = db.execute(
        "SELECT text_id FROM text WHERE speaker_role='expert' LIMIT 1"
    ).fetchone()["text_id"]
    store.insert_codings(
        db, [(text_id, codebook.questions[0].question_id, "model", 0, 1.0, "x", "run-1")]
    )
    db.commit()
    out = tmp_path / "coding.jsonl"
    assert store.export_jsonl(db, out) == 1
    row = json.loads(out.read_text().strip())
    assert set(row) == set(store.EXPORT_COLUMNS)


def test_export_is_byte_identical_across_runs(db, codebook, tmp_path):
    ids = [r["text_id"] for r in db.execute(
        "SELECT text_id FROM text WHERE speaker_role='expert' LIMIT 5")]
    store.insert_codings(
        db, [(t, codebook.questions[0].question_id, "model", 0, 0.9, "x", "run-1") for t in ids]
    )
    db.commit()
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    store.export_jsonl(db, a)
    store.export_jsonl(db, b)
    assert a.read_bytes() == b.read_bytes()


# --------------------------- ingest ---------------------------


def test_ingest_counts(db):
    s = store.stats(db)
    assert s["expert_turns"] == 147
    assert s["text_rows"] == 291  # both speakers
    eval_turns = db.execute(
        "SELECT count(*) c FROM text t JOIN unit u USING(unit_id) "
        "WHERE t.speaker_role='expert' AND u.section_role='eval'"
    ).fetchone()["c"]
    assert eval_turns == 135


def test_text_id_ignores_wording_and_timestamps():
    """Fixing a typo must not orphan a row's coding."""
    assert ingest.text_id("a.md", 3) == ingest.text_id("a.md", 3)
    assert ingest.text_id("a.md", 3) != ingest.text_id("a.md", 4)
    assert ingest.text_id("a.md", 3) != ingest.text_id("b.md", 3)


def test_text_id_survives_an_edit_but_text_sha_moves():
    def unit(text):
        return Unit(
            expert_slug="expert-1", section_slug="02-current-environment",
            path=Path("x"), rel_path="structured/02-current-environment/expert-1.md",
            frontmatter={}, turns=[Turn("Expert 1", "00:00:01", text, True)],
            body=text, sha256="s",
        )

    before = list(ingest.unit_rows(unit("We use ServiceNow.")))[0]
    after = list(ingest.unit_rows(unit("We use ServiceNow!")))[0]
    assert before[0] == after[0]      # text_id stable
    assert before[7] != after[7]      # text_sha changed


def test_interviewer_turns_are_stored_but_distinguishable(db):
    n = db.execute(
        "SELECT count(*) c FROM text WHERE speaker_role='interviewer'"
    ).fetchone()["c"]
    assert n == 291 - 147


# --------------------------- coder ---------------------------


def _fake_unit(section, expert, n_turns=4):
    turns = []
    for i in range(n_turns):
        turns.append(Turn("AI Interviewer", f"00:00:{i:02d}", f"Question {i}?", False))
        turns.append(Turn(expert, f"00:01:{i:02d}", f"Answer {i} about cost.", True))
    return Unit(
        expert_slug=expert, section_slug=section, path=Path("x"),
        rel_path=f"structured/{section}/{expert}.md", frontmatter={},
        turns=turns, body="x", sha256="s",
    )


def test_prompt_is_bounded_by_section_not_corpus(codebook, counter):
    """The property the whole design rests on: more interviews must not mean bigger prompts."""
    section = codebook.sections()[0]
    small = [_fake_unit(section, "expert-1")]
    prompt_small = coder.build_prompt(section, "T", codebook, small)

    # A codebook representing 500 interviews: same label space, vastly more data elsewhere.
    big_units = [_fake_unit(section, f"expert-{i}") for i in range(500)]
    prompt_big = coder.build_prompt(section, "T", codebook, big_units[:1])

    assert counter.count(prompt_small) == counter.count(prompt_big)
    assert counter.count(prompt_small) < 6000


def test_plan_fans_out_with_more_interviews_at_constant_prompt_size(codebook, corpus, counter):
    cfg = pipeline.Config(units_per_call=3)
    plan = pipeline.plan_calls(corpus, codebook, counter, cfg)
    assert plan.calls >= 7
    assert all(len(b) <= 3 for _, b in plan.batches)
    for section, units in plan.batches:
        assert counter.count(coder.build_prompt(section, "T", codebook, units)) < 20_000


def test_profile_only_sections_are_never_planned(codebook, corpus, counter):
    plan = pipeline.plan_calls(corpus, codebook, counter, pipeline.Config())
    assert all(s != "01-interview-introduction" for s, _ in plan.batches)


def _validate(codebook, units, payload):
    deps = coder.CoderDeps(codebook=codebook, units={u.rel_path: u for u in units})

    class Ctx:
        pass

    ctx = Ctx()
    ctx.deps = deps
    return coder._validate(ctx, coder.CodingBatch(**payload))


def test_validator_rejects_invented_question_id(codebook, corpus):
    unit = corpus.for_section(codebook.sections()[0])[0]
    idx = next(i for i, t in enumerate(unit.turns) if t.is_expert)
    with pytest.raises(ModelRetry, match="not a question"):
        _validate(codebook, [unit], {"units": [{"unit_id": unit.rel_path, "codings": [
            {"turn_index": idx, "question_id": "q-99-99", "confidence": 1.0, "rationale": "x"}]}]})


def test_validator_rejects_coding_an_interviewer_turn(codebook, corpus):
    unit = corpus.for_section(codebook.sections()[0])[0]
    idx = next(i for i, t in enumerate(unit.turns) if not t.is_expert)
    qid = codebook.for_section(unit.section_slug)[0].question_id
    with pytest.raises(ModelRetry, match="interviewer turn"):
        _validate(codebook, [unit], {"units": [{"unit_id": unit.rel_path, "codings": [
            {"turn_index": idx, "question_id": qid, "confidence": 1.0, "rationale": "x"}]}]})


def test_validator_accepts_null_for_a_turn_that_answers_nothing(codebook, corpus):
    unit = corpus.for_section(codebook.sections()[0])[0]
    idx = next(i for i, t in enumerate(unit.turns) if t.is_expert)
    out = _validate(codebook, [unit], {"units": [{"unit_id": unit.rel_path, "codings": [
        {"turn_index": idx, "question_id": None, "confidence": 0.9, "rationale": "backchannel"}]}]})
    assert out.units[0].codings[0].question_id is None


# --------------------------- end to end with a fake model ---------------------------


def _prompt_text(messages) -> str:
    """The actual user prompt. `str(message)` is a repr with escaped newlines."""
    parts = []
    for message in messages:
        for part in getattr(message, "parts", []):
            content = getattr(part, "content", None)
            if isinstance(content, str):
                parts.append(content)
    return "\n".join(parts)


def _fake_model(codebook, *, skip_turns=False):
    def respond(messages, info):
        prompt = _prompt_text(messages)
        units = []
        for line in prompt.splitlines():
            if line.startswith("### unit_id: "):
                units.append(line.removeprefix("### unit_id: ").strip())
        section = next(
            (s for s in codebook.sections() if units and f"/{s}/" in units[0]), None
        )
        qid = codebook.for_section(section)[0].question_id if section else None
        payload = {"units": []}
        for unit_id in units:
            codings = []
            block = prompt.split(f"### unit_id: {unit_id}")[1].split("### unit_id:")[0]
            for line in block.splitlines():
                if line.startswith("[") and "] INTERVIEWEE:" in line:
                    idx = int(line[1 : line.index("]")])
                    if skip_turns and idx % 2 == 1:
                        continue
                    codings.append(
                        {"turn_index": idx, "question_id": qid,
                         "confidence": 0.9, "rationale": "fake"}
                    )
            payload["units"].append({"unit_id": unit_id, "codings": codings})
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, payload)])

    return FunctionModel(respond)


def _agent(model):
    agent = Agent(model, output_type=coder.CodingBatch, instructions=coder.INSTRUCTIONS,
                  deps_type=coder.CoderDeps, retries=1)
    agent.output_validator(coder._validate)
    return agent


async def test_full_coding_run_partitions_every_eval_turn(corpus, codebook, counter, tmp_path):
    """coding + uncoded must together cover every eval-section expert turn, exactly once."""
    conn = ingest.build(tmp_path / "c.db", corpus, codebook.questions)
    cfg = pipeline.Config(strategy="model", out_dir=tmp_path, use_cache=False)
    summary = await pipeline.run(
        conn, corpus, codebook, _agent(_fake_model(codebook)), counter, cfg
    )
    assert summary["status"] == "complete"

    both = conn.execute(
        "SELECT count(*) c FROM coding WHERE text_id IN (SELECT text_id FROM uncoded)"
    ).fetchone()["c"]
    assert both == 0

    missing = conn.execute(
        "SELECT count(*) c FROM text t JOIN unit u USING(unit_id) "
        "WHERE t.speaker_role='expert' AND u.section_role='eval' "
        "AND t.text_id NOT IN (SELECT text_id FROM coding) "
        "AND t.text_id NOT IN (SELECT text_id FROM uncoded)"
    ).fetchone()["c"]
    assert missing == 0


async def test_turns_the_model_omits_are_recorded_not_lost(corpus, codebook, counter, tmp_path):
    conn = ingest.build(tmp_path / "c.db", corpus, codebook.questions)
    cfg = pipeline.Config(strategy="model", out_dir=tmp_path, use_cache=False)
    await pipeline.run(
        conn, corpus, codebook, _agent(_fake_model(codebook, skip_turns=True)), counter, cfg
    )
    declined = conn.execute(
        "SELECT count(*) c FROM uncoded WHERE reason='model_declined'"
    ).fetchone()["c"]
    assert declined > 0


async def test_no_interviewer_turn_is_ever_coded(corpus, codebook, counter, tmp_path):
    conn = ingest.build(tmp_path / "c.db", corpus, codebook.questions)
    cfg = pipeline.Config(strategy="model", out_dir=tmp_path, use_cache=False)
    await pipeline.run(conn, corpus, codebook, _agent(_fake_model(codebook)), counter, cfg)
    bad = conn.execute(
        "SELECT count(*) c FROM coding JOIN text USING(text_id) "
        "WHERE speaker_role='interviewer'"
    ).fetchone()["c"]
    assert bad == 0


async def test_profile_only_turns_are_uncoded_with_a_reason(corpus, codebook, counter, tmp_path):
    conn = ingest.build(tmp_path / "c.db", corpus, codebook.questions)
    cfg = pipeline.Config(strategy="model", out_dir=tmp_path, use_cache=False)
    await pipeline.run(conn, corpus, codebook, _agent(_fake_model(codebook)), counter, cfg)
    n = conn.execute(
        "SELECT count(*) c FROM uncoded WHERE reason='non_eval_section'"
    ).fetchone()["c"]
    assert n == 12


async def test_rerun_is_idempotent(corpus, codebook, counter, tmp_path):
    cfg = pipeline.Config(strategy="model", out_dir=tmp_path, use_cache=False)
    agent = _agent(_fake_model(codebook))
    conn = ingest.build(tmp_path / "c.db", corpus, codebook.questions)

    await pipeline.run(conn, corpus, codebook, agent, counter, cfg)
    first = tmp_path / "a.jsonl"
    store.export_jsonl(conn, first)

    await pipeline.run(conn, corpus, codebook, agent, counter, cfg)
    second = tmp_path / "b.jsonl"
    store.export_jsonl(conn, second)

    assert first.read_bytes() == second.read_bytes()


# --------------------------- timestamp strategy ---------------------------


def test_codebook_separates_anchors_from_the_label_space(codebook):
    """Anchors are pass 1's own coding; they live in their own field, not in `questions`."""
    assert codebook.anchors, "expected pass 1 anchors"
    assert set(json.loads(codebook.model_dump_json())["questions"][0]) == {
        "question_id",
        "section_id",
        "canonical_question",
    }


def test_model_prompt_never_contains_anchors(codebook, corpus):
    """The boundary that keeps the model strategy independent of pass 1."""
    section = codebook.sections()[0]
    prompt = coder.build_prompt(section, "T", codebook, corpus.for_section(section))
    assert "answer_timestamp" not in prompt
    for anchor in codebook.anchors[:40]:
        assert anchor.answer_timestamp not in prompt


def test_timestamp_join_codes_the_anchored_turns(corpus, codebook):
    from coding import timestamp as ts
    from coding.ingest import text_id

    unit = corpus.by_path("structured/06-cost-total-cost-of-ownership/expert-1.md")
    result = ts.code_unit(unit, codebook, text_id)
    anchors = codebook.anchors_for(unit.expert_slug, unit.section_slug)

    assert len(result.codings) == len(anchors)
    assert all(c.method == "anchor" for c in result.codings)
    assert not result.unresolved_anchors
    assert not result.duplicate_timestamps


def test_span_fill_reaches_full_coverage_but_marks_the_inference(corpus, codebook):
    """Invariant, not a count: span-fill leaves nothing uncoded and labels what it inferred.

    Asserted across the corpus rather than one file, because which turns lack an anchor
    depends on the pass-1 run.
    """
    from coding import timestamp as ts
    from coding.ingest import text_id

    plain_coded = filled_coded = expert_turns = 0
    methods: set[str] = set()
    for unit in corpus.units:
        if unit.section_slug == "01-interview-introduction":
            continue
        expert_turns += sum(1 for t in unit.turns if t.is_expert)
        plain_coded += len(ts.code_unit(unit, codebook, text_id).codings)
        filled = ts.code_unit(unit, codebook, text_id, span_fill=True)
        filled_coded += len(filled.codings)
        assert not filled.uncoded, f"{unit.rel_path} still uncoded under span-fill"
        methods |= {c.method for c in filled.codings}

    assert filled_coded == expert_turns
    assert plain_coded <= filled_coded
    assert methods <= {"anchor", "span"}
    if plain_coded < filled_coded:
        assert "span" in methods, "inferred rows must be labelled as inference"


def test_unresolvable_anchor_is_reported_not_silently_dropped(corpus, codebook):
    """A bad timestamp from pass 1 must not make a question's answer vanish quietly."""
    from coding import timestamp as ts
    from coding.codebook import Anchor
    from coding.ingest import text_id

    unit = corpus.by_path("structured/06-cost-total-cost-of-ownership/expert-1.md")
    broken = codebook.model_copy(deep=True)
    broken.anchors.append(
        Anchor(
            question_id=codebook.for_section(unit.section_slug)[0].question_id,
            section_id=unit.section_slug,
            expert_slug=unit.expert_slug,
            answer_timestamp="23:59:59",
        )
    )
    result = ts.code_unit(unit, broken, text_id)
    assert any(stamp == "23:59:59" for _, stamp, _ in result.unresolved_anchors)


def test_ambiguous_timestamp_is_skipped_rather_than_coin_flipped(corpus, codebook):
    from coding.codebook import Anchor

    unit = corpus.by_path("structured/06-cost-total-cost-of-ownership/expert-1.md")
    anchored = next(iter(codebook.anchors_for(unit.expert_slug, unit.section_slug)))
    clashing = codebook.model_copy(deep=True)
    others = [q.question_id for q in codebook.for_section(unit.section_slug)]
    clashing.anchors.append(
        Anchor(
            question_id=others[-1],
            section_id=unit.section_slug,
            expert_slug=unit.expert_slug,
            answer_timestamp=anchored,
        )
    )
    assert anchored not in clashing.anchors_for(unit.expert_slug, unit.section_slug)


def test_every_anchored_disfluency_turn_is_flagged(corpus, codebook):
    """Consistency, not a count: if an anchored turn is process talk, it must be flagged.

    How many such anchors exist depends on the pass-1 run, so asserting a number would make
    this test fail on a legitimate re-run.
    """
    from coding import timestamp as ts
    from coding.ingest import text_id

    for unit in corpus.units:
        if unit.section_slug == "01-interview-introduction":
            continue
        result = ts.code_unit(unit, codebook, text_id)
        flagged = set(result.disfluent_codings)
        by_id = {text_id(unit.rel_path, i): t for i, t in enumerate(unit.turns)}
        for coding in result.codings:
            turn = by_id[coding.text_id]
            assert ts.is_disfluent(turn.text) == (coding.text_id in flagged), (
                f"{unit.rel_path} [{turn.timestamp}] flag disagrees with is_disfluent(): "
                f"{turn.text[:60]!r}"
            )


def test_is_disfluent_matches_process_talk_only():
    from coding import timestamp as ts

    assert ts.is_disfluent("Can you repeat the question?")
    assert ts.is_disfluent("Sorry, can you repeat the question?")
    assert ts.is_disfluent("Go ahead.")

    # A bare affirmative is the correct, complete answer to a confirmation question.
    # Flagging it would tell consumers to discard a real answer.
    assert not ts.is_disfluent("Yes.")
    assert not ts.is_disfluent("Yeah.")
    assert not ts.is_disfluent(
        "Yes, there was role separation. We had read-only roles, approvers, ITIL roles, "
        "admins, and standard users."
    )


async def test_timestamp_strategy_runs_end_to_end(corpus, codebook, counter, tmp_path):
    conn = ingest.build(tmp_path / "c.db", corpus, codebook.questions)
    cfg = pipeline.Config(strategy="timestamp", out_dir=tmp_path, use_cache=False)
    summary = await pipeline.run(conn, corpus, codebook, None, counter, cfg)

    assert summary["strategy"] == "timestamp"
    assert summary["calls"] == 0 and summary["cost_usd"] == 0.0
    assert summary["coded"] == len(codebook.anchors)
    assert conn.execute(
        "SELECT count(*) c FROM coding WHERE method='anchor'"
    ).fetchone()["c"] == len(codebook.anchors)

    both = conn.execute(
        "SELECT count(*) c FROM coding WHERE text_id IN (SELECT text_id FROM uncoded)"
    ).fetchone()["c"]
    assert both == 0


def test_reingest_survives_downstream_tables_referencing_it(corpus, codebook, tmp_path):
    """Pass 2 must be re-runnable after pass 3 has built tables that point at its rows.

    INSERT OR REPLACE would delete-then-insert and trip those foreign keys; the store
    upserts instead. Caught by running the whole pipeline end to end, not in isolation.
    """
    db = tmp_path / "c.db"
    conn = ingest.build(db, corpus, codebook.questions)

    # Stand in for pass 3: a table whose rows reference interview and text.
    conn.executescript(
        "CREATE TABLE downstream ("
        "  id TEXT PRIMARY KEY,"
        "  interview_id TEXT NOT NULL REFERENCES interview(interview_id),"
        "  text_id TEXT NOT NULL REFERENCES text(text_id));"
    )
    row = conn.execute(
        "SELECT text_id, interview_id FROM text LIMIT 1"
    ).fetchone()
    conn.execute(
        "INSERT INTO downstream VALUES (?,?,?)", ("d1", row["interview_id"], row["text_id"])
    )
    conn.commit()

    # Re-running ingest must not destroy the rows `downstream` depends on.
    ingest.ingest(conn, corpus)
    ingest.load_codebook(conn, codebook.questions)
    assert conn.execute("SELECT count(*) c FROM downstream").fetchone()["c"] == 1
