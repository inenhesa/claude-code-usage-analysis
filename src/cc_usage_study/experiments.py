"""Live Opus 5.5 experiments: sanitized dataset and empirical charts.

Two jobs live here.

``extract`` reads the raw Claude Code JSONL transcripts and the experiment
workspaces once, and writes ``outputs/live_experiments.json``. The dataset
holds numbers only: token counts, cache-weighted units (with the logged
list-price dollars kept as provenance), compaction metadata, tool and skill
counts, test results. It never stores prompt text, tool output, API keys, or
local paths. Each transcript is identified by its session id and a SHA-256 of
the file, so a reader with the same files can confirm the provenance.

``figures`` (the default) reads only the committed JSON and draws the three
live charts under ``outputs/figures/``. It never touches a transcript, so
anyone can regenerate the charts from the repository alone.

    python3 -m cc_usage_study.experiments figures --out outputs
    python3 -m cc_usage_study.experiments extract --projects ~/.claude/projects --raw-root RAW_DIR

Units are cache-weighted tokens: logged list price divided by the Opus 5.5
input price, so input 1, 5-minute write 1.25, 1-hour write 2, read 0.05,
output 5. ``normalize`` also re-weights the visible token buckets at 0.1x and
0.025x reads. Every live run used a dedicated API key with the default
5-minute prompt cache, so none of this is a subscription meter reading.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from .pricing import HAIKU_55_SHORT, PRICES_USD_PER_MILLION, SNAPSHOT_DATE

DATASET_NAME = "live_experiments.json"
FIGURE_NAMES = (
    "live_client_comparison.png",
    "live_compaction_arms.png",
    "live_clear_vs_warm.png",
)

CLI_VERSION = "2.1.325"
T3_VERSION = "0.1.9"
DESKTOP_VERSION = "5.1231.2"
DESKTOP_BUNDLED_CLI = "2.1.321"
MODEL = "claude-opus-5-5"

# Claude Code v2.1.325, read from the shipped binary.
#   tle(e, n): window - min(maxOutputTokens, 20000)   (output reserve, g_o = 20000)
#   WX(e, n):  e - 13000, or min(floor(e * pct / 100), e - 13000) with the override
OUTPUT_RESERVE_CAP = 20_000
SUMMARY_BUFFER = 13_000

_PRICES = {
    MODEL: PRICES_USD_PER_MILLION[MODEL],
    "claude-haiku-5-5": HAIKU_55_SHORT,
}

# Session ids of the 14 live runs on 13 November 2026.
CLIENT_SESSIONS = {
    "cold_cli_p": ("exp_p2_cold_cli", "9fdd4b50-9d2b-43cb-8fb0-c8c21a1017aa"),
    "cold_cli_tui": ("exp_p2_cold_cli_tui", "9412b239-0f60-4677-9065-3a4fceb0e7e6"),
    "cold_t3": ("exp_p2_cold_t3", "8153574a-77e8-49a7-9753-d8e734c5ad10"),
    "cold_desktop": ("exp_p2_cold_desktop", "f6447251-c11c-439c-be13-d46dcf42ee4b"),
    "task_cli": ("exp_p2_task_cli", "fcc50be1-11ab-43eb-b253-aeccb1a926d4"),
    "task_t3": ("exp_p2_task_t3", "d6cb5419-2c6a-4c15-a6e4-beb0218790e4"),
    "task_desktop": ("exp_p2_task_desktop", "514fa8b5-e959-4b4a-a8c8-4816b66b4ca2"),
}
CLIENT_LABELS = {
    "cold_cli_p": ("Claude CLI", CLI_VERSION, "claude -p (headless)"),
    "cold_cli_tui": ("Claude CLI", CLI_VERSION, "interactive TUI"),
    "cold_t3": ("T3 Code", T3_VERSION, f"Agent SDK wrapping Claude Code {CLI_VERSION}"),
    "cold_desktop": ("Claude Desktop", DESKTOP_VERSION, f"Code tab, bundled Claude Code {DESKTOP_BUNDLED_CLI}"),
    "task_cli": ("Claude CLI", CLI_VERSION, "interactive TUI"),
    "task_t3": ("T3 Code", T3_VERSION, f"Agent SDK wrapping Claude Code {CLI_VERSION}"),
    "task_desktop": ("Claude Desktop", DESKTOP_VERSION, f"Code tab, bundled Claude Code {DESKTOP_BUNDLED_CLI}"),
}

COMPACTION_ARMS = {
    "default_1m": {
        "workspace": "exp_p1_pilot",
        "session": "34eeb454-c687-493b-849b-a08299ebd9a9",
        "label": "Default 1M",
        "window": 1_000_000,
        "pct_override": None,
        "flags": "--autocompact auto (1M window)",
    },
    "window_180k": {
        "workspace": "exp_p1_w180k",
        "session": "8e83452e-a45b-42d1-b2f1-fe2d1729e228",
        "label": "180k window",
        "window": 180_000,
        "pct_override": None,
        "flags": "--autocompact 180k",
    },
    "window_140k": {
        "workspace": "exp_p1_w140k",
        "session": "c339e387-7b83-47d9-9f5a-587a03f8f9ba",
        "label": "140k window",
        "window": 140_000,
        "pct_override": None,
        "flags": "--autocompact 140k",
    },
    "window_100k": {
        "workspace": "exp_p1_w100k",
        "session": "d8350d72-6379-49c4-b8f5-527a68c94f7c",
        "label": "100k window",
        "window": 100_000,
        "pct_override": None,
        "flags": "--autocompact 100k",
    },
    "compound_25pct_250k": {
        "workspace": "exp_p1_compound",
        "session": "6ddf20db-680c-4bf2-bd44-04e9b615fbdd",
        "label": "250,000-token window + 0.25 override",
        "window": 250_000,
        "pct_override": 25,
        "flags": "--autocompact 250k with CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=25",
    },
}

SHORT_TASKS = ("median_bug", "slice_bug", "keep_currency")
SHORT_ISOLATED = {
    "median_bug": ("exp_p1_short_iso_median_bug", "53645ba3-afe2-4008-8268-269ab4fe2efb"),
    "slice_bug": ("exp_p1_short_iso_slice_bug", "9b1db275-555f-45e5-92cd-ac2ee014bd98"),
    "keep_currency": ("exp_p1_short_iso_keep_currency", "b6658ae1-45da-403b-9d56-87ba8b399c37"),
}
SHORT_SHARED = ("exp_p1_short_shared", "760891f7-8ef3-470d-bc3e-26177ffe8712")


def predicted_threshold(window: int, pct: float | None = None, max_output_tokens: int = 128_000) -> int:
    """Auto-compact threshold in Claude Code v2.1.325 (functions ``tle`` and ``WX``)."""
    effective = window - min(max_output_tokens, OUTPUT_RESERVE_CAP)
    cap = effective - SUMMARY_BUFFER
    if pct is not None and 0 < pct <= 100:
        return min(int(effective * pct / 100), cap)
    return cap


def usage_usd(model: str, usage: dict) -> float:
    base, write_5m, write_1h, read, output = _PRICES[model]
    cache = usage.get("cache_creation") or {}
    w5 = cache.get("ephemeral_5m_input_tokens", usage.get("cache_creation_input_tokens", 0))
    w1 = cache.get("ephemeral_1h_input_tokens", 0)
    return (
        usage.get("input_tokens", 0) * base
        + w5 * write_5m
        + w1 * write_1h
        + usage.get("cache_read_input_tokens", 0) * read
        + usage.get("output_tokens", 0) * output
    ) / 1_000_000


def _project_dir(projects: Path, workspace: str) -> Path:
    return projects / ("-tmp-" + workspace.replace("_", "-"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _r(value: float, digits: int = 6) -> float:
    return round(value, digits) + 0.0


def _assistant_calls(rows: list[dict], end: int | None = None) -> list[dict]:
    """One record per API response (assistant rows repeat per content block)."""
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for row in rows[:end]:
        if row.get("type") != "assistant":
            continue
        message = row["message"]
        if message["id"] not in by_id:
            order.append(message["id"])
        by_id[message["id"]] = row
    calls = []
    for message_id in order:
        row = by_id[message_id]
        message = row["message"]
        usage = message["usage"]
        cache = usage.get("cache_creation") or {}
        calls.append(
            {
                "model": message["model"],
                "sidechain": bool(row.get("isSidechain")),
                "input": usage.get("input_tokens", 0),
                "cache_write_5m": cache.get("ephemeral_5m_input_tokens", usage.get("cache_creation_input_tokens", 0)),
                "cache_write_1h": cache.get("ephemeral_1h_input_tokens", 0),
                "cache_read": usage.get("cache_read_input_tokens", 0),
                "output": usage.get("output_tokens", 0),
                "context": usage.get("input_tokens", 0)
                + usage.get("cache_creation_input_tokens", 0)
                + usage.get("cache_read_input_tokens", 0),
                "usd": usage_usd(message["model"], usage),
            }
        )
    return calls


def _token_totals(calls: list[dict]) -> dict:
    keys = ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")
    return {key: sum(call[key] for call in calls) for key in keys}


def _dollar_split(tokens: dict) -> dict:
    base, write_5m, write_1h, read, output = _PRICES[MODEL]
    return {
        "input_usd": _r(tokens["input"] * base / 1e6),
        "cache_write_usd": _r((tokens["cache_write_5m"] * write_5m + tokens["cache_write_1h"] * write_1h) / 1e6),
        "cache_read_usd": _r(tokens["cache_read"] * read / 1e6),
        "output_usd": _r(tokens["output"] * output / 1e6),
    }


def _cost_states(rows: list[dict]) -> list[tuple[int, dict]]:
    return [(index, row) for index, row in enumerate(rows) if row.get("type") == "cost-state"]


def _session_record(path: Path) -> tuple[list[dict], dict]:
    rows = _rows(path)
    calls = _assistant_calls(rows)
    states = _cost_states(rows)
    versions = sorted({row["version"] for row in rows if row.get("version")})
    entrypoints = sorted({row["entrypoint"] for row in rows if row.get("entrypoint")})
    requests = [row for row in rows if row.get("type") == "api-request"]
    assistant_usd = sum(call["usd"] for call in calls)
    cost_state = states[-1][1] if states else None
    record = {
        "session_id": rows[0].get("sessionId") or path.stem,
        "jsonl_sha256": _sha256(path),
        "jsonl_bytes": path.stat().st_size,
        "claude_code_version": versions,
        "entrypoint": entrypoints,
        "models": sorted({call["model"] for call in calls}),
        "effort": sorted({row.get("effort") for row in rows if row.get("type") == "assistant" and row.get("effort")}),
        "prompt_cache_ttl": sorted({row.get("promptCacheRequested") for row in rows if row.get("type") == "assistant"} - {None}),
        "max_output_tokens": sorted({row["params"].get("max_tokens") for row in requests if row.get("params")}),
        "assistant_calls": len(calls),
        "tokens": _token_totals(calls),
        "assistant_usd": _r(assistant_usd),
        "cost_state_usd": _r(cost_state["totalCostUSD"]) if cost_state else None,
        "cost_state_by_model_usd": (
            {model: _r(data["costUSD"]) for model, data in cost_state["modelUsage"].items()} if cost_state else None
        ),
        "first_call_prefix_tokens": calls[0]["context"] if calls else None,
        "peak_context_tokens": max(call["context"] for call in calls) if calls else None,
        "end_context_tokens": calls[-1]["context"] if calls else None,
    }
    record["dollars"] = _dollar_split(record["tokens"])
    if cost_state:
        record["hidden_usd"] = _r(cost_state["totalCostUSD"] - assistant_usd)
    return rows, record


def _startup_shape(rows: list[dict]) -> dict:
    shape = next(row["shape"] for row in rows if row.get("type") == "api-request-shape")
    system_text = "".join(block.get("text", "") for block in shape["system"])
    tools = [tool["name"] for tool in shape["tools"]]
    deferred: list[str] = []
    skills = None
    for row in rows:
        if row.get("type") != "attachment":
            continue
        attachment = row["attachment"]
        if attachment["type"] == "deferred_tools_delta" and not deferred:
            deferred = list(attachment.get("addedNames", []))
        if attachment["type"] == "skill_listing" and skills is None:
            skills = attachment.get("skillCount")
    groups: dict[str, int] = {}
    for name in deferred:
        group = name.split("__")[1] if name.startswith("mcp__") else "built-in"
        groups[group] = groups.get(group, 0) + 1
    inline_groups: dict[str, int] = {}
    for name in tools:
        group = name.split("__")[1] if name.startswith("mcp__") else "built-in"
        inline_groups[group] = inline_groups.get(group, 0) + 1
    tagged = {}
    for tag in ("runtime_info", "pull_request_linking"):
        match = re.search(rf"<{tag}>.*?</{tag}>", system_text, re.S)
        tagged[tag] = len(match.group(0)) if match else 0
    return {
        "system_prompt_chars": len(system_text),
        "system_blocks": len(shape["system"]),
        "inline_tools": len(tools),
        "inline_tool_names": tools,
        "inline_tool_groups": inline_groups,
        "deferred_tools": len(deferred),
        "deferred_tool_groups": groups,
        "skills": skills,
        "tagged_block_chars": tagged,
    }


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=".")
    return subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=300)


def _pytest_counts(result: subprocess.CompletedProcess) -> dict:
    tail = (result.stdout or "").strip().splitlines()[-1:] or [""]
    passed = re.search(r"(\d+) passed", tail[0])
    failed = re.search(r"(\d+) failed", tail[0])
    return {
        "exit_code": result.returncode,
        "passed": int(passed.group(1)) if passed else 0,
        "failed": int(failed.group(1)) if failed else 0,
    }


def _bughunt_verification(workspace: Path, hidden: Path, pytest: str) -> dict:
    manifest = json.loads((workspace / "MANIFEST.json").read_text(encoding="utf-8"))
    decoy = "relayd/metrics.py"
    result = _run([pytest, "-q", "-p", "no:cacheprovider", "tests/visible", str(hidden)], workspace)
    counts = _pytest_counts(result)
    hidden_counts = _pytest_counts(_run([pytest, "-q", "-p", "no:cacheprovider", str(hidden)], workspace))
    changed = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"], cwd=workspace, capture_output=True, text=True
    ).stdout.split()
    return {
        "visible_and_hidden_tests": counts,
        "all_tests_passed": result.returncode == 0 and counts["failed"] == 0,
        "hidden_bug_tests_passed": hidden_counts["passed"],
        # One hidden test per planted bug, so a passing hidden test is a fixed bug.
        "bugs_fixed": hidden_counts["passed"] if hidden_counts["failed"] == 0 else None,
        "bugs_fixed_basis": "count of passing hidden bug tests, one per planted bug",
        "files_changed": sorted(changed),
        "metrics_py_unmodified": _sha256(workspace / decoy) == manifest["files"][decoy],
    }


def extract_clients(projects: Path, raw_root: Path, repo_root: Path, pytest: str) -> dict:
    sessions = {}
    for key, (workspace, session_id) in CLIENT_SESSIONS.items():
        path = _project_dir(projects, workspace) / f"{session_id}.jsonl"
        rows, record = _session_record(path)
        client, version, surface = CLIENT_LABELS[key]
        record.update({"client": client, "client_version": version, "surface": surface})
        if key.startswith("cold_"):
            record["startup"] = _startup_shape(rows)
        else:
            hidden = repo_root / "benchmarks" / "tasks" / "noisy_bughunt" / "hidden" / "test_bugs.py"
            record["verification"] = _bughunt_verification(raw_root / workspace, hidden, pytest)
        record["cost_usd"] = record["cost_state_usd"] if record["cost_state_usd"] is not None else record["assistant_usd"]
        record["cost_source"] = (
            "cost_state.totalCostUSD" if record["cost_state_usd"] is not None
            else "deduped assistant usage at Opus 5.5 list price (Desktop writes no cost_state)"
        )
        sessions[key] = record
    claude_md = {key: _sha256(raw_root / CLIENT_SESSIONS[key][0] / "CLAUDE.md") for key in CLIENT_SESSIONS}
    cold_md = {claude_md[key] for key in claude_md if key.startswith("cold_")}
    task_md = {claude_md[key] for key in claude_md if key.startswith("task_")}
    tui = sessions["cold_cli_tui"]["first_call_prefix_tokens"]
    cli_main = sessions["task_cli"]["assistant_usd"]
    cli_total = sessions["task_cli"]["cost_usd"]
    comparisons = {
        "cold_prefix_vs_cli_tui": {
            key: _r(sessions[key]["first_call_prefix_tokens"] / tui, 4) for key in ("cold_cli_p", "cold_t3", "cold_desktop")
        },
        "cold_prefix_desktop_vs_cli_p": _r(
            sessions["cold_desktop"]["first_call_prefix_tokens"] / sessions["cold_cli_p"]["first_call_prefix_tokens"], 4
        ),
        "task_cost_vs_cli_main": {
            key: _r(sessions[key]["cost_usd"] / cli_main, 4) for key in ("task_t3", "task_desktop")
        },
        "task_cost_vs_cli_total": {
            key: _r(sessions[key]["cost_usd"] / cli_total, 4) for key in ("task_t3", "task_desktop")
        },
        "t3_extra_system_prompt_chars_vs_cli_tui": sessions["cold_t3"]["startup"]["system_prompt_chars"]
        - sessions["cold_cli_tui"]["startup"]["system_prompt_chars"],
    }
    return {
        "setup": {
            "model": MODEL,
            "effort": "medium",
            "context_window": 1_000_000,
            "claude_md": "identical 3-line CLAUDE.md per experiment (cold and bugfix arms each share one file)",
            "claude_md_identical_cold": len(cold_md) == 1,
            "claude_md_identical_task": len(task_md) == 1,
            "user_mcp_servers": 0,
            "task": "noisy_bughunt (benchmarks/generators/gen_noisy_bughunt.py, seed 1337, scale S, P-doc): 3 planted bugs",
            "cold_turn": "one-word reply, single API call, measures the startup prefix each client sends",
            "five_hour_meter": "not measured: every run used a dedicated API key, not a subscription",
        },
        "sessions": sessions,
        "comparisons": comparisons,
        "notes": [
            "CLI task_cli ran in the interactive TUI. Its cost_state exceeds its deduped assistant rows by %.1f%%: "
            "the TUI fires background calls (Haiku title plus an Opus side request) that write no assistant row."
            % ((cli_total / cli_main - 1) * 100),
            "T3 Code cost_state equals its assistant rows. Its separate title-generation sessions write no transcript.",
            "Claude Desktop writes no cost_state row. Its cost is computed from deduped assistant usage and is a floor.",
        ],
    }


def _phase_segments(rows: list[dict]) -> list[tuple[int, int]]:
    """(start, end) row ranges, one per --resume phase, closed by a cost-state row."""
    segments = []
    start = 0
    for index, _row in _cost_states(rows):
        segments.append((start, index + 1))
        start = index + 1
    return segments


def _assistant_text(rows: list[dict]) -> str:
    parts = []
    for row in rows:
        if row.get("type") != "assistant":
            continue
        for block in row["message"].get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
    return "\n".join(parts)


def extract_compaction(projects: Path, raw_root: Path, checker: Path, pytest: str) -> dict:
    arms = {}
    for key, spec in COMPACTION_ARMS.items():
        path = _project_dir(projects, spec["workspace"]) / f"{spec['session']}.jsonl"
        rows, record = _session_record(path)
        workspace = raw_root / spec["workspace"]
        calls = _assistant_calls(rows)
        max_out = max(record["max_output_tokens"])
        threshold = predicted_threshold(spec["window"], spec["pct_override"], max_out)
        boundaries = []
        compact_rows = [i for i, row in enumerate(rows) if row.get("subtype") == "compact_boundary"]
        for index in compact_rows:
            meta = rows[index]["compactMetadata"]
            before = len(_assistant_calls(rows, index))
            boundaries.append(
                {
                    "trigger": meta["trigger"],
                    "preTokens": meta["preTokens"],
                    "postTokens": meta["postTokens"],
                    "durationMs": meta.get("durationMs"),
                    "after_call_index": before,
                    "preTokens_minus_threshold": meta["preTokens"] - threshold,
                }
            )
        manifest = json.loads((workspace / "MANIFEST.json").read_text(encoding="utf-8"))
        anomalies = sorted(n for batch in manifest["anomalies"].values() for n in batch)
        phases = []
        prior_state = 0.0
        prior_assistant = 0.0
        found_all: set[int] = set()
        for number, (start, end) in enumerate(_phase_segments(rows), start=1):
            state = rows[end - 1]["totalCostUSD"]
            assistant = sum(call["usd"] for call in _assistant_calls(rows, end))
            compacts = sum(1 for i in compact_rows if start <= i < end)
            numbers = {int(x) for x in re.findall(r"\b\d{4}\b", _assistant_text(rows[start:end]))}
            found = sorted(numbers & set(anomalies))
            found_all |= set(found)
            hidden = (state - prior_state) - (assistant - prior_assistant)
            phases.append(
                {
                    "phase": number,
                    "cost_state_usd": _r(state - prior_state),
                    "assistant_usd": _r(assistant - prior_assistant),
                    "hidden_usd": _r(hidden),
                    "compactions": compacts,
                    "hidden_phase_mean_usd": _r(hidden / compacts) if compacts else None,
                    "check_anomalies_reported": len(found),
                }
            )
            prior_state, prior_assistant = state, assistant
        recon = (workspace / "RECONCILIATION.md").read_text(encoding="utf-8")
        recon_numbers = {int(x) for x in re.findall(r"\b\d{4}\b", recon)}
        tests = _pytest_counts(_run([pytest, "-q", "-p", "no:cacheprovider", "tests/visible"], workspace))
        check = json.loads(_run([sys.executable, str(checker), str(workspace)], workspace).stdout)
        modules = sorted((workspace / "ledgerkit" / "modules").glob("m*.py"))
        sources = {module.name: module.read_text(encoding="utf-8") for module in modules}
        delegated = sorted(name for name, src in sources.items() if "clock.now()" in src)
        direct = sorted(name for name, src in sources.items() if re.search(r"datetime\.(datetime\.)?now\(\)", src))
        record.update(
            {
                "label": spec["label"],
                "flags": spec["flags"],
                "window": spec["window"],
                "pct_override": spec["pct_override"],
                "predicted_threshold": threshold,
                "compactions": len(boundaries),
                "compact_boundaries": boundaries,
                "context_per_call": [call["context"] for call in calls],
                "phases": phases,
                "hidden_share_of_total": _r(record["hidden_usd"] / record["cost_state_usd"], 4),
                "check_anomalies_planted": len(anomalies),
                "check_anomalies_found_phases_1_3": len(found_all),
                "reconciliation_md_lists_anomalies": len(recon_numbers & set(anomalies)),
                "visible_tests": tests,
                "visible_tests_passed": tests["exit_code"] == 0,
                "checker": {
                    "functional_failures": len(check.get("functional_failures", [])),
                    "violations": check.get("violations_nonzero_only", {}),
                    "inv02_modules_delegating_to_clock_now": len(delegated),
                    "inv02_modules_calling_datetime_now_directly": len(direct),
                    "semantic_violations": 0
                    if not direct and not check.get("functional_failures")
                    and set(check.get("violations_nonzero_only", {})) <= {"INV-02"}
                    else None,
                },
            }
        )
        arms[key] = record
    default_cost = arms["default_1m"]["cost_state_usd"]
    for record in arms.values():
        record["cost_vs_default"] = _r(record["cost_state_usd"] / default_cost - 1, 4)
    return {
        "setup": {
            "suite": "LedgerKit v3.2 migration, P-doc variant, 4 phases joined with --resume",
            "model": MODEL,
            "effort": "medium",
            "prompt_cache_ttl": "5m",
            "phases": [
                "migrate m00-m07, read architecture docs, report CHECK_ANOMALY rows in batches 1a/1b",
                "migrate m08-m15, read runbooks, report CHECK_ANOMALY rows in batches 2a/2b",
                "migrate m16-m23, re-check m00-m15, report CHECK_ANOMALY rows in batches 3a/3b",
                "read golden fixtures, write RECONCILIATION.md, confirm tests",
            ],
        },
        "threshold_formula": {
            "source": f"Claude Code v{CLI_VERSION} binary, functions tle and WX",
            "effective_window": "window - min(maxOutputTokens, 20000)",
            "threshold_no_override": "effective_window - 13000 = window - 33000",
            "threshold_with_pct_override": "min(floor(effective_window * pct / 100), effective_window - 13000)",
            "examples": {
                "1M": predicted_threshold(1_000_000),
                "180k": predicted_threshold(180_000),
                "140k": predicted_threshold(140_000),
                "100k": predicted_threshold(100_000),
                "250k_with_25": predicted_threshold(250_000, 25),
                "1M_with_18": predicted_threshold(1_000_000, 18),
            },
            "note": (
                "preTokens is logged by a different counter from the trigger check, which can add pending tokens "
                "(e + h >= threshold). Fires can log a few thousand tokens under the line or overshoot it after a "
                "large tool batch."
            ),
        },
        "arms": arms,
        "inv02_explanation": (
            "The checker's INV-02 rule flags any .now() call in a module through an AST match. The 180k and 100k arms "
            "replaced datetime.datetime.now() with clock.now(), which is the sanctioned fix but still matches the rule. "
            "The other three arms deleted the unused _stamp line. No arm calls datetime.now() directly in a module."
        ),
        "w140k_reconciliation_note": (
            "The 140k arm reported all 12 CHECK_ANOMALY rows in phases 1-3, but its RECONCILIATION.md does not "
            "repeat them because the phase 4 prompt did not ask for anomalies."
        ),
    }


def extract_short(projects: Path, raw_root: Path, pytest: str) -> dict:
    isolated = {}
    for task, (workspace, session_id) in SHORT_ISOLATED.items():
        _rows_unused, record = _session_record(_project_dir(projects, workspace) / f"{session_id}.jsonl")
        tests = _pytest_counts(_run([pytest, "-q", "-p", "no:cacheprovider"], raw_root / "exp_p1_short_iso" / task))
        record["tests"] = tests
        record["tests_passed"] = tests["exit_code"] == 0
        isolated[task] = record
    rows, shared = _session_record(_project_dir(projects, SHORT_SHARED[0]) / f"{SHORT_SHARED[1]}.jsonl")
    prior = 0.0
    turns = []
    for task, (_index, state) in zip(SHORT_TASKS, _cost_states(rows)):
        turns.append({"task": task, "cost_usd": _r(state["totalCostUSD"] - prior)})
        prior = state["totalCostUSD"]
    shared["turns"] = turns
    tests = _pytest_counts(_run([pytest, "-q", "-p", "no:cacheprovider"], raw_root / SHORT_SHARED[0]))
    shared["tests"] = tests
    shared["tests_passed"] = tests["exit_code"] == 0
    iso_tokens = {key: sum(r["tokens"][key] for r in isolated.values()) for key in shared["tokens"]}
    iso_total = sum(r["cost_state_usd"] for r in isolated.values())
    return {
        "setup": {
            "tasks": list(SHORT_TASKS),
            "model": MODEL,
            "effort": "medium",
            "prompt_cache_ttl": "5m",
            "isolated_arm": "3 fresh sessions, equivalent to /clear between tasks",
            "shared_arm": "1 session, 3 back-to-back --resume turns",
        },
        "isolated": isolated,
        "shared": shared,
        "totals": {
            "isolated_usd": _r(iso_total),
            "shared_usd": shared["cost_state_usd"],
            "shared_saving_usd": _r(iso_total - shared["cost_state_usd"]),
            "shared_saving_pct": _r((1 - shared["cost_state_usd"] / iso_total) * 100, 2),
            "isolated_tokens": iso_tokens,
            "shared_tokens": shared["tokens"],
            "isolated_dollars": _dollar_split(iso_tokens),
            "shared_dollars": shared["dollars"],
            "cache_write_ratio": _r(iso_tokens["cache_write_5m"] / shared["tokens"]["cache_write_5m"], 3),
        },
    }


def extract(projects: Path, raw_root: Path, repo_root: Path, checker: Path, pytest: str) -> dict:
    clients = extract_clients(projects, raw_root, repo_root, pytest)
    compaction = extract_compaction(projects, raw_root, checker, pytest)
    short = extract_short(projects, raw_root, pytest)
    spend = (
        sum(record["cost_usd"] for record in clients["sessions"].values())
        + sum(record["cost_state_usd"] for record in compaction["arms"].values())
        + short["totals"]["isolated_usd"]
        + short["totals"]["shared_usd"]
    )
    data = {
        "schema": 1,
        "date": "2026-11-13",
        "pricing_snapshot": SNAPSHOT_DATE,
        "prices_usd_per_million": {
            "model": MODEL,
            "input": _PRICES[MODEL][0],
            "cache_write_5m": _PRICES[MODEL][1],
            "cache_write_1h": _PRICES[MODEL][2],
            "cache_read": _PRICES[MODEL][3],
            "output": _PRICES[MODEL][4],
        },
        "versions": {
            "claude_cli": CLI_VERSION,
            "t3_code": T3_VERSION,
            "claude_desktop": DESKTOP_VERSION,
            "claude_desktop_bundled_claude_code": DESKTOP_BUNDLED_CLI,
        },
        "runs": {
            "client_comparison": len(clients["sessions"]),
            "compaction_arms": len(compaction["arms"]),
            "clear_vs_warm_arms": 2,
            "total": len(clients["sessions"]) + len(compaction["arms"]) + 2,
        },
        "total_api_spend_usd": _r(spend, 4),
        "privacy": "numbers only: no prompt text, tool output, keys, or local paths",
        "client_comparison": clients,
        "compaction": compaction,
        "clear_vs_warm": short,
    }
    return normalize(data)


# ----------------------------------------------------------------------------- units

READ_WEIGHTS = (0.1, 0.05, 0.025)
GATE = 0.15  # protocol.md: the first preTokens must land within 15% of the trigger


def _input_price() -> float:
    return _PRICES[MODEL][0]


def _units(usd: float | None) -> int | None:
    """Dollars at Opus 5.5 list price back to cache-weighted units (input = 1)."""
    if usd is None:
        return None
    return round(usd / _input_price() * 1_000_000)


def weighted_units(tokens: dict, read_weight: float = 0.05) -> float:
    """Cache-weighted units from token buckets: 1 / 1.25 / 2 / read / 5."""
    return (
        tokens["input"]
        + tokens["cache_write_5m"] * 1.25
        + tokens["cache_write_1h"] * 2.0
        + tokens["cache_read"] * read_weight
        + tokens["output"] * 5.0
    )


def _raw(tokens: dict) -> int:
    return sum(tokens[key] for key in ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output"))


def _by_weight(tokens: dict) -> dict:
    return {str(r): round(weighted_units(tokens, r), 1) for r in READ_WEIGHTS}


def _session_units(record: dict) -> None:
    record["raw_tokens"] = _raw(record["tokens"])
    record["assistant_units"] = _units(record.get("assistant_usd"))
    record["assistant_units_by_read_weight"] = _by_weight(record["tokens"])
    if "cost_state_usd" in record:
        record["cost_state_units"] = _units(record.get("cost_state_usd"))
    if "hidden_usd" in record:
        record["hidden_units"] = _units(record["hidden_usd"])
    if "cost_usd" in record:
        record["cost_units"] = _units(record["cost_usd"])


def _pct(new: float, old: float) -> float:
    return _r((new / old - 1) * 100, 2)


def normalize(data: dict) -> dict:
    """Add units, raw tokens, model-weight ranges, and validity flags. Idempotent.

    Dollars stay in the JSON only as provenance (they are what Claude Code's
    cost_state logged). Every user-facing number is derived from the units.
    """
    data["units"] = {
        "name": "cache-weighted units",
        "definition": "usd / input list price x 1e6, i.e. input 1, 5m write 1.25, 1h write 2, read 0.05, output 5",
        "read_weight_sensitivity": list(READ_WEIGHTS),
    }
    clients = data["client_comparison"]
    for record in clients["sessions"].values():
        _session_units(record)
        verification = record.get("verification")
        if verification is not None:
            verification.setdefault(
                "bugs_fixed_basis",
                "the 3 hidden bug tests passed (the combined visible + hidden run passed 4 tests, 1 of them visible)",
            )
    sessions = clients["sessions"]
    main = sessions["task_cli"]["tokens"]
    clients["comparisons"]["task_units_vs_cli_main_by_read_weight"] = {
        str(r): {
            key: _r(weighted_units(sessions[key]["tokens"], r) / weighted_units(main, r), 4)
            for key in ("task_t3", "task_desktop")
        }
        for r in READ_WEIGHTS
    }
    clients["comparisons"]["task_raw_tokens_vs_cli_main"] = {
        key: _r(_raw(sessions[key]["tokens"]) / _raw(main), 4) for key in ("task_t3", "task_desktop")
    }
    clients["comparisons"]["first_request_note"] = (
        "Every first request read 63% to 68% of its prefix from a warm shared cache, so these are first-request "
        "sizes, not true cold starts."
    )
    cli_side = sessions["task_cli"]
    clients["notes"] = [
        "CLI task_cli ran in the interactive TUI. Its cost_state exceeds its deduped assistant rows by "
        f"{_pct(cli_side['cost_usd'], cli_side['assistant_usd']):.1f}%: the TUI fires background calls "
        "(Haiku title plus an Opus side request) that write no assistant row.",
        "T3 Code cost_state equals its assistant rows. Its separate title-generation sessions write no transcript.",
        "Claude Desktop writes no cost_state row. Its usage is computed from deduped assistant rows and is a floor.",
    ]

    compaction = data["compaction"]
    arms = compaction["arms"]
    default_tokens = arms["default_1m"]["tokens"]
    all_hidden = 0.0
    all_compactions = 0
    for record in arms.values():
        _session_units(record)
        for phase in record["phases"]:
            if "hidden_usd_per_compaction" in phase:
                phase["hidden_phase_mean_usd"] = phase.pop("hidden_usd_per_compaction")
            phase["cost_state_units"] = _units(phase["cost_state_usd"])
            phase["assistant_units"] = _units(phase["assistant_usd"])
            phase["hidden_units"] = _units(phase["hidden_usd"])
            mean = phase.get("hidden_phase_mean_usd")
            phase["hidden_phase_mean_units"] = _units(mean) if mean is not None else None
        record.pop("per_compaction_hidden_usd_range", None)
        means = [p["hidden_phase_mean_usd"] for p in record["phases"] if p["compactions"]]
        record["hidden_phase_mean_usd_range"] = [min(means), max(means)] if means else None
        record["hidden_phase_mean_units_range"] = [_units(min(means)), _units(max(means))] if means else None
        record["hidden_units_note"] = (
            "Hidden usage is the cost_state residual over assistant rows. It has no token breakdown, so it is an "
            "input-token equivalent, not a measured token count."
        )
        all_hidden += record["hidden_usd"]
        all_compactions += record["compactions"]
        boundaries = record["compact_boundaries"]
        if boundaries:
            first = boundaries[0]["preTokens"]
            overshoot = (first - record["predicted_threshold"]) / record["predicted_threshold"]
            record["first_fire_vs_threshold"] = _r(overshoot, 4)
            record["validity_gate_passed"] = abs(overshoot) <= GATE
        else:
            record["first_fire_vs_threshold"] = None
            record["validity_gate_passed"] = None
        record["cost_vs_default_by_read_weight"] = {
            str(r): _r(
                (weighted_units(record["tokens"], r) + (record["hidden_units"] or 0))
                / weighted_units(default_tokens, r)
                - 1,
                4,
            )
            for r in READ_WEIGHTS
        }
        record["raw_tokens_vs_default"] = _r(record["raw_tokens"] / _raw(default_tokens) - 1, 4)
        checker = record["checker"]
        checker.setdefault(
            "semantic_violations_basis",
            "0 means no module calls datetime.now() directly, zero functional failures, and the only checker hits "
            "are INV-02 matches on the sanctioned clock.now() delegation",
        )
        record.setdefault(
            "check_anomalies_basis",
            "an anomaly counts as reported when its 4-digit id appears in assistant text, so this is an upper bound",
        )
    compaction["hidden_mean_units_per_compaction"] = round(_units(all_hidden) / all_compactions)
    compaction["total_compactions"] = all_compactions
    compaction["validity_gate"] = (
        "first preTokens within 15% of the predicted threshold (benchmarks/protocol.md). "
        "window_180k (+15.7%) and compound_25pct_250k (+55.5%) fail it."
    )

    short = data["clear_vs_warm"]
    for record in list(short["isolated"].values()) + [short["shared"]]:
        _session_units(record)
    for turn in short["shared"]["turns"]:
        turn["cost_units"] = _units(turn["cost_usd"])
    totals = short["totals"]
    totals["isolated_units"] = _units(totals["isolated_usd"])
    totals["shared_units"] = _units(totals["shared_usd"])
    totals["isolated_raw_tokens"] = _raw(totals["isolated_tokens"])
    totals["shared_raw_tokens"] = _raw(totals["shared_tokens"])
    totals["shared_raw_tokens_change_pct"] = _pct(totals["shared_raw_tokens"], totals["isolated_raw_tokens"])
    totals["shared_change_pct_by_read_weight"] = {
        str(r): _pct(weighted_units(totals["shared_tokens"], r), weighted_units(totals["isolated_tokens"], r))
        for r in READ_WEIGHTS
    }
    totals["saved_write_tokens_per_avoided_clear"] = (
        totals["isolated_tokens"]["cache_write_5m"] - totals["shared_tokens"]["cache_write_5m"]
    ) / 2
    return data


# ----------------------------------------------------------------------------- figures

# Two hues only, plus neutral ink and greys. Series differ by alpha or hatch.
GREEN = "#1a7f37"
RED = "#cf222e"
INK = "#1f2328"
MUTED = "#59636e"
GRID = "#eaeef2"
AXIS = "#d0d7de"
FOOTER = "Weights 1 / 1.25 / 0.05 / 5 (Opus 5.5). Range across models in README."


def _style(plt) -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 11.5,
            "axes.titlesize": 14,
            "axes.labelsize": 11.5,
            "xtick.labelsize": 11,
            "ytick.labelsize": 11,
            "figure.facecolor": "white",
            "savefig.facecolor": "white",
            "axes.facecolor": "white",
            "text.color": INK,
            "axes.labelcolor": INK,
            "xtick.color": INK,
            "ytick.color": INK,
            "axes.titlecolor": INK,
            "text.parse_math": False,
        }
    )


def _clean(ax, grid_axis: str = "x") -> None:
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color(AXIS)
    ax.spines["bottom"].set_color(AXIS)
    ax.tick_params(length=0, pad=6)
    ax.grid(axis=grid_axis, color=GRID, linewidth=1)
    ax.set_axisbelow(True)


def _save(fig, path: Path, plt) -> None:
    fig.savefig(path, dpi=170, bbox_inches="tight", pad_inches=0.4, facecolor="white")
    plt.close(fig)


def _k(value: float) -> str:
    return f"{value / 1000:.0f}k"


def _change_text(change_pct: float) -> str:
    """+14%, +18%, but one decimal where whole-number rounding would be ambiguous (+25.2%)."""
    text = f"{change_pct:+.0f}%"
    if abs(change_pct - round(change_pct)) > 0.05 and round(abs(change_pct)) == 25:
        text = f"{change_pct:+.1f}%"
    return text


def figure_clients(data: dict, path: Path, plt) -> None:
    sessions = data["client_comparison"]["sessions"]
    fig, (left, right) = plt.subplots(1, 2, figsize=(16, 6.4), gridspec_kw={"wspace": 0.55})

    first = ["cold_cli_p", "cold_cli_tui", "cold_t3", "cold_desktop"]
    names = [
        f"Claude CLI v{CLI_VERSION}\nclaude -p",
        f"Claude CLI v{CLI_VERSION}\ninteractive",
        f"T3 Code v{T3_VERSION}",
        f"Claude Desktop v{DESKTOP_VERSION}\nCode tab",
    ]
    styles = [
        {"color": GREEN, "alpha": 1.0, "hatch": None},
        {"color": GREEN, "alpha": 1.0, "hatch": "////"},
        {"color": RED, "alpha": 0.6, "hatch": None},
        {"color": RED, "alpha": 1.0, "hatch": None},
    ]
    values = [sessions[key]["first_call_prefix_tokens"] for key in first]
    tui = sessions["cold_cli_tui"]["first_call_prefix_tokens"]
    for index, (value, style) in enumerate(zip(values, styles)):
        if style["hatch"]:
            left.barh(index, value, color="white", edgecolor=style["color"], hatch=style["hatch"], linewidth=1.4,
                      height=0.6, zorder=3)
        else:
            left.barh(index, value, color=style["color"], alpha=style["alpha"], height=0.6, zorder=3)
    left.invert_yaxis()
    left.set_yticks(range(len(first)))
    left.set_yticklabels(names)
    _clean(left)
    for index, key in enumerate(first):
        value = values[index]
        startup = sessions[key]["startup"]
        ratio = value / tui
        if key == "cold_cli_tui":
            tag = "baseline"
        elif ratio < 1.5:
            tag = f"{(ratio - 1) * 100:+.0f}%"
        else:
            tag = f"{ratio:.2f}x"
        left.text(value + 700, index - 0.13, f"{value:,} tokens ({tag})", va="center", ha="left",
                  fontsize=11.5, color=styles[index]["color"], fontweight="bold")
        left.text(value + 700, index + 0.2,
                  f"{startup['inline_tools']} inline / {startup['deferred_tools']} deferred tools",
                  va="center", ha="left", fontsize=10, color=MUTED)
    left.set_xlim(0, max(values) * 1.55)
    left.xaxis.set_major_formatter(lambda v, _p: _k(v))
    left.set_xlabel("Tokens in the first request (input + cache write + cache read)", labelpad=8)
    left.set_title("First request size", loc="left", pad=12)

    task = ["task_cli", "task_t3", "task_desktop"]
    labels = [f"Claude CLI v{CLI_VERSION}", f"T3 Code v{T3_VERSION}", f"Claude Desktop v{DESKTOP_VERSION}\nCode tab"]
    task_styles = [styles[0], styles[2], styles[3]]
    main_units = sessions["task_cli"]["assistant_usd"]
    usage = [sessions["task_cli"]["assistant_usd"], sessions["task_t3"]["cost_usd"], sessions["task_desktop"]["cost_usd"]]
    relative = [value / main_units for value in usage]
    raw_main = _raw(sessions["task_cli"]["tokens"])
    for index, (value, style) in enumerate(zip(relative, task_styles)):
        right.barh(index, value, color=style["color"], alpha=style["alpha"], height=0.6, zorder=3)
    right.invert_yaxis()
    right.set_yticks(range(3))
    right.set_yticklabels(labels)
    _clean(right)
    for index, key in enumerate(task):
        record = sessions[key]
        head = "1.00x (baseline)" if index == 0 else f"{relative[index]:.2f}x ({(relative[index] - 1) * 100:+.0f}%)"
        raw_ratio = _raw(record["tokens"]) / raw_main
        calls = record["assistant_calls"]
        detail = (
            f"{calls} calls, tests pass" if index == 0
            else f"{raw_ratio:.2f}x raw tokens, {calls} calls vs {sessions['task_cli']['assistant_calls']}"
        )
        right.text(relative[index] + 0.03, index - 0.13, head, va="center", ha="left", fontsize=11.5,
                   color=task_styles[index]["color"], fontweight="bold")
        right.text(relative[index] + 0.03, index + 0.2, detail, va="center", ha="left", fontsize=10, color=MUTED)
    right.axvline(1.0, color=INK, linewidth=1.0, linestyle=(0, (3, 3)), zorder=4)
    right.set_xlim(0, max(relative) * 1.32)
    right.xaxis.set_major_formatter(lambda v, _p: f"{v:.1f}x")
    right.set_xlabel("Usage of the same 3-bug fix, x CLI (cache-weighted)", labelpad=8)
    right.set_title("Same bugfix, same model, three clients", loc="left", pad=12)
    side = (sessions["task_cli"]["cost_usd"] / main_units - 1) * 100
    fig.suptitle(
        "Claude Opus 5.5, effort medium, identical CLAUDE.md, no user MCP servers. Measured 13 November 2026, n=1 each.",
        x=0.06, y=1.0, ha="left", fontsize=11, color=MUTED,
    )
    fig.text(
        0.06, -0.04,
        f"CLI baseline is its main assistant rows; its TUI also fired side calls worth +{side:.0f}% (Haiku title, "
        "Opus side request).\nDesktop writes no cost_state, so its figure is a floor. "
        "First requests read 63-68% of the prefix warm. " + FOOTER,
        ha="left", va="top", fontsize=9.5, color=MUTED, linespacing=1.4,
    )
    _save(fig, path, plt)


def figure_compaction(data: dict, path: Path, plt) -> None:
    arms = data["compaction"]["arms"]
    order = ["default_1m", "window_180k", "compound_25pct_250k", "window_140k", "window_100k"]
    labels = {
        "default_1m": "Default 1M",
        "window_180k": "--autocompact 180k",
        "compound_25pct_250k": "250k window\n+ override 25",
        "window_140k": "--autocompact 140k",
        "window_100k": "--autocompact 100k",
    }
    fig, (left, right) = plt.subplots(1, 2, figsize=(16.5, 7.0), gridspec_kw={"wspace": 0.28, "width_ratios": [1.1, 1]})

    base = arms["default_1m"]["cost_state_usd"]
    visible = [arms[k]["assistant_usd"] / base * 100 for k in order]
    hidden = [arms[k]["hidden_usd"] / base * 100 for k in order]
    totals = [arms[k]["cost_state_usd"] / base * 100 for k in order]
    rows = list(range(len(order)))
    left.barh(rows, visible, color=GREEN, height=0.6, zorder=3, label="visible in the transcript")
    left.barh(rows, hidden, left=visible, color=RED, height=0.6, zorder=3, label="hidden summary calls")
    left.invert_yaxis()
    left.set_yticks(rows)
    left.set_yticklabels([labels[k] for k in order])
    _clean(left)
    left.axvline(100, color=INK, linestyle=(0, (3, 3)), linewidth=1.0, zorder=4)
    left.text(100, -0.62, "default = 100", ha="center", va="bottom", fontsize=10, color=INK)
    for index, key in enumerate(order):
        record = arms[key]
        change = record["cost_vs_default"] * 100
        if key == "default_1m":
            head, color = "100 (baseline)", GREEN
        elif abs(change) < 1:
            head, color = "100 (tie)", GREEN
        else:
            head, color = _change_text(change), RED
        left.text(totals[index] + 1.5, index - 0.13, head, va="center", ha="left", fontsize=11.5, color=color,
                  fontweight="bold")
        if record["compactions"]:
            detail = f"{record['compactions']} compacts, {record['hidden_share_of_total'] * 100:.1f}% hidden summary usage"
            if record.get("validity_gate_passed") is False:
                detail += "\nfirst fire failed the 15% preTokens gate"
        else:
            detail = f"0 compacts, peak {_k(record['peak_context_tokens'])} context"
        left.text(totals[index] + 1.5, index + 0.24, detail, va="center", ha="left", fontsize=9.8, color=MUTED,
                  linespacing=1.1)
    left.set_ylim(len(order) - 0.45, -0.85)
    left.set_xlim(0, 196)
    left.set_xticks([0, 25, 50, 75, 100, 125])
    left.set_xlabel("Usage of one 4-phase build, default = 100 (cache-weighted)", labelpad=8)
    left.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.4, -0.12), ncol=2, fontsize=10.5)
    left.set_title("A lower trigger adds usage you never see in the transcript", loc="left", pad=18)

    fire = ["window_180k", "window_140k", "window_100k", "compound_25pct_250k"]
    fire_labels = ["180k window", "140k window", "100k window", "250k window\n+ override 25"]
    step = 1.7
    for index, key in enumerate(fire):
        x = index * step
        record = arms[key]
        stacked = record["pct_override"] is not None
        nominal = record["window"] if not stacked else int(record["window"] * record["pct_override"] / 100)
        threshold = record["predicted_threshold"]
        right.plot([x - 0.36, x + 0.36], [nominal, nominal], color=RED, alpha=0.5, linewidth=2.2,
                   linestyle=(0, (4, 3)), zorder=2)
        right.plot([x - 0.36, x + 0.36], [threshold, threshold], color=GREEN, linewidth=3.2, zorder=3)
        observed = [b["preTokens"] for b in record["compact_boundaries"]]
        spread = [x - 0.24 + 0.48 * i / max(1, len(observed) - 1) for i in range(len(observed))]
        right.scatter(spread, observed, color=RED, s=46, zorder=4, edgecolor="white", linewidth=0.8)
        if record.get("validity_gate_passed") is False:
            right.scatter([spread[0]], [observed[0]], facecolor="none", edgecolor=INK, s=150, linewidth=1.2, zorder=5)
        nominal_text = f"typed {_k(nominal)}" if not stacked else f"naive 0.25 x 250k\n= {nominal / 1000:.1f}k"
        right.text(x + 0.45, nominal + (7000 if stacked else 0), nominal_text, va="center", ha="left",
                   fontsize=9.5, color=RED)
        right.text(x + 0.45, threshold - (9000 if stacked else 0), f"trips at\n{threshold / 1000:.1f}k",
                   va="center", ha="left", fontsize=9.5, color=GREEN, fontweight="bold")
        if stacked:
            worst = max(range(len(observed)), key=lambda i: observed[i])
            over = observed[worst] - threshold
            right.annotate(f"+{over / 1000:.0f}k past the line\nafter a big tool batch",
                           xy=(spread[worst], observed[worst]),
                           xytext=(14, 34), textcoords="offset points", fontsize=9.5, color=RED, ha="left",
                           arrowprops={"arrowstyle": "-", "color": RED, "linewidth": 0.9})
    right.set_xticks([i * step for i in range(len(fire))])
    right.set_xticklabels(fire_labels)
    right.set_xlim(-0.6, (len(fire) - 1) * step + 1.45)
    right.set_ylim(40_000, 195_000)
    right.yaxis.set_major_formatter(lambda v, _p: _k(v))
    right.set_ylabel("Context tokens at compaction (preTokens)", labelpad=8)
    _clean(right, grid_axis="y")
    right.scatter([], [], color=RED, s=40, label="observed fire")
    right.scatter([], [], facecolor="none", edgecolor=INK, s=110, label="first fire >15% past the line")
    right.plot([], [], color=GREEN, linewidth=3, label="where the check trips (tle + WX)")
    right.plot([], [], color=RED, alpha=0.5, linewidth=2.2, linestyle=(0, (4, 3)), label="the number you typed")
    right.legend(frameon=False, loc="upper right", fontsize=10)
    right.set_title("The check trips 33,000 below what you typed.\nBig tool batches overshoot it.", loc="left", pad=12)
    fig.suptitle(
        "5 live Opus 5.5 sessions, effort medium, 5-minute cache, no pauses, n=1 each, 13 November 2026. "
        "Every arm found 12/12 planted anomalies and passed its tests.",
        x=0.06, y=1.02, ha="left", fontsize=11, color=MUTED,
    )
    fig.text(0.06, -0.05, "Hidden usage is the cost_state residual over the transcript rows. " + FOOTER,
             ha="left", va="top", fontsize=9.5, color=MUTED)
    _save(fig, path, plt)


def figure_clear_vs_warm(data: dict, path: Path, plt) -> None:
    """Three /clear sessions vs one warm session.

    If ``data["ttl_runs"]["stale_bar"]`` exists (``{"label": ..., "pct_of_clear": ...}``),
    a third hatched red bar is drawn for the stale resume measured live.
    """
    short = data["clear_vs_warm"]
    totals = short["totals"]
    fig, (left, right) = plt.subplots(1, 2, figsize=(15, 6.2), gridspec_kw={"wspace": 0.3})

    iso_total = totals["isolated_usd"]
    iso_parts = [short["isolated"][task]["cost_state_usd"] / iso_total * 100 for task in SHORT_TASKS]
    warm_parts = [turn["cost_usd"] / iso_total * 100 for turn in short["shared"]["turns"]]
    bars = [("3 sessions,\n/clear between", iso_parts, RED), ("1 warm session,\n--resume", warm_parts, GREEN)]
    stale = (data.get("ttl_runs") or {}).get("stale_bar")
    scale_top = max([100.0] + ([stale["pct_of_clear"]] if stale else []))
    for index, (_label, parts, color) in enumerate(bars):
        start = 0.0
        for task, part in zip(SHORT_TASKS, parts):
            left.bar(index, part, bottom=start, color=color, width=0.55, edgecolor="white", linewidth=2, zorder=3)
            units = part / 100 * _units(iso_total)
            if part / scale_top < 0.12:
                start += part
                continue
            left.text(index, start + part / 2, f"{task}\n{units / 1000:.1f}k units", ha="center", va="center",
                      fontsize=9.5, color="white")
            start += part
    warm_pct = totals["shared_usd"] / iso_total * 100
    left.text(0, 101.5, "100%", ha="center", va="bottom", fontsize=13, color=RED, fontweight="bold")
    left.text(1, warm_pct + 1.5, f"{warm_pct:.1f}% ({warm_pct - 100:+.1f}%)", ha="center", va="bottom",
              fontsize=13, color=GREEN, fontweight="bold")
    ticks = [label for label, _p, _c in bars]
    if stale:
        pct = stale["pct_of_clear"]
        left.bar(2, pct, color="white", edgecolor=RED, hatch="////", linewidth=1.4, width=0.55, zorder=3)
        left.text(2, pct + 1.5, f"{pct:.0f}% ({pct - 100:+.0f}%)", ha="center", va="bottom", fontsize=13,
                  color=RED, fontweight="bold")
        ticks.append(stale.get("label", "resumed after 6.5m"))
    left.set_xticks(range(len(ticks)))
    left.set_xticklabels(ticks)
    left.set_xlim(-0.6, len(ticks) - 0.4)
    top = max([100.0] + ([stale["pct_of_clear"]] if stale else []))
    left.set_ylim(0, top * 1.16)
    left.yaxis.set_major_formatter(lambda v, _p: f"{v:.0f}%")
    left.set_ylabel("Usage, % of the /clear arm (cache-weighted)", labelpad=8)
    _clean(left, grid_axis="y")
    left.set_title("Warm session wins; a stale resume loses big" if stale else "Back-to-back fixes: the warm session wins",
                   loc="left", pad=12)

    keys = [("cache_write_5m", "writes\n1.25x"), ("cache_read", "reads\n0.05x"), ("output", "output\n5x")]
    iso = totals["isolated_tokens"]
    warm = totals["shared_tokens"]
    width = 0.36
    for index, (key, _label) in enumerate(keys):
        right.bar(index - width / 2, iso[key], width=width, color=RED, zorder=3)
        right.bar(index + width / 2, warm[key], width=width, color=GREEN, zorder=3)
        right.text(index - width / 2, iso[key] * 1.12, f"{iso[key]:,}", ha="center", va="bottom", fontsize=10,
                   color=RED)
        right.text(index + width / 2, warm[key] * 1.12, f"{warm[key]:,}", ha="center", va="bottom", fontsize=10,
                   color=GREEN)
    right.set_yscale("log")
    right.set_ylim(300, max(iso["cache_read"], warm["cache_read"]) * 4)
    right.set_xticks(range(len(keys)))
    right.set_xticklabels(
        [f"{label}\nwarm {(warm[key] / iso[key] - 1) * 100:+.0f}%" for key, label in keys]
    )
    right.yaxis.set_major_formatter(lambda v, _p: _k(v) if v >= 1000 else f"{v:.0f}")
    right.set_ylabel("Raw tokens (log scale)", labelpad=8)
    _clean(right, grid_axis="y")
    from matplotlib.patches import Patch

    right.legend(handles=[Patch(color=RED, label="/clear between tasks"), Patch(color=GREEN, label="warm session")],
                 frameon=False, loc="upper left", ncol=2)
    right.set_title("More tokens read, far fewer rebuilt", loc="left", pad=12)
    by_weight = totals["shared_change_pct_by_read_weight"]
    fig.suptitle(
        "median_bug, slice_bug, keep_currency on Claude Opus 5.5, effort medium, 5-minute cache, n=1. "
        "All tests pass in both arms.",
        x=0.06, y=1.0, ha="left", fontsize=11, color=MUTED,
    )
    fig.text(
        0.06, -0.03,
        f"The warm arm used {totals['shared_raw_tokens_change_pct']:+.1f}% raw tokens and "
        f"{by_weight['0.05']:+.1f}% usage ({by_weight['0.1']:+.1f}% at 0.1x reads, {by_weight['0.025']:+.1f}% at 0.025x). "
        f"Context peaked at {short['shared']['peak_context_tokens']:,} tokens. " + FOOTER,
        ha="left", va="top", fontsize=9.5, color=MUTED,
    )
    _save(fig, path, plt)


def write_figures(data: dict, out: Path) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    data = normalize(json.loads(json.dumps(data)))
    _style(plt)
    figures = out / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    paths = [figures / name for name in FIGURE_NAMES]
    figure_clients(data, paths[0], plt)
    figure_compaction(data, paths[1], plt)
    figure_clear_vs_warm(data, paths[2], plt)
    return paths


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live Opus 5.5 experiments: dataset and charts.")
    sub = parser.add_subparsers(dest="command")
    fig = sub.add_parser("figures", help="normalize the committed JSON and draw the three live charts")
    fig.add_argument("--out", type=Path, default=Path("outputs"))
    ext = sub.add_parser("extract", help="rebuild the JSON from raw transcripts (needs the original files)")
    ext.add_argument("--projects", type=Path, default=Path.home() / ".claude" / "projects")
    ext.add_argument("--raw-root", type=Path, required=True, help="directory holding the exp_p* workspaces")
    ext.add_argument("--checker", type=Path, required=True, help="LedgerKit invariant checker script")
    ext.add_argument("--pytest", default="pytest")
    ext.add_argument("--out", type=Path, default=Path("outputs"))
    args = parser.parse_args(argv)
    out = getattr(args, "out", Path("outputs"))
    if args.command == "extract":
        repo_root = Path(__file__).resolve().parents[2]
        data = extract(args.projects, args.raw_root, repo_root, args.checker, args.pytest)
        out.mkdir(parents=True, exist_ok=True)
        previous = out / DATASET_NAME
        if previous.is_file() and "ttl_runs" not in data:
            # The cache-expiry runs come from a separate runner; keep them across a rebuild.
            kept = load(previous).get("ttl_runs")
            if kept:
                data["ttl_runs"] = kept
        (out / DATASET_NAME).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {out / DATASET_NAME}")
    data = normalize(load(out / DATASET_NAME))
    (out / DATASET_NAME).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    for path in write_figures(data, out):
        print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
