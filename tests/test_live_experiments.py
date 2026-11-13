import json
import re
from pathlib import Path

from cc_usage_study.experiments import FIGURE_NAMES, main, predicted_threshold, usage_usd, write_figures

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "outputs" / "live_experiments.json"


def _data() -> dict:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def test_dataset_loads_and_counts_fourteen_runs():
    data = _data()
    assert data["runs"]["total"] == 14
    assert len(data["client_comparison"]["sessions"]) == 7
    assert len(data["compaction"]["arms"]) == 5
    assert data["versions"] == {
        "claude_cli": "2.1.325",
        "t3_code": "0.1.9",
        "claude_desktop": "5.1231.2",
        "claude_desktop_bundled_claude_code": "2.1.321",
    }
    assert round(data["total_api_spend_usd"], 2) == 18.57


def test_dataset_holds_no_private_material():
    text = DATA_PATH.read_text(encoding="utf-8")
    assert "sk-ant" not in text
    assert "/home/" not in text
    assert "/tmp/" not in text
    assert "/Users/" not in text
    assert "\u2014" not in text
    assert not re.search(r"\.claude_study_key", text)


def test_threshold_formula_matches_the_binary():
    assert predicted_threshold(1_000_000) == 967_000
    assert predicted_threshold(180_000) == 147_000
    assert predicted_threshold(140_000) == 107_000
    assert predicted_threshold(100_000) == 67_000
    assert predicted_threshold(250_000, 25) == 57_500
    assert predicted_threshold(1_000_000, 18) == 176_400
    arms = _data()["compaction"]["arms"]
    for record in arms.values():
        assert record["predicted_threshold"] == predicted_threshold(record["window"], record["pct_override"])
    compound = [b["preTokens"] for b in arms["compound_25pct_250k"]["compact_boundaries"]]
    assert compound == [89402, 62687, 98668, 57993, 68068, 57044]


def test_compaction_arms_add_up():
    arms = _data()["compaction"]["arms"]
    expected = {
        "default_1m": (0, 3.17, 0.00),
        "window_180k": (2, 3.17, 0.37),
        "window_140k": (4, 3.74, 0.52),
        "window_100k": (7, 3.97, 1.01),
        "compound_25pct_250k": (6, 3.60, 0.87),
    }
    for key, (compactions, total, hidden) in expected.items():
        record = arms[key]
        assert record["compactions"] == compactions
        assert round(record["cost_state_usd"], 2) == total
        assert round(record["hidden_usd"], 2) == hidden
        assert abs(record["assistant_usd"] + record["hidden_usd"] - record["cost_state_usd"]) < 1e-5
        assert record["check_anomalies_found_phases_1_3"] == 12
        assert record["visible_tests_passed"]
        assert record["checker"]["functional_failures"] == 0
        assert record["checker"]["inv02_modules_calling_datetime_now_directly"] == 0
        assert record["checker"]["semantic_violations"] == 0
    assert round(arms["window_100k"]["hidden_share_of_total"] * 100, 1) == 25.5
    assert arms["default_1m"]["peak_context_tokens"] == 270_132


def test_client_comparison_numbers():
    data = _data()["client_comparison"]
    sessions = data["sessions"]
    prefixes = [sessions[k]["first_call_prefix_tokens"] for k in ("cold_cli_p", "cold_cli_tui", "cold_t3", "cold_desktop")]
    assert prefixes == [16_850, 19_528, 22_842, 39_749]
    startup = [sessions[k]["startup"] for k in ("cold_cli_p", "cold_cli_tui", "cold_t3", "cold_desktop")]
    assert [s["system_prompt_chars"] for s in startup] == [7227, 7485, 12857, 25865]
    assert [s["inline_tools"] for s in startup] == [13, 14, 15, 35]
    assert [s["deferred_tools"] for s in startup] == [13, 15, 82, 82]
    assert startup[2]["deferred_tool_groups"]["t3-code"] == 67
    assert "mcp__t3-code__request_secret" in startup[2]["inline_tool_names"]
    assert round(sessions["task_cli"]["assistant_usd"], 4) == 0.1145
    assert round(sessions["task_cli"]["cost_usd"], 4) == 0.1247
    assert round(sessions["task_t3"]["cost_usd"], 4) == 0.1362
    assert round(sessions["task_desktop"]["cost_usd"], 4) == 0.2057
    assert sessions["task_desktop"]["cost_state_usd"] is None
    for key in ("task_cli", "task_t3", "task_desktop"):
        verification = sessions[key]["verification"]
        assert verification["all_tests_passed"]
        assert verification["bugs_fixed"] == 3
        assert verification["metrics_py_unmodified"]


def test_desktop_cost_matches_opus_list_price():
    usage = {
        "input_tokens": 2,
        "cache_creation_input_tokens": 5353,
        "cache_read_input_tokens": 11495,
        "output_tokens": 4,
        "cache_creation": {"ephemeral_5m_input_tokens": 5353, "ephemeral_1h_input_tokens": 0},
    }
    assert round(usage_usd("claude-opus-5-5", usage), 6) == 0.029152


def test_clear_vs_warm():
    totals = _data()["clear_vs_warm"]["totals"]
    assert round(totals["isolated_usd"], 4) == 0.1507
    assert round(totals["shared_usd"], 4) == 0.1089
    assert totals["shared_saving_pct"] == 27.72
    assert round(totals["cache_write_ratio"], 1) == 2.1


def test_committed_live_figures_exist():
    for name in FIGURE_NAMES:
        path = ROOT / "outputs" / "figures" / name
        assert path.is_file(), name
        assert path.stat().st_size > 1000, name


def test_figures_regenerate_from_json_alone(tmp_path: Path):
    paths = write_figures(_data(), tmp_path)
    assert [p.name for p in paths] == list(FIGURE_NAMES)
    for path in paths:
        assert path.stat().st_size > 1000
    (tmp_path / "live_experiments.json").write_text(DATA_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    assert main(["figures", "--out", str(tmp_path)]) == 0
