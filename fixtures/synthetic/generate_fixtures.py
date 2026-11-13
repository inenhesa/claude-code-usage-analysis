"""Build the synthetic JSONL suite from the physical-caching simulator.

Every assistant usage line is one non-compaction call from model.simulate.
Compaction calls are written as compact_boundary plus isCompactSummary, not as
assistant usage. The expected file records both, so a reader can see the
summary-request tokens that a transcript-only total misses.

SYNTHETIC-05 is hand-specified edge cases, including the realistic resume,
subagent, and orphaned duplicates. It is not produced by simulate.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cc_usage_study.model import Assumptions, SimulationResult, simulate

MODEL = "SYNTHETIC-claude-sonnet-5"
ROOT = Path(__file__).resolve().parent


def _ts(start: datetime, index: int) -> str:
    return (start + timedelta(seconds=30 * index)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _usage(call) -> dict:
    return {
        "input_tokens": call.input_tokens,
        "output_tokens": call.output_tokens,
        "cache_creation_input_tokens": call.cache_write_tokens,
        "cache_read_input_tokens": call.cache_read_tokens,
        "cache_creation": {
            "ephemeral_5m_input_tokens": call.cache_write_5m_tokens,
            "ephemeral_1h_input_tokens": call.cache_write_1h_tokens,
        },
    }


def session_lines(
    session_id: str,
    start_iso: str,
    entrypoint: str,
    assumptions: Assumptions,
    *,
    sidechain: bool = False,
    agent_id: str | None = None,
) -> tuple[list[dict], SimulationResult]:
    start = datetime.fromisoformat(start_iso).replace(tzinfo=timezone.utc)
    result = simulate(assumptions)
    lines: list[dict] = [
        {
            "type": "fixture_meta",
            "synthetic": True,
            "sessionId": session_id,
            "timestamp": _ts(start, 0),
            "note": "SYNTHETIC. Generated from cc_usage_study.model.simulate. Not a real Claude Code session.",
            "assumptions_label": assumptions.label,
            "trigger_tokens": result.trigger_tokens,
        }
    ]
    for index, call in enumerate(result.calls, start=1):
        base = {
            "sessionId": session_id,
            "timestamp": _ts(start, index),
            "entrypoint": entrypoint,
            "isSidechain": sidechain,
            "version": "SYNTHETIC",
        }
        if agent_id:
            base["agentId"] = agent_id
        if call.kind == "compaction":
            lines.append(
                base
                | {
                    "type": "system",
                    "subtype": "compact_boundary",
                    "uuid": f"{session_id}-cb{index}",
                    "compactMetadata": {
                        "trigger": "auto",
                        "preTokens": call.prompt_tokens,
                        "postTokens": call.history_after and assumptions.system_prompt_tokens,
                    },
                }
            )
            # postTokens should be the reset context, not the system prompt.
            lines[-1]["compactMetadata"]["postTokens"] = (
                assumptions.system_prompt_tokens
                + assumptions.project_context_tokens
                + assumptions.summary_min
                + assumptions.reinjected_tokens
            )
            lines.append(
                base
                | {
                    "type": "user",
                    "uuid": f"{session_id}-cs{index}",
                    "isCompactSummary": True,
                    "message": {"role": "user", "content": "SYNTHETIC summary"},
                }
            )
            continue
        uid = f"{session_id}-{agent_id or 'main'}-a{index}"
        usage = _usage(call)
        partial = dict(usage, output_tokens=1)
        partial.pop("cache_creation", None)
        lines.append(
            base
            | {
                "type": "assistant",
                "uuid": uid + "p",
                "requestId": f"req_{uid}",
                "message": {
                    "id": f"msg_{uid}",
                    "role": "assistant",
                    "model": MODEL,
                    "usage": partial,
                },
            }
        )
        lines.append(
            base
            | {
                "type": "assistant",
                "uuid": uid,
                "requestId": f"req_{uid}",
                "message": {
                    "id": f"msg_{uid}",
                    "role": "assistant",
                    "model": MODEL,
                    "usage": usage,
                    "stop_reason": "end_turn",
                },
            }
        )
    return lines, result


def _dump(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(line, sort_keys=True) + "\n" for line in lines), encoding="utf-8")


def _expect(session_id: str, result: SimulationResult) -> dict:
    api = [call for call in result.calls if call.kind != "compaction"]
    compact = [call for call in result.calls if call.kind == "compaction"]
    return {
        "calls_in_jsonl": len(api),
        "compactions": len(compact),
        "prompt_tokens_in_jsonl": sum(call.prompt_tokens for call in api),
        "output_tokens_in_jsonl": sum(call.output_tokens for call in api),
        "cache_read_tokens_in_jsonl": sum(call.cache_read_tokens for call in api),
        "cache_write_tokens_in_jsonl": sum(call.cache_write_tokens for call in api),
        "compaction_request_prompt_tokens_not_in_jsonl": sum(call.prompt_tokens for call in compact),
        "pre_tokens": [call.prompt_tokens for call in compact],
    }


def write_generated(projects: Path) -> dict:
    long = Assumptions(label="long")
    short = Assumptions(label="short", user_turns=6, calls_per_turn=4)
    compound = Assumptions(label="compound", user_turns=12, calls_per_turn=4, window=250_000, pct=0.25, rework_calls=1)
    specs = [
        ("SYNTHETIC-01-before-default-95pct", "syn-before-1", "2026-09-08T09:00:00", long),
        ("SYNTHETIC-01-before-default-95pct", "syn-before-2", "2026-09-15T09:00:00", short),
        ("SYNTHETIC-02-after-25pct", "syn-after-1", "2026-10-13T09:00:00", replace(long, pct=0.25, label="long-25")),
        ("SYNTHETIC-02-after-25pct", "syn-after-2", "2026-10-20T09:00:00", replace(short, pct=0.25, label="short-25")),
        ("SYNTHETIC-03-compounding-62k", "syn-compound-1", "2026-10-27T09:00:00", compound),
    ]
    expected = {}
    for project, sid, start, assumptions in specs:
        lines, result = session_lines(sid, start, "cli", assumptions)
        _dump(projects / project / f"{sid}.jsonl", lines)
        expected[sid] = _expect(sid, result) | {"project": project, "trigger": result.trigger_tokens}
    sub_main = Assumptions(label="sub-main", user_turns=4, calls_per_turn=3, pct=0.25)
    sub_agent = Assumptions(
        label="sub-agent",
        user_turns=2,
        calls_per_turn=2,
        system_prompt_tokens=4_000,
        project_context_tokens=1_000,
        reinjected_tokens=1_000,
        summary_min=500,
        tail=2_000,
        pct=0.05,
    )
    for style, shared in (("cli", False), ("sdk", True)):
        parent = f"syn-sub-{style}"
        lines, result = session_lines(parent, "2026-10-21T09:00:00", "sdk-cli" if style == "sdk" else "cli", sub_main)
        project = projects / "SYNTHETIC-04-subagent-heavy"
        _dump(project / f"{parent}.jsonl", lines)
        expected[parent] = _expect(parent, result) | {"project": "SYNTHETIC-04-subagent-heavy", "role": "main", "style": style}
        for agent in ("ag-a", "ag-b"):
            sid = parent if shared else f"{parent}-{agent}"
            agent_lines, agent_result = session_lines(
                sid,
                "2026-10-21T10:00:00",
                "sdk-cli" if style == "sdk" else "cli",
                sub_agent,
                sidechain=True,
                agent_id=agent,
            )
            _dump(project / parent / "subagents" / f"agent-{agent}.jsonl", agent_lines)
            expected[f"{parent}/agent-{agent}"] = _expect(sid, agent_result) | {
                "project": "SYNTHETIC-04-subagent-heavy",
                "role": "subagent",
                "style": style,
                "session_id": sid,
            }
    return expected


def write_edge_cases(projects: Path) -> None:
    """Worker 2's edge session plus Worker 1's resume/subagent fixture."""
    root = projects / "SYNTHETIC-05-edge-cases"
    canary_note = "SYNTHETIC edge cases. Not a real Claude Code session."
    edge = [
        {"type": "fixture_meta", "synthetic": True, "sessionId": "syn-edge-1", "timestamp": "2026-11-01T10:00:00Z", "note": canary_note, "secret_canary": "CANARY_PROMPT_do_not_emit_9f3a"},
        {"type": "assistant", "sessionId": "syn-edge-1", "entrypoint": "cli", "isSidechain": False, "timestamp": "2026-11-01T10:00:01Z", "uuid": "e1a", "requestId": "req_e1", "message": {"id": "msg_e1", "role": "assistant", "model": "SYNTHETIC-claude-sonnet-5", "content": [{"type": "thinking", "thinking": "CANARY_PROMPT_do_not_emit_9f3a"}], "usage": {"input_tokens": 3, "output_tokens": 1, "cache_creation_input_tokens": 2000, "cache_read_input_tokens": 0}}},
        {"type": "assistant", "sessionId": "syn-edge-1", "entrypoint": "cli", "isSidechain": False, "timestamp": "2026-11-01T10:00:02Z", "uuid": "e1b", "requestId": "req_e1", "message": {"id": "msg_e1", "role": "assistant", "model": "SYNTHETIC-claude-sonnet-5", "content": [{"type": "text", "text": "CANARY_PROMPT_do_not_emit_9f3a"}], "usage": {"input_tokens": 3, "output_tokens": 180, "cache_creation_input_tokens": 2000, "cache_read_input_tokens": 0, "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 2000}}}},
        {"type": "assistant", "sessionId": "syn-edge-1", "entrypoint": "cli", "isSidechain": False, "timestamp": "2026-11-01T10:00:03Z", "uuid": "e1c", "requestId": "req_e1", "message": {"id": "msg_e1", "role": "assistant", "model": "SYNTHETIC-claude-sonnet-5", "content": [{"type": "tool_use", "id": "toolu_1", "name": "Agent", "input": {"prompt": "CANARY_PROMPT_do_not_emit_9f3a"}}], "usage": {"input_tokens": 3, "output_tokens": 1, "cache_creation_input_tokens": 2000, "cache_read_input_tokens": 0}}},
        {"type": "user", "sessionId": "syn-edge-1", "entrypoint": "cli", "isSidechain": False, "timestamp": "2026-11-01T10:02:00Z", "uuid": "e1r", "toolUseResult": {"agentId": "ag1", "totalTokens": 3241, "usage": {"input_tokens": 1, "output_tokens": 40, "cache_creation_input_tokens": 200, "cache_read_input_tokens": 3000}}, "message": {"role": "user", "content": [{"type": "tool_result", "content": "CANARY_PROMPT_do_not_emit_9f3a"}]}},
        {"type": "assistant", "sessionId": "syn-edge-1", "entrypoint": "cli", "isSidechain": False, "timestamp": "2026-11-01T10:02:05Z", "uuid": "e-syn", "message": {"id": "msg_syn", "role": "assistant", "model": "<synthetic>", "usage": {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}},
        {"type": "assistant", "sessionId": "syn-edge-1", "entrypoint": "cli", "isSidechain": False, "timestamp": "2026-11-01T10:02:06Z", "uuid": "e-err", "isApiErrorMessage": True, "message": {"id": "msg_err", "role": "assistant", "model": "SYNTHETIC-claude-sonnet-5", "usage": {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}},
        {"type": "system", "subtype": "compact_boundary", "sessionId": "syn-edge-1", "timestamp": "2026-11-01T10:03:00Z", "uuid": "e-cb", "compactMetadata": {"trigger": "auto", "preTokens": 120000, "postTokens": 9000}},
        {"type": "user", "sessionId": "syn-edge-1", "timestamp": "2026-11-01T10:03:01Z", "uuid": "e-cs", "isCompactSummary": True, "message": {"role": "user", "content": "CANARY_PROMPT_do_not_emit_9f3a summary"}},
        {"type": "assistant", "sessionId": "syn-edge-1", "entrypoint": "cli", "isSidechain": False, "timestamp": "2026-11-01T10:03:30Z", "uuid": "e2", "requestId": "req_e2", "message": {"id": "msg_e2", "role": "assistant", "model": "SYNTHETIC-claude-sonnet-5", "usage": {"input_tokens": 5, "output_tokens": 300, "cache_creation_input_tokens": 9000, "cache_read_input_tokens": 3000}}},
        "NOT_JSON this line is not json",
        {"type": "assistant", "sessionId": "syn-edge-1", "entrypoint": "cli", "isSidechain": False, "timestamp": "2026-11-01T10:04:00Z", "uuid": "e3", "requestId": "req_e3", "message": {"role": "assistant", "model": "SYNTHETIC-claude-sonnet-5", "usage": {"input_tokens": 2, "output_tokens": 50, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 12000}}},
    ]
    path = root / "syn-edge-1.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    chunks = []
    for line in edge:
        if isinstance(line, str):
            chunks.append(line + "\n")
        else:
            chunks.append(json.dumps(line, sort_keys=True) + "\n")
    path.write_text("".join(chunks), encoding="utf-8")
    sub = [
        {"type": "assistant", "sessionId": "syn-edge-1", "isSidechain": True, "agentId": "ag1", "timestamp": "2026-11-01T10:00:30Z", "uuid": "s1", "requestId": "req_s1", "message": {"id": "msg_s1", "role": "assistant", "model": "SYNTHETIC-claude-haiku-5-5", "usage": {"input_tokens": 4, "output_tokens": 70, "cache_creation_input_tokens": 3000, "cache_read_input_tokens": 0}}},
        {"type": "assistant", "sessionId": "syn-edge-1", "isSidechain": True, "agentId": "ag1", "timestamp": "2026-11-01T10:01:30Z", "uuid": "s2", "requestId": "req_s2", "message": {"id": "msg_s2", "role": "assistant", "model": "SYNTHETIC-claude-haiku-5-5", "usage": {"input_tokens": 1, "output_tokens": 40, "cache_creation_input_tokens": 200, "cache_read_input_tokens": 3000}}},
    ]
    _dump(root / "syn-edge-1" / "subagents" / "agent-ag1.jsonl", sub)
    (root / "syn-edge-1" / "subagents" / "agent-ag1.meta.json").write_text("{}\n", encoding="utf-8")
    (root / "syn-edge-1" / "tool-results").mkdir(parents=True, exist_ok=True)
    (root / "syn-edge-1" / "tool-results" / "toolu_big.txt").write_text("not jsonl\n", encoding="utf-8")
    orphan = [edge[9]]  # the msg_e2 final line, a dict
    _dump(root / "syn-edge-1.orphaned-20261101T100500Z-ab12.jsonl", orphan if isinstance(orphan[0], dict) else [])
    # Worker 1 realistic resume: same message id under a new session, plus a sidechain.
    realistic = [
        {"type": "assistant", "sessionId": "S1", "requestId": "req_1", "timestamp": "2026-11-01T10:00:00Z", "uuid": "u1", "message": {"id": "msg_1", "model": "claude-sonnet-5-5", "usage": {"input_tokens": 5, "output_tokens": 3, "cache_creation_input_tokens": 20000, "cache_read_input_tokens": 0, "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 20000}}}},
        {"type": "assistant", "sessionId": "S1", "requestId": "req_1", "timestamp": "2026-11-01T10:00:01Z", "uuid": "u1b", "message": {"id": "msg_1", "model": "claude-sonnet-5-5", "stop_reason": "tool_use", "usage": {"input_tokens": 5, "output_tokens": 500, "cache_creation_input_tokens": 20000, "cache_read_input_tokens": 0, "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 20000}}}},
        {"type": "system", "subtype": "compact_boundary", "sessionId": "S1", "timestamp": "2026-11-01T10:05:00Z", "compactMetadata": {"trigger": "auto", "preTokens": 250000}},
        {"type": "user", "sessionId": "S1", "isCompactSummary": True, "timestamp": "2026-11-01T10:05:01Z", "message": {"role": "user", "content": "summary"}},
    ]
    _dump(root / "sess1.jsonl", realistic)
    _dump(
        root / "sess1" / "subagents" / "agent-abc.jsonl",
        [{"type": "assistant", "sessionId": "S1", "isSidechain": True, "agentId": "abc", "requestId": "req_9", "timestamp": "2026-11-01T10:02:00Z", "uuid": "s1w", "message": {"id": "msg_9", "model": "claude-haiku-4-5", "usage": {"input_tokens": 3, "output_tokens": 100, "cache_creation_input_tokens": 15000, "cache_read_input_tokens": 0, "cache_creation": {"ephemeral_5m_input_tokens": 15000, "ephemeral_1h_input_tokens": 0}}}}],
    )
    _dump(
        root / "sess2-resumed.jsonl",
        [{"type": "assistant", "sessionId": "S2", "requestId": "req_1", "timestamp": "2026-11-01T10:00:01Z", "uuid": "u1b", "message": {"id": "msg_1", "model": "claude-sonnet-5-5", "stop_reason": "tool_use", "usage": {"input_tokens": 5, "output_tokens": 500, "cache_creation_input_tokens": 20000, "cache_read_input_tokens": 0, "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 20000}}}}],
    )


def main() -> int:
    projects = ROOT / "projects"
    expected = write_generated(projects)
    write_edge_cases(projects)
    dest = ROOT / "expected"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "generated-sessions.json").write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: {"calls": value["calls_in_jsonl"], "compactions": value["compactions"], "prompt": value["prompt_tokens_in_jsonl"]} for key, value in expected.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
