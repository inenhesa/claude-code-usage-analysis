"""Aggregate Claude Code session transcripts without reading prompt text.

Transcripts live under ``~/.claude/projects/**/*.jsonl``. Assistant records
carry ``message.usage`` with ``input_tokens``, ``output_tokens``,
``cache_creation_input_tokens``, and ``cache_read_input_tokens``. One assistant
message is often written once per content block, with the same ``message.id``
and a repeated usage object. This parser keeps the last record for each
``message.id`` in a session.

The parser copies an allowlist of fields. It does not copy ``content``,
``text``, tool inputs, or thinking. Parse errors are counted and the offending
line is not stored.

The on-disk duplication pattern is described by people who have read the
files. Anthropic's Agent SDK docs say per-step ``output_tokens`` on the
streaming API can be a placeholder and that the result message is the
cumulative total. Those are different surfaces. For the JSONL files this tool
reads, last-record-per-message.id is the rule, and it is a parser convention
rather than an Anthropic guarantee.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from cc_usage_study.pricing import estimate_call_usd


TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_5m_tokens: int = 0
    cache_creation_1h_tokens: int = 0
    # True until a call without a TTL split is added. An empty total is not a claim
    # that a real call's writes were classified.
    ttl_split_known: bool = True
    cache_creation_mismatch: bool = False

    @property
    def prompt_tokens(self) -> int:
        """Fresh input plus cache writes plus cache reads.

        This matches the status-line definition of context input:
        input_tokens + cache_creation_input_tokens + cache_read_input_tokens.
        The nested TTL split is not added again.
        """
        return (
            self.input_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_creation_input_tokens += other.cache_creation_input_tokens
        self.cache_read_input_tokens += other.cache_read_input_tokens
        self.cache_creation_5m_tokens += other.cache_creation_5m_tokens
        self.cache_creation_1h_tokens += other.cache_creation_1h_tokens
        self.ttl_split_known = self.ttl_split_known and other.ttl_split_known
        self.cache_creation_mismatch = self.cache_creation_mismatch or other.cache_creation_mismatch

    def as_dict(self) -> dict[str, int | bool]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "cache_creation_5m_tokens": self.cache_creation_5m_tokens,
            "cache_creation_1h_tokens": self.cache_creation_1h_tokens,
            "prompt_tokens": self.prompt_tokens,
            "ttl_split_known": self.ttl_split_known,
            "cache_creation_mismatch": self.cache_creation_mismatch,
        }


@dataclass
class CallRecord:
    session_id: str
    message_id: str
    model: str
    timestamp: str | None
    role: str
    project_label: str
    synthetic: bool
    usage: Usage


@dataclass
class FileStats:
    files: int = 0
    lines: int = 0
    parse_errors: int = 0
    records: int = 0
    usage_records_before_dedup: int = 0
    compactions: int = 0
    synthetic: bool = False


@dataclass
class SessionStats:
    session_id: str
    project_label: str
    role: str
    synthetic: bool
    first_timestamp: str | None = None
    last_timestamp: str | None = None
    models: set[str] = field(default_factory=set)
    calls: int = 0
    compactions: int = 0
    usage: Usage = field(default_factory=Usage)

    def as_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "project": self.project_label,
            "role": self.role,
            "synthetic": self.synthetic,
            "first_timestamp": self.first_timestamp,
            "last_timestamp": self.last_timestamp,
            "models": sorted(self.models),
            "calls": self.calls,
            "compactions": self.compactions,
            "tokens": self.usage.as_dict(),
        }


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def usage_from_mapping(raw: dict) -> Usage | None:
    """Build a Usage from an API usage object. Returns None if no token field is present."""
    if not isinstance(raw, dict):
        return None
    if not any(field in raw for field in TOKEN_FIELDS):
        nested = raw.get("cache_creation")
        if not isinstance(nested, dict):
            return None
    flat = _int(raw.get("cache_creation_input_tokens"))
    nested = raw.get("cache_creation")
    five_m = None
    one_h = None
    if isinstance(nested, dict) and (
        "ephemeral_5m_input_tokens" in nested or "ephemeral_1h_input_tokens" in nested
    ):
        five_m = _int(nested.get("ephemeral_5m_input_tokens"))
        one_h = _int(nested.get("ephemeral_1h_input_tokens"))
    mismatch = False
    if five_m is not None and one_h is not None:
        nested_sum = five_m + one_h
        if flat and nested_sum != flat:
            mismatch = True
        if not flat:
            flat = nested_sum
    usage = Usage(
        input_tokens=_int(raw.get("input_tokens")),
        output_tokens=_int(raw.get("output_tokens")),
        cache_creation_input_tokens=flat,
        cache_read_input_tokens=_int(raw.get("cache_read_input_tokens")),
        cache_creation_5m_tokens=five_m or 0,
        cache_creation_1h_tokens=one_h or 0,
        ttl_split_known=five_m is not None and one_h is not None,
        cache_creation_mismatch=mismatch,
    )
    return usage


def _message_usage(record: dict) -> tuple[dict | None, str, str]:
    message = record.get("message")
    model = ""
    message_id = ""
    usage = None
    if isinstance(message, dict):
        if isinstance(message.get("usage"), dict):
            usage = message["usage"]
        model = message.get("model") if isinstance(message.get("model"), str) else ""
        message_id = message.get("id") if isinstance(message.get("id"), str) else ""
    if usage is None and isinstance(record.get("usage"), dict):
        usage = record["usage"]
    return usage, model, message_id


def is_compaction_marker(record: dict) -> bool:
    """True only for structural markers. Message text is not inspected."""
    subtype = record.get("subtype")
    if isinstance(subtype, str) and "compact" in subtype.lower():
        return True
    if record.get("isCompactSummary") is True:
        return True
    message = record.get("message")
    if isinstance(message, dict) and message.get("isCompactSummary") is True:
        return True
    compact = record.get("compactMetadata")
    if isinstance(compact, dict):
        return True
    return False


def project_dir_for(path: Path) -> Path:
    parts = path.parts
    if "subagents" in parts:
        index = parts.index("subagents")
        return Path(*parts[:index]).parent
    return path.parent


def project_label_for(path: Path) -> str:
    directory = project_dir_for(path)
    name = directory.name
    if "SYNTHETIC" in name.upper() or "synthetic" in str(path).lower():
        return name
    digest = hashlib.sha256(str(directory).encode("utf-8")).hexdigest()[:12]
    return f"project-{digest}"


def transcript_role(path: Path) -> str:
    return "subagent" if "subagents" in path.parts else "main"


def iter_jsonl(path: Path) -> Iterator[tuple[int, dict | None, bool]]:
    """Yield (line_number, object or None, had_decode_error)."""
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for lineno, line in enumerate(handle, 1):
            text = line.strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                yield lineno, None, True
                continue
            if not isinstance(obj, dict):
                yield lineno, None, True
                continue
            yield lineno, obj, False


def _parse_time(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def analyze_paths(paths: Iterable[Path]) -> dict:
    files: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            files.extend(sorted(p for p in path.rglob("*.jsonl") if p.is_file()))
        elif path.is_file():
            files.append(path)
    stats = FileStats()
    # Last record for (session_id, message_id) wins. Keyed in file order.
    latest: dict[tuple[str, str], CallRecord] = {}
    order: list[tuple[str, str]] = []
    sessions: dict[str, SessionStats] = {}
    synthetic_any = False

    for path in files:
        stats.files += 1
        role = transcript_role(path)
        label = project_label_for(path)
        path_synthetic = "synthetic" in str(path).lower()
        for _lineno, record, failed in iter_jsonl(path):
            stats.lines += 1
            if failed or record is None:
                stats.parse_errors += 1
                continue
            stats.records += 1
            record_synthetic = path_synthetic or record.get("synthetic") is True
            synthetic_any = synthetic_any or record_synthetic
            session_id = record.get("sessionId") or record.get("session_id")
            if not isinstance(session_id, str) or not session_id:
                session_id = path.stem
            timestamp = record.get("timestamp") if isinstance(record.get("timestamp"), str) else None
            session = sessions.get(session_id)
            if session is None:
                session = SessionStats(
                    session_id=session_id,
                    project_label=label,
                    role=role,
                    synthetic=record_synthetic,
                )
                sessions[session_id] = session
            session.synthetic = session.synthetic or record_synthetic
            if is_compaction_marker(record):
                stats.compactions += 1
                session.compactions += 1
            _touch_span(session, timestamp)
            raw_usage, model, message_id = _message_usage(record)
            if raw_usage is None:
                continue
            usage = usage_from_mapping(raw_usage)
            if usage is None:
                continue
            stats.usage_records_before_dedup += 1
            if not message_id:
                message_id = record.get("uuid") if isinstance(record.get("uuid"), str) else ""
            if not message_id:
                message_id = f"{path}:{_lineno}"
            key = (session_id, message_id)
            call = CallRecord(
                session_id=session_id,
                message_id=message_id,
                model=model,
                timestamp=timestamp,
                role=role,
                project_label=label,
                synthetic=record_synthetic,
                usage=usage,
            )
            if key not in latest:
                order.append(key)
            latest[key] = call
            if model:
                session.models.add(model)

    calls = [latest[key] for key in order]
    for call in calls:
        session = sessions[call.session_id]
        session.calls += 1
        session.usage.add(call.usage)
        _touch_span(session, call.timestamp)

    report = _report(stats, sessions, calls, synthetic_any)
    return report


def _touch_span(session: SessionStats, timestamp: str | None) -> None:
    if not timestamp:
        return
    if session.first_timestamp is None or timestamp < session.first_timestamp:
        session.first_timestamp = timestamp
    if session.last_timestamp is None or timestamp > session.last_timestamp:
        session.last_timestamp = timestamp


def _median(values: list[int]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2


def _summarize_sessions(sessions: list[SessionStats]) -> dict:
    usage = Usage(ttl_split_known=True)
    if not sessions:
        usage.ttl_split_known = False
    calls = 0
    compactions = 0
    prompts = []
    for session in sessions:
        usage.add(session.usage)
        calls += session.calls
        compactions += session.compactions
        prompts.append(session.usage.prompt_tokens)
    prompt_per_call = (usage.prompt_tokens / calls) if calls else None
    cache_share = (usage.cache_read_input_tokens / usage.prompt_tokens) if usage.prompt_tokens else None
    return {
        "sessions": len(sessions),
        "calls": calls,
        "compactions": compactions,
        "tokens": usage.as_dict(),
        "prompt_tokens_per_call": prompt_per_call,
        "prompt_tokens_per_session_mean": (sum(prompts) / len(prompts)) if prompts else None,
        "prompt_tokens_per_session_median": _median(prompts),
        "cache_read_share_of_prompt": cache_share,
    }


def _day(timestamp: str | None) -> str | None:
    parsed = _parse_time(timestamp)
    if parsed is None:
        return None
    return parsed.astimezone(timezone.utc).date().isoformat()


def _report(
    stats: FileStats,
    sessions: dict[str, SessionStats],
    calls: list[CallRecord],
    synthetic_any: bool,
) -> dict:
    by_model: dict[str, Usage] = defaultdict(lambda: Usage(ttl_split_known=True))
    model_calls: dict[str, int] = defaultdict(int)
    model_cost: dict[str, dict] = {}
    by_day: dict[str, Usage] = defaultdict(lambda: Usage(ttl_split_known=True))
    day_calls: dict[str, int] = defaultdict(int)
    for call in calls:
        model_name = call.model or "unknown"
        by_model[model_name].add(call.usage)
        model_calls[model_name] += 1
        day = _day(call.timestamp) or "undated"
        by_day[day].add(call.usage)
        day_calls[day] += 1
    for model_name, usage in by_model.items():
        if model_name == "unknown":
            model_cost[model_name] = {"usd": None, "reason": "model id missing"}
            continue
        if not usage.ttl_split_known and usage.cache_creation_input_tokens:
            model_cost[model_name] = {
                "usd": None,
                "reason": "cache write TTL split missing on at least one call",
            }
            continue
        usd, reason = estimate_call_usd(
            model_name,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_input_tokens,
            cache_write_5m_tokens=usage.cache_creation_5m_tokens,
            cache_write_1h_tokens=usage.cache_creation_1h_tokens,
            prompt_tokens=usage.prompt_tokens,
        )
        model_cost[model_name] = {
            "usd": usd,
            "reason": reason,
            "note": "list-price estimate from the 2026-11-13 Anthropic pricing snapshot, not a subscription bill",
        }

    session_list = sorted(
        sessions.values(),
        key=lambda item: (item.first_timestamp or "", item.session_id),
    )
    return {
        "synthetic": synthetic_any,
        "privacy": "aggregates only; prompt content is not copied",
        "files": stats.files,
        "lines": stats.lines,
        "records": stats.records,
        "parse_errors": stats.parse_errors,
        "usage_records_before_dedup": stats.usage_records_before_dedup,
        "calls": len(calls),
        "sessions": len(session_list),
        "compactions": stats.compactions,
        "totals": _summarize_sessions(session_list),
        "by_model": {
            name: {
                "calls": model_calls[name],
                "tokens": usage.as_dict(),
                "list_price_estimate": model_cost[name],
            }
            for name, usage in sorted(by_model.items())
        },
        "by_day": {
            day: {"calls": day_calls[day], "tokens": usage.as_dict()}
            for day, usage in sorted(by_day.items())
        },
        "per_session": [session.as_dict() for session in session_list],
    }


def split_periods(report: dict, split_date: str) -> dict:
    """Split sessions by the UTC date of their first timestamp.

    Sessions that start on ``split_date`` count as after. Sessions with no
    parseable timestamp are ``undated`` and are in neither side.
    """
    boundary = datetime.fromisoformat(split_date).date()
    before: list[SessionStats] = []
    after: list[SessionStats] = []
    undated: list[SessionStats] = []
    # Rebuild lightweight session objects from the serialized report so the
    # CLI can split without keeping the internal objects. Totals are enough.
    for item in report["per_session"]:
        stamp = _parse_time(item.get("first_timestamp"))
        bucket_name = "undated"
        if stamp is not None:
            bucket_name = "before" if stamp.date() < boundary else "after"
        item["_bucket"] = bucket_name
        target = {"before": before, "after": after, "undated": undated}[bucket_name]
        target.append(_session_from_dict(item))
    for item in report["per_session"]:
        item.pop("_bucket", None)
    return {
        "split_date": split_date,
        "rule": "sessions are grouped by the UTC date of their first timestamp; the split date itself is 'after'",
        "before": _summarize_sessions(before),
        "after": _summarize_sessions(after),
        "undated": _summarize_sessions(undated),
        "comparison_note": (
            "Compare prompt tokens per session and per call. "
            "Raw totals confound how much work was done in each period."
        ),
    }


def _session_from_dict(item: dict) -> SessionStats:
    tokens = item["tokens"]
    usage = Usage(
        input_tokens=tokens["input_tokens"],
        output_tokens=tokens["output_tokens"],
        cache_creation_input_tokens=tokens["cache_creation_input_tokens"],
        cache_read_input_tokens=tokens["cache_read_input_tokens"],
        cache_creation_5m_tokens=tokens["cache_creation_5m_tokens"],
        cache_creation_1h_tokens=tokens["cache_creation_1h_tokens"],
        ttl_split_known=bool(tokens["ttl_split_known"]),
        cache_creation_mismatch=bool(tokens["cache_creation_mismatch"]),
    )
    return SessionStats(
        session_id=item["session_id"],
        project_label=item["project"],
        role=item["role"],
        synthetic=item["synthetic"],
        first_timestamp=item.get("first_timestamp"),
        last_timestamp=item.get("last_timestamp"),
        calls=item["calls"],
        compactions=item["compactions"],
        usage=usage,
    )


def report_contains_canary(report: dict, canary: str) -> bool:
    blob = json.dumps(report)
    return canary in blob


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate Claude Code JSONL transcripts. Prompt text is dropped."
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="JSONL files or directories (searched for *.jsonl)",
    )
    parser.add_argument("--output", "-o", type=Path, help="Write the JSON report here. Default: stdout")
    parser.add_argument(
        "--split-date",
        help="UTC date YYYY-MM-DD. Sessions that start before this date are 'before'",
    )
    parser.add_argument(
        "--sessions",
        action="store_true",
        help="Include per-session rows. Still no prompt content. Omit to print totals only",
    )
    args = parser.parse_args(argv)
    report = analyze_paths(args.paths)
    if args.split_date:
        report["periods"] = split_periods(report, args.split_date)
    if not args.sessions:
        report = {key: value for key, value in report.items() if key != "per_session"}
        report["per_session"] = "omitted; pass --sessions to include session ids and token totals"
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")
    if report.get("synthetic") is True:
        print(
            "Note: this report includes synthetic fixture data. Do not publish it as real usage.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
