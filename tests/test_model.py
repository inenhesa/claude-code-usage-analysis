from dataclasses import replace

import pytest

from cc_usage_study.model import (
    DEFAULT_ASSUMPTIONS,
    SHORT_ASSUMPTIONS,
    Assumptions,
    closed_form_prompt_tokens,
    simulate,
    with_threshold,
)


def _toy(**overrides) -> Assumptions:
    base = dict(
        context_window=100,
        threshold_fraction=0.5,
        turns=3,
        growth_per_turn=15,
        output_per_turn=5,
        system_prompt_tokens=10,
        project_context_tokens=10,
        summary_tokens=5,
        reinjected_tokens=5,
        compaction_instruction_tokens=2,
        compaction_output_tokens=3,
        caching=False,
        cache_hit_rate=1.0,
        rework_turns_per_compaction=0,
        rework_growth_per_turn=15,
        label="toy",
    )
    base.update(overrides)
    return Assumptions(**base)


def test_closed_form_matches_simulation_when_nothing_compacts():
    scenario = Assumptions(
        context_window=1_000_000,
        threshold_fraction=0.99,
        turns=5,
        growth_per_turn=100,
        output_per_turn=10,
        system_prompt_tokens=50,
        project_context_tokens=50,
        caching=False,
    )
    result = simulate(scenario)
    assert result.compaction_count == 0
    assert result.total_prompt_tokens == closed_form_prompt_tokens(scenario)
    assert result.total_prompt_tokens == 2100
    assert result.total_input_tokens == result.total_prompt_tokens
    assert result.total_cache_read_tokens == 0


def test_prompt_buckets_partition_every_call():
    result = simulate(DEFAULT_ASSUMPTIONS)
    for call in result.calls:
        assert (
            call.input_tokens + call.cache_read_tokens + call.cache_write_tokens
            == call.prompt_tokens
        )


def test_enumerated_compaction_without_cache():
    result = simulate(_toy())
    kinds = [call.kind for call in result.calls]
    assert kinds == ["turn", "compaction", "turn", "compaction", "turn"]
    assert [call.prompt_tokens for call in result.calls] == [35, 42, 45, 52, 45]
    assert result.total_prompt_tokens == 219
    assert result.compaction_count == 2
    assert result.productive_turns == 3


def test_cache_does_not_change_raw_prompt_total():
    uncached = simulate(_toy(caching=False))
    cached = simulate(_toy(caching=True, cache_hit_rate=1.0))
    assert cached.total_prompt_tokens == uncached.total_prompt_tokens == 219
    assert cached.total_cache_read_tokens == 110
    assert cached.total_cache_write_tokens == 109
    assert cached.total_input_tokens == 0


def test_five_minute_cache_is_cheaper_once_the_prefix_is_reread():
    # 5-minute writes are 1.25x and reads are 0.1x. Anthropic's pricing note
    # says that pair pays for itself after one cache read. A session that
    # never compacts gives the prefix that read.
    common = dict(
        context_window=1_000_000,
        threshold_fraction=0.99,
        turns=6,
        growth_per_turn=100,
        output_per_turn=10,
        system_prompt_tokens=50,
        project_context_tokens=50,
        cache_hit_rate=1.0,
        cache_read_multiplier=0.1,
        cache_write_multiplier=1.25,
        output_multiplier=5.0,
    )
    cached = simulate(Assumptions(caching=True, **common))
    uncached = simulate(Assumptions(caching=False, **common))
    assert cached.compaction_count == 0
    assert cached.total_prompt_tokens == uncached.total_prompt_tokens
    assert cached.api_equivalent_cost() < uncached.api_equivalent_cost()


def test_rework_turns_are_added_on_top_of_productive_work():
    result = simulate(_toy(turns=2, rework_turns_per_compaction=1))
    # Turn 1 stays under the threshold. Turn 2 compacts, then pays one rework
    # turn, then does the productive turn. Rework is not taken out of ``turns``.
    assert [call.kind for call in result.calls] == ["turn", "compaction", "rework", "turn"]
    assert [call.prompt_tokens for call in result.calls] == [35, 42, 45, 65]
    assert result.productive_turns == 2
    assert result.rework_turns == 1
    assert result.compaction_count == 1


def test_long_session_25_percent_uses_fewer_raw_tokens_than_95():
    low = simulate(with_threshold(DEFAULT_ASSUMPTIONS, fraction=0.25))
    high = simulate(with_threshold(DEFAULT_ASSUMPTIONS, fraction=0.95))
    assert low.compaction_count > high.compaction_count > 0
    assert low.total_prompt_tokens < high.total_prompt_tokens
    # The gap in raw tokens is larger than the gap in cache-priced units,
    # because most of the re-sent prefix is a cache read at 0.1x.
    raw_ratio = low.total_prompt_tokens / high.total_prompt_tokens
    price_ratio = low.api_equivalent_cost() / high.api_equivalent_cost()
    assert raw_ratio < price_ratio < 1


def test_short_session_high_threshold_never_compacts():
    high = simulate(with_threshold(SHORT_ASSUMPTIONS, fraction=0.95))
    low = simulate(with_threshold(SHORT_ASSUMPTIONS, fraction=0.25))
    assert high.compaction_count == 0
    assert low.compaction_count >= 1
    assert "no_compaction" in " ".join(high.warnings)


def test_threshold_inside_the_summary_stays_finite():
    # Threshold 25 is below the reset size of 30, so every later turn compacts
    # once and then sends anyway. The walk still ends.
    scenario = _toy(threshold_tokens=25, context_window=100, turns=4)
    result = simulate(scenario)
    assert result.productive_turns == 4
    assert 1 <= result.compaction_count <= 4
    assert any("request_still_over_threshold" in warning for warning in result.warnings)


def test_invalid_hit_rate_is_rejected():
    with pytest.raises(ValueError):
        simulate(replace(DEFAULT_ASSUMPTIONS, cache_hit_rate=1.5))


def test_absolute_threshold_overrides_fraction():
    result = simulate(with_threshold(DEFAULT_ASSUMPTIONS, tokens=250_000))
    assert result.threshold_tokens == 250_000
