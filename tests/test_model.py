from dataclasses import replace

from cc_usage_study.model import (
    Assumptions,
    closed_form_prompt_tokens,
    effective_trigger,
    eoq_trigger,
    fixed_compaction_cost,
    simulate,
)


def test_effective_trigger_cases():
    default, warnings = effective_trigger(Assumptions())
    assert default == 967_000
    assert warnings == []
    t3, notes = effective_trigger(Assumptions(window=250_000, reserve=33_000))
    assert t3 == 217_000
    assert notes == []
    compound, notes = effective_trigger(Assumptions(window=250_000, pct=0.25))
    assert compound == 62_500
    assert any("compounding" in note for note in notes)
    # A percentage cannot raise the documented default.
    capped, _ = effective_trigger(Assumptions(pct=1.0))
    assert capped == 967_000


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
    assert lo.trigger_tokens == 250_000
    assert hi.compactions == 2
    assert lo.compactions == 10
    api = hi.cost("api") / lo.cost("api")
    raw = hi.cost("raw_prompt") / lo.cost("raw_prompt")
    assert round(api, 2) == 2.06
    assert round(raw, 2) == 3.05


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


def test_attribution_scheme_is_separate_from_api():
    result = simulate(Assumptions(user_turns=1, calls_per_turn=2))
    assert result.cost("attribution_v2_1_317") != result.cost("api")
    assert result.cost("h_zero") < result.cost("read_full")
