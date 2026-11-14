import json
from pathlib import Path

from cc_usage_study import validate

ROOT = Path(__file__).resolve().parents[1]


def test_validation_runs_and_scores_every_live_run():
    report = validate.run()
    live = json.loads((ROOT / "outputs" / "live_experiments.json").read_text(encoding="utf-8"))
    expected = 1 + 1 + len(live["ttl_runs"]["stale"]) + len(live["compact_runs"]["before"]) + len(live["compact_runs"]["after"])
    assert report["score"]["short_task_runs"] == expected
    assert report["score"]["trigger_formula_matches"] == report["score"]["trigger_arms"]
    assert len([r for r in report["compaction_arms"]["rows"] if r["variant"] == "published"]) == len(live["compaction"]["arms"]) - 1


def test_calibration_uses_fresh_runs_only():
    report = validate.run()
    assert report["short_tasks"]["calibration"]["source"] == "ttl_runs.clear only"


def test_errors_are_relative_to_measured():
    row = validate._row("x", measured=100.0, published=150.0, calibrated=90.0)
    assert row["published_error"] == 0.5
    assert row["calibrated_error"] == -0.1
    assert row["published_hit"] is False
    assert row["calibrated_hit"] is True


def test_committed_validation_matches_regenerated():
    committed = ROOT / "outputs" / "validation.json"
    assert json.loads(committed.read_text(encoding="utf-8")) == validate.run()
