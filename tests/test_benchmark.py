import json
from pathlib import Path

from cc_usage_study.benchmark import (
    discover_tasks,
    execute_arm,
    extra_files,
    main,
    parse_stream_text,
    plan_arms,
    score_workdir,
    window_tokens_for_percent,
)

CANARY = "CANARY_STREAM_TEXT_do_not_keep"


def test_window_clamp_on_a_200k_context():
    low = window_tokens_for_percent(25, 200_000)
    assert low["raw_tokens"] == 50_000
    assert low["autocompact_tokens"] == 100_000
    assert low["clamped"] is True
    ten = window_tokens_for_percent(10, 200_000)
    assert ten["autocompact_tokens"] == 100_000
    full = window_tokens_for_percent(25, 1_000_000)
    assert full["autocompact_tokens"] == 250_000
    assert full["clamped"] is False
    top = window_tokens_for_percent(95, 1_000_000)
    assert top["autocompact_tokens"] == 950_000


def test_dry_run_lists_both_methods_and_hides_the_prompt(tmp_path: Path, capsys):
    destination = tmp_path / "plan.json"
    code = main(
        [
            "--task",
            "median_bug",
            "--percents",
            "95,25",
            "--methods",
            "window,pct",
            "--output",
            str(destination),
        ]
    )
    assert code == 0
    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["dry_run"] is True
    assert len(payload["arms"]) == 4
    methods = {arm["method"] for arm in payload["arms"]}
    assert methods == {"window", "pct"}
    window_25 = next(arm for arm in payload["arms"] if arm["method"] == "window" and arm["percent"] == 25)
    assert window_25["settings_env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "250000"
    assert "<prompt from task prompt.txt>" in window_25["argv_without_prompt"]
    assert "Fix median" not in json.dumps(window_25)
    pct_95 = next(arm for arm in payload["arms"] if arm["method"] == "pct" and arm["percent"] == 95)
    assert pct_95["settings_env"]["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] == "95"
    joined = " ".join(pct_95["caveats"])
    assert "cannot raise" in joined or "ignored" in joined
    assert "--no-session-persistence" not in window_25["argv_without_prompt"]

    code = main(
        [
            "--task",
            "median_bug",
            "--percents",
            "25",
            "--methods",
            "pct",
            "--no-session-persistence",
            "--output",
            str(tmp_path / "ephemeral.json"),
        ]
    )
    assert code == 0
    ephemeral = json.loads((tmp_path / "ephemeral.json").read_text(encoding="utf-8"))
    assert "--no-session-persistence" in ephemeral["arms"][0]["argv_without_prompt"]


def test_stream_parser_drops_message_text_and_counts_compaction():
    stream = "\n".join(
        [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": CANARY}]},
                }
            ),
            json.dumps({"type": "system", "subtype": "compact_boundary"}),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "result": CANARY,
                    "session_id": "sess-1",
                    "num_turns": 4,
                    "total_cost_usd": 0.5,
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 20,
                        "cache_creation_input_tokens": 30,
                        "cache_read_input_tokens": 40,
                        "cache_creation": {
                            "ephemeral_5m_input_tokens": 5,
                            "ephemeral_1h_input_tokens": 25,
                        },
                    },
                    "modelUsage": {
                        "claude-sonnet-5-5": {
                            "inputTokens": 11,
                            "outputTokens": 22,
                            "cacheReadInputTokens": 44,
                            "cacheCreationInputTokens": 33,
                            "costUSD": 0.5,
                            "text": CANARY,
                        }
                    },
                }
            ),
        ]
    )
    parsed = parse_stream_text(stream)
    assert parsed["ok"] is True
    assert parsed["compaction_markers"] == 1
    assert parsed["usage"]["cache_read_input_tokens"] == 40
    assert parsed["usage"]["ephemeral_1h_input_tokens"] == 25
    assert parsed["model_usage"]["claude-sonnet-5-5"]["cacheReadInputTokens"] == 44
    assert "text" not in parsed["model_usage"]["claude-sonnet-5-5"]
    assert CANARY not in json.dumps(parsed)


def test_score_detects_the_bug_and_a_fix(tmp_path: Path):
    tasks = {task["id"]: task for task in discover_tasks()}
    source = Path(tasks["median_bug"]["dir"])
    broken = tmp_path / "broken"
    broken.mkdir()
    for name in ("stats.py", "test_stats.py"):
        (broken / name).write_text((source / name).read_text(encoding="utf-8"), encoding="utf-8")
    failed = score_workdir(broken, ["python", "-m", "pytest", "-q", "test_stats.py"])
    assert failed["passed"] is False

    fixed = tmp_path / "fixed"
    fixed.mkdir()
    (fixed / "test_stats.py").write_text((source / "test_stats.py").read_text(encoding="utf-8"), encoding="utf-8")
    (fixed / "stats.py").write_text(
        "def median(values):\n"
        "    if not values:\n"
        "        raise ValueError('empty')\n"
        "    ordered = sorted(values)\n"
        "    mid = len(ordered) // 2\n"
        "    if len(ordered) % 2:\n"
        "        return ordered[mid]\n"
        "    return (ordered[mid - 1] + ordered[mid]) / 2\n",
        encoding="utf-8",
    )
    passed = score_workdir(fixed, ["python", "-m", "pytest", "-q", "test_stats.py"])
    assert passed["passed"] is True
    original = {"stats.py", "test_stats.py"}
    (fixed / "notes.txt").write_text("extra", encoding="utf-8")
    assert extra_files(fixed, original, ["stats.py"]) == ["notes.txt"]


def test_execute_is_not_the_default_and_tasks_are_discoverable():
    ids = {task["id"] for task in discover_tasks()}
    assert ids == {"median_bug", "slice_bug", "keep_currency"}
    arms = plan_arms(percents=[95, 25], methods=["window"], repeats=2)
    assert len(arms) == 3 * 2 * 2
    assert all(arm["bare_mode"] is False for arm in arms)
