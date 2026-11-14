"""Sanity checks that reconcile an analyzer report against itself.

Most checks compare two numbers the analyzer derives by different routes
(per model, per day, per entrypoint, main plus subagent, per session). They
catch grouping and bookkeeping bugs, and a report edited or corrupted after the
fact. They re-sum the same deduplicated calls, so they cannot catch a wrong
dedup key; the dedup tests in ``tests/`` cover that. A failure is a bug, not a
usage finding. Warnings flag data quality that limits what the report can claim.

These checks cannot prove the totals match what Anthropic billed. For that,
compare ``by_model[...]['list_price_estimate']`` with a source you trust (the
Console usage page or Claude Code's own cost line) over the same window.
"""

from __future__ import annotations

TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "cache_creation_5m_tokens",
    "cache_creation_1h_tokens",
)

# Share of calls without a stop_reason above which output tokens are a weak lower bound.
OUTPUT_PLACEHOLDER_WARN = 0.05


def _sum_groups(groups: dict) -> dict[str, int]:
    out = {key: 0 for key in TOKEN_KEYS}
    out["calls"] = 0
    for row in groups.values():
        out["calls"] += row["calls"]
        for key in TOKEN_KEYS:
            out[key] += row["tokens"][key]
    return out


def _compare(name: str, label: str, expected: dict, actual: dict) -> dict:
    diffs = {
        key: (expected[key], actual[key])
        for key in ("calls", *TOKEN_KEYS)
        if expected[key] != actual[key]
    }
    return {
        "name": name,
        "ok": not diffs,
        "detail": f"{label} matches" if not diffs else f"{label} differs: {diffs}",
        "severity": "error",
    }


def run_checks(report: dict) -> list[dict]:
    totals = {"calls": report["totals"]["calls"], **{k: report["totals"]["tokens"][k] for k in TOKEN_KEYS}}
    results = []
    for name, field in (
        ("by_model_sums_to_totals", "by_model"),
        ("by_day_sums_to_totals", "by_day"),
        ("by_entrypoint_sums_to_totals", "by_entrypoint"),
        ("by_version_sums_to_totals", "by_version"),
    ):
        results.append(_compare(name, field, totals, _sum_groups(report[field])))

    main = report["main"]
    sub = report["subagent"]
    role_sum = {"calls": main["calls"] + sub["calls"]}
    for key in TOKEN_KEYS:
        role_sum[key] = main["tokens"][key] + sub["tokens"][key]
    results.append(_compare("main_plus_subagent_equals_totals", "main + subagent", totals, role_sum))

    kept = report["usage_records_before_dedup"] - report["duplicate_records_dropped"]
    results.append(
        {
            "name": "records_minus_duplicates_equals_calls",
            "ok": kept == report["calls"],
            "detail": f"{report['usage_records_before_dedup']} records - {report['duplicate_records_dropped']} duplicates = {kept}, calls = {report['calls']}",
            "severity": "error",
        }
    )

    tokens = report["totals"]["tokens"]
    prompt = tokens["input_tokens"] + tokens["cache_creation_input_tokens"] + tokens["cache_read_input_tokens"]
    results.append(
        {
            "name": "prompt_tokens_is_sum_of_input_kinds",
            "ok": prompt == tokens["prompt_tokens"],
            "detail": f"{prompt} vs {tokens['prompt_tokens']}",
            "severity": "error",
        }
    )

    if "per_session" in report and isinstance(report["per_session"], list):
        session_sum = sum(row["tokens"]["prompt_tokens"] for row in report["per_session"])
        results.append(
            {
                "name": "per_session_sums_to_totals",
                "ok": session_sum == tokens["prompt_tokens"],
                "detail": f"{session_sum} vs {tokens['prompt_tokens']}",
                "severity": "error",
            }
        )

    periods = report.get("periods")
    if periods:
        side_calls = sum(periods[side]["calls"] for side in ("before", "after", "undated"))
        results.append(
            {
                "name": "periods_cover_all_calls",
                "ok": side_calls == report["calls"],
                "detail": f"{side_calls} vs {report['calls']}",
                "severity": "error",
            }
        )

    # Warnings: data quality that limits the claims, not bugs.
    if tokens["cache_creation_mismatch"]:
        results.append(
            {
                "name": "cache_write_split_mismatch",
                "ok": False,
                "detail": "at least one call has a 5m + 1h cache-write split that differs from the flat field; the flat field is kept and the split is a lower bound",
                "severity": "warning",
            }
        )
    share = report.get("output_without_stop_reason_share")
    if share is not None and share > OUTPUT_PLACEHOLDER_WARN:
        results.append(
            {
                "name": "output_tokens_may_be_placeholders",
                "ok": False,
                "detail": f"{share:.1%} of calls have no stop_reason; treat output tokens as a lower bound",
                "severity": "warning",
            }
        )
    if report.get("tokenizer_warning"):
        results.append(
            {
                "name": "mixed_tokenizer_generations",
                "ok": False,
                "detail": report["tokenizer_warning"],
                "severity": "warning",
            }
        )
    unpriced = sum(
        row["list_price_estimate"].get("usd") is None for row in report["by_model"].values()
    )
    partial = sum(
        row["list_price_estimate"].get("usd") is not None and bool(row["list_price_estimate"].get("reason"))
        for row in report["by_model"].values()
    )
    if unpriced or partial:
        results.append(
            {
                "name": "models_without_price",
                "ok": False,
                "detail": (
                    f"{unpriced} model id(s) could not be priced and {partial} were priced only in part "
                    "(for example a missing cache TTL split); that cost is missing from any USD figure"
                ),
                "severity": "warning",
            }
        )
    return results


def format_checks(results: list[dict]) -> str:
    lines = []
    for item in results:
        mark = "ok  " if item["ok"] else ("FAIL" if item["severity"] == "error" else "warn")
        lines.append(f"[{mark}] {item['name']}: {item['detail']}")
    errors = sum(1 for item in results if not item["ok"] and item["severity"] == "error")
    warnings = sum(1 for item in results if not item["ok"] and item["severity"] == "warning")
    lines.append(f"{errors} error(s), {warnings} warning(s)")
    return "\n".join(lines)


def has_errors(results: list[dict]) -> bool:
    return any(not item["ok"] and item["severity"] == "error" for item in results)
