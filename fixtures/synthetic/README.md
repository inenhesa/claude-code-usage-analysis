# Synthetic transcripts

Every directory name starts with `SYNTHETIC`. None of these files are a Claude Code session from a real account. Reports set `"synthetic": true`. A canary string is planted in prompt text and must not appear in an analyzer report.

Regenerate the model-backed sessions:

```bash
python3 fixtures/synthetic/generate_fixtures.py
```

`generate_fixtures.py` calls `cc_usage_study.model.simulate` (physical prefix caching). Assistant lines are the non-compaction calls, each written twice so the deduper has to keep the final snapshot. Compaction calls are a `compact_boundary` plus an `isCompactSummary` user line, not a second compaction and not an assistant usage row. The summary request's prompt size is in `fixtures/synthetic/expected/generated-sessions.json` under `compaction_request_prompt_tokens_not_in_jsonl`. Whether real Claude Code writes that request's usage into the JSONL is unverified. OTel `query_source=compact` is the documented label.

| Project | What it is |
| --- | --- |
| `SYNTHETIC-project-alpha` | Small hand-written session for the original parser tests |
| `SYNTHETIC-01-before-default-95pct` | Long and short sessions at the about-967k trigger. The directory name says 95pct because that is the common shorthand. The trigger in the file is 967,000 |
| `SYNTHETIC-02-after-25pct` | The same shapes at 250,000 |
| `SYNTHETIC-03-compounding-62k` | Window 250,000 and percentage 25, plus one rework call per compaction |
| `SYNTHETIC-04-subagent-heavy` | CLI copy (distinct session ids) and SDK copy (shared parent `sessionId`, `isSidechain`, `agentId`). Totals match. Subagent `toolUseResult.usage` is not a pattern here; the edge project covers that rollup |
| `SYNTHETIC-05-edge-cases` | Streaming partials, `<synthetic>`, `isApiErrorMessage`, one boundary plus `isCompactSummary`, an orphaned duplicate, a resumed copy of the same `message.id` and `requestId`, and a sidechain that reuses the parent session id |

Do not publish fixture totals as usage.
