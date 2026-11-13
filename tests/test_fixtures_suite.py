import json
from pathlib import Path

from cc_usage_study.analyze import analyze_paths, report_contains_canary

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = ROOT / "fixtures" / "synthetic" / "projects"
EXPECTED = json.loads((ROOT / "fixtures" / "synthetic" / "expected" / "generated-sessions.json").read_text())
CANARY = "CANARY_PROMPT_do_not_emit_9f3a"


def test_generated_sessions_match_the_analyzer():
    for key, spec in EXPECTED.items():
        if spec.get("role") == "subagent":
            continue
        report = analyze_paths([PROJECTS / spec["project"] / f"{key}.jsonl"])
        assert report["main"]["calls"] == spec["calls_in_jsonl"]
        assert report["main"]["tokens"]["prompt_tokens"] == spec["prompt_tokens_in_jsonl"]
        assert report["compactions"] == spec["compactions"]
        assert report["synthetic"] is True


def test_cli_and_sdk_subagent_copies_have_the_same_totals():
    report = analyze_paths([PROJECTS / "SYNTHETIC-04-subagent-heavy"])
    def bucket(prefix: str) -> tuple[int, int, int]:
        rows = [row for row in report["per_session"] if row["session_id"].startswith(prefix)]
        calls = sum(row["calls"] for row in rows)
        prompt = sum(row["tokens"]["prompt_tokens"] for row in rows)
        compactions = sum(row["compactions"] for row in rows)
        return calls, prompt, compactions
    assert bucket("syn-sub-cli") == bucket("syn-sub-sdk")
    roles = {row["role"] for row in report["per_session"]}
    assert roles == {"main", "subagent"}


def test_edge_cases_match_the_review_counts():
    report = analyze_paths([PROJECTS / "SYNTHETIC-05-edge-cases"])
    assert report["files"] == 6
    assert report["parse_errors"] == 1
    assert report["excluded_lines"] == 2
    assert report["orphaned_files"] == 1
    assert report["compactions"] == 2
    assert report["compaction_markers"]["pre_tokens"] == [250000, 120000]
    edge = next(row for row in report["per_session"] if row["session_id"] == "syn-edge-1")
    assert edge["calls"] == 3
    assert edge["tokens"]["output_tokens"] == 530
    assert edge["tokens"]["input_tokens"] == 10
    assert edge["tokens"]["cache_creation_input_tokens"] == 11_100
    assert edge["tokens"]["cache_read_input_tokens"] == 15_000
    assert edge["tokens"]["cache_creation_1h_tokens"] == 2_000
    sub = next(row for row in report["per_session"] if row["session_id"] == "syn-edge-1/agent-ag1")
    assert sub["role"] == "subagent"
    assert sub["calls"] == 2
    assert sub["tokens"]["output_tokens"] == 110
    realistic = next(row for row in report["per_session"] if row["session_id"] == "S1")
    assert realistic["calls"] == 1
    assert realistic["tokens"]["output_tokens"] == 500
    assert realistic["compactions"] == 1
    assert report_contains_canary(report, CANARY) is False
    assert "<synthetic>" not in report["by_model"]
