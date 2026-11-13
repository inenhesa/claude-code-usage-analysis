import json
from pathlib import Path

from cc_usage_study.simulate import build_summary, main

ROOT = Path(__file__).resolve().parents[1]


def test_simulate_writes_summary_and_plots(tmp_path: Path):
    code = main(["--out", str(tmp_path)])
    assert code == 0
    summary = (tmp_path / "summary.json").read_text(encoding="utf-8")
    assert "ratio_0.25_over_0.95" in summary
    figures = tmp_path / "figures"
    expected = [
        "cumulative_raw_tokens.png",
        "cumulative_api_equivalent.png",
        "threshold_sweep.png",
        "context_sawtooth.png",
        "sensitivity_cache_hit.png",
        "sensitivity_rework.png",
        "short_session.png",
    ]
    for name in expected:
        path = figures / name
        assert path.is_file()
        assert path.stat().st_size > 1000


def test_committed_summary_matches_the_model():
    committed = json.loads((ROOT / "outputs" / "summary.json").read_text(encoding="utf-8"))
    fresh = build_summary()
    assert (
        committed["long_session"]["ratio_0.25_over_0.95"]
        == fresh["long_session"]["ratio_0.25_over_0.95"]
    )
    assert (
        committed["short_session"]["ratio_0.25_over_0.95"]["prompt_tokens_ratio"]
        == fresh["short_session"]["ratio_0.25_over_0.95"]["prompt_tokens_ratio"]
    )
