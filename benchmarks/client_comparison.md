# Desktop, CLI, and T3 Code

The posts mix two changes: a lower compaction trigger, and a client switch described as "Claude App to Claude CLI (T3 Code)." This protocol separates them. No cell below is a measured token count from Ilja's account.

## What each client is

| Client | Claude Code? | Entrypoint | Transcripts | Compaction control | Side traffic |
| --- | --- | --- | --- | --- | --- |
| Desktop chat | No | none | Not in `~/.claude/projects` | Chat product's own context management | Meter only |
| Desktop Code tab | Yes | `claude-desktop` when OTel entrypoint is on | `~/.claude/projects`, kept at any age | Same settings as the CLI, but the Desktop launch environment overrides settings `env` | Prompt suggestions can be a background request |
| Interactive `claude` | Yes | `cli` | `~/.claude/projects/<slug>/<id>.jsonl` | env, then flag, then `/autocompact`, then settings | Titles and other auxiliary requests |
| `claude -p` | Yes | `sdk-cli` | Same, unless `--no-session-persistence` | Same | Reported higher five-hour burn per token than `cli` in anthropics/claude-code#97074. Their report, not a rate |
| T3 Code | Yes, via the Agent SDK | historically `sdk-ts`; `t3-code` after their entrypoint change | Under that instance's `CLAUDE_CONFIG_DIR` | "Auto-compact after" is an integer from 100,000 to 1,000,000. The UI computes window minus about 33,000, from a source comment in `ClaudeAdapterV2.ts`, not from Anthropic | Titles, commits, PR text, and branch names may save no session history. Prompt suggestions default off. Idle threads: When you return, Before the cache expires (beta), or Never |

OTel `app.entrypoint` is documented and needs `OTEL_METRICS_INCLUDE_ENTRYPOINT=true`. A JSONL `entrypoint` field is reported by third-party parsers. It is not an Anthropic schema page. Use both, and say which one you saw.

T3 loads user, project, and local settings by default, so a `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` in `~/.claude/settings.json` still applies on top of "Auto-compact after." That is the compounding trap (S8).

## Instruments

1. Meter: `/usage` `usage_report`, a status-line `rate_limits` logger on the CLI, and Settings > Usage for Desktop chat.
2. Effective window: `/context` `raw_max_tokens` in every Claude Code client at the start of a block.
3. Fixed overhead: `/context` categories, plus the first assistant record's input plus cache write plus cache read. A T3 issue mentions 34.6k versus 47.0k prefix tokens on their machines. That is their observation. Reproduce it or leave the cell empty.
4. Summary-request cost: OTel `api_request` grouped by `query_source` and entrypoint.
5. Token buckets: the analyzer on the session files. In T3, pass every `CLAUDE_CONFIG_DIR` you use. Hashed project labels will otherwise split one project.
6. While a T3 thread runs, record the child `claude` command line so you can see whether "Auto-compact after" arrived as an env var or a flag.

## Cards

Same model, same effort, same repo snapshot, idle account, randomized client order. Repeat.

| Card | Task | Fixed setup | Primary measures |
| --- | --- | --- | --- |
| C1 | New session, 3-line CLAUDE.md, `/context`, then "Reply OK" | No MCP servers | Prefix tokens and `raw_max_tokens` |
| C2 | C1 plus one fixed local MCP server | Same server JSON everywhere | MCP token delta versus C1 |
| C3 | `median_bug` | Default compaction | Small-task baseline. Run several back to back if the meter does not move |
| C4 | `ledger_migration` scale S, `P-doc` | The same window in every client: 100,000. Check with `/context` | Boundaries, buckets, hidden pass, meter. This is the client effect with the threshold held fixed |
| C5 | Pause C4 for 70 minutes, then resume | Claude Code `idleCompaction` false and T3 idle Never, then repeat with When you return | Cold rebuild versus idle compaction |
| C6 | Ask for a commit message, and in T3 let it title the thread | As is | Meter delta beyond C4. JSONL may miss T3 text generation |

## How to read a result

- Same `raw_max_tokens`, same token buckets, different meter: an entrypoint-metering observation. Not "this client used fewer tokens."
- Different `raw_max_tokens`: you did not match the trigger. Do not call it a client effect.
- The posts' client claim is C4 and C5, Desktop versus T3, at the same window. A drop in practice is more likely the trigger, or Desktop chat's own summarizer, than the client. The public entrypoint reports point the other way from "CLI is automatically cheaper."
- Retention: `cleanupPeriodDays` defaults to 30. Desktop and Cowork transcripts are kept. If the "before" CLI logs are gone, say so. Do not fill the gap with the simulation.
