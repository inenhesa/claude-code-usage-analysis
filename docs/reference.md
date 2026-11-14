# Reference

Checked 13 November 2026. Workflow chart numbers come from the physical-caching simulator, not from an account. The `live_*` charts and `outputs/live_experiments.json` are 14 measured Claude Opus 5.5 sessions. This page is the setting and accounting detail the README leaves out.

## Words

A **user turn** is a message a person sends. A **model request** is one API call inside that turn, including a tool batch. The baseline scenario is 60 user turns of 8 model requests, not 60 messages.

## The two knobs

They are independent, and setting both changes the meaning of each.

| Knob | Where it lives | What it does |
| --- | --- | --- |
| Percentage | `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` | 1 to 100, as a percent of the auto-compact window. It cannot raise the threshold. Values above the unpublished default percentage are ignored. |
| Window | `autoCompactWindow`, `/autocompact`, `--autocompact`, `CLAUDE_CODE_AUTO_COMPACT_WINDOW` | An absolute budget from 100,000 to 1,000,000 tokens, capped at the model context. An empty or invalid window is ignored. |

The environment variable outranks the flag, and the flag outranks the setting. Settings files themselves rank: managed, then command-line `--settings`, then project local, then shared project, then user. A shell export is often overwritten by a settings `env` block. An overlay that wants a clean experiment has to set both variables itself.

`CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=100` does not delay compaction. A "95 percent" arm can be identical to the default. The docs do not publish a default percentage. The phrase "95 percent or so" is close to the documented about-967K trigger on a 1M context. It is not a setting named 95.

The trigger, read from Claude Code v2.1.325, functions `tle` and `WX` in the shipped binary. `cc_usage_study.model.effective_trigger` implements exactly this:

```text
effective_window = window - min(maxOutputTokens, 20000)
threshold = effective_window - 13000                                       (no override)
threshold = min(floor(effective_window * pct / 100), effective_window - 13000)   (with the override)
```

If both knobs are set, the percentage applies to the effective window and the model warns. A 250,000 window with 25 fires at floor(230,000 x 0.25) = 57,500, not at the naive 0.25 x 250k = 62.5k. The live arm logged `preTokens` 89,402, 62,687, 98,668, 57,993, 68,068, and 57,044: two fires at the 57,500 line, the rest overshooting by up to 41k after big tool batches. Its first fire was 55.5% past the line, so that arm fails the 15% `preTokens` gate in `benchmarks/protocol.md`. Scenario S8: the compounded arm sends 5.79 times fewer raw prompt tokens than the default, but only 1.12 times less cache-weighted usage, and it uses 1.83 times as much as the 25 percent override alone (S8b, ratio 0.55). Check `compactMetadata.preTokens` before trusting a label.

A plain window fires 33,000 short of the number you type when `maxOutputTokens` is at least 20,000: 180k fires at 147,000, 140k at 107,000, 100k at 67,000. `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=18` on a 1M window fires at floor(980,000 x 0.18) = 176,400, and 25 fires at 245,000. A 200k model fires at 167,000 by default and at 45,000 with 25. `preTokens` is logged by a different counter from the trigger check, so a fire can land a few thousand tokens under the line or overshoot after a large tool batch.

T3 Code's "Auto-compact after" writes the window integer, 100,000 to 1,000,000. A comment in `ClaudeAdapterV2.ts` (commit 65e4272, read 13 November 2026) says Claude Code compacts about 33,000 tokens short of that window, so 250,000 is shown as about 217,000. That comment is not an Anthropic spec, but the v2.1.325 binary agrees: 20,000 of output reserve plus a 13,000 buffer. This model applies the same 33,000 by default (S1b, a 250,000 window firing at 217,000: 2.15 times less cache-weighted usage and 3.39 times fewer raw prompt tokens than the default). Passing `reserve` replaces the whole 33,000 without subtracting it twice. T3 also loads user settings by default, so a percentage in `~/.claude/settings.json` still applies on top of the window.

## Which window you actually get

From the [model config](https://code.claude.com/docs/en/model-config), if you set neither knob:

| Models | Default compaction |
| --- | --- |
| Native 1M models (Fable, Sonnet 5 and later, Haiku 5.5, Opus 4.7 and later) | About 967K |
| Sonnet 4.6 and Opus 4.6 without extended context | 200K |
| Same native 1M models with `CLAUDE_CODE_DISABLE_1M_CONTEXT=1` | Held at 200K |

[Changelog v2.1.323](https://code.claude.com/docs/en/changelog) (12 November 2026) moved Sonnet 5's default window to the full 1M, so those sessions compact at about 967K instead of about 934K. The same release stopped applying the percentage override to auto-memory extraction, and in some sessions to `/btw`, inside Anthropic-hosted cloud sessions. The main conversation still follows the override.

`1,000,000 - 33,000 = 967,000` matches the documented approximation, and the v2.1.325 binary computes it as 1,000,000 - 20,000 - 13,000. A 200k model fires at 200,000 - 33,000 = 167,000. On a 200k window the absolute knob's 100k floor clamps low values if you use the window control. The percentage override is not forced back up. On a 200k context, percentage 25 fires at floor(180,000 x 0.25) = 45,000, below the ~46k post-compaction reset, and S6 says that thrash uses 3.39 times the default.

`autoCompactEnabled` defaults to true. `DISABLE_AUTO_COMPACT` turns automatic compaction off.

## Caching, in the terms this model uses

Each request re-sends the context. If nothing compacts, cumulative prompt tokens grow with the square of the request count:

```text
N * base + growth * N * (N + 1) / 2 + output * N * (N - 1) / 2
```

`growth` is new input only. Previous output is its own term.

A warm request in this model reads the entire previous prompt and writes the previous output plus the new tail. It does not rewrite a fixed fraction of the prefix on every call. A cold request, after the TTL expires, rewrites the prefix at that call's cache-write rate: 2x on a 1-hour main conversation, 1.25x on a compaction (the 5-minute bucket). Pricing a cold prefix as fresh input at 1.0x would differ. `outputs/summary.json` says which choice the files use. Fresh input is zero by construction in these scenarios.

Published API shape, snapshot 13 November 2026, from the [pricing page](https://platform.claude.com/docs/en/about-claude/pricing) and the [prompt-caching guide](https://code.claude.com/docs/en/prompt-caching):

| Bucket | Usual multiplier |
| --- | --- |
| Cache read | 0.1x. Opus 5.5 and Sonnet 5.5 are 0.05x. Fable 5.2, Mythos 5.2, Fable 5.1, and Mythos 5.1 are 0.025x. |
| 5-minute cache write | 1.25x |
| 1-hour cache write | 2x |
| Output | 5x on Opus 5.5 and Sonnet 5.5. Not a universal ratio. |

Inside the subscription, the main conversation uses a 1-hour TTL. It drops to 5 minutes on usage credits. Subagents, compaction, and titles default to 5 minutes. Some server-controlled helper requests still get 1 hour. These multipliers are list prices, not the five-hour meter.

Haiku 5.5 has a second price tier above 100,000 prompt tokens on that request. The estimator applies the tier per request, not to a week's sum. Fast-mode cache prices are not published here, so a fast call that used the cache is left unpriced. Models from 4.7 onward turn the same text into about 30% more tokens. The analyzer warns when a before/after split crosses that generation. It does not try to correct it.

Idle compaction is on by default since v2.1.299 in the terminal and v2.1.314 in VS Code and the Desktop Code tab. It fires only when all four conditions hold:

1. `idleCompaction` is on (`idleCompaction: false`, v2.1.290 and later, or `DISABLE_AUTO_COMPACT=1` turns it off).
2. The session uses the 1-hour cache. On an API key or usage credits (5-minute cache) it never runs.
3. The Claude Code process is still alive through the pause. A sleeping laptop, a closed terminal, or a finished `claude -p` run means nothing runs it.
4. Context is at least `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS` (default 200,000, floor 100,000).

It then runs at about 90% of the TTL (about 54 minutes idle), while the cache is still warm, so the summary reads cheaply. The user returns after the TTL, so the next request rebuilds the short post-summary context cold. A before/after that crosses those versions is not a clean compaction experiment.

`--autocompact 180000` fires at 147,000 on a 1M model, so context never reaches the 200,000 idle minimum and idle compaction never runs. Pair it with `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS=100000` if you want the safety net back.

In S4 (idle compaction off) and S4d (on, but the laptop slept), four 75-minute breaks give the same ratio of 2.25 between the default and a 245k fire. With idle compaction running (S4b) that falls to 1.33. The default arm alone uses 1.74 times as much when idle compaction lapses (33.1M against 19.0M units).

## The meter

[Usage limits](https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work): a five-hour session limit that starts at the first message and resets in full, and weekly limits across models. Some plans have a separate Fable weekly limit. claude.ai, Claude Code, and Claude Desktop share one pool. Length, complexity, features, model, and effort all count. There is no published weight for fresh tokens versus cache reads versus messages.

`total_cost_usd` is a client-side estimate ([cost tracking](https://code.claude.com/docs/en/agent-sdk/cost-tracking)). Result-level `usage` excludes subagents. `modelUsage` includes them. A per-step `output_tokens` can be a placeholder from the start of a streamed message. The analyzer keeps the more complete record.

`five_hour_blocks` in an analyzer report are labeled weights, not the server meter. One of them, `attribution_v2_1_317`, is a third-party client-side reading around Claude Code v2.1.317. It is marked as that. It is not Anthropic's formula.

## What the analyzer keeps

Point it at `~/.claude/projects`. It hashes project paths unless the path is this repo's synthetic fixture.

- Main transcripts and subagent files are separate. A subagent JSONL often carries the parent's `sessionId`. The split uses `isSidechain`, `agentId`, and a `subagents` directory.
- Records are deduped across files, including resumed, forked, and `.orphaned-` transcripts, on `(message.id, requestId)`. The copy with the real output, a stop reason, and any nested 5-minute or 1-hour cache split wins.
- Lines marked `<synthetic>` or `isApiErrorMessage` are skipped.
- A compaction is counted only for `type == "system"` and `subtype == "compact_boundary"`. A following `isCompactSummary` line is not a second compaction. `trigger`, `preTokens`, and `postTokens` are kept.
- Per-call averages and cache-rebuild checks use the main stream only.
- Entrypoint, version, and per-5-hour-block buckets are reported. Prompt text is not copied.

`cleanupPeriodDays` defaults to 30, so a CLI "before" period older than that may already be gone. Desktop and Cowork transcripts are kept. `CLAUDE_CONFIG_DIR` moves the whole tree. T3 can use its own directory, and some of its title and commit-message calls save no session history.

## What to record on the machine that has the logs

1. `claude --version`, the model, and the date the setting changed.
2. The percentage, the window, and T3's idle mode: When you return, Before the cache expires, or Never. If both a window and a percentage are set, say so.
3. Main-stream prompt tokens per call, the four buckets (fresh input, cache write, cache read, output), and `pre_tokens`.
4. Five-hour and weekly percentages from Settings > Usage, `/usage`, or a status-line `rate_limits` logger, for the same blocks, with the account otherwise idle.
5. Whether the work got worse: failed tests, extra edits, time spent putting a forgotten decision back.

```bash
python3 -m cc_usage_study.analyze ~/.claude/projects \
  --split-date YYYY-MM-DD \
  --split-tz Europe/Lisbon \
  --sessions \
  --output outputs/local-report.json
```

The split date itself counts as "after." The default timezone is UTC. Use `Europe/Lisbon` for a local calendar day. A stream can appear on both sides of the split if its calls cross the date.

Headless `claude -p` is entrypoint `sdk-cli`. Token totals from it are usable. A meter delta from it is not an interactive-CLI result. `--bare` bills an API key and skips the subscription, so it is the wrong mode for a limit question. The harness rules are in `benchmarks/protocol.md`.

## Formula

The closed-form trigger used on the EOQ chart is:

```text
T* = R + sqrt(2 * K * (g + o) / r)
```

R is the residual context after a summary (46,000 in the baseline: system, project, summary, and re-injected files). K is the fixed cost of compacting. `g + o` is new input plus output per request. `r` is the cache-read multiplier. Cheaper reads move the valley to the right. The formula estimates 126,393 at 0.1x, 158,880 at 0.05x, and 205,060 at 0.025x. A 10,000-token grid of the same session bottoms at 120,000, 160,000, and 220,000. Those are fire points: type 33,000 more with `--autocompact` (153,000, 193,000, 253,000).

K is kept equal to the per-compaction overhead `simulate()` charges, so the closed form and the trace agree. The modeled summary call alone (instruction, summary output, and reading a 107k context at 0.05x) is about 21.6k units. The 19 live Opus 5.5 compactions averaged about 36.6k units, bimodal from about 19k to 85k per phase. Raising K by that 15k moves the 0.05x estimate from 158,880 to 170,507, and the simulated grid valley from 160,000 to 170,000. `outputs/summary.json` reports both under `eoq.live_calibrated`.

## Sources

- [Environment variables](https://code.claude.com/docs/en/env-vars)
- [Settings reference](https://code.claude.com/docs/en/settings-reference)
- [Model config](https://code.claude.com/docs/en/model-config)
- [Changelog](https://code.claude.com/docs/en/changelog)
- [Prompt caching](https://code.claude.com/docs/en/prompt-caching)
- [Pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- [Context window](https://code.claude.com/docs/en/context-window)
- [Usage and length limits](https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work)
- [Agent SDK cost tracking](https://code.claude.com/docs/en/agent-sdk/cost-tracking)

## Hidden compaction cost

The summary request a compaction sends writes no assistant row in the session JSONL. Claude Code's `cost-state` row (`totalCostUSD`, `modelUsage`) includes it. In the live `LedgerKit v3.2` arms on Opus 5.5 with a 5-minute cache, `cost_state` minus the deduplicated assistant rows was zero at the default (no compaction) and, as a share of session usage, 11.8% at 180k (2 compactions), 13.9% at 140k (4), 24.3% at the 250,000 window with 25 (6), and 25.5% at 100k (7). In input-token equivalents that is about 94k, 130k, 219k, and 253k. Those are a residual, not measured tokens: the hidden calls have no token breakdown. Phase means run from about 19k to 85k units per compaction, 36.6k on average. Any analysis that prices only assistant rows will flatter a low trigger. Claude Desktop writes no `cost-state` row at all, so a Desktop session's assistant-row total is a floor.
