import json
import re
from pathlib import Path

from cc_usage_study.experiments import (
    FIGURE_NAMES,
    main,
    normalize,
    predicted_threshold,
    usage_usd,
    weighted_units,
    write_figures,
)

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
    for needle in ("sk-" + "ant", ".claude_" + "study_key", "/" + "home/", "/" + "tmp/", "/" + "Users/", "\u2014"):
        assert needle not in text


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


def test_compaction_units_and_validity_gate():
    compaction = _data()["compaction"]
    arms = compaction["arms"]
    gates = {key: record["validity_gate_passed"] for key, record in arms.items()}
    assert gates == {
        "default_1m": None,
        "window_180k": False,
        "window_140k": True,
        "window_100k": True,
        "compound_25pct_250k": False,
    }
    assert round(arms["window_180k"]["first_fire_vs_threshold"] * 100, 1) == 15.7
    assert round(arms["compound_25pct_250k"]["first_fire_vs_threshold"] * 100, 1) == 55.5
    hidden = {key: round(record["hidden_units"] / 1000) for key, record in arms.items()}
    assert hidden == {"default_1m": 0, "window_180k": 94, "window_140k": 130, "window_100k": 253,
                      "compound_25pct_250k": 219}
    assert compaction["total_compactions"] == 19
    assert round(compaction["hidden_mean_units_per_compaction"] / 100) == 366
    lo, hi = arms["compound_25pct_250k"]["hidden_phase_mean_units_range"]
    assert round(lo / 1000) == 19 and round(hi / 1000) == 85
    for record in arms.values():
        for phase in record["phases"]:
            assert "hidden_usd_per_compaction" not in phase
            assert "hidden_phase_mean_units" in phase
        assert record["cost_state_units"] == round(record["cost_state_usd"] / 4.0 * 1_000_000)
    # The ranking flips with the read weight (visible re-weighted, hidden held).
    by_weight = arms["window_180k"]["cost_vs_default_by_read_weight"]
    assert round(by_weight["0.1"] * 100) == -14
    assert round(by_weight["0.05"] * 100) == 0
    assert round(arms["window_100k"]["cost_vs_default_by_read_weight"]["0.1"] * 100) == 0
    # Raw tokens point the other way: 63% fewer at +25.2% usage.
    assert round(arms["window_100k"]["raw_tokens_vs_default"] * 100) == -63


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
        assert verification["hidden_bug_tests_passed"] == 3
        assert "hidden" in verification["bugs_fixed_basis"]
        assert verification["metrics_py_unmodified"]
    comparisons = data["comparisons"]
    desktop = [comparisons["task_units_vs_cli_main_by_read_weight"][r]["task_desktop"] for r in ("0.1", "0.05", "0.025")]
    assert [round(x, 2) for x in desktop] == [1.92, 1.80, 1.71]
    t3 = [comparisons["task_units_vs_cli_main_by_read_weight"][r]["task_t3"] for r in ("0.1", "0.05", "0.025")]
    assert [round(x, 2) for x in t3] == [1.17, 1.19, 1.20]
    assert round(comparisons["task_raw_tokens_vs_cli_main"]["task_desktop"], 2) == 2.39
    assert round(comparisons["task_raw_tokens_vs_cli_main"]["task_t3"], 2) == 1.09
    assert not any("$" in note for note in data["notes"])


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
    assert totals["isolated_raw_tokens"] == 157_821
    assert totals["shared_raw_tokens"] == 168_921
    assert round(totals["shared_raw_tokens_change_pct"], 1) == 7.0
    by_weight = totals["shared_change_pct_by_read_weight"]
    assert [round(by_weight[r], 1) for r in ("0.1", "0.05", "0.025")] == [-21.1, -27.7, -32.0]
    assert totals["saved_write_tokens_per_avoided_clear"] == 4_984.5
    assert abs(weighted_units(totals["isolated_tokens"]) - totals["isolated_units"]) < 2


def test_normalize_is_idempotent():
    data = _data()
    once = normalize(json.loads(json.dumps(data)))
    twice = normalize(json.loads(json.dumps(once)))
    assert once == twice
    assert once == data


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


def test_ttl_runs_hold_their_invariants():
    ttl = _data()["ttl_runs"]
    assert ttl["model"] == "claude-opus-5-5"
    assert ttl["prompt_cache_ttl"] == "5m"
    inv = ttl["invariants"]
    assert inv["one_hour_cache_write_tokens"] == 0
    assert inv["compact_boundaries"] == 0
    assert inv["tests_passed"] == "9 of 9"
    runs = [ttl["hot"], *ttl["stale"], *ttl["clear"]]
    assert len(ttl["stale"]) == len(ttl["clear"]) == 3
    for run in runs:
        assert run["tokens"]["cache_write_1h"] == 0
        assert run["compact_boundaries"] == 0
        assert run["raw_tokens"] == sum(run["tokens"].values())
        for weight, units in run["units_by_read_weight"].items():
            assert abs(weighted_units(run["tokens"], float(weight)) - units) < 0.2
        assert run["units"] == round(run["units_by_read_weight"]["0.05"])
    # Hot resume came back inside the 5-minute window and read its history from cache.
    hot = ttl["hot"]
    assert hot["pause_before_s"] < 300
    assert hot["first_call"]["cache_read"] > 90_000
    assert hot["first_call"]["cache_write_5m"] < 1_000
    # Stale resumes came back past the window and wrote the history again.
    for stale, clear in zip(ttl["stale"], ttl["clear"]):
        assert stale["task"] == clear["task"]
        assert stale["pause_before_s"] > 300
        assert stale["first_call"]["cache_write_5m"] > 85_000
        assert stale["first_call"]["cache_read"] < 12_000
        assert stale["history_tokens"] > 95_000
        assert clear["end_context_tokens"] < 20_000
        assert 9.5 < stale["units"] / clear["units"] < 10.5
    head = ttl["headline_by_read_weight"]["0.05"]
    assert head["stale_vs_clear"] == 9.87
    assert head["hot_vs_clear"] == 1.51
    assert head["stale_vs_hot"] == 6.53
    points = ttl["points"]
    assert [point["kind"] for point in points] == ["hot", "stale", "stale", "stale"]
    assert ttl["stale_bar"]["pct_of_clear"] == 986.6


def test_ttl_runs_carry_no_dollars_or_ids():
    text = json.dumps(_data()["ttl_runs"])
    assert "usd" not in text.lower()
    assert "session_id" not in text and "sessionId" not in text
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", text)
