from dataclasses import replace

from cc_usage_study.model import (
    LIVE_MEAN_COMPACTION_UNITS,
    Assumptions,
    closed_form_prompt_tokens,
    effective_trigger,
    eoq_trigger,
    fixed_compaction_cost,
    simulate,
)


def test_effective_trigger_matches_the_binary():
    """Claude Code v2.1.325, tle + WX: window - min(maxOutputTokens, 20000) - 13000."""
    cases = [
        (Assumptions(), 967_000),
        (Assumptions(context_window=200_000), 167_000),
        (Assumptions(pct=0.25), 245_000),
        (Assumptions(pct=0.18), 176_400),
        (Assumptions(context_window=200_000, pct=0.25), 45_000),
        (Assumptions(window=180_000), 147_000),
        (Assumptions(window=140_000), 107_000),
        (Assumptions(window=100_000), 67_000),
        (Assumptions(window=250_000), 217_000),
        # Explicit reserve replaces the 33,000 and is not subtracted twice.
        (Assumptions(window=250_000, reserve=33_000), 217_000),
        (Assumptions(reserve=33_000), 967_000),
        (Assumptions(window=100_000, reserve=0), 100_000),
        # A percentage cannot raise the default.
        (Assumptions(pct=1.0), 967_000),
        # Small max output shrinks the reserve.
        (Assumptions(max_output_tokens=8_000), 979_000),
    ]
    for assumptions, expected in cases:
        trigger, warnings = effective_trigger(assumptions)
        assert trigger == expected, (assumptions, trigger)
        assert not any("compounding" in note for note in warnings)
    compound, notes = effective_trigger(Assumptions(window=250_000, pct=0.25))
    assert compound == 57_500
    assert any("compounding" in note and "57500" in note for note in notes)
    assert not any("unverified" in note for note in notes)
    clamped, notes = effective_trigger(Assumptions(window=50_000))
    assert clamped == 67_000
    assert any("clamped" in note for note in notes)


def test_warm_call_reads_the_previous_prompt_and_writes_the_tail():
    result = simulate(Assumptions(user_turns=1, calls_per_turn=2, tail=4_000, output_per_call=600))
    first, second = result.calls
    assert first.cache_read_tokens == 0
    assert first.cache_write_tokens == first.prompt_tokens
    # Second request reads the first prompt and writes the first output plus the new tail.
    assert second.cache_read_tokens == first.prompt_tokens
    assert second.cache_write_tokens == first.output_tokens + 4_000
    assert second.input_tokens == 0


def test_closed_form_matches_a_session_that_never_compacts():
    assumptions = Assumptions(user_turns=5, calls_per_turn=1, tail=100, output_per_call=10, pct=0.99)
    result = simulate(assumptions)
    assert result.compactions == 0
    base = assumptions.system_prompt_tokens + assumptions.project_context_tokens
    expected = closed_form_prompt_tokens(base=base, growth=100, output=10, turns=5)
    assert result.totals()["prompt_tokens"] == expected


def test_rework_can_trip_the_trigger():
    assumptions = Assumptions(
        user_turns=3,
        calls_per_turn=2,
        tail=40_000,
        output_per_call=1_000,
        window=100_000,
        reserve=0,
        rework_calls=2,
        rework_tail=5_000,
        summary_min=1_000,
        reinjected_tokens=1_000,
        system_prompt_tokens=2_000,
        project_context_tokens=1_000,
    )
    result = simulate(assumptions)
    assert result.compactions >= 1
    assert result.rework_call_count >= 2


def test_idle_compaction_requires_the_one_hour_ttl_and_200k():
    gaps = ((1, 75.0),)
    small = simulate(
        Assumptions(user_turns=2, calls_per_turn=1, tail=1_000, long_gaps=gaps, idle_compaction=True)
    )
    assert small.idle_compactions == 0
    # Build a context over 200k, then idle.
    large = Assumptions(
        user_turns=2,
        calls_per_turn=1,
        tail=250_000,
        output_per_call=1_000,
        long_gaps=gaps,
        idle_compaction=True,
        ttl_min=60,
    )
    # Turn 0 grows context. The gap is before turn 1.
    hit = simulate(large)
    assert hit.idle_compactions == 1
    credits = simulate(replace(large, ttl_min=5))
    assert credits.idle_compactions == 0


def test_s1_ratios_match_the_reference_simulator():
    hi = simulate(Assumptions())
    lo = simulate(replace(Assumptions(), pct=0.25))
    assert hi.trigger_tokens == 967_000
    assert lo.trigger_tokens == 245_000
    assert hi.compactions == 2
    assert lo.compactions == 11
    api = hi.cost("api") / lo.cost("api")
    raw = hi.cost("raw_prompt") / lo.cost("raw_prompt")
    assert round(api, 2) == 2.06
    assert round(raw, 2) == 3.07


def _idle_session(**overrides):
    base = Assumptions(
        user_turns=3,
        calls_per_turn=1,
        tail=250_000,
        output_per_call=1_000,
        long_gaps=((1, 75.0),),
        idle_compaction=True,
        ttl_min=60,
        rework_calls=2,
        rework_tail=1_000,
    )
    return simulate(replace(base, **overrides))


def test_idle_compaction_needs_all_four_conditions():
    hit = _idle_session()
    assert hit.idle_compactions == 1
    assert hit.compactions == 0
    assert hit.total_compactions == 1
    assert hit.totals()["idle_compactions"] == 1
    assert hit.totals()["total_compactions"] == 1
    assert any(call.kind == "idle_compaction" for call in hit.calls)
    assert not any("no_compaction" in note for note in hit.warnings)
    assert _idle_session(idle_compaction=False).idle_compactions == 0
    assert _idle_session(process_alive_through_gaps=False).idle_compactions == 0
    assert _idle_session(ttl_min=5).idle_compactions == 0
    assert _idle_session(idle_min_tokens=400_000).idle_compactions == 0


def test_rework_after_idle_compaction_starts_cold():
    hit = _idle_session()
    index = next(i for i, call in enumerate(hit.calls) if call.kind == "idle_compaction")
    idle_call = hit.calls[index]
    # The summary itself reads the warm cache at about 54 minutes.
    assert idle_call.cache_read_tokens > 0
    first_rework = hit.calls[index + 1]
    assert first_rework.kind == "rework"
    assert first_rework.cache_read_tokens == 0
    assert first_rework.cold
    assert hit.cold_rebuilds == 1


def test_lapsed_idle_rebuilds_the_whole_context_cold():
    lapsed = _idle_session(process_alive_through_gaps=False)
    assert lapsed.cold_rebuilds == 1
    after_gap = [call for call in lapsed.calls if call.user_turn == 1][0]
    assert after_gap.cache_read_tokens == 0
    assert after_gap.cache_write_tokens > 250_000


def test_s4_family_idle_accounting():
    from cc_usage_study.scenarios import run_all

    rows = {row["name"]: row for row in run_all()}
    assert rows["S4"]["cold_rebuilds_hi"] == 4
    assert rows["S4"]["idle_compactions_hi"] == 0
    assert rows["S4b"]["idle_compactions_hi"] == 4
    assert rows["S4b"]["compactions_hi"] == 0
    assert rows["S4d"]["idle_compactions_hi"] == 0
    assert rows["S4d"]["api_hi"] == rows["S4"]["api_hi"]
    # Idle compaction lapsing costs the default arm about 1.74 times more.
    assert round(rows["S4"]["api_hi"] / rows["S4b"]["api_hi"], 2) == 1.74
    assert rows["S4c"]["cold_rebuilds_hi"] == 59
    assert rows["S1b"]["trigger_lo"] == 217_000
    assert rows["S6"]["trigger_hi"] == 167_000
    assert rows["S6"]["trigger_lo"] == 45_000
    assert rows["S8"]["trigger_lo"] == 57_500


def test_eoq_formula_is_finite_and_above_the_reset():
    assumptions = Assumptions()
    k, reset = fixed_compaction_cost(assumptions)
    estimate = eoq_trigger(
        reset_tokens=reset,
        fixed_compaction_cost=k,
        growth_per_call=assumptions.tail + assumptions.output_per_call,
        read_multiplier=0.1,
    )
    assert estimate > reset
    assert 80_000 < estimate < 250_000


def test_fixed_compaction_cost_formula():
    """K is the analytical per-compaction overhead; the live mean is kept beside it."""
    base = Assumptions(cache_read_multiplier=0.05)
    k, reset = fixed_compaction_cost(base)
    assert reset == 46_000
    assert k == 1.25 * 1_000 + 5 * 3_000 + 2.0 * 26_000 + 0.05 * 20_000
    assert LIVE_MEAN_COMPACTION_UNITS == 36_600


def test_attribution_scheme_is_separate_from_api():
    result = simulate(Assumptions(user_turns=1, calls_per_turn=2))
    assert result.cost("attribution_v2_1_317") != result.cost("api")
    assert result.cost("h_zero") < result.cost("read_full")
