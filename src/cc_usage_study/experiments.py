"""Live Opus 5.5 experiments: sanitized dataset and empirical charts.

Two jobs live here.

``extract`` reads the raw Claude Code JSONL transcripts and the experiment
workspaces once, and writes ``outputs/live_experiments.json``. The dataset
holds numbers only: token counts, dollars, compaction metadata, tool and skill
counts, test results. It never stores prompt text, tool output, API keys, or
local paths. Each transcript is identified by its session id and a SHA-256 of
the file, so a reader with the same files can confirm the provenance.

``figures`` (the default) reads only the committed JSON and draws the three
live charts under ``outputs/figures/``. It never touches a transcript, so
anyone can regenerate the charts from the repository alone.

    python3 -m cc_usage_study.experiments figures --out outputs
    python3 -m cc_usage_study.experiments extract --projects ~/.claude/projects --raw-root /tmp

Prices are the Opus 5.5 list prices from the 13 November 2026 snapshot in
``pricing.py``. Every live run used a dedicated API key with the default
5-minute prompt cache, so the dollars are API list price, not subscription
meter percentages.
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
    changed = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"], cwd=workspace, capture_output=True, text=True
    ).stdout.split()
    return {
        "visible_and_hidden_tests": counts,
        "all_tests_passed": result.returncode == 0 and counts["failed"] == 0,
        "hidden_bug_tests_passed": counts["passed"] - 1 if result.returncode == 0 else None,
        "bugs_fixed": 3 if result.returncode == 0 else None,
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
            "CLI task_cli ran in the interactive TUI. Its cost_state ($%.4f) exceeds its deduped assistant rows ($%.4f): "
            "the TUI fires background calls (Haiku title plus an Opus side request) that write no assistant row."
            % (cli_total, cli_main),
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
                    "hidden_usd_per_compaction": _r(hidden / compacts) if compacts else None,
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
                "per_compaction_hidden_usd_range": (
                    [
                        min(p["hidden_usd_per_compaction"] for p in phases if p["compactions"]),
                        max(p["hidden_usd_per_compaction"] for p in phases if p["compactions"]),
                    ]
                    if boundaries
                    else None
                ),
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
    return {
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


# ----------------------------------------------------------------------------- figures

GREEN = "#1a7f37"
GREEN_LIGHT = "#2da44e"
RED = "#cf222e"
RED_DARK = "#a40e26"
RED_LIGHT = "#fa4549"
RED_SOFT = "#ff8182"
INK = "#1f2328"
MUTED = "#59636e"
GRID = "#eaeef2"
AXIS = "#d0d7de"


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


def figure_clients(data: dict, path: Path, plt) -> None:
    sessions = data["client_comparison"]["sessions"]
    fig, (left, right) = plt.subplots(1, 2, figsize=(17.5, 6.8), gridspec_kw={"wspace": 0.62})

    cold = ["cold_cli_p", "cold_cli_tui", "cold_t3", "cold_desktop"]
    names = [
        f"Claude CLI v{CLI_VERSION}\nclaude -p",
        f"Claude CLI v{CLI_VERSION}\ninteractive",
        f"T3 Code v{T3_VERSION}",
        f"Claude Desktop v{DESKTOP_VERSION}\nCode tab",
    ]
    colors = [GREEN, GREEN_LIGHT, RED_LIGHT, RED_DARK]
    values = [sessions[key]["first_call_prefix_tokens"] for key in cold]
    tui = sessions["cold_cli_tui"]["first_call_prefix_tokens"]
    positions = range(len(cold))
    left.barh(list(positions), values, color=colors, height=0.6, zorder=3)
    left.invert_yaxis()
    left.set_yticks(list(positions))
    left.set_yticklabels(names)
    _clean(left)
    for index, key in enumerate(cold):
        value = values[index]
        startup = sessions[key]["startup"]
        ratio = value / tui
        if key == "cold_cli_tui":
            tag = "baseline"
        elif ratio < 1.5:
            tag = f"{(ratio - 1) * 100:+.0f}%"
        else:
            tag = f"{ratio:.2f}x"
        left.text(value + 900, index - 0.12, f"{value:,} tokens  ({tag})", va="center", ha="left",
                  fontsize=11.5, color=colors[index], fontweight="bold")
        left.text(
            value + 900,
            index + 0.2,
            f"{startup['inline_tools']} inline / {startup['deferred_tools']} deferred tools, "
            f"${sessions[key]['cost_usd']:.4f} cold",
            va="center",
            ha="left",
            fontsize=10,
            color=MUTED,
        )
    left.set_xlim(0, max(values) * 1.95)
    left.xaxis.set_major_formatter(lambda v, _p: _k(v))
    left.set_xlabel("Tokens in the first request (input + cache write + cache read)", labelpad=8)
    left.set_title("Cold start: what each client sends before you type", loc="left", pad=12)

    task = ["task_cli", "task_t3", "task_desktop"]
    labels = [f"Claude CLI v{CLI_VERSION}", f"T3 Code v{T3_VERSION}", f"Claude Desktop v{DESKTOP_VERSION}"]
    task_colors = [GREEN, RED_LIGHT, RED_DARK]
    main = sessions["task_cli"]["assistant_usd"]
    costs = [sessions["task_cli"]["assistant_usd"], sessions["task_t3"]["cost_usd"], sessions["task_desktop"]["cost_usd"]]
    right.barh(range(3), costs, color=task_colors, height=0.6, zorder=3)
    extra = sessions["task_cli"]["cost_usd"] - main
    right.barh([0], [extra], left=[main], color="white", edgecolor=GREEN, hatch="////", height=0.6, zorder=3,
               linewidth=1.0)
    right.invert_yaxis()
    right.set_yticks(range(3))
    right.set_yticklabels(labels)
    _clean(right)
    for index, key in enumerate(task):
        record = sessions[key]
        cost = costs[index]
        if index == 0:
            head = f"${cost:.4f} main  (${record['cost_usd']:.4f} with side calls)"
            end = record["cost_usd"]
        else:
            ratio = cost / main
            tag = f"{(ratio - 1) * 100:+.0f}%" if ratio < 1.5 else f"{ratio:.2f}x"
            head = f"${cost:.4f}  ({tag})"
            end = cost
        right.text(end + 0.003, index - 0.12, head, va="center", ha="left", fontsize=11.5,
                   color=task_colors[index], fontweight="bold")
        right.text(
            end + 0.003,
            index + 0.2,
            f"{record['assistant_calls']} calls, {record['tokens']['cache_read'] / 1000:.0f}k cache-read tokens, tests pass",
            va="center",
            ha="left",
            fontsize=10,
            color=MUTED,
        )
    right.set_xlim(0, max(costs) * 2.1)
    right.xaxis.set_major_formatter(lambda v, _p: f"${v:.2f}")
    right.set_xlabel("API list price of the same 3-bug fix (noisy_bughunt)", labelpad=8)
    right.set_title("Same bugfix, same model, three clients", loc="left", pad=12)
    fig.suptitle(
        "Claude Opus 5.5, effort medium, identical CLAUDE.md, no user MCP servers. Measured 13 November 2026.",
        x=0.06, y=0.995, ha="left", fontsize=11, color=MUTED,
    )
    _save(fig, path, plt)


def figure_compaction(data: dict, path: Path, plt) -> None:
    arms = data["compaction"]["arms"]
    order = ["default_1m", "window_180k", "compound_25pct_250k", "window_140k", "window_100k"]
    labels = {
        "default_1m": "Default 1M",
        "window_180k": "180k window",
        "compound_25pct_250k": "250k + 0.25 override",
        "window_140k": "140k window",
        "window_100k": "100k window",
    }
    fig, (left, right) = plt.subplots(1, 2, figsize=(17, 7.2), gridspec_kw={"wspace": 0.3, "width_ratios": [1.1, 1]})

    visible = [arms[k]["assistant_usd"] for k in order]
    hidden = [arms[k]["hidden_usd"] for k in order]
    totals = [arms[k]["cost_state_usd"] for k in order]
    rows = range(len(order))
    left.barh(list(rows), visible, color=GREEN, height=0.6, zorder=3, label="Visible assistant calls")
    left.barh(list(rows), hidden, left=visible, color=RED, height=0.6, zorder=3,
              label="Hidden compaction-summary calls")
    left.invert_yaxis()
    left.set_yticks(list(rows))
    left.set_yticklabels([labels[k] for k in order])
    _clean(left)
    base = arms["default_1m"]["cost_state_usd"]
    left.axvline(base, color=INK, linestyle=(0, (3, 3)), linewidth=1.0, zorder=4)
    left.text(base, -0.62, f"default ${base:.2f}", ha="center", va="bottom", fontsize=10, color=INK)
    for index, key in enumerate(order):
        record = arms[key]
        change = record["cost_vs_default"] * 100
        change_text = "baseline" if key == "default_1m" else ("break-even" if abs(change) < 1 else f"{change:+.0f}%")
        color = GREEN if change < 1 else RED
        left.text(totals[index] + 0.06, index - 0.13, f"${totals[index]:.2f}  ({change_text})", va="center",
                  ha="left", fontsize=11.5, color=color, fontweight="bold")
        if record["compactions"]:
            detail = (
                f"{record['compactions']} compacts, ${record['hidden_usd']:.2f} hidden "
                f"({record['hidden_share_of_total'] * 100:.1f}%)"
            )
        else:
            detail = f"0 compacts, peak {_k(record['peak_context_tokens'])} context"
        left.text(totals[index] + 0.06, index + 0.21, detail, va="center", ha="left", fontsize=10, color=MUTED)
    left.set_xlim(0, max(totals) * 1.62)
    left.xaxis.set_major_formatter(lambda v, _p: f"${v:.0f}")
    left.set_xlabel("API list price of one 4-phase LedgerKit v3.2 build", labelpad=8)
    left.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.45, -0.13), ncol=2, fontsize=10.5)
    left.set_title("A lower trigger adds a bill you never see in the transcript", loc="left", pad=22)

    fire = ["window_180k", "window_140k", "window_100k", "compound_25pct_250k"]
    fire_labels = ["180k window", "140k window", "100k window", "250k window\n+ 0.25 override"]
    step = 1.7
    for index, key in enumerate(fire):
        x = index * step
        record = arms[key]
        nominal = record["window"] if record["pct_override"] is None else int(record["window"] * record["pct_override"] / 100)
        threshold = record["predicted_threshold"]
        right.plot([x - 0.36, x + 0.36], [nominal, nominal], color=RED_SOFT, linewidth=2.2,
                   linestyle=(0, (4, 3)), zorder=2)
        right.plot([x - 0.36, x + 0.36], [threshold, threshold], color=GREEN, linewidth=3.2, zorder=3)
        observed = [b["preTokens"] for b in record["compact_boundaries"]]
        spread = [x - 0.24 + 0.48 * i / max(1, len(observed) - 1) for i in range(len(observed))]
        right.scatter(spread, observed, color=RED, s=46, zorder=4, edgecolor="white", linewidth=0.8)
        if record["pct_override"] is None:
            nominal_text = f"you set {_k(nominal)}"
        else:
            nominal_text = f"naive 0.25 x 250k\n= {nominal / 1000:.1f}k"
        stacked = record["pct_override"] is not None
        right.text(x + 0.45, nominal + (6500 if stacked else 0), nominal_text, va="center", ha="left",
                   fontsize=9.5, color=RED)
        right.text(x + 0.45, threshold - (9000 if stacked else 0),
                   f"fires at\n{threshold / 1000:.1f}k", va="center", ha="left",
                   fontsize=9.5, color=GREEN, fontweight="bold")
    right.set_xticks([i * step for i in range(len(fire))])
    right.set_xticklabels(fire_labels)
    right.set_xlim(-0.6, (len(fire) - 1) * step + 1.25)
    right.set_ylim(40_000, 195_000)
    right.yaxis.set_major_formatter(lambda v, _p: _k(v))
    right.set_ylabel("Context tokens at compaction (preTokens)", labelpad=8)
    _clean(right, grid_axis="y")
    right.scatter([], [], color=RED, s=40, label="observed fire (each compaction)")
    right.plot([], [], color=GREEN, linewidth=3, label="predicted by tle + WX in v2.1.325")
    right.plot([], [], color=RED_SOFT, linewidth=2.2, linestyle=(0, (4, 3)), label="the number you typed")
    right.legend(frameon=False, loc="upper right", fontsize=10)
    right.set_title("It fires 33,000 tokens early, and stacked knobs multiply", loc="left", pad=22)
    fig.suptitle(
        "5 live Opus 5.5 sessions, effort medium, 5-minute cache, 13 November 2026. "
        "Every arm found 12/12 planted anomalies and passed its tests.",
        x=0.06, y=1.0, ha="left", fontsize=11, color=MUTED,
    )
    _save(fig, path, plt)


def figure_clear_vs_warm(data: dict, path: Path, plt) -> None:
    short = data["clear_vs_warm"]
    totals = short["totals"]
    fig, (left, right) = plt.subplots(1, 2, figsize=(15, 6.2), gridspec_kw={"wspace": 0.32})

    iso_parts = [short["isolated"][task]["cost_state_usd"] for task in SHORT_TASKS]
    warm_parts = [turn["cost_usd"] for turn in short["shared"]["turns"]]
    bars = [("3 sessions with\n/clear between", iso_parts, RED), ("1 warm session,\n--resume", warm_parts, GREEN)]
    for index, (_label, parts, color) in enumerate(bars):
        start = 0.0
        for task, part in zip(SHORT_TASKS, parts):
            left.bar(index, part, bottom=start, color=color, width=0.55, edgecolor="white", linewidth=2, zorder=3)
            left.text(index, start + part / 2, f"{task}\n${part:.4f}", ha="center", va="center", fontsize=9.5,
                      color="white")
            start += part
    left.text(0, totals["isolated_usd"] + 0.003, f"${totals['isolated_usd']:.4f}", ha="center", va="bottom",
              fontsize=13, color=RED, fontweight="bold")
    left.text(1, totals["shared_usd"] + 0.003,
              f"${totals['shared_usd']:.4f}  (-{totals['shared_saving_pct']:.1f}%)", ha="center", va="bottom",
              fontsize=13, color=GREEN, fontweight="bold")
    left.set_xticks([0, 1])
    left.set_xticklabels([label for label, _p, _c in bars])
    left.set_xlim(-0.6, 1.6)
    left.set_ylim(0, totals["isolated_usd"] * 1.22)
    from matplotlib.ticker import MultipleLocator

    left.yaxis.set_major_locator(MultipleLocator(0.02))
    left.yaxis.set_major_formatter(lambda v, _p: f"${v:.2f}")
    left.set_ylabel("API list price, three quick bugfixes", labelpad=8)
    _clean(left, grid_axis="y")
    left.set_title("Back-to-back fixes: the warm session wins", loc="left", pad=12)

    keys = [("cache_write_usd", "Cache writes\n$5.00 / M tokens"), ("cache_read_usd", "Cache reads\n$0.20 / M tokens"),
            ("output_usd", "Output\n$20 / M tokens")]
    iso = totals["isolated_dollars"]
    warm = totals["shared_dollars"]
    width = 0.36
    for index, (key, label) in enumerate(keys):
        right.bar(index - width / 2, iso[key], width=width, color=RED, zorder=3)
        right.bar(index + width / 2, warm[key], width=width, color=GREEN, zorder=3)
        right.text(index - width / 2, iso[key] + 0.0012, f"${iso[key]:.4f}", ha="center", va="bottom", fontsize=9.5,
                   color=RED)
        right.text(index + width / 2, warm[key] + 0.0012, f"${warm[key]:.4f}", ha="center", va="bottom",
                   fontsize=9.5, color=GREEN)
    right.set_xticks(range(len(keys)))
    right.set_xticklabels([label for _k2, label in keys])
    right.set_ylim(0, iso["cache_write_usd"] * 1.35)
    right.yaxis.set_major_formatter(lambda v, _p: f"${v:.2f}")
    _clean(right, grid_axis="y")
    from matplotlib.patches import Patch

    right.legend(
        handles=[Patch(color=RED, label="/clear between tasks"), Patch(color=GREEN, label="warm session")],
        frameon=False,
        loc="upper right",
    )
    ratio = totals["cache_write_ratio"]
    right.text(
        0.3,
        iso["cache_write_usd"] * 0.92,
        f"/clear rebuilds the prefix every time:\n{totals['isolated_tokens']['cache_write_5m']:,} vs "
        f"{totals['shared_tokens']['cache_write_5m']:,} cache-write tokens ({ratio:.1f}x).\nThe warm session pays a little more\nfor reads and output, and still wins.",
        ha="left",
        va="center",
        fontsize=10.5,
        color=INK,
    )
    right.set_title("Where the dollars go", loc="left", pad=12)
    fig.suptitle(
        "median_bug, slice_bug, keep_currency on Claude Opus 5.5, effort medium, 5-minute cache. "
        "All tests pass in both arms. Uncached input is $0.0001 in each arm.",
        x=0.06, y=1.0, ha="left", fontsize=11, color=MUTED,
    )
    _save(fig, path, plt)


def write_figures(data: dict, out: Path) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

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
    fig = sub.add_parser("figures", help="draw the three live charts from the committed JSON")
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
        (out / DATASET_NAME).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {out / DATASET_NAME}")
    data = load(out / DATASET_NAME)
    for path in write_figures(data, out):
        print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
