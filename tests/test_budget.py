"""Scaling behaviour: packing, turn-boundary chunking, hierarchical reduce.

These are the tests that matter for a corpus larger than the one on disk today, and the
only way to exercise that path without actually having a huge corpus.
"""

from context_pass.budget import (
    HeuristicTokenCounter,
    chunk_unit,
    pack,
    plan_extract_calls,
    plan_reduce_rounds,
)


def test_pack_respects_token_budget():
    items = ["x" * 100] * 10
    counter = HeuristicTokenCounter()
    batches = pack(items, counter.count, budget=counter.count("x" * 100) * 3)
    assert all(len(b) <= 3 for b in batches)
    assert sum(len(b) for b in batches) == 10


def test_pack_respects_item_cap_independently_of_tokens():
    """Input budget is not the only constraint: output has to fit max_tokens too."""
    batches = pack(["tiny"] * 9, lambda _: 1, budget=1_000_000, max_items=4)
    assert [len(b) for b in batches] == [4, 4, 1]


def test_oversized_item_gets_its_own_batch():
    batches = pack(["small", "x" * 10_000, "small"], len, budget=100)
    assert len(batches) == 3


def test_whole_unit_is_not_chunked_when_it_fits(corpus, counter):
    unit = min(corpus.units, key=lambda u: u.words)
    chunks = chunk_unit(unit, counter, budget=1_000_000)
    assert len(chunks) == 1
    assert chunks[0].is_whole


def test_oversized_unit_chunks_on_turn_boundaries(corpus, counter):
    unit = max(corpus.units, key=lambda u: u.words)
    chunks = chunk_unit(unit, counter, budget=800)

    assert len(chunks) > 1
    # Every chunked turn must be one of the original turns, unaltered - never a fragment.
    originals = {(t.timestamp, t.text) for t in unit.turns}
    for chunk in chunks:
        assert chunk.turns
        for turn in chunk.turns:
            assert (turn.timestamp, turn.text) in originals


def test_chunking_loses_no_turns(corpus, counter):
    unit = max(corpus.units, key=lambda u: u.words)
    chunks = chunk_unit(unit, counter, budget=800)
    seen = {(t.timestamp, t.text) for c in chunks for t in c.turns}
    assert seen == {(t.timestamp, t.text) for t in unit.turns}


def test_chunk_headers_declare_the_split(corpus, counter):
    unit = max(corpus.units, key=lambda u: u.words)
    chunks = chunk_unit(unit, counter, budget=800)
    assert f"PART 1 of {len(chunks)}" in chunks[0].render()
    assert unit.rel_path in chunks[0].render()


def test_extract_plan_fans_out_as_the_budget_shrinks(corpus, counter):
    wide = plan_extract_calls(corpus.units, counter, 120_000, max_units_per_call=4)
    narrow = plan_extract_calls(corpus.units, counter, 2_000, max_units_per_call=4)
    assert len(narrow) > len(wide)
    assert all(len(b) <= 4 for b in wide)


def test_extract_plan_covers_every_unit_exactly_once(corpus, counter):
    plan = plan_extract_calls(corpus.units, counter, 120_000, max_units_per_call=4)
    paths = [c.unit.rel_path for batch in plan for c in batch]
    assert sorted(paths) == sorted(u.rel_path for u in corpus.units)


def test_reduce_runs_in_one_round_when_it_fits(counter):
    assert len(plan_reduce_rounds(["a" * 50] * 5, counter, 1_000_000)) == 1


def test_reduce_splits_into_rounds_when_it_does_not_fit(counter):
    rounds = plan_reduce_rounds(["a" * 4_000] * 8, counter, 2_000)
    assert len(rounds) > 1
    assert sum(len(r) for r in rounds) == 8
