import json
from pathlib import Path

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
