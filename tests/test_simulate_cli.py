import json
from pathlib import Path

from cc_usage_study.pricing import PRICES_USD_PER_MILLION
from cc_usage_study.simulate import build_summary, main

ROOT = Path(__file__).resolve().parents[1]


def test_simulate_writes_summary_and_plots(tmp_path: Path):
    code = main(["--out", str(tmp_path)])
    assert code == 0
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["s1"]["api_saving"] > 1
    figures = tmp_path / "figures"
    expected = [
        "cumulative_raw_tokens.png",
        "cumulative_api_equivalent.png",
        "threshold_sweep.png",
        "context_sawtooth.png",
        "sensitivity_cache_hit.png",
        "sensitivity_rework.png",
        "short_session.png",
        "eoq_curve.png",
        "amnesia_breakeven.png",
        "ttl_cliff.png",
        "compounding_sawtooth.png",
        "scenario_comparison.png",
    ]
    for name in expected:
        path = figures / name
        assert path.is_file()
        assert path.stat().st_size > 1000


def test_committed_summary_matches_the_model():
    committed = json.loads((ROOT / "outputs" / "summary.json").read_text(encoding="utf-8"))
    fresh = build_summary()
    assert committed["s1"]["api_saving"] == fresh["s1"]["api_saving"]
    assert committed["s1"]["raw_prompt_saving"] == fresh["s1"]["raw_prompt_saving"]
    assert committed["list_price"] == fresh["list_price"]


def test_list_price_anchor_uses_published_rates():
    summary = build_summary()
    prices = summary["list_price"]
    sonnet_rate = PRICES_USD_PER_MILLION["claude-sonnet-5"][0]
    opus_rate = PRICES_USD_PER_MILLION["claude-opus-5-5"][0]
    assert sonnet_rate == 2.0
    assert opus_rate == 4.0
    assert prices["sonnet_5"]["default_967k_usd"] == round(summary["s1"]["api_hi"] * sonnet_rate / 1_000_000, 2)
    sweet = summary["eoq"]["simulated_best_10k_grid"]["0.1"]
    assert prices["sonnet_5"]["sweet_spot_usd"] == round(sweet["best_api"] * sonnet_rate / 1_000_000, 2)
    assert prices["sonnet_5"]["sweet_spot_trigger"] == sweet["best_trigger"]
    opus_default = next(row["api_hi"] for row in summary["scenarios"] if row["name"] == "S7-0.05")
    assert prices["opus_5_5"]["default_967k_usd"] == round(opus_default * opus_rate / 1_000_000, 2)
    assert "trigger_250k_usd" not in prices["sonnet_5"]
    by_id = {row["id"]: row for row in summary["use_cases"]}
    assert by_id["deep_0_1"]["best_trigger"] == 120_000
    assert by_id["deep_0_05"]["best_trigger"] == 160_000
    assert by_id["deep_0_1"]["best_trigger"] == prices["sonnet_5"]["sweet_spot_trigger"]
    assert by_id["deep_0_05"]["best_trigger"] == prices["opus_5_5"]["sweet_spot_trigger"]
    assert by_id["heavy_memory_0_05"]["best_trigger"] > by_id["deep_0_05"]["best_trigger"]
    assert by_id["unpinned_rules_0_05"]["best_trigger"] > by_id["heavy_memory_0_05"]["best_trigger"]
    assert by_id["context_200k"]["default_trigger"] == 200_000
    assert by_id["context_200k"]["chart_change_pct"] > 100
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for amount in (
        prices["sonnet_5"]["default_967k_usd"],
        prices["sonnet_5"]["sweet_spot_usd"],
        prices["opus_5_5"]["default_967k_usd"],
        prices["opus_5_5"]["sweet_spot_usd"],
    ):
        assert f"${amount:.2f}" in readme
    for case_id in ("deep_0_1", "deep_0_05", "heavy_memory_0_05", "unpinned_rules_0_05"):
        assert f"{by_id[case_id]['best_trigger'] // 1000}k" in readme
