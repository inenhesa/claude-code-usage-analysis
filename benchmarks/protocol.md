# Controlled benchmark protocol

`--execute` spends quota. The default is a dry run.

## Live runs, 13 November 2026

Fourteen live Claude Opus 5.5 sessions were run on a dedicated study API key, effort medium, 5-minute cache, Claude CLI v2.1.325 unless noted. Every number is in `outputs/live_experiments.json`, rebuilt from the raw transcripts by `python3 -m cc_usage_study.experiments extract`. Usage is in cache-weighted units (input 1, 5-minute write 1.25, read 0.05, output 5), and every arm is n=1 with no pause longer than the cache window.

| Experiment | Runs | Result (cache-weighted, vs the experiment's baseline) |
| --- | ---: | --- |
| 1A. `LedgerKit v3.2` 4-phase build, `P-doc` | 5 arms | Default 1M = 100 (0 compactions, 792k units). 180k: 100, a tie (2). 250,000 window + percentage 25: +13.6% (6). 140k: +18.1% (4). 100k: +25.2% (7) |
| 1B. `median_bug`, `slice_bug`, `keep_currency` | 2 arms | 3 isolated sessions = 100%. 1 shared `--resume` session 72.3% (-27.7%; -21.1% to -32.0% across read weights), with 7.0% more raw tokens |
| 2. Client comparison, first request and `noisy_bughunt` | 7 | See `benchmarks/client_comparison.md` |

Every 1A arm passed its visible tests and the functional checker, and reported all 12 planted `CHECK_ANOMALY` rows across phases 1-3. The 140k arm's `RECONCILIATION.md` does not repeat them, because the phase 4 prompt did not ask. The 180k and 100k arms log 4 `INV-02` hits each: they replaced `datetime.datetime.now()` with `clock.now()`, which the checker's AST `.now()` rule still matches. The other three arms deleted the unused `_stamp` line. No arm calls `datetime.now()` in a module. Both 1B arms passed every test.

### The trigger, from the binary

Claude Code v2.1.325, functions `tle` and `WX`:

```text
effective_window = window - min(maxOutputTokens, 20000)
threshold = effective_window - 13000                                       (no override)
threshold = min(floor(effective_window * pct / 100), effective_window - 13000)   (CLAUDE_AUTOCOMPACT_PCT_OVERRIDE)
```

With `maxOutputTokens` 128,000 that is window minus 33,000: 1M fires at 967,000, 180k at 147,000, 140k at 107,000, 100k at 67,000. A 250,000 window with percentage 25 fires at floor(230,000 x 0.25) = 57,500, not the naive 62.5k. The simulator's `effective_trigger` uses the same formula. The live compound arm logged `preTokens` 89,402, 62,687, 98,668, 57,993, 68,068, and 57,044. The two lowest sit at the 57,500 line. `preTokens` is logged by a different counter from the trigger check, so fires can land a few thousand tokens under the line or overshoot after a large tool batch.

### Hidden compaction-summary cost

The summary request a compaction sends writes no assistant row. Claude Code's own `cost_state.totalCostUSD` includes it. In 1A the gap between `cost_state` and the deduplicated assistant rows is zero on the default arm and 11.8%, 13.9%, 24.3%, and 25.5% of session usage on the 180k, 140k, compound, and 100k arms (about 94k, 130k, 219k, and 253k input-token equivalents). Across 19 compactions that is about 36.6k units each on average, with phase means from about 19k to 85k. It is a residual with no token breakdown, not a measured token count. Price compaction from `cost_state` or OTel `query_source`, never from assistant rows alone.

Generator scales are still design targets. The live pilot used one repeat per arm, not three, so it supports directions and mechanisms, not significance.

### Validity gate in the live pilot

Rule 2 below requires the first `preTokens` within 15 percent of the trigger. Two live arms fail it and are reported as mechanism evidence only:

| Arm | Trigger | First `preTokens` | Off by | Gate |
| --- | ---: | ---: | ---: | --- |
| 180k window | 147,000 | 170,089 | +15.7% | fails |
| 140k window | 107,000 | 107,286 | +0.3% | passes |
| 100k window | 67,000 | 73,041 | +9.0% | passes |
| 250,000 window + percentage 25 | 57,500 | 89,402 | +55.5% | fails |

`validity_gate_passed` in `outputs/live_experiments.json` records this per arm.

## What a run is allowed to claim

An arm counts toward a compaction claim only when `valid_for_compaction_claims` is true:

1. The low arm logged at least one `compact_boundary`.
2. The first boundary's `pre_tokens` is within 15 percent of the configured trigger. Revisit that band after the pilot.
3. The run did not time out, and no `rate_limit_event` rejected it.
4. `/context` `raw_max_tokens` at the start matches the arm's intended window.

Zero boundaries means the arm is noise. The three smoke tasks (`median_bug`, `slice_bug`, `keep_currency`) are scored so the harness can tell a pass from a fail. They are excluded from the default grid. They cannot reach 250,000 tokens.

## Tiers

| Tier | Baseline | Low arm | Why |
| --- | --- | --- | --- |
| S | window 400,000 | window 100,000 | Same 4:1 ratio as 25 percent of 1M, at less context |
| F | `--autocompact auto` (about 967K on native 1M) | window 250,000, and a separate percentage-25 arm | The long-session comparison |
| X | as F | window 250,000 and percentage 25 | The compounding trap: fires at 57,500 in Claude Code v2.1.325 and in the model |

Do not treat 95 percent as a distinct arm. The percentage override cannot raise the default, so 95 can be a no-op.

## Tasks

Generated, deterministic, seed 1337:

- `ledger_migration`: many similar modules, invariants that are easy to violate late.
- `noisy_bughunt`: verbose logs, decoy stacks, three chained bugs.
- `steered_feature`: six checkpoint messages. A later decision must survive compaction.

Placement variants:

- `P-claude`: invariants in project-root `CLAUDE.md` (re-injected).
- `P-prompt`: invariants only in the initial prompt.
- `P-doc`: invariants only in a long `docs/ARCHITECTURE.md`.
- `P-hook`: `P-doc` plus a PreCompact hook whose stdout says to preserve each probe id.

Hidden tests are copied in at score time. They are not in the workdir the agent edits. Checkers in `benchmarks/checkers.py` count violations. Visible tests are hashed. Editing them fails the arm.

Hooks, booleans only: PreCompact and PostCompact may record which probe ids appear in the summary. Do not store the summary text. SessionStart on `compact` records a timestamp. Set `idleCompaction` false so a pause is not a second trigger.

## Launch

`claude -p` is entrypoint `sdk-cli`. Token totals from it are usable. A five-hour meter delta from it is not an interactive-CLI result. Public issue anthropics/claude-code#97074 reports a higher five-hour burn per token for `sdk-cli` than for `cli`. That is their report, not an Anthropic rate. The `tui` method drives interactive `claude` in tmux for meter questions.

Isolation without `--bare` (bare mode needs an API key and skips the subscription login):

- `--setting-sources project`
- `--strict-mcp-config` and an empty MCP config
- `--session-id` so the JSONL path is known
- child environment clears `DISABLE_AUTO_COMPACT`, `CLAUDE_CODE_DISABLE_1M_CONTEXT`, `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`, and `CLAUDE_CODE_AUTO_COMPACT_WINDOW` before the arm sets its own
- record `system/init` and a `/context` snapshot (`raw_max_tokens`, memory files, MCP tools, skills)

The meter is not in the transcript. It is readable:

- `claude -p "/usage"` returns `usage_report` on a claude.ai credential. Confirm on the pilot that this call adds no model request.
- A status-line command can append `rate_limits.five_hour.used_percentage` and `rate_limits.seven_day.used_percentage`.
- Settings > Usage remains the cross-check.

Optional OTel: `CLAUDE_CODE_ENABLE_TELEMETRY=1`, `OTEL_METRICS_INCLUDE_ENTRYPOINT=true`, logs grouped by `query_source`. `compact` is the summary request, which may not appear as a JSONL assistant row. `auxiliary` is side traffic.

Prefer `modelUsage` over top-level `usage`. Top-level `usage` omits subagents. `total_cost_usd` is a client-side estimate.

## Order

Pilot: `ledger_migration`, scale S, `P-doc`, Tier S, baseline versus low, 3 repeats. Six sessions. Calibrate whether the low arm actually compacts.

Then the main grid. Randomize order inside a block. Leave a gap longer than the 1-hour TTL between blocks when first-request numbers must be truly cold. The live client runs did not: each first request read 63% to 68% of its prefix from a warm shared cache. With three repeats, report medians and ranges. Do not claim significance.

Stop a looping arm with `--max-budget-usd`. That flag is a client-side estimate, not the meter.

## What to record

Version, model, effort, entrypoint, `raw_max_tokens`, session id, wall time, four token buckets, `pre_tokens` and `post_tokens`, hidden pass, checker counts, summary-retention booleans, meter start and end in percentage points, and whether the account was otherwise idle.

## Cache-expiry runs (14 November 2026)

Same key, model, effort, and CLI as above, 5-minute cache. Two sessions each built about 97k tokens of context on one codebase task (3 calls each). The hot session was resumed with `--resume` 198 seconds after its last call, inside the cache window, for `median_bug`. The stale session was resumed three times, 609, 771, and 770 seconds after its previous call, for `median_bug`, `slice_bug`, and `keep_currency`. About 380 seconds after each stale run, the same fix ran in a brand-new session, the same as `/clear`. The third fresh session started about 34 minutes after its stale run because the machine paused, which is still a cold start. All 9 test checks passed. Every transcript had zero 1-hour cache writes and zero `compact_boundary` rows. Numbers only, no ids or prompt text, are in `outputs/live_experiments.json` under `ttl_runs`. n=1 per condition.

## Cache-expiry dose-response runs (15-16 November 2026)

Same key, model, effort, and CLI as the 14 November cache-expiry runs, 5-minute cache. Two new sessions each built about 97k tokens of context on the same codebase task, with a unique session tag so they could not share a cache prefix. Each session then ran the same three small fixes, in order, after pauses of 123, 243, and 383 seconds from the previous call (2, 4, and 6 minutes). The 2-minute and 4-minute continues were inside the cache window; the 6-minute continues were past it. Each fix is scored against the matching new-session run in `ttl_runs.clear`. All 6 test checks passed. Every transcript had zero 1-hour cache writes and zero `compact_boundary` rows. Numbers only, no ids or prompt text, are in `outputs/live_experiments.json` under `dose_response_runs`. n=2 per pause length.

## Tool-output and between-turn runs (14 November 2026)

Same model, effort, and CLI as above, 5-minute cache, the same `claude -p` flags (`--strict-mcp-config`, `--setting-sources project`, the same `--append-system-prompt`), and a 3-line `CLAUDE.md`. Every turn came within a few seconds of the last, so the cache was always warm. Every transcript had zero 1-hour cache writes and zero `compact_boundary` rows.

Tool output (`tool_output_runs`): a new session runs a fake failing test suite once with Bash and answers in one line, then 5 short questions with no tools, each through `--resume`. The arms differ only in the suite's output: 48, 803, or 8,602 tokens (counted with the token-counting endpoint). Claude Code cut the 8,602-token output to about 10,000 characters. Each session starts with a unique tag. 9 earlier sessions without the tag are left out, because identical sessions read each other's cache. n=2 per arm.

Between-turn changes (`mid_session_runs`): a session reads 2 log files with the Read tool (about 44,000 tokens), then answers one short question per turn with no tools. In order: control, effort high, effort back to medium, one `CLAUDE.md` line edited, control, a 1-tool stdio MCP server added through `--mcp-config`, control, and in 2 of the 3 sessions `--disallowedTools Edit`, then control. Each change is compared with the mean of the same session's control turns. No model switch was run, because live runs use only Opus 5.5. The model-switch result is quoted from Anthropic's docs. Numbers only are in `outputs/live_experiments.json`.
