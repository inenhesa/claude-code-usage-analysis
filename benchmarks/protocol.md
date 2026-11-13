# Controlled benchmark protocol

No live Claude session has been run for this study. Generator scales and the 15 percent `pre_tokens` gate are design targets for a pilot, not measured constants. `--execute` spends quota. The default is a dry run.

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
| F | `--autocompact auto` (about 967K on native 1M) | window 250,000, and a separate percentage-25 arm | Matches the posts |
| X | as F | window 250,000 and percentage 25 | The compounding trap (62,500 in the model) |

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

Then the main grid. Randomize order inside a block. Leave a gap longer than the 1-hour TTL between blocks when cold-start numbers matter. With three repeats, report medians and ranges. Do not claim significance.

Stop a looping arm with `--max-budget-usd`. That flag is a client-side estimate, not the meter.

## What to record

Version, model, effort, entrypoint, `raw_max_tokens`, session id, wall time, four token buckets, `pre_tokens` and `post_tokens`, hidden pass, checker counts, summary-retention booleans, meter start and end in percentage points, and whether the account was otherwise idle.
