"""Pipeline assembly and orchestration, driven by a fake model. No tokens spent."""

import json

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from interview_synthesis.context.agents import Deps
from interview_synthesis.context.models import (
    AskedQuestion,
    Evidence,
    QuestionAsking,
    SectionPass,
    SectionQuestion,
    UnitExtract,
)
from interview_synthesis.context.pipeline import (
    Config,
    _merge_chunk_extracts,
    audit_evidence,
    finalize_questions,
    run,
)


# --------------------------- coverage derivation ---------------------------


def _question(text, experts):
    return SectionQuestion(
        question_id="q-99-99",
        canonical_question=text,
        asked_of=[
            QuestionAsking(expert_slug=e, as_asked=text, answered="answered") for e in experts
        ],
    )


def test_coverage_and_ids_are_derived_in_python():
    section = SectionPass(
        section_slug="06-cost-total-cost-of-ownership",
        questions=[
            _question("asked of everyone", ["expert-1", "expert-2", "expert-3"]),
            _question("asked of two", ["expert-1", "expert-2"]),
            _question("asked of one", ["expert-1"]),
        ],
    )
    finalize_questions(section, roster_size=3)

    assert [q.question_id for q in section.questions] == ["q-06-01", "q-06-02", "q-06-03"]
    assert [q.coverage for q in section.questions] == [
        "all_interviewees",
        "subset",
        "single_interviewee",
    ]


def test_a_question_asked_of_one_person_is_kept_not_dropped():
    """Absence is the signal the downstream evaluator needs; it must survive assembly."""
    section = SectionPass(
        section_slug="06-cost-total-cost-of-ownership",
        questions=[_question("did you face the approver tax?", ["expert-1"])],
    )
    finalize_questions(section, roster_size=3)
    assert len(section.questions) == 1
    assert section.questions[0].coverage == "single_interviewee"
    assert [a.expert_slug for a in section.questions[0].asked_of] == ["expert-1"]


def test_repeat_askings_to_one_person_collapse_to_one_entry():
    """The interviewer sometimes re-asks after 'Can you repeat the question?'."""
    question = SectionQuestion(
        question_id="q-06-01",
        canonical_question="How did TCO compare to budget?",
        asked_of=[
            QuestionAsking(
                expert_slug="expert-1", as_asked="first ask", answered="no_answer"
            ),
            QuestionAsking(
                expert_slug="expert-1",
                as_asked="re-asked",
                answered="answered",
                answer_timestamp="00:39:27",
            ),
            QuestionAsking(expert_slug="expert-2", as_asked="asked once", answered="answered"),
        ],
    )
    section = SectionPass(section_slug="06-cost-total-cost-of-ownership", questions=[question])
    finalize_questions(section, roster_size=3)

    assert [a.expert_slug for a in question.asked_of] == ["expert-1", "expert-2"]
    # The informative asking wins over the one that got no answer.
    assert question.asked_of[0].as_asked == "re-asked"
    assert question.coverage == "subset"


# --------------------------- chunk merging ---------------------------


def test_chunks_of_one_file_are_folded_back_together():
    def part(index, question):
        return UnitExtract(
            expert_slug="expert-1",
            section_slug="02-current-environment",
            chunk_index=index,
            chunk_count=2,
            questions=[AskedQuestion(text=question, answered="answered")],
            scale_markers=[f"marker-{index}", "shared"],
        )

    merged = _merge_chunk_extracts([part(0, "first half"), part(1, "second half")])

    assert len(merged) == 1
    assert [q.text for q in merged[0].questions] == ["first half", "second half"]
    assert merged[0].chunk_count == 1
    assert merged[0].scale_markers.count("shared") == 1


def test_different_files_are_not_merged():
    def unit(expert):
        return UnitExtract(expert_slug=expert, section_slug="02-current-environment")

    assert len(_merge_chunk_extracts([unit("expert-1"), unit("expert-2")])) == 2


# --------------------------- full fake run ---------------------------


def _fake_model(corpus, real_quotes, *, fail_on: str | None = None):
    """A model that answers whichever agent called it, citing a genuinely real quote."""
    evidence = {
        "quote": real_quotes["expert"],
        "speaker": "expert",
        "expert_slug": "expert-1",
        "section_slug": "02-current-environment",
        "source_file": "structured/02-current-environment/expert-1.md",
    }

    def respond(messages, info):
        prompt = str(messages[-1])
        if fail_on and fail_on in prompt:
            raise RuntimeError("simulated provider failure")

        schema = info.output_tools[0].parameters_json_schema
        properties = set(schema.get("properties", {}))

        if "extracts" in properties:
            payload = {
                "extracts": [
                    {
                        "expert_slug": unit.expert_slug,
                        "section_slug": unit.section_slug,
                        "questions": [{"text": "a question?", "answered": "answered"}],
                        "claims": [{"statement": "a claim", "evidence": evidence}],
                        "credential_facts": [],
                        "scale_markers": [],
                    }
                    for unit in corpus.units
                    if unit.rel_path in prompt
                ]
            }
        elif "profile" in properties:
            payload = {
                "profile": {
                    "expert_slug": "expert-1",
                    "role_title": "Role",
                    "org_description": "Org",
                    "tenure": "5 years",
                    "background": "Background.",
                    "ui_statement": "A factual caption.",
                    "credentials": [
                        {
                            "claim": "Ran the platform",
                            "kind": "prior_role",
                            "evidence": evidence,
                        }
                    ],
                    "platform_experience": [],
                    "scale_markers": [],
                    "stated_limits": [],
                },
            }
        else:
            payload = {
                "section_slug": "02-current-environment",
                "questions": [
                    {
                        "question_id": "q-02-01",
                        "canonical_question": "a question?",
                        "asked_of": [
                            {
                                "expert_slug": "expert-1",
                                "as_asked": "a question?",
                                "answered": "answered",
                            }
                        ],
                    }
                ],
            }
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, payload)])

    return FunctionModel(respond)


def _agents(model):
    from interview_synthesis.context import prompts
    from interview_synthesis.context.agents import _dedup_validator, _evidence_validator
    from interview_synthesis.context.models import (
        ExpertPass,
        SectionQuestions,
        UnitExtractBatch,
    )

    built = {}
    for name, output_type in (
        ("extract", UnitExtractBatch),
        ("expert", ExpertPass),
        ("question", SectionQuestions),
    ):
        agent = Agent(
            model,
            output_type=output_type,
            instructions=prompts.STAGE_INSTRUCTIONS[name],
            deps_type=Deps,
            retries=1,
        )
        agent.output_validator(_evidence_validator)
        if name == "question":
            agent.output_validator(_dedup_validator)
        built[name] = agent
    return built


async def test_full_run_produces_a_serializable_document(corpus, counter, real_quotes, tmp_path):
    cfg = Config(out_dir=tmp_path, use_cache=False, only_experts=["expert-1"])
    agents = _agents(_fake_model(corpus, real_quotes))

    context = await run(corpus, agents, counter, cfg)

    assert context.run.status == "complete"
    assert context.interviewees
    assert context.sections
    assert context.evidence_audit.total > 0
    assert context.evidence_audit.verified == context.evidence_audit.total
    assert not context.evidence_audit.unverified

    # The deliverable must round-trip as JSON.
    payload = json.loads(context.model_dump_json())
    assert payload["run"]["corpus_digest"]
    assert payload["run"]["prompt_digest"]
    assert payload["run"]["call_plan"]["extract"] >= 1


async def test_stage_results_are_persisted_for_resume(corpus, counter, real_quotes, tmp_path):
    cfg = Config(out_dir=tmp_path, use_cache=False, only_experts=["expert-1"])
    await run(corpus, _agents(_fake_model(corpus, real_quotes)), counter, cfg)

    assert (tmp_path / "stages" / "extract").exists()
    assert list((tmp_path / "stages" / "expert").glob("*.json"))


async def test_cache_avoids_a_second_round_of_calls(corpus, counter, real_quotes, tmp_path):
    cfg = Config(out_dir=tmp_path, use_cache=True, only_experts=["expert-1"])
    agents = _agents(_fake_model(corpus, real_quotes))

    first = await run(corpus, agents, counter, cfg)
    second = await run(corpus, agents, counter, cfg)

    assert sum(s.calls for s in first.run.usage) > 0
    assert sum(s.calls for s in second.run.usage) == 0
    assert sum(s.cached_calls for s in second.run.usage) > 0


async def test_one_failed_unit_does_not_sink_the_run(corpus, counter, real_quotes, tmp_path):
    cfg = Config(out_dir=tmp_path, use_cache=False, only_experts=["expert-1"])
    agents = _agents(_fake_model(corpus, real_quotes, fail_on="08-switching-dynamics"))

    context = await run(corpus, agents, counter, cfg)

    assert context.run.status == "partial"
    assert any("failed" in w for w in context.run.warnings)
    assert context.interviewees, "surviving units must still be assembled"


async def test_fail_fast_raises_instead(corpus, counter, real_quotes, tmp_path):
    cfg = Config(out_dir=tmp_path, use_cache=False, fail_fast=True, only_experts=["expert-1"])
    agents = _agents(_fake_model(corpus, real_quotes, fail_on="08-switching-dynamics"))

    with pytest.raises(Exception):
        await run(corpus, agents, counter, cfg)


# --------------------------- audit ---------------------------


def test_audit_reports_unverified_quotes(corpus):
    """A fabricated quote anywhere in the document is caught by the post-hoc audit."""
    from datetime import datetime, timezone

    from interview_synthesis.context.models import (
        Credential,
        FirstPassContext,
        IntervieweeProfile,
        RunMetadata,
    )

    fabricated = Evidence(
        quote="this line appears in no transcript at all",
        speaker="expert",
        expert_slug="expert-1",
        section_slug="02-current-environment",
        source_file="structured/02-current-environment/expert-1.md",
    )
    context = FirstPassContext(
        run=RunMetadata(generated_at=datetime.now(timezone.utc), model="test"),
        interviewees=[
            IntervieweeProfile(
                expert_slug="expert-1",
                role_title="Role",
                org_description="Org",
                tenure="5 years",
                background="Background.",
                ui_statement="A factual caption.",
                credentials=[
                    Credential(claim="Ran the platform", kind="prior_role", evidence=fabricated)
                ],
            )
        ],
    )
    audit = audit_evidence(corpus, context)
    assert audit.total == 1
    assert audit.verified == 0
    assert audit.unverified[0].reason == "not_found"
