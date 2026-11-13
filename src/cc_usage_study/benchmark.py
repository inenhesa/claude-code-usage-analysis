"""Scaffolding for a controlled Claude Code compaction benchmark.

``--dry-run`` is the default. It prints the commands and does not launch
Claude. ``--execute`` spends real quota. Read benchmarks/protocol.md before
running it.

Headless usage fields, and the fact that ``usage`` on a result omits
subagents while ``modelUsage`` includes them, are documented at
https://code.claude.com/docs/en/agent-sdk/cost-tracking
and https://code.claude.com/docs/en/headless.

``total_cost_usd`` is a client-side estimate. It is not the subscription meter.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable

# Documented range for the auto-compact window setting and --autocompact.
# https://code.claude.com/docs/en/model-config
AUTOCOMPACT_WINDOW_MIN = 100_000
AUTOCOMPACT_WINDOW_MAX = 1_000_000

DEFAULT_PERCENTS = (95, 75, 50, 25, 10)
DEFAULT_CONTEXT_WINDOW = 1_000_000

USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)
MODEL_USAGE_KEYS = (
    "inputTokens",
    "outputTokens",
    "cacheReadInputTokens",
    "cacheCreationInputTokens",
    "costUSD",
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def tasks_dir() -> Path:
    return repo_root() / "benchmarks" / "tasks"


def discover_tasks(root: Path | None = None) -> list[dict]:
    base = root or tasks_dir()
    found = []
    if not base.is_dir():
        return found
    for task_json in sorted(base.glob("*/task.json")):
        payload = json.loads(task_json.read_text(encoding="utf-8"))
        payload["dir"] = str(task_json.parent)
        found.append(payload)
    return found


def window_tokens_for_percent(percent: float, context_window: int) -> dict:
    """Translate a percent of the context window into an ``--autocompact`` value.

    The CLI accepts 100,000 to 1,000,000 tokens and also caps the value at the
    model window. On a 200k window, 25% and 10% both clamp to 100,000, so they
    are not separate arms. ``CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`` is the control
    that can name a percentage below that floor, and it cannot raise the
    trigger above the unpublished default percentage.
    """
    if context_window < 1:
        raise ValueError("context_window must be positive")
    if not 0 < percent <= 100:
        raise ValueError("percent must be in (0, 100]")
    raw = int(round(percent / 100 * context_window))
    floor = min(AUTOCOMPACT_WINDOW_MIN, context_window)
    ceiling = min(AUTOCOMPACT_WINDOW_MAX, context_window)
    clamped = min(max(raw, floor), ceiling)
    return {
        "percent": percent,
        "context_window": context_window,
        "raw_tokens": raw,
        "autocompact_tokens": clamped,
        "clamped": clamped != raw,
        "floor": floor,
        "ceiling": ceiling,
    }


def _settings_for_arm(method: str, percent: float, window: dict) -> dict:
    env: dict[str, str] = {}
    caveats = [
        "Managed settings outrank --settings. This harness cannot see them.",
        "total_cost_usd is a client-side estimate, not the five-hour or weekly meter.",
        "Result usage excludes subagent tokens. modelUsage includes them. Record both.",
    ]
    if method == "window":
        env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = str(window["autocompact_tokens"])
        # A value above the default percentage is ignored, so this clears a
        # lower override from user settings and leaves the default percentage
        # of the window we just set. The numeric default percentage is not
        # published.
        env["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] = "100"
        autocompact_arg = str(window["autocompact_tokens"])
        caveats.append(
            "Window arm: the trigger is the default percentage of "
            f"{window['autocompact_tokens']} tokens. Docs say the percentage "
            "override cannot raise that default, and they do not publish the number."
        )
        if window["clamped"]:
            caveats.append(
                f"{percent:g}% of this window is {window['raw_tokens']} tokens, "
                f"which is outside {window['floor']} to {window['ceiling']}. "
                f"The window arm uses {window['autocompact_tokens']}."
            )
    elif method == "pct":
        env["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] = str(int(percent) if percent == int(percent) else percent)
        autocompact_arg = "auto"
        caveats.append(
            "Percent arm: matches the env var named in the docs, "
            "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE, and passes --autocompact auto so "
            "the window stays on the model default. Values above the default "
            "percentage are ignored. A 95 arm may therefore be identical to "
            "the default. On native 1M models that default trigger is about 967K tokens."
        )
        caveats.append(
            "If user settings still set a valid CLAUDE_CODE_AUTO_COMPACT_WINDOW, "
            "that variable outranks --autocompact. Remove it before a percent-arm run, "
            "or the percentage applies to that window instead of the model default."
        )
    else:
        raise ValueError(f"unknown method {method}")
    return {"env": env, "autocompact_arg": autocompact_arg, "caveats": caveats}


def build_arm(
    task: dict,
    *,
    method: str,
    percent: float,
    context_window: int,
    model: str | None,
    max_turns: int,
    include_permission_prompts_flag: bool,
    persist_session: bool,
) -> dict:
    window = window_tokens_for_percent(percent, context_window)
    settings = _settings_for_arm(method, percent, window)
    prompt_path = Path(task["dir"]) / task.get("prompt_file", "prompt.txt")
    prompt = prompt_path.read_text(encoding="utf-8").strip()
    argv = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "acceptEdits",
        "--allowedTools",
        "Bash,Read,Edit",
        "--max-turns",
        str(max_turns),
        "--autocompact",
        settings["autocompact_arg"],
        "--settings",
        json.dumps({"env": settings["env"]}),
    ]
    if model:
        argv.extend(["--model", model])
    if include_permission_prompts_flag:
        # Requires Claude Code v2.1.259 or later.
        argv.extend(["--permission-prompts", "none"])
    if not persist_session:
        argv.append("--no-session-persistence")
    argv.extend(["--append-system-prompt", "You are in a fixed benchmark. Do the task and stop."])
    return {
        "task_id": task["id"],
        "task_dir": task["dir"],
        "method": method,
        "percent": percent,
        "window": window,
        "settings_env": settings["env"],
        "argv": argv,
        "caveats": settings["caveats"],
        "score": task.get("score", []),
        "editable": task.get("editable", []),
        "bare_mode": False,
        "bare_mode_note": (
            "Bare mode is off. --bare skips the subscription login and needs "
            "ANTHROPIC_API_KEY, and it shortens the prompt, so it does not "
            "measure the five-hour limit."
        ),
    }


def plan_arms(
    *,
    tasks: list[dict] | None = None,
    percents: Iterable[float] = DEFAULT_PERCENTS,
    methods: Iterable[str] = ("window", "pct"),
    context_window: int = DEFAULT_CONTEXT_WINDOW,
    model: str | None = None,
    max_turns: int = 20,
    include_permission_prompts_flag: bool = True,
    persist_session: bool = True,
    repeats: int = 1,
) -> list[dict]:
    chosen = tasks if tasks is not None else discover_tasks()
    arms = []
    for task in chosen:
        for method in methods:
            for percent in percents:
                for repeat in range(1, repeats + 1):
                    arm = build_arm(
                        task,
                        method=method,
                        percent=float(percent),
                        context_window=context_window,
                        model=model,
                        max_turns=max_turns,
                        include_permission_prompts_flag=include_permission_prompts_flag,
                        persist_session=persist_session,
                    )
                    arm["repeat"] = repeat
                    arms.append(arm)
    return arms


def _numeric_usage(raw: dict) -> dict[str, int]:
    usage = {}
    for key in USAGE_KEYS:
        value = raw.get(key, 0)
        usage[key] = int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0
    nested = raw.get("cache_creation")
    if isinstance(nested, dict):
        usage["ephemeral_5m_input_tokens"] = int(nested.get("ephemeral_5m_input_tokens") or 0)
        usage["ephemeral_1h_input_tokens"] = int(nested.get("ephemeral_1h_input_tokens") or 0)
    return usage


def _numeric_model_usage(raw: dict) -> dict[str, dict]:
    parsed = {}
    if not isinstance(raw, dict):
        return parsed
    for model, payload in raw.items():
        if not isinstance(payload, dict):
            continue
        row = {}
        for key in MODEL_USAGE_KEYS:
            value = payload.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                row[key] = value
        parsed[str(model)] = row
    return parsed


def parse_stream_text(text: str) -> dict:
    """Reduce a stream-json transcript to usage aggregates.

    Message text, tool inputs, and thinking are discarded.
    """
    compactions = 0
    result = None
    lines = 0
    bad_lines = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lines += 1
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            bad_lines += 1
            continue
        if not isinstance(obj, dict):
            bad_lines += 1
            continue
        subtype = obj.get("subtype")
        if isinstance(subtype, str) and "compact" in subtype.lower():
            compactions += 1
        if obj.get("type") == "result":
            result = obj
    summary: dict = {
        "stream_lines": lines,
        "bad_lines": bad_lines,
        "compaction_markers": compactions,
        "result_seen": result is not None,
    }
    if result is None:
        return summary
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    summary.update(
        {
            "subtype": result.get("subtype") if isinstance(result.get("subtype"), str) else None,
            "session_id": result.get("session_id") if isinstance(result.get("session_id"), str) else None,
            "num_turns": result.get("num_turns") if isinstance(result.get("num_turns"), int) else None,
            "total_cost_usd": result.get("total_cost_usd")
            if isinstance(result.get("total_cost_usd"), (int, float))
            else None,
            "usage": _numeric_usage(usage),
            "model_usage": _numeric_model_usage(result.get("modelUsage") or result.get("model_usage") or {}),
            "ok": result.get("subtype") == "success",
        }
    )
    return summary


def _score_command(score: list[str]) -> list[str]:
    if not score:
        return score
    command = list(score)
    if command[0] in {"python", "python3"}:
        command[0] = sys.executable
    return command


def score_workdir(workdir: Path, score: list[str]) -> dict:
    if not score:
        return {"ran": False, "passed": None, "returncode": None}
    command = _score_command(score)
    completed = subprocess.run(
        command,
        cwd=workdir,
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "ran": True,
        "passed": completed.returncode == 0,
        "returncode": completed.returncode,
        "command": command,
    }


def extra_files(workdir: Path, original: set[str], editable: list[str]) -> list[str]:
    ignore = {"__pycache__", ".pytest_cache"}
    current = set()
    for path in workdir.rglob("*"):
        if not path.is_file():
            continue
        if any(part in ignore for part in path.parts):
            continue
        current.add(path.relative_to(workdir).as_posix())
    allowed = set(original) | set(editable)
    return sorted(path for path in current if path not in allowed)


def _snapshot(workdir: Path) -> set[str]:
    names = set()
    for path in workdir.rglob("*"):
        if path.is_file():
            names.add(path.relative_to(workdir).as_posix())
    return names


def preflight_notes(settings_path: Path | None = None) -> list[str]:
    path = settings_path or (Path.home() / ".claude" / "settings.json")
    notes = [
        "Record claude --version. The about-967K default is documented for current native 1M models and changed for Sonnet 5 in v2.1.323.",
        "Record the five-hour and weekly percentages from Settings > Usage before and after the run, on an otherwise idle account. This harness cannot read that meter.",
        "Do not use --bare if the question is the subscription limit.",
    ]
    if not path.is_file():
        notes.append(f"No settings file at {path}.")
        return notes
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        notes.append(f"{path} is not valid JSON. Claude Code will skip or warn on it.")
        return notes
    env = payload.get("env") if isinstance(payload, dict) else None
    watched = ("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "CLAUDE_CODE_AUTO_COMPACT_WINDOW")
    if isinstance(env, dict):
        for key in watched:
            if key in env:
                notes.append(
                    f"{path} sets {key}. The arm's --settings overlay outranks user settings for that key. "
                    "A valid CLAUDE_CODE_AUTO_COMPACT_WINDOW still outranks --autocompact when the overlay does not replace it."
                )
    if isinstance(payload, dict) and "autoCompactWindow" in payload:
        notes.append(
            f"{path} sets autoCompactWindow. --autocompact and CLAUDE_CODE_AUTO_COMPACT_WINDOW outrank it."
        )
    return notes


def execute_arm(arm: dict, *, timeout_s: int, work_root: Path) -> dict:
    source = Path(arm["task_dir"])
    destination = work_root / f"{arm['task_id']}-{arm['method']}-{arm['percent']}-r{arm['repeat']}"
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)
    original = _snapshot(destination)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            arm["argv"],
            cwd=destination,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired:
        return {
            "task_id": arm["task_id"],
            "method": arm["method"],
            "percent": arm["percent"],
            "repeat": arm["repeat"],
            "timed_out": True,
            "wall_seconds": time.monotonic() - started,
        }
    wall = time.monotonic() - started
    parsed = parse_stream_text(completed.stdout)
    scored = score_workdir(destination, arm["score"])
    return {
        "task_id": arm["task_id"],
        "method": arm["method"],
        "percent": arm["percent"],
        "repeat": arm["repeat"],
        "timed_out": False,
        "returncode": completed.returncode,
        "wall_seconds": wall,
        "stderr_bytes": len(completed.stderr.encode("utf-8")),
        "usage_summary": parsed,
        "score": {key: value for key, value in scored.items() if key != "command"} | {"command": scored.get("command")},
        "extra_files": extra_files(destination, original, arm["editable"]),
        "settings_env": arm["settings_env"],
    }


def _arm_for_display(arm: dict) -> dict:
    shown = {
        "task_id": arm["task_id"],
        "method": arm["method"],
        "percent": arm["percent"],
        "repeat": arm["repeat"],
        "window": arm["window"],
        "settings_env": arm["settings_env"],
        "argv_without_prompt": _redact_prompt(arm["argv"]),
        "caveats": arm["caveats"],
        "score": arm["score"],
        "bare_mode_note": arm["bare_mode_note"],
    }
    return shown


def _redact_prompt(argv: list[str]) -> list[str]:
    """Replace the prompt argument so dry-run output stays short.

    The prompt is the fixed task file, not user data. The path is enough.
    """
    redacted = []
    skip_next = False
    for index, part in enumerate(argv):
        if skip_next:
            skip_next = False
            continue
        if part == "-p" and index + 1 < len(argv):
            redacted.extend(["-p", "<prompt from task prompt.txt>"])
            skip_next = True
            continue
        redacted.append(part)
    return redacted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan or run the compaction benchmark. Dry-run is the default.")
    parser.add_argument("--execute", action="store_true", help="Launch claude. This spends quota.")
    parser.add_argument("--task", action="append", help="Task id. Repeat to select more than one. Default: all.")
    parser.add_argument("--percents", default="95,75,50,25,10")
    parser.add_argument("--methods", default="window,pct", help="Comma-separated: window, pct")
    parser.add_argument("--context-window", type=int, default=DEFAULT_CONTEXT_WINDOW)
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-turns", type=int, default=20)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument(
        "--no-permission-prompts-flag",
        action="store_true",
        help="Omit --permission-prompts none (that flag needs Claude Code v2.1.259 or later).",
    )
    parser.add_argument(
        "--no-session-persistence",
        action="store_true",
        help="Pass --no-session-persistence so the run is not written under ~/.claude/projects.",
    )
    parser.add_argument("--output", type=Path, default=None, help="Where to write the JSON plan or results.")
    args = parser.parse_args(argv)

    tasks = discover_tasks()
    if args.task:
        wanted = set(args.task)
        tasks = [task for task in tasks if task["id"] in wanted]
        missing = wanted - {task["id"] for task in tasks}
        if missing:
            print(f"Unknown task ids: {sorted(missing)}", file=sys.stderr)
            return 2
    percents = [float(part) for part in args.percents.split(",") if part.strip()]
    methods = [part.strip() for part in args.methods.split(",") if part.strip()]
    arms = plan_arms(
        tasks=tasks,
        percents=percents,
        methods=methods,
        context_window=args.context_window,
        model=args.model,
        max_turns=args.max_turns,
        include_permission_prompts_flag=not args.no_permission_prompts_flag,
        persist_session=not args.no_session_persistence,
        repeats=args.repeats,
    )
    claude_path = shutil.which("claude")
    payload: dict = {
        "dry_run": not args.execute,
        "claude_on_path": claude_path is not None,
        "claude_path": claude_path,
        "preflight": preflight_notes(),
        "arms": [_arm_for_display(arm) for arm in arms],
    }
    if args.execute:
        if claude_path is None:
            print("claude is not on PATH. Install Claude Code, or run without --execute.", file=sys.stderr)
            return 2
        work_root = Path.cwd() / "outputs" / "benchmark-work"
        work_root.mkdir(parents=True, exist_ok=True)
        results = []
        for arm in arms:
            print(f"running {arm['task_id']} {arm['method']} {arm['percent']}% repeat {arm['repeat']}", file=sys.stderr)
            results.append(execute_arm(arm, timeout_s=args.timeout, work_root=work_root))
        payload["results"] = results
        payload["dry_run"] = False
    text = json.dumps(payload, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
