import copy
import json
from pathlib import Path

from cc_usage_study import validate

ROOT = Path(__file__).resolve().parents[1]
LIVE = json.loads((ROOT / "outputs" / "live_experiments.json").read_text(encoding="utf-8"))


def test_validation_scores_every_live_run_with_a_role():
    report = validate.run()
    rows = report["short_tasks"]["rows"]
    expected = (
        1
        + 1
        + len(LIVE["ttl_runs"]["stale"])
        + len(LIVE["compact_runs"]["before"])
        + len(LIVE["compact_runs"]["after"])
    )
    assert len(rows) == expected
    roles = [r["role"] for r in rows]
    assert roles.count("fit") == 1
    assert roles.count("prediction") == 1 + len(LIVE["ttl_runs"]["stale"])
    assert len(report["compaction_arms"]["rows"]) == len(LIVE["compaction"]["arms"]) - 1


def test_calibration_reads_only_the_fresh_runs():
    # Changing every run except the fresh ones must not move the calibration.
    baseline = validate._calibration(LIVE)
    perturbed = copy.deepcopy(LIVE)
    perturbed["ttl_runs"]["hot"]["tokens"]["output"] *= 3
    for run in perturbed["ttl_runs"]["stale"]:
        run["tokens"]["cache_write_5m"] *= 2
        run["history_tokens"] += 10_000
    for side in ("before", "after"):
        for run in perturbed["compact_runs"][side]:
            run["compact"]["tokens"]["output"] *= 2
    assert validate._calibration(perturbed) == baseline


def test_predictions_do_not_read_their_own_measured_tokens():
    # A prediction row's calibrated value must not change when its measured tokens change.
    report = validate.short_task_checks(LIVE)
    perturbed = copy.deepcopy(LIVE)
    perturbed["ttl_runs"]["hot"]["tokens"]["cache_read"] *= 5
    for run in perturbed["ttl_runs"]["stale"]:
        run["tokens"]["cache_write_5m"] *= 5
    again = validate.short_task_checks(perturbed)
    for a, b in zip(report["rows"], again["rows"]):
        if a["role"] == "prediction":
            assert a["calibrated"] == b["calibrated"]
            assert a["measured"] != b["measured"]


def test_errors_are_relative_to_measured():
    row = validate._row("x", "prediction", measured=100.0, published=150.0, calibrated=90.0)
    assert row["published_error"] == 0.5
    assert row["calibrated_error"] == -0.1
    assert row["published_hit"] is False
    assert row["calibrated_hit"] is True


def test_one_history_definition_for_pause_claims():
    # The break-even history must be where the calibrated extra crosses zero.
    cal = validate._calibration(LIVE)
    rows = {r["claim"]: r for r in validate.modeled_claims_recalibrated(LIVE)}
    be = next(v for k, v in rows.items() if "break-even" in k and "5m" in k)["calibrated"]
    explore = validate.exploration_call_units(validate.WRITE_5M, validate.READ)
    clear = validate._cal_fresh(cal) + 2 * explore
    assert abs(validate._cal_stale(cal, be) - clear) / clear < 0.01


def test_committed_validation_matches_regenerated():
    committed = ROOT / "outputs" / "validation.json"
    assert json.loads(committed.read_text(encoding="utf-8")) == validate.run()
