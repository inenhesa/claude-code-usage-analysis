"""One command: analyze, reconcile, simulate, draw, and verify the committed model summary.

Only ``outputs/summary.json`` is compared; ``grid.csv``, ``series_long.csv`` and
the figures are regenerated under --out but not compared. Per-session rows
(session ids and timestamps) are left out of the written report.

    python -m cc_usage_study.pipeline                      # synthetic fixtures
    python -m cc_usage_study.pipeline --projects ~/.claude/projects --split-date 2026-11-01

Real transcripts are analyzed and written under --out only. Nothing real is
written into the repository's committed ``outputs/`` unless you point --out there.
Exit code is nonzero when a reconciliation check fails or the regenerated model
summary differs from the committed ``outputs/summary.json``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

from . import analyze, experiments, simulate, validate
from .checks import format_checks, has_errors, run_checks

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures" / "synthetic" / "projects"
COMMITTED = ROOT / "outputs"


def _strip_volatile(value):
    """Drop fields that legitimately differ run to run so a comparison is meaningful."""
    if isinstance(value, dict):
        return {k: _strip_volatile(v) for k, v in value.items() if k not in {"generated_at"}}
    if isinstance(value, list):
        return [_strip_volatile(v) for v in value]
    return value


def run(projects: Path, out: Path, split_date: str | None, split_tz: str) -> int:
    out.mkdir(parents=True, exist_ok=True)
    failed = False

    print(f"== 1/5 analyze {projects}")
    report = analyze.analyze_paths([projects])
    if split_date:
        report["periods"] = analyze.split_periods(report, split_date, split_tz)
    results = run_checks(report)
    print(format_checks(results))
    failed |= has_errors(results)
    report.pop("call_rows", None)
    report.pop("boundary_rows", None)
    report.pop("per_session", None)
    report["checks"] = results
    (out / "analysis-report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(analyze.format_scorecard(report))
    if report.get("synthetic") is True:
        print("Note: synthetic fixture data. Do not publish as real usage.")

    print("== 2/5 simulate (caching model)")
    simulate.write_outputs(out)

    print("== 3/5 live-experiment figures")
    shutil.copyfile(COMMITTED / experiments.DATASET_NAME, out / experiments.DATASET_NAME)
    data = experiments.normalize(experiments.load(out / experiments.DATASET_NAME))
    (out / experiments.DATASET_NAME).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    experiments.write_figures(data, out)

    print("== 4/5 back-test the model against the live runs")
    report_v = validate.run(COMMITTED / "live_experiments.json")
    print(validate.format_report(report_v))
    (out / "validation.json").write_text(json.dumps(report_v, indent=2) + "\n", encoding="utf-8")
    validate.write_figure(report_v, out)
    committed_v = COMMITTED / "validation.json"
    if committed_v.is_file() and json.loads(committed_v.read_text(encoding="utf-8")) != report_v:
        failed = True
        print("[FAIL] validation differs from the committed outputs/validation.json")

    print("== 5/5 verify regenerated model against committed outputs/summary.json")
    fresh = _strip_volatile(json.loads((out / "summary.json").read_text(encoding="utf-8")))
    committed = _strip_volatile(json.loads((COMMITTED / "summary.json").read_text(encoding="utf-8")))
    if fresh == committed:
        print("[ok  ] simulator output equals the committed summary")
    else:
        failed = True
        print("[FAIL] simulator output differs from the committed summary; rerun and commit, or find the nondeterminism")
    print("FAILED" if failed else "OK")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reproduce the study end to end and reconcile it.")
    parser.add_argument("--projects", type=Path, default=FIXTURES, help="transcript directory (default: synthetic fixtures)")
    parser.add_argument("--out", type=Path, help="output directory (default: a temp dir)")
    parser.add_argument("--split-date", help="YYYY-MM-DD for a before/after comparison")
    parser.add_argument("--split-tz", default="UTC")
    args = parser.parse_args(argv)
    if args.out is None:
        args.out = Path(tempfile.mkdtemp(prefix="cc-usage-study-"))
        print(f"output: {args.out}", file=sys.stderr)
    return run(args.projects, args.out, args.split_date, args.split_tz)


if __name__ == "__main__":
    raise SystemExit(main())
