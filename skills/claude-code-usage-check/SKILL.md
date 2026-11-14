---
name: claude-code-usage-check
description: Check your Claude Code logs and suggest settings that fit how you work. Use when the user wants a usage self-check, asks which settings would help, or wants to know what is using up their limits.
---

# Claude Code usage self-check

Everyone uses Claude Code differently. Score this user's own logs, and recommend only what fits how they work. Do not give general advice that their data does not support.

## Procedure

1. Clone `https://github.com/inevolin/claude-code-usage-analysis` into a temp folder, or use a local copy if one already exists. From that folder:

```bash
python3 -m pip install -e .
OUT=$(mktemp -d)
python3 -m cc_usage_study.analyze ~/.claude/projects --summary --sessions --calls -o "$OUT/report.json"
```

Write the report file outside any git repository. If `~/.claude/projects` is missing, stop and say so.

2. Use only that JSON report: summary numbers, timestamps, token counts, entrypoints, and `compact_boundary` metadata (`trigger`, `pre_tokens`, `timestamp`). Do not read, paste, or upload the raw text of chats. If a field you need is missing from the JSON, you may compute it from timestamps and `compact_boundary` metadata only. If you still cannot tell, say so.

3. Do not report money amounts. Use percentages and multipliers (cache-weighted usage: tokens counted the way Anthropic charges for them). Ignore any `usd` or `list_price` fields in the JSON.

## Habits to score

Treat the cache time limit as 5 minutes when `cache_creation_5m_tokens` are most of the writes, and 1 hour when `cache_creation_1h_tokens` are.

- Continuing a session after the cache expired: `cache_rebuilds_main.ttl_expired_rebuild` (and `rewritten_tokens.ttl_expired_rebuild`). That count already uses a gap over 5 minutes on a 5-minute cache, and over 1 hour on a 1-hour cache.
- `/compact` after a break, or before: compare `boundary_rows` (`timestamp`, `trigger`) with gaps between `call_rows` timestamps in the same `stream_id`. A compact just after a gap past the cache time limit is "after the break". A compact followed by such a gap is "before".
- Many short sessions started close together (a rough sign of `/clear` between related work) instead of staying in one continued session.
- `sdk-cli` (`claude -p`) sessions continued across gaps, instead of a new session each run.
- Which app they used, in `by_entrypoint`: `cli` is the interactive Claude Code CLI, `sdk-cli` is `claude -p`, `t3-code` is T3 Code, `claude-desktop-3p` is Claude Desktop's Code tab. Prefer the CLI, or T3 Code. Desktop's Code tab used at least 1.80 times the CLI in this study. The Claude chat app was not measured.
- Sessions where one request reaches 300,000 tokens: use a single call's `prompt_tokens` or a compact `pre_tokens`. Do not use the session-sum of `prompt_tokens`.

## Report

Write a short report that fits this user:

1. The top 3 habits costing them the most, with their own percentages or multipliers.
2. Concrete changes that fit their pattern, chosen only from: `/compact` before breaks, `/clear` plus a 3-line handoff when they are already back, a new `claude -p` session each scheduled run, prefer the CLI or T3 Code over Desktop's Code tab. Mention `--autocompact` only as an unproven idea, and only if their sessions actually get large enough for it to matter.
3. What you could not tell from the logs.
