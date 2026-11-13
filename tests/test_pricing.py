from cc_usage_study.pricing import (
    cache_read_multiplier,
    canonical_model_id,
    estimate_call_usd,
)


def test_opus_55_cache_read_is_the_published_rate():
    multiplier, note = cache_read_multiplier("claude-opus-5-5")
    assert multiplier == 0.05
    assert note == "claude-opus-5-5"
    usd, reason = estimate_call_usd(
        "claude-opus-5-5",
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=1_000_000,
        cache_write_5m_tokens=0,
        cache_write_1h_tokens=0,
        prompt_tokens=1_000_000,
    )
    assert reason is None
    assert usd == 0.20


def test_unknown_model_is_not_priced():
    assert canonical_model_id("synthetic-model") is None
    usd, reason = estimate_call_usd(
        "synthetic-model",
        input_tokens=100,
        output_tokens=10,
        cache_read_tokens=0,
        cache_write_5m_tokens=0,
        cache_write_1h_tokens=0,
        prompt_tokens=100,
    )
    assert usd is None
    assert reason is not None


def test_missing_ttl_split_is_not_guessed():
    usd, reason = estimate_call_usd(
        "claude-sonnet-5-5",
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_5m_tokens=None,
        cache_write_1h_tokens=None,
        prompt_tokens=0,
    )
    assert usd is None
    assert "TTL" in reason


def test_dated_and_provider_ids_match_the_snapshot_row():
    assert canonical_model_id("claude-sonnet-4-5-20250929") == "claude-sonnet-4-5"
    assert canonical_model_id("us.anthropic.claude-opus-4-6-v1:0") == "claude-opus-4-6"
    assert canonical_model_id("claude-fable-5-2") == "claude-fable-5-2"
    assert cache_read_multiplier("claude-fable-5-2")[0] == 0.025
    assert cache_read_multiplier("claude-fable-5")[0] == 0.1


def test_haiku_55_long_prompt_uses_the_higher_row():
    short, reason = estimate_call_usd(
        "claude-haiku-5-5",
        input_tokens=1_000_000,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_5m_tokens=0,
        cache_write_1h_tokens=0,
        prompt_tokens=50_000,
    )
    long, long_reason = estimate_call_usd(
        "claude-haiku-5-5",
        input_tokens=1_000_000,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_5m_tokens=0,
        cache_write_1h_tokens=0,
        prompt_tokens=200_000,
    )
    assert reason is None and long_reason is None
    assert short == 0.10
    assert long == 0.50
