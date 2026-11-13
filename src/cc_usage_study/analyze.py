"""Aggregate Claude Code session transcripts without copying prompt text.

Transcripts live under ``~/.claude/projects/**/*.jsonl``. One API call is often
several JSONL lines that share ``message.id`` and ``requestId``. This parser
dedupes globally on ``(message.id, requestId)`` and keeps the most complete
snapshot: highest ``output_tokens``, then a non-null ``stop_reason``, then a
nested 5-minute/1-hour cache-write split. Output on a per-step line can be a
placeholder, so a high share of kept rows with no ``stop_reason`` means the
output total is a lower bound.

Subagent files often carry the parent's ``sessionId``. Role comes from
``isSidechain``, ``agentId``, or a ``subagents`` path segment, and each agent
is its own stream. Per-call averages and cache-rebuild checks use main
streams only.

Compactions count only ``type == system`` and ``subtype == compact_boundary``.
An ``isCompactSummary`` user line is the same event, not a second one.

``<synthetic>`` models and ``isApiErrorMessage`` lines are excluded.
``toolUseResult.usage`` is a subagent rollup and is not added.

Nothing in the report is copied from message content.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator
from zoneinfo import ZoneInfo

from cc_usage_study.pricing import (
    ATTRIBUTION_WEIGHTS_V2_1_317,
    TOKENIZER_GENERATION_NOTE,
    attribution_family,
    estimate_call_usd,
    tokenizer_generation,
)


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
    ttl_split_known: bool = True
    cache_creation_mismatch: bool = False

    @property
    def prompt_tokens(self) -> int:
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
        self.cache_creation_mismatch = (
            self.cache_creation_mismatch or other.cache_creation_mismatch
        )

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


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def usage_from_mapping(raw: dict) -> Usage | None:
    """Build a Usage from an API usage object. None if no token field is present."""
    if not isinstance(raw, dict):
        return None
    if not any(field_name in raw for field_name in TOKEN_FIELDS):
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
    return Usage(
        input_tokens=_int(raw.get("input_tokens")),
        output_tokens=_int(raw.get("output_tokens")),
        cache_creation_input_tokens=flat,
        cache_read_input_tokens=_int(raw.get("cache_read_input_tokens")),
        cache_creation_5m_tokens=five_m or 0,
        cache_creation_1h_tokens=one_h or 0,
        ttl_split_known=five_m is not None and one_h is not None,
        cache_creation_mismatch=mismatch,
    )


def _message_usage(record: dict) -> tuple[dict | None, str, str, str | None]:
    message = record.get("message")
    model = ""
    message_id = ""
    stop_reason = None
    usage = None
    if isinstance(message, dict):
        if isinstance(message.get("usage"), dict):
            usage = message["usage"]
        model = message.get("model") if isinstance(message.get("model"), str) else ""
        message_id = message.get("id") if isinstance(message.get("id"), str) else ""
        if isinstance(message.get("stop_reason"), str):
            stop_reason = message["stop_reason"]
    if usage is None and isinstance(record.get("usage"), dict):
        usage = record["usage"]
    if isinstance(record.get("stop_reason"), str):
        stop_reason = record["stop_reason"]
    return usage, model, message_id, stop_reason


def is_excluded_line(record: dict) -> bool:
    if record.get("isApiErrorMessage") is True:
        return True
    message = record.get("message")
    model = ""
    if isinstance(message, dict) and isinstance(message.get("model"), str):
        model = message["model"]
    if model == "<synthetic>":
        return True
    return False


def compaction_metadata(record: dict) -> dict | None:
    """Return metadata for a real boundary, or None.

    Only ``compact_boundary`` counts. Other subtypes containing "compact"
    are reported separately so a future microcompact marker is not added
    to the auto-compact total.
    """
    if record.get("type") == "system" and record.get("subtype") == "compact_boundary":
        meta = record.get("compactMetadata")
        if not isinstance(meta, dict):
            meta = record.get("compact_metadata")
        if not isinstance(meta, dict):
            meta = {}
        pre = meta.get("preTokens", meta.get("pre_tokens"))
        post = meta.get("postTokens", meta.get("post_tokens"))
        return {
            "trigger": meta.get("trigger") if isinstance(meta.get("trigger"), str) else None,
            "pre_tokens": _int(pre) if pre is not None else None,
            "post_tokens": _int(post) if post is not None else None,
        }
    return None


def is_other_compact_marker(record: dict) -> bool:
    subtype = record.get("subtype")
    return (
        isinstance(subtype, str)
        and "compact" in subtype.lower()
        and not (record.get("type") == "system" and subtype == "compact_boundary")
    )


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


def record_role(path: Path, record: dict) -> str:
    if record.get("isSidechain") is True or isinstance(record.get("agentId"), str):
        return "subagent"
    if "subagents" in path.parts:
        return "subagent"
    return "main"


def stream_id_for(path: Path, record: dict, role: str) -> str:
    session_id = record.get("sessionId") or record.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        session_id = path.stem.split(".orphaned-", 1)[0]
    if role != "subagent":
        return session_id
    agent = record.get("agentId")
    if not isinstance(agent, str) or not agent:
        stem = path.stem
        agent = stem[6:] if stem.startswith("agent-") else stem
    return f"{session_id}/agent-{agent}"


def iter_jsonl(path: Path) -> Iterator[tuple[int, dict | None, bool]]:
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


def _is_orphaned(path: Path) -> bool:
    name = path.name
    return ".orphaned-" in name or ".superseded-" in name or ".jsonl.superseded" in name


def _completeness(usage: Usage, stop_reason: str | None) -> tuple[int, int, int]:
    return (
        usage.output_tokens,
        1 if stop_reason else 0,
        1 if usage.ttl_split_known else 0,
    )


def _weight_usage(usage: Usage, *, read_multiplier: float, output_multiplier: float, family: str) -> dict:
    """Token-equivalent weights. Unknown write TTLs are priced as 1-hour writes
    and flagged. That assumption is labeled. It is not a meter weight.
    """
    assumed = not usage.ttl_split_known and usage.cache_creation_input_tokens > 0
    if usage.ttl_split_known:
        write_cost = usage.cache_creation_5m_tokens * 1.25 + usage.cache_creation_1h_tokens * 2.0
    else:
        write_cost = usage.cache_creation_input_tokens * 2.0
    fresh = usage.input_tokens
    read = usage.cache_read_input_tokens
    output = usage.output_tokens
    api = fresh + read * read_multiplier + write_cost + output * output_multiplier
    weights = ATTRIBUTION_WEIGHTS_V2_1_317
    factor = float(weights["model_factor"].get(family, 1.0))
    attribution = factor * (
        fresh * weights["input"]
        + read * weights["read"]
        + usage.cache_creation_input_tokens * weights["write"]
        + output * weights["output"]
    )
    return {
        "api": api,
        "raw_prompt": float(usage.prompt_tokens),
        "read_full": fresh + read * 1.0 + write_cost + output * output_multiplier,
        "h_zero": fresh + write_cost + output * output_multiplier,
        "attribution_v2_1_317": attribution,
        "attribution_disclaimer": weights["disclaimer"],
        "assumed_1h_writes": assumed,
    }


def analyze_paths(paths: Iterable[Path]) -> dict:
    files: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            files.extend(sorted(p for p in path.rglob("*.jsonl") if p.is_file()))
        elif path.is_file() and path.suffix == ".jsonl":
            files.append(path)
    files = sorted(set(files))

    stats = {
        "files": 0,
        "lines": 0,
        "records": 0,
        "parse_errors": 0,
        "excluded_lines": 0,
        "usage_records_before_dedup": 0,
        "duplicate_records_dropped": 0,
        "orphaned_files": 0,
        "other_compact_markers": 0,
    }
    kept: dict[tuple, dict] = {}
    compactions: dict[tuple, dict] = {}
    synthetic_any = False

    for path in files:
        stats["files"] += 1
        orphaned = _is_orphaned(path)
        if orphaned:
            stats["orphaned_files"] += 1
        label = project_label_for(path)
        path_synthetic = "synthetic" in str(path).lower()
        for lineno, record, failed in iter_jsonl(path):
            stats["lines"] += 1
            if failed or record is None:
                stats["parse_errors"] += 1
                continue
            stats["records"] += 1
            record_synthetic = path_synthetic or record.get("synthetic") is True
            synthetic_any = synthetic_any or record_synthetic
            if is_excluded_line(record):
                stats["excluded_lines"] += 1
                continue
            role = record_role(path, record)
            stream = stream_id_for(path, record, role)
            timestamp = record.get("timestamp") if isinstance(record.get("timestamp"), str) else None
            entrypoint = record.get("entrypoint") if isinstance(record.get("entrypoint"), str) else None
            version = record.get("version") if isinstance(record.get("version"), str) else None
            boundary = compaction_metadata(record)
            if boundary is not None:
                uuid = record.get("uuid") if isinstance(record.get("uuid"), str) else ""
                pre = boundary["pre_tokens"]
                key = ("compact", stream, uuid or f"{timestamp}:{pre}:{lineno}")
                if key not in compactions:
                    compactions[key] = {
                        "stream_id": stream,
                        "role": role,
                        "project": label,
                        "timestamp": timestamp,
                        "synthetic": record_synthetic,
                        "orphaned_only": orphaned,
                        **boundary,
                    }
                else:
                    compactions[key]["orphaned_only"] = (
                        compactions[key]["orphaned_only"] and orphaned
                    )
                continue
            if is_other_compact_marker(record):
                stats["other_compact_markers"] += 1
            raw_usage, model, message_id, stop_reason = _message_usage(record)
            if raw_usage is None:
                continue
            usage = usage_from_mapping(raw_usage)
            if usage is None:
                continue
            stats["usage_records_before_dedup"] += 1
            request_id = record.get("requestId") if isinstance(record.get("requestId"), str) else ""
            if message_id:
                key = ("msg", message_id, request_id)
            else:
                uuid = record.get("uuid") if isinstance(record.get("uuid"), str) else ""
                key = ("anon", stream, uuid or f"{path}:{lineno}")
            speed = raw_usage.get("speed") if isinstance(raw_usage.get("speed"), str) else None
            item = {
                "stream_id": stream,
                "role": role,
                "project": label,
                "model": model,
                "timestamp": timestamp,
                "entrypoint": entrypoint,
                "version": version,
                "synthetic": record_synthetic,
                "usage": usage,
                "stop_reason": stop_reason,
                "speed": speed,
                "orphaned_only": orphaned,
                "file": str(path),
            }
            previous = kept.get(key)
            if previous is None:
                kept[key] = item
                continue
            stats["duplicate_records_dropped"] += 1
            previous["orphaned_only"] = previous["orphaned_only"] and orphaned
            if _completeness(usage, stop_reason) > _completeness(
                previous["usage"], previous["stop_reason"]
            ):
                item["stream_id"] = previous["stream_id"]
                item["role"] = previous["role"]
                item["project"] = previous["project"]
                item["orphaned_only"] = previous["orphaned_only"]
                if not item["entrypoint"]:
                    item["entrypoint"] = previous["entrypoint"]
                if not item["version"]:
                    item["version"] = previous["version"]
                if not item["timestamp"]:
                    item["timestamp"] = previous["timestamp"]
                kept[key] = item

    calls = list(kept.values())
    boundaries = list(compactions.values())
    return _report(stats, calls, boundaries, synthetic_any)


def _median(values: list[int]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2


def _empty_usage() -> Usage:
    return Usage(ttl_split_known=False)


def _sum_usage(calls: list[dict]) -> Usage:
    total = Usage(ttl_split_known=True)
    if not calls:
        total.ttl_split_known = False
        return total
    for call in calls:
        total.add(call["usage"])
    return total


def _role_block(calls: list[dict], boundaries: list[dict], role: str) -> dict:
    chosen = [call for call in calls if call["role"] == role]
    bounds = [item for item in boundaries if item["role"] == role]
    usage = _sum_usage(chosen)
    prompts = []
    by_stream: dict[str, int] = defaultdict(int)
    for call in chosen:
        by_stream[call["stream_id"]] += call["usage"].prompt_tokens
    prompts = list(by_stream.values())
    calls_n = len(chosen)
    return {
        "calls": calls_n,
        "sessions": len(by_stream),
        "compactions": len(bounds),
        "tokens": usage.as_dict(),
        "prompt_tokens_per_call": (usage.prompt_tokens / calls_n) if calls_n else None,
        "prompt_tokens_per_session_mean": (sum(prompts) / len(prompts)) if prompts else None,
        "prompt_tokens_per_session_median": _median(prompts),
        "cache_read_share_of_prompt": (
            usage.cache_read_input_tokens / usage.prompt_tokens if usage.prompt_tokens else None
        ),
    }


def _rebuilds(calls: list[dict], boundaries: list[dict]) -> dict:
    """Classify cache misses on main streams only."""
    counts = {
        "post_compaction_rebuild": 0,
        "ttl_expired_rebuild": 0,
        "other_miss": 0,
        "rewritten_tokens": {
            "post_compaction_rebuild": 0,
            "ttl_expired_rebuild": 0,
            "other_miss": 0,
        },
    }
    by_stream: dict[str, list] = defaultdict(list)
    for call in calls:
        if call["role"] != "main":
            continue
        by_stream[call["stream_id"]].append(("call", call))
    for boundary in boundaries:
        if boundary["role"] != "main":
            continue
        by_stream[boundary["stream_id"]].append(("boundary", boundary))
    for events in by_stream.values():
        events.sort(key=lambda item: (item[1].get("timestamp") or "", 0 if item[0] == "boundary" else 1))
        previous = None
        boundary_since = False
        for kind, item in events:
            if kind == "boundary":
                boundary_since = True
                continue
            if previous is None:
                previous = item
                boundary_since = False
                continue
            usage = item["usage"]
            prev_usage = previous["usage"]
            prev_prompt = prev_usage.prompt_tokens
            miss = prev_prompt > 0 and usage.cache_read_input_tokens < 0.5 * prev_prompt
            label = None
            if boundary_since and miss:
                label = "post_compaction_rebuild"
            elif miss:
                gap = None
                left = _parse_time(previous.get("timestamp"))
                right = _parse_time(item.get("timestamp"))
                if left and right:
                    gap = (right - left).total_seconds() / 60.0
                ttl = 60.0 if prev_usage.cache_creation_1h_tokens > 0 else 5.0
                if gap is not None and gap > ttl:
                    label = "ttl_expired_rebuild"
                else:
                    label = "other_miss"
            if label:
                counts[label] += 1
                rewritten = usage.input_tokens + usage.cache_creation_input_tokens
                counts["rewritten_tokens"][label] += rewritten
            previous = item
            boundary_since = False
    return counts


def _five_hour_blocks(calls: list[dict]) -> list[dict]:
    main = [call for call in calls if call["role"] == "main" and _parse_time(call.get("timestamp"))]
    main.sort(key=lambda call: call["timestamp"] or "")
    blocks: list[dict] = []
    for call in main:
        stamp = _parse_time(call["timestamp"])
        assert stamp is not None
        if not blocks or stamp >= blocks[-1]["_end"]:
            blocks.append(
                {
                    "start": stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "_end": stamp + timedelta(hours=5),
                    "calls": [],
                }
            )
        blocks[-1]["calls"].append(call)
    rendered = []
    for block in blocks:
        chosen = block["calls"]
        usage = _sum_usage(chosen)
        # Weight each call, then sum. Haiku tiering is not involved here.
        # Read multiplier 0.1 is the standard published rate, not a meter weight.
        totals = {name: 0.0 for name in ("api", "raw_prompt", "read_full", "h_zero", "attribution_v2_1_317")}
        assumed = False
        for call in chosen:
            family = attribution_family(call["model"] or "")
            weights = _weight_usage(
                call["usage"],
                read_multiplier=0.1,
                output_multiplier=5.0,
                family=family,
            )
            assumed = assumed or weights["assumed_1h_writes"]
            for name in totals:
                totals[name] += weights[name]
        rendered.append(
            {
                "start": block["start"],
                "end": block["_end"].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "calls": len(chosen),
                "tokens": usage.as_dict(),
                "weights": {
                    **{key: round(value, 3) for key, value in totals.items()},
                    "read_multiplier_for_api": 0.1,
                    "output_multiplier_for_api": 5.0,
                    "assumed_1h_writes": assumed,
                    "note": (
                        "api, read_full, and h_zero are published-price shapes or labeled "
                        "hypotheses. attribution_v2_1_317 is a third-party client-side "
                        "reading, not the server meter. None of these is the five-hour bar."
                    ),
                },
            }
        )
    return rendered


def _price_models(calls: list[dict]) -> dict[str, dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for call in calls:
        grouped[call["model"] or "unknown"].append(call)
    out = {}
    for model, rows in sorted(grouped.items()):
        usage = _sum_usage(rows)
        if model == "unknown":
            estimate = {"usd": None, "reason": "model id missing"}
        else:
            usd_total = 0.0
            reasons = []
            priced = 0
            for call in rows:
                usage_call = call["usage"]
                five = usage_call.cache_creation_5m_tokens if usage_call.ttl_split_known else None
                one = usage_call.cache_creation_1h_tokens if usage_call.ttl_split_known else None
                if not usage_call.ttl_split_known and usage_call.cache_creation_input_tokens:
                    reasons.append("cache write TTL split missing on at least one call")
                    continue
                usd, reason = estimate_call_usd(
                    model,
                    input_tokens=usage_call.input_tokens,
                    output_tokens=usage_call.output_tokens,
                    cache_read_tokens=usage_call.cache_read_input_tokens,
                    cache_write_5m_tokens=five if five is not None else 0,
                    cache_write_1h_tokens=one if one is not None else 0,
                    prompt_tokens=usage_call.prompt_tokens,
                    speed=call.get("speed"),
                )
                if usd is None:
                    reasons.append(reason or "unpriced")
                    continue
                usd_total += usd
                priced += 1
            if reasons and priced == 0:
                estimate = {"usd": None, "reason": reasons[0]}
            elif reasons:
                estimate = {
                    "usd": round(usd_total, 6),
                    "reason": reasons[0],
                    "priced_calls": priced,
                    "note": "per-request list price; Haiku 5.5 tier is decided per call. Not a subscription bill.",
                }
            else:
                estimate = {
                    "usd": round(usd_total, 6),
                    "reason": None,
                    "priced_calls": priced,
                    "note": "per-request list price from the 2026-11-13 snapshot. Not a subscription bill.",
                }
        out[model] = {"calls": len(rows), "tokens": usage.as_dict(), "list_price_estimate": estimate}
    return out


def _group_counts(calls: list[dict], field_name: str) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for call in calls:
        grouped[call.get(field_name) or "unknown"].append(call)
    return {
        name: {"calls": len(rows), "tokens": _sum_usage(rows).as_dict()}
        for name, rows in sorted(grouped.items())
    }


def _sessions(calls: list[dict], boundaries: list[dict]) -> list[dict]:
    streams = sorted({call["stream_id"] for call in calls} | {item["stream_id"] for item in boundaries})
    rows = []
    for stream in streams:
        chosen = [call for call in calls if call["stream_id"] == stream]
        bounds = [item for item in boundaries if item["stream_id"] == stream]
        sample = chosen[0] if chosen else bounds[0]
        stamps = [call["timestamp"] for call in chosen if call.get("timestamp")]
        stamps += [item["timestamp"] for item in bounds if item.get("timestamp")]
        stamps = [stamp for stamp in stamps if stamp]
        usage = _sum_usage(chosen)
        rows.append(
            {
                "session_id": stream,
                "project": sample["project"],
                "role": sample["role"],
                "synthetic": bool(sample.get("synthetic")),
                "first_timestamp": min(stamps) if stamps else None,
                "last_timestamp": max(stamps) if stamps else None,
                "models": sorted({call["model"] for call in chosen if call.get("model")}),
                "entrypoints": sorted({call["entrypoint"] for call in chosen if call.get("entrypoint")}),
                "calls": len(chosen),
                "compactions": len(bounds),
                "pre_tokens": [item["pre_tokens"] for item in bounds if item.get("pre_tokens") is not None],
                "post_tokens": [item["post_tokens"] for item in bounds if item.get("post_tokens") is not None],
                "tokens": usage.as_dict(),
            }
        )
    rows.sort(key=lambda item: (item["first_timestamp"] or "", item["session_id"]))
    return rows


def _report(stats: dict, calls: list[dict], boundaries: list[dict], synthetic_any: bool) -> dict:
    no_stop = sum(1 for call in calls if not call.get("stop_reason"))
    generations = {tokenizer_generation(call["model"]) for call in calls if call.get("model")}
    generations.discard(None)
    tokenizer_warning = None
    if "4.7+" in generations and "earlier" in generations:
        tokenizer_warning = TOKENIZER_GENERATION_NOTE
    main = _role_block(calls, boundaries, "main")
    sub = _role_block(calls, boundaries, "subagent")
    combined_usage = _sum_usage(calls)
    call_rows = [
        {
            "stream_id": call["stream_id"],
            "role": call["role"],
            "project": call["project"],
            "model": call["model"],
            "timestamp": call["timestamp"],
            "entrypoint": call["entrypoint"],
            "version": call["version"],
            "tokens": call["usage"].as_dict(),
        }
        for call in calls
    ]
    boundary_rows = [
        {
            "stream_id": item["stream_id"],
            "role": item["role"],
            "project": item["project"],
            "timestamp": item["timestamp"],
            "trigger": item["trigger"],
            "pre_tokens": item["pre_tokens"],
            "post_tokens": item["post_tokens"],
        }
        for item in boundaries
    ]
    return {
        "synthetic": synthetic_any,
        "privacy": "aggregates only; prompt content is not copied",
        "files": stats["files"],
        "lines": stats["lines"],
        "records": stats["records"],
        "parse_errors": stats["parse_errors"],
        "excluded_lines": stats["excluded_lines"],
        "usage_records_before_dedup": stats["usage_records_before_dedup"],
        "duplicate_records_dropped": stats["duplicate_records_dropped"],
        "orphaned_files": stats["orphaned_files"],
        "orphaned_only_calls": sum(1 for call in calls if call["orphaned_only"]),
        "calls": len(calls),
        "sessions": len({call["stream_id"] for call in calls} | {item["stream_id"] for item in boundaries}),
        "compactions": len(boundaries),
        "other_compact_markers": stats["other_compact_markers"],
        "output_without_stop_reason_share": (no_stop / len(calls)) if calls else None,
        "output_tokens_note": (
            "Per-step output_tokens can be a placeholder. When "
            "output_without_stop_reason_share is high, treat output as a lower bound."
        ),
        "tokenizer_warning": tokenizer_warning,
        "totals": {
            **_role_block(calls, boundaries, "main"),
            "calls": len(calls),
            "sessions": len({call["stream_id"] for call in calls}),
            "compactions": len(boundaries),
            "tokens": combined_usage.as_dict(),
            "note": "totals.tokens sums main and subagent calls. prompt_tokens_per_call in main is the per-call average to use.",
        },
        "main": main,
        "subagent": sub,
        "cache_rebuilds_main": _rebuilds(calls, boundaries),
        "compaction_markers": {
            "count": len(boundaries),
            "triggers": sorted({item["trigger"] for item in boundaries if item.get("trigger")}),
            "pre_tokens": [item["pre_tokens"] for item in boundaries if item.get("pre_tokens") is not None],
            "post_tokens": [item["post_tokens"] for item in boundaries if item.get("post_tokens") is not None],
        },
        "by_model": _price_models(calls),
        "by_entrypoint": _group_counts(calls, "entrypoint"),
        "by_version": _group_counts(calls, "version"),
        "by_day": _by_day(calls),
        "five_hour_blocks": _five_hour_blocks(calls),
        "per_session": _sessions(calls, boundaries),
        "call_rows": call_rows,
        "boundary_rows": boundary_rows,
    }


def _by_day(calls: list[dict]) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for call in calls:
        stamp = _parse_time(call.get("timestamp"))
        day = stamp.astimezone(timezone.utc).date().isoformat() if stamp else "undated"
        grouped[day].append(call)
    return {
        day: {"calls": len(rows), "tokens": _sum_usage(rows).as_dict()}
        for day, rows in sorted(grouped.items())
    }


def split_periods(report: dict, split_date: str, tz_name: str = "UTC") -> dict:
    """Split calls by timestamp. The split date itself is 'after'.

    A stream that crosses the date contributes calls to both sides. Session
    counts are streams with at least one call in that side. Compactions use
    their own timestamps. Default timezone is UTC. Pass Europe/Lisbon for a
    local calendar day.
    """
    zone = ZoneInfo(tz_name)
    boundary = datetime.fromisoformat(split_date).date()
    buckets: dict[str, list[dict]] = {"before": [], "after": [], "undated": []}
    bound_buckets: dict[str, list[dict]] = {"before": [], "after": [], "undated": []}

    def side(timestamp: str | None) -> str:
        stamp = _parse_time(timestamp)
        if stamp is None:
            return "undated"
        local_date = stamp.astimezone(zone).date()
        return "before" if local_date < boundary else "after"

    for row in report.get("call_rows", []):
        usage = Usage(
            input_tokens=row["tokens"]["input_tokens"],
            output_tokens=row["tokens"]["output_tokens"],
            cache_creation_input_tokens=row["tokens"]["cache_creation_input_tokens"],
            cache_read_input_tokens=row["tokens"]["cache_read_input_tokens"],
            cache_creation_5m_tokens=row["tokens"]["cache_creation_5m_tokens"],
            cache_creation_1h_tokens=row["tokens"]["cache_creation_1h_tokens"],
            ttl_split_known=bool(row["tokens"]["ttl_split_known"]),
            cache_creation_mismatch=bool(row["tokens"]["cache_creation_mismatch"]),
        )
        buckets[side(row.get("timestamp"))].append(
            {**row, "usage": usage, "role": row["role"], "stream_id": row["stream_id"]}
        )
    for row in report.get("boundary_rows", []):
        bound_buckets[side(row.get("timestamp"))].append(row)

    def summarize(name: str) -> dict:
        chosen = buckets[name]
        bounds = bound_buckets[name]
        block = _role_block(chosen, bounds, "main")
        # Session median in the historical test is over every stream in the
        # period, main and subagent. Keep that, and also expose the main-only block.
        by_stream: dict[str, int] = defaultdict(int)
        for call in chosen:
            by_stream[call["stream_id"]] += call["usage"].prompt_tokens
        prompts = list(by_stream.values())
        usage = _sum_usage(chosen)
        return {
            "sessions": len(by_stream),
            "calls": len(chosen),
            "compactions": len(bounds),
            "tokens": usage.as_dict(),
            "prompt_tokens_per_call": block["prompt_tokens_per_call"],
            "prompt_tokens_per_session_mean": (sum(prompts) / len(prompts)) if prompts else None,
            "prompt_tokens_per_session_median": _median(prompts),
            "cache_read_share_of_prompt": (
                usage.cache_read_input_tokens / usage.prompt_tokens if usage.prompt_tokens else None
            ),
            "main_calls": block["calls"],
        }

    return {
        "split_date": split_date,
        "timezone": tz_name,
        "rule": (
            "calls are grouped by the local date of their own timestamp; "
            "the split date itself is 'after'. Streams that cross the date "
            "appear in both sides."
        ),
        "before": summarize("before"),
        "after": summarize("after"),
        "undated": summarize("undated"),
        "comparison_note": (
            "Compare prompt tokens per session and per main call. "
            "Raw totals confound how much work was done in each period. "
            "A client switch in the same week is a separate variable; split by entrypoint too."
        ),
    }


def report_contains_canary(report: dict, canary: str) -> bool:
    return canary in json.dumps(report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate Claude Code JSONL transcripts. Prompt text is dropped."
    )
    parser.add_argument("paths", nargs="+", type=Path, help="JSONL files or directories")
    parser.add_argument("--output", "-o", type=Path, help="Write the JSON report here. Default: stdout")
    parser.add_argument(
        "--split-date",
        help="YYYY-MM-DD. Calls before this local date are 'before'. The date itself is after.",
    )
    parser.add_argument(
        "--split-tz",
        default="UTC",
        help="Timezone for --split-date. Default UTC. Europe/Lisbon matches a Lisbon calendar day.",
    )
    parser.add_argument("--sessions", action="store_true", help="Include per-session rows")
    parser.add_argument(
        "--calls",
        action="store_true",
        help="Include per-call token rows. Still no prompt content.",
    )
    args = parser.parse_args(argv)
    report = analyze_paths(args.paths)
    if args.split_date:
        report["periods"] = split_periods(report, args.split_date, args.split_tz)
    if not args.calls:
        report.pop("call_rows", None)
        report.pop("boundary_rows", None)
    if not args.sessions:
        report.pop("per_session", None)
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
