---
name: claude-code-usage-check
description: Evaluate your Claude Code transcripts and recommend personal usage settings. Use when the user wants a self-check of their Claude Code habits, asks which settings would reduce their usage, or wants to know what is burning their limits.
---

# Claude Code usage self-check

Everyone uses Claude Code differently. Score this user's own logs and recommend only what fits their pattern. Do not give generic advice that their data does not support.

## Procedure

1. Clone `https://github.com/inevolin/claude-code-usage-analysis` into a temp directory, or use a local checkout if one already exists. From that directory:

```bash
python3 -m pip install -e .
OUT=$(mktemp -d)
python3 -m cc_usage_study.analyze ~/.claude/projects --summary --sessions --calls -o "$OUT/report.json"
```

Write the JSON outside any git repository. If `~/.claude/projects` is missing, stop and say so.

2. Work only from that JSON report: aggregates, timestamps, token counts, entrypoints, and `compact_boundary` metadata (`trigger`, `pre_tokens`, `timestamp`). Never read, paste, or upload raw transcript message text. If a needed field is missing from the JSON, you may compute it from timestamps and `compact_boundary` metadata only. If you still cannot tell, say so.

3. Do not report dollar amounts. Use percentages and multipliers (cache-weighted usage). Ignore any `usd` or `list_price` fields in the JSON.

## Habits to score

Treat cache TTL as 5 minutes when `cache_creation_5m_tokens` dominate, and 1 hour when `cache_creation_1h_tokens` do.

- Resumes after the cache expired: `cache_rebuilds_main.ttl_expired_rebuild` (and `rewritten_tokens.ttl_expired_rebuild`). That count already uses a gap greater than 5 minutes on a 5-minute cache and greater than 1 hour on a 1-hour cache.
- `/compact` after a break vs before: compare `boundary_rows` (`timestamp`, `trigger`) with gaps between `call_rows` timestamps in the same `stream_id`. A compact just after a gap past the TTL is "after the break"; a compact followed by such a gap is "before".
- Many short sessions started close together (a proxy for `/clear` between related work) vs staying in one warm session.
- `sdk-cli` (`claude -p`) sessions resumed across gaps vs a fresh session each run.
- Client mix in `by_entrypoint`: `cli` is the interactive Claude Code CLI, `sdk-cli` is `claude -p`, `t3-code` is T3 Code, `claude-desktop-3p` is Claude Desktop's Code tab. Prefer the CLI, or T3 Code. Desktop's Code tab was 1.80x the CLI in this study (a floor). The Claude chat app was not measured.
- Sessions whose peak request size reaches 300k tokens: use a single call's `prompt_tokens` or a compact `pre_tokens`, never the session-sum of `prompt_tokens`.

## Report

Write a short report tailored to this user:

1. The top 3 habits costing them the most, with their own percentages or multipliers.
2. Concrete changes that fit their pattern, chosen only from: `/compact` before breaks, `/clear` plus a 3-line handoff when they are already back, a fresh `claude -p` each scheduled run, prefer the CLI or T3 Code over Desktop's Code tab. Mention `--autocompact` only as an unproven hypothesis, and only if their sessions actually get large enough for it to matter.
3. What you could not tell from the logs.
