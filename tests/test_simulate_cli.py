import json
import re
from pathlib import Path

from cc_usage_study.model import Assumptions, simulate
from cc_usage_study.simulate import (
    TASK_CALLS,
    TASK_OUTPUT,
    TASK_PROJECT,
    TASK_SYSTEM,
    TASK_TAIL,
    build_summary,
    cron_jobs,
    hot_resume_crossover,
    main,
    price_of_a_pause,
    stale_resume_vs_clear,
    task_units,
)

ROOT = Path(__file__).resolve().parents[1]
FIGURES = [
    "cumulative_raw_tokens.png",
    "cumulative_api_equivalent.png",
    "threshold_sweep.png",
    "context_sawtooth.png",
    "sensitivity_cache_hit.png",
    "sensitivity_rework.png",
    "short_session.png",
    "eoq_curve.png",
    "amnesia_breakeven.png",
    "compounding_sawtooth.png",
    "scenario_comparison.png",
    "ttl_price_of_a_pause.png",
    "resume_crossover.png",
    "cron_resume_vs_fresh.png",
]


def test_simulate_writes_summary_and_plots(tmp_path: Path):
    figures = tmp_path / "figures"
    figures.mkdir()
    (figures / "ttl_cliff.png").write_bytes(b"old")
    code = main(["--out", str(tmp_path)])
    assert code == 0
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["s1"]["api_saving"] > 1
    for name in FIGURES:
        path = figures / name
        assert path.is_file(), name
        assert path.stat().st_size > 10_000, name
    assert not (figures / "ttl_cliff.png").exists()


def test_committed_outputs_match_the_model():
    committed = json.loads((ROOT / "outputs" / "summary.json").read_text(encoding="utf-8"))
    fresh = json.loads(json.dumps(build_summary()))
    assert committed["s1"]["api_saving"] == fresh["s1"]["api_saving"]
    assert committed["s1"]["raw_prompt_saving"] == fresh["s1"]["raw_prompt_saving"]
    assert committed["use_cases"] == fresh["use_cases"]
    assert committed["pause_tax"] == fresh["pause_tax"]
    for name in FIGURES:
        assert (ROOT / "outputs" / "figures" / name).is_file(), name
    assert not (ROOT / "outputs" / "figures" / "ttl_cliff.png").exists()


def test_summary_has_no_dollar_amounts():
    summary = build_summary()
    assert "list_price" not in summary
    text = json.dumps(summary)
    assert not re.search(r"\$[0-9]", text)
    assert "_usd" not in text
    committed = (ROOT / "outputs" / "summary.json").read_text(encoding="utf-8")
    assert not re.search(r"\$[0-9]", committed)


def test_use_case_sweet_spots():
    by_id = {row["id"]: row for row in build_summary()["use_cases"]}
    assert by_id["deep_0_1"]["best_trigger"] == 120_000
    assert by_id["deep_0_05"]["best_trigger"] == 160_000
    assert by_id["deep_0_05"]["setting_to_type"] == 193_000
    assert round(by_id["deep_0_05"]["cost_change_pct"]) == -43
    assert round(by_id["deep_0_1"]["cost_change_pct"]) == -57
    assert by_id["heavy_memory_0_05"]["best_trigger"] > by_id["deep_0_05"]["best_trigger"]
    assert by_id["unpinned_rules_0_05"]["best_trigger"] > by_id["heavy_memory_0_05"]["best_trigger"]
    small = by_id["context_200k"]
    assert small["default_trigger"] == 167_000
    assert small["caution_trigger"] == 45_000
    assert small["caution_setting"] == "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=25"
    assert 200 < small["chart_change_pct"] < 260
    for case_id in ("breaks_lapsed_0_05", "gaps_5m_0_05", "stale_resume_60k", "cron_hourly_resume"):
        assert by_id[case_id]["chart_kind"] == "penalty"
        assert by_id[case_id]["chart_change_pct"] > 50, case_id
    assert round(by_id["breaks_lapsed_0_05"]["chart_change_pct"]) == 76
    assert by_id["breaks_idle_0_05"]["chart_change_pct"] < 0
    assert 1.9 < by_id["stale_resume_60k"]["chart_change_pct"] / 100 + 1 < 2.2


def test_task_units_matches_the_simulator():
    for ttl, write in ((5.0, 1.25), (60.0, 2.0)):
        result = simulate(
            Assumptions(
                user_turns=1,
                calls_per_turn=TASK_CALLS,
                tail=TASK_TAIL,
                output_per_call=TASK_OUTPUT,
                system_prompt_tokens=TASK_SYSTEM,
                project_context_tokens=TASK_PROJECT,
                ttl_min=ttl,
                cache_read_multiplier=0.05,
            )
        )
        exact = task_units(cached_prefix=0, cold_prefix=TASK_SYSTEM + TASK_PROJECT, write_mult=write, read_mult=0.05)
        assert abs(result.cost("api") - exact) < 1e-6


def test_price_of_a_pause():
    rows = {(row["ttl"], row["read_multiplier"]): row["times_a_warm_read"] for row in price_of_a_pause()}
    assert rows == {("5m", 0.1): 12.5, ("5m", 0.05): 25.0, ("1h", 0.1): 20.0, ("1h", 0.05): 40.0}


def test_stale_resume_crossover():
    stale = stale_resume_vs_clear(history_grid=(0, 60_000))
    five = stale["5m"]["0.05"]
    hour = stale["1h"]["0.05"]
    assert five["slope_units_per_history_token"] == 1.45
    assert hour["slope_units_per_history_token"] == 2.2
    assert round(five["saved_calls_2"]["crossover_history_tokens"] / 100) == 101
    assert round(hour["saved_calls_2"]["crossover_history_tokens"] / 100) == 83
    assert five["saved_calls_0"]["crossover_history_tokens"] == 0
    # Stale resume at 60k history roughly doubles the next task.
    assert 195 < five["saved_calls_2"]["at_60k_pct"] < 210
    assert 135 < five["saved_calls_2"]["at_30k_pct"] < 145
    # Rule of thumb: about 5k of history per saved exploration call.
    assert 4_500 < five["rule_of_thumb_tokens_per_saved_call"] < 5_500


def test_hot_resume_crossover():
    hot = hot_resume_crossover(history_grid=(0,))
    assert hot["live_saved_write_tokens_per_avoided_clear"] == 4_985
    assert round(hot["5m"]["0.05"]["crossover_token_calls"]) == 120_000
    assert round(hot["5m"]["0.1"]["crossover_token_calls"]) == 57_500
    assert hot["5m"]["0.05"]["curve"][0]["hot_pct_of_fresh"] < 100


def test_cron_jobs():
    cron = cron_jobs()
    for key, expected in (("1h", 33), ("5m", 35)):
        block = cron[key]
        assert block["resumes"] == 11
        assert block["cold_rebuilds"] == 11
        assert block["compactions"] == 0
        assert round(block["fresh_pct_of_resume"]) == expected
    assert 2.8 < cron["1h"]["resume_vs_fresh_multiplier"] < 3.1


def test_eoq_reports_the_live_calibration():
    eoq = build_summary()["eoq"]
    calibrated = eoq["live_calibrated"]["0.05"]
    assert calibrated["model_summary_call_units_at_107k"] == 21_600
    assert calibrated["live_mean_compaction_units"] == 36_600
    assert 8_000 < calibrated["valley_shift_tokens"] < 14_000
    assert eoq["simulated_live_calibrated_0.05"]["best_trigger"] >= eoq["simulated_best_10k_grid"]["0.05"]["best_trigger"]
    assert eoq["simulated_best_10k_grid"]["0.05"]["setting_to_type"] == 193_000
