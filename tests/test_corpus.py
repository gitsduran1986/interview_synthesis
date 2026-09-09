"""Corpus parsing and quote normalization. No model involved."""

from interview_synthesis.corpus import normalize
from interview_synthesis.sections import SKIP, eval_sections


def test_roster_is_discovered_not_hardcoded(corpus):
    assert corpus.experts == ["expert-1", "expert-2", "expert-3"]
    assert all(s not in SKIP for s in corpus.sections)


def test_every_unit_parses_with_turns_and_frontmatter(corpus):
    assert len(corpus.units) == 24
    for unit in corpus.units:
        assert unit.turns, f"{unit.rel_path} produced no turns"
        assert unit.frontmatter.get("expert"), unit.rel_path
        assert all(len(t.timestamp) == 8 for t in unit.turns), unit.rel_path


def test_both_speakers_present_and_separable(corpus):
    unit = corpus.by_path("structured/03-vendor-selection-decision-criteria/expert-1.md")
    assert any(t.is_expert for t in unit.turns)
    assert any(not t.is_expert for t in unit.turns)
    interviewer_only = {t.text for t in unit.turns if not t.is_expert}
    assert not any(text in unit.expert_text() for text in interviewer_only)


def test_digest_is_stable_across_loads(corpus):
    from pathlib import Path

    from interview_synthesis import corpus as corpus_mod

    again = corpus_mod.load(Path("structured"))
    assert again.digest() == corpus.digest()


def test_eval_sections_excludes_intro_and_wrapup():
    slugs = eval_sections()
    assert len(slugs) == 7
    assert "01-interview-introduction" not in slugs
    assert "09-interview-wrap-up" not in slugs


def test_normalize_folds_smart_punctuation():
    assert normalize("I’d say 2M tickets—and more") == normalize("I'd say 2M tickets-and more")
    assert normalize("  spaced   out\n") == "spaced out"
    assert normalize("“quoted”") == normalize('"quoted"')


def test_transcripts_really_do_contain_smart_punctuation(corpus):
    """Guards the normalizer against being quietly deleted as unnecessary."""
    blob = "".join(u.body for u in corpus.units)
    assert any(ch in blob for ch in "’“”—")
