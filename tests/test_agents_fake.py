"""Agent wiring and validator behaviour, driven by fake models. No tokens spent.

`FunctionModel` lets us hand the agent a specific bad output and assert the validator
sends it back, which is the only way to test the retry loop without paying for it.
"""

import pytest
from pydantic_ai import Agent, ModelRetry, RunContext, UnexpectedModelBehavior
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from context_pass.agents import (
    Deps,
    _dedup_validator,
    build_agents,
    check_evidence,
    question_key,
)
from context_pass.models import (
    Evidence,
    ExpertPass,
    ExpertTheme,
    QuestionAsking,
    SectionPass,
    SectionQuestion,
    UnitExtractBatch,
)


def _evidence(quote, speaker="expert", path="structured/02-current-environment/expert-1.md"):
    return Evidence(
        quote=quote,
        speaker=speaker,
        expert_slug="expert-1",
        section_slug="02-current-environment",
        source_file=path,
    )


def _theme(evidence):
    return ExpertTheme(
        theme_id="a-theme",
        label="Label",
        statement="Statement.",
        sections=["02-current-environment"],
        evidence=[evidence],
    )


# --------------------------- evidence checking ---------------------------


def test_verbatim_expert_quote_passes(corpus, real_quotes):
    assert check_evidence(corpus, _theme(_evidence(real_quotes["expert"]))) == []


def test_quote_with_smart_punctuation_passes(corpus):
    """The regression test for the normalizer: a correct quote must not be rejected."""
    unit = corpus.by_path("structured/02-current-environment/expert-1.md")
    smart = next(
        (t.text for t in unit.turns if t.is_expert and any(c in t.text for c in "’—“”")), None
    )
    if smart is None:
        pytest.skip("no smart punctuation in this file")
    assert check_evidence(corpus, _theme(_evidence(smart[:150]))) == []


def test_fabricated_quote_is_rejected(corpus):
    problems = check_evidence(corpus, _theme(_evidence("nobody in this corpus ever said this")))
    assert problems and "does not appear" in problems[0]


def test_interviewer_words_attributed_to_expert_are_rejected(corpus, real_quotes):
    problems = check_evidence(corpus, _theme(_evidence(real_quotes["interviewer"])))
    assert problems and "AI Interviewer" in problems[0]


def test_same_interviewer_quote_is_fine_when_labelled_correctly(corpus, real_quotes):
    assert check_evidence(corpus, _theme(_evidence(real_quotes["interviewer"], "interviewer"))) == []


def test_unknown_source_file_is_rejected(corpus, real_quotes):
    problems = check_evidence(corpus, _theme(_evidence(real_quotes["expert"], path="nope.md")))
    assert problems and "does not exist" in problems[0]


# --------------------------- dedup validator ---------------------------


def _question(qid, text, experts):
    return SectionQuestion(
        question_id=qid,
        canonical_question=text,
        asked_of=[
            QuestionAsking(expert_slug=e, as_asked=text, answered="answered") for e in experts
        ],
    )


def test_dedup_validator_flags_identical_questions():
    section = SectionPass(
        section_slug="06-cost-total-cost-of-ownership",
        questions=[
            _question("q-06-01", "How did TCO compare to budget?", ["expert-1"]),
            _question("q-06-02", "How did TCO compare to budget!", ["expert-2"]),
        ],
    )
    with pytest.raises(ModelRetry, match="same question"):
        _dedup_validator(None, section)


def test_question_key_ignores_punctuation_and_case():
    assert question_key("How did TCO compare to budget?") == question_key(
        "how did tco compare to budget."
    )
    assert question_key("Were there licensing surprises?") != question_key(
        "How did TCO compare to budget?"
    )


def test_dedup_validator_allows_genuinely_different_questions():
    section = SectionPass(
        section_slug="06-cost-total-cost-of-ownership",
        questions=[
            _question("q-06-01", "How did TCO compare to budget?", ["expert-1"]),
            _question("q-06-02", "Were there licensing surprises?", ["expert-1"]),
        ],
    )
    assert _dedup_validator(None, section) is section


# --------------------------- agent wiring ---------------------------


def test_all_agents_build_and_schemas_are_representable():
    agents = build_agents()
    assert set(agents) == {"extract", "expert", "section"}


@pytest.mark.parametrize(
    "name,expected",
    [("extract", "UnitExtractBatch"), ("section", "SectionPass")],
)
async def test_agents_run_end_to_end_against_testmodel(corpus, name, expected):
    """Proves each output type is schema-representable and the deps plumbing is wired."""
    agents = build_agents()
    with agents[name].override(model=TestModel()):
        result = await agents[name].run("go", deps=Deps(corpus=corpus))
    assert type(result.output).__name__ == expected


async def test_invented_evidence_never_survives_validation(corpus):
    """TestModel fabricates quotes; ExpertPass requires them, so the run must not succeed.

    This is the guarantee the whole pipeline rests on: no unverified quote gets through.
    """
    agents = build_agents()
    with agents["expert"].override(model=TestModel()):
        with pytest.raises(UnexpectedModelBehavior):
            await agents["expert"].run("go", deps=Deps(corpus=corpus))


@pytest.mark.parametrize("model", [UnitExtractBatch, ExpertPass, SectionPass])
def test_output_types_generate_valid_json_schemas(model):
    schema = model.model_json_schema()
    assert schema["type"] == "object"
    assert schema.get("properties")


async def test_validator_sends_a_bad_quote_back_then_accepts_a_good_one(corpus, real_quotes):
    """The full ModelRetry loop: first response fabricates, second one cites correctly."""
    calls = {"n": 0}

    def respond(messages, info):
        calls["n"] += 1
        quote = "this was never said by anyone" if calls["n"] == 1 else real_quotes["expert"]
        payload = {
            "theme_id": "a-theme",
            "label": "Label",
            "statement": "Statement.",
            "sections": ["02-current-environment"],
            "evidence": [
                {
                    "quote": quote,
                    "speaker": "expert",
                    "expert_slug": "expert-1",
                    "section_slug": "02-current-environment",
                    "source_file": "structured/02-current-environment/expert-1.md",
                }
            ],
        }
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, payload)])

    agent = Agent(
        FunctionModel(respond), output_type=ExpertTheme, deps_type=Deps, retries=2
    )

    @agent.output_validator
    def validate(ctx: RunContext[Deps], out: ExpertTheme) -> ExpertTheme:
        problems = check_evidence(ctx.deps.corpus, out)
        if problems:
            raise ModelRetry("bad citations: " + "; ".join(problems))
        return out

    result = await agent.run("go", deps=Deps(corpus=corpus))
    assert calls["n"] == 2, "validator should have forced exactly one retry"
    assert result.output.evidence[0].quote == real_quotes["expert"]
