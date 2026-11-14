import copy
from pathlib import Path

from cc_usage_study.analyze import analyze_paths, format_scorecard, split_periods
from cc_usage_study.checks import has_errors, run_checks
from cc_usage_study.pipeline import FIXTURES, main

ROOT = Path(__file__).resolve().parents[1]
ALPHA = FIXTURES / "SYNTHETIC-project-alpha"


def test_checks_pass_on_fixtures():
    report = analyze_paths([FIXTURES])
    report["periods"] = split_periods(report, "2026-10-01")
    assert not has_errors(run_checks(report))


def test_checks_catch_a_grouping_bug():
    report = analyze_paths([FIXTURES])
    broken = copy.deepcopy(report)
    first_model = next(iter(broken["by_model"]))
    broken["by_model"][first_model]["tokens"]["cache_read_input_tokens"] += 1
    failed = [r["name"] for r in run_checks(broken) if not r["ok"] and r["severity"] == "error"]
    assert failed == ["by_model_sums_to_totals"]


def test_checks_catch_a_corrupted_record_count():
    # Catches a report whose counters were edited or corrupted after the fact.
    # A wrong dedup key is not caught here; the dedup tests cover that.
    report = analyze_paths([FIXTURES])
    report["duplicate_records_dropped"] -= 1
    names = [r["name"] for r in run_checks(report) if not r["ok"] and r["severity"] == "error"]
    assert "records_minus_duplicates_equals_calls" in names


def test_periods_carry_weighted_usage():
    report = analyze_paths([ALPHA])
    periods = split_periods(report, "2026-10-01")
    for side in ("before", "after"):
        block = periods[side]["weighted_main"]
        assert block["priced_calls"] + block["unpriced_calls"] == periods[side]["main_calls"]
    # Alpha fixture calls carry no cache TTL split, so nothing can be priced:
    # cost is missing, never zero.
    for side in ("before", "after"):
        block = periods[side]["weighted_main"]
        assert block["priced_calls"] == 0
        assert block["list_price_usd_total"] is None
        assert block["list_price_usd_per_call"] is None


def _periods(before_tok, after_tok, before_usd, after_usd, before_share, after_share):
    def side(tok, usd, share):
        return {
            "calls": 10,
            "prompt_tokens_per_call": tok,
            "weighted_main": {"list_price_usd_per_call": usd, "priced_share": share},
        }

    return {
        "split_date": "2026-10-01",
        "before": side(before_tok, before_usd, before_share),
        "after": side(after_tok, after_usd, after_share),
    }


def test_scorecard_labels_raw_tokens_and_prints_cost_delta():
    report = analyze_paths([ALPHA])
    report["periods"] = _periods(100.0, 50.0, 1.0, 1.2, 1.0, 1.0)
    text = format_scorecard(report)
    assert "-50% versus before (raw, can mislead)" in text
    assert "list-price cost per priced main call +20% versus before" in text


def test_scorecard_hides_cost_delta_when_coverage_is_low():
    report = analyze_paths([ALPHA])
    report["periods"] = _periods(100.0, 50.0, 1.0, 1.2, 0.2, 1.0)
    text = format_scorecard(report)
    assert "cost delta not shown" in text
    assert "+20%" not in text


def test_pipeline_runs_clean_on_fixtures(tmp_path):
    assert main(["--out", str(tmp_path)]) == 0
    assert (tmp_path / "analysis-report.json").is_file()
    assert (tmp_path / "summary.json").is_file()
