# Desktop, CLI, and T3 Code

A compaction change and a client switch are different experiments. This protocol keeps them apart.

## Measured on 13 November 2026

Seven live runs, cards C1 and C3 below, on a dedicated study API key. Claude Opus 5.5 (`claude-opus-5-5`), effort medium, 1M window, 5-minute cache, identical 3-line `CLAUDE.md` per card, no user MCP servers. Source: `outputs/live_experiments.json`, chart `outputs/figures/live_client_comparison.png`.

| Run | Client | Entrypoint | Claude Code | First-request tokens | System prompt chars | Inline / deferred tools | Skills | Calls | Cache-read tokens | API list price | Five-hour meter |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| C1 cold | Claude CLI v2.1.325, `claude -p` | `sdk-cli` | 2.1.325 | 16,850 | 7,227 | 13 / 13 | 13 | 1 | 11,495 | $0.0292 | not measured |
| C1 cold | Claude CLI v2.1.325, interactive | `cli` | 2.1.325 | 19,528 | 7,485 | 14 / 15 | 13 | 1 | 12,924 | $0.0358 | not measured |
| C1 cold | T3 Code v0.1.9 | `t3-code` | 2.1.325 | 22,842 | 12,857 | 15 / 82 | 13 | 1 | 13,575 | $0.0491 | not measured |
| C1 cold | Claude Desktop v5.1231.2, Code tab | `claude-desktop-3p` | 2.1.321 | 39,749 | 25,865 | 35 / 82 | 22 | 1 | 24,968 | $0.0790 | not measured |
| C3 `noisy_bughunt` | Claude CLI v2.1.325, interactive | `cli` | 2.1.325 | 21,291 | | | | 6 | 129,672 | $0.1145 main, $0.1247 total | not measured |
| C3 `noisy_bughunt` | T3 Code v0.1.9 | `t3-code` | 2.1.325 | 22,958 | | | | 6 | 138,735 | $0.1362 | not measured |
| C3 `noisy_bughunt` | Claude Desktop v5.1231.2, Code tab | `claude-desktop-3p` | 2.1.321 | 40,046 | | | | 8 | 317,669 | $0.2057 | not measured |

C3 here used `noisy_bughunt` (seed 1337, scale S, `P-doc`, three planted bugs) instead of `median_bug`, so the fix needs several tool rounds. All three clients fixed all three bugs, passed 1 visible and 3 hidden tests, and left the decoy `relayd/metrics.py` byte-identical to its manifest hash.

What the startup rows show:

- T3 Code adds 67 `mcp__t3-code__*` deferred tools, an inline `mcp__t3-code__request_secret`, and 5,372 system-prompt characters over the interactive CLI, including a `<runtime_info>` block (412 characters) and a `<pull_request_linking>` block (940 characters). Cold prefix +17%, bugfix +19% over the CLI's main-thread rows, +9% over the CLI's total with background calls.
- Claude Desktop's Code tab adds 13 `Claude_Browser` preview tools, 6 `ccd_session` and `ccd_turn` tools, a `terminal` reader, and `SendUserFile` inline, plus 69 MCP deferred tools (59 `mcp__ccd_*`, 6 scheduled-task, 4 terminal) and 9 more skills. Cold prefix 2.04 times the interactive CLI, bugfix 1.80 times.
- Cost sources differ. The CLI and T3 Code totals are `cost_state.totalCostUSD`. The interactive CLI's total includes background calls (a Haiku title and an Opus side request) that write no assistant row. Desktop writes no `cost_state`, so its dollars are deduplicated assistant usage at list price, a floor.
- The five-hour meter column is empty on purpose. These runs used an API key, not a subscription.

C2 (MCP delta), C4 (fixed-window long build), C5 (idle pause), and C6 (side text generation) are not yet run across clients.

## What each client is

| Client | Claude Code? | Entrypoint | Transcripts | Compaction control | Side traffic |
| --- | --- | --- | --- | --- | --- |
| Desktop chat | No | none | Not in `~/.claude/projects` | Chat product's own context management | Meter only |
| Desktop Code tab | Yes | `claude-desktop` when OTel entrypoint is on | `~/.claude/projects`, kept at any age | Same settings as the CLI, but the Desktop launch environment overrides settings `env` | Prompt suggestions can be a background request |
| Interactive `claude` | Yes | `cli` | `~/.claude/projects/<slug>/<id>.jsonl` | env, then flag, then `/autocompact`, then settings | Titles and other auxiliary requests |
| `claude -p` | Yes | `sdk-cli` | Same, unless `--no-session-persistence` | Same | Reported higher five-hour burn per token than `cli` in anthropics/claude-code#97074. Their report, not a rate |
| T3 Code | Yes, via the Agent SDK | historically `sdk-ts`; `t3-code` after their entrypoint change | Under that instance's `CLAUDE_CONFIG_DIR` | "Auto-compact after" is an integer from 100,000 to 1,000,000. The UI computes window minus about 33,000, from a source comment in `ClaudeAdapterV2.ts`. Claude Code v2.1.325 confirms it: window minus a 20,000 output reserve minus a 13,000 buffer | Titles, commits, PR text, and branch names may save no session history. Prompt suggestions default off. Idle threads: When you return, Before the cache expires (beta), or Never |

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
- C4 and C5 compare Desktop and T3 at the same window. A drop in practice is more likely the trigger, or Desktop chat's own summarizer, than the client. The public entrypoint reports point the other way from "CLI is automatically cheaper."
- Retention: `cleanupPeriodDays` defaults to 30. Desktop and Cowork transcripts are kept. If the "before" CLI logs are gone, say so. Do not fill the gap with the simulation.
