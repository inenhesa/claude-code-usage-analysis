"""Named scenarios S1-S8. Numbers come from model.simulate, not from a table.

Hi is the higher trigger (the before arm). Lo is the lower trigger.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from cc_usage_study.model import Assumptions, SimulationResult, saving, simulate


@dataclass(frozen=True)
class Scenario:
    name: str
    lo: Assumptions
    hi: Assumptions


def _s1() -> Assumptions:
    return Assumptions(label="S1")


def all_scenarios() -> list[Scenario]:
    base = _s1()
    marathon = Assumptions(label="S1c", user_turns=150, tail=2_000)
    short = Assumptions(label="S2", user_turns=3, calls_per_turn=10)
    gaps = ((12, 75.0), (24, 75.0), (36, 75.0), (48, 75.0))
    idle_off = Assumptions(label="S4", long_gaps=gaps, idle_compaction=False)
    idle_on = Assumptions(label="S4b", long_gaps=gaps, idle_compaction=True)
    credits = Assumptions(label="S4c", ttl_min=5, gap_min=7)
    small = Assumptions(label="S6", context_window=200_000, default_trigger=200_000)
    rows: list[Scenario] = [
        Scenario("S1", replace(base, pct=0.25), base),
        Scenario("S1b", replace(base, window=250_000, reserve=33_000), base),
        Scenario("S1c", replace(marathon, pct=0.25), marathon),
        Scenario("S2", replace(short, pct=0.25), short),
        Scenario("S2b", replace(short, window=100_000), short),
    ]
    for k in (0, 3, 6, 10, 15):
        arm = Assumptions(label=f"S3-{k}", rework_calls=k, rework_tail=8_000)
        rows.append(Scenario(f"S3-{k}", replace(arm, pct=0.25), arm))
    rows.extend(
        [
            Scenario("S4", replace(idle_off, pct=0.25), idle_off),
            Scenario("S4b", replace(idle_on, pct=0.25), idle_on),
            Scenario("S4c", replace(credits, pct=0.25), credits),
            Scenario("S6", replace(small, pct=0.25), small),
            Scenario("S6b", replace(small, window=100_000), small),
            Scenario("S7-0.1", replace(base, pct=0.25), base),
            Scenario(
                "S7-0.05",
                replace(base, pct=0.25, cache_read_multiplier=0.05),
                replace(base, cache_read_multiplier=0.05),
            ),
            Scenario(
                "S7-0.025",
                replace(base, pct=0.25, cache_read_multiplier=0.025),
                replace(base, cache_read_multiplier=0.025),
            ),
            Scenario("S8", replace(base, window=250_000, pct=0.25), base),
            Scenario(
                "S8b",
                replace(base, window=250_000, pct=0.25),
                replace(base, pct=0.25),
            ),
        ]
    )
    return rows


def run_scenario(scenario: Scenario) -> dict:
    hi = simulate(scenario.hi)
    lo = simulate(scenario.lo)
    return {
        "name": scenario.name,
        "trigger_hi": hi.trigger_tokens,
        "trigger_lo": lo.trigger_tokens,
        "compactions_hi": hi.compactions,
        "compactions_lo": lo.compactions,
        "warnings_lo": lo.warnings,
        "api_hi": hi.cost("api"),
        "api_lo": lo.cost("api"),
        "api_saving": saving(hi, lo, "api"),
        "raw_prompt_saving": saving(hi, lo, "raw_prompt"),
        "read_full_saving": saving(hi, lo, "read_full"),
        "h_zero_saving": saving(hi, lo, "h_zero"),
        "prompt_hi": hi.totals()["prompt_tokens"],
        "prompt_lo": lo.totals()["prompt_tokens"],
    }


def run_all() -> list[dict]:
    return [run_scenario(scenario) for scenario in all_scenarios()]


def trace_pair(lo: Assumptions, hi: Assumptions) -> tuple[SimulationResult, SimulationResult]:
    return simulate(lo), simulate(hi)
