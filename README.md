# Claude Code compaction study

A reproducible look at two anecdotal claims from Ilja Nevolin's Claude Code setup at work, posted on LinkedIn:

1. Setting auto-compaction to 25 percent, instead of a default he described as "95 percent or so," cut his Claude Code usage by almost 4x. He stopped hitting the five-hour limit and the weekly limit.
2. Switching from the Claude app to the Claude CLI, via T3 Code, cut usage further. The screenshot reads "cuts take usage by another 200 percent," which is most naturally read as token usage. "Another 200 percent" is not a defined ratio.

The claims are one person's experience. This repo separates the mechanism, a simulation with explicit assumptions, a privacy-safe transcript analyzer, and a benchmark protocol. It does not contain his transcripts, and it does not treat the simulation as a measurement of his account.

Docs were checked on 13 November 2026. Prices and default triggers change. Each section cites the page it depends on.

## Research questions

1. On a long session, does a lower auto-compaction trigger reduce cumulative prompt tokens, and by how much, once you count the summary calls?
2. How much of that reduction survives prompt caching, at the published cache-read and cache-write prices?
3. Does the same reduction show up in the five-hour and weekly usage limits, or only in the token buckets?
4. When does earlier compaction cost more than it saves, because the summary dropped something the model has to rebuild?
5. Is a Claude app versus CLI (T3 Code) difference a compaction effect, a client effect, or a different mix of work?

## Hypotheses

These are predictions to test, not findings.

- H1. On sessions that cross the high trigger more than once, cumulative prompt tokens (fresh input + cache writes + cache reads) fall as the trigger falls. With a small post-compaction context and no extra rework, the ratio is in the direction of the threshold ratio, and smaller than a casual "4x" reading of 25 versus 95.
- H2. At published cache prices, the list-price gap is smaller than the raw-token gap, because most of a warm prefix is a cache read.
- H3. The five-hour and weekly meters are not documented as either raw tokens or list price. A token ratio does not imply the same meter ratio. The analyzer reports buckets so a before/after on one account can be set next to the meter.
- H4. Extra turns spent repairing lost context can erase the list-price saving while raw tokens are still lower.
- H5. Sessions that never reach the high trigger show a much smaller difference. A 25 percent trigger that never fires is identical to 95 percent.
- H6. The Claude desktop chat app, the Desktop Code tab, the official CLI, and T3 Code are different clients. A lower total after switching apps is not, by itself, a compaction result.

## Mechanism

Claude Code sends a new request on every turn. The model does not keep the conversation between requests, so the request includes the system prompt, project context, the prior messages and tool results, and the new message. The prompt-caching docs say this directly: "Without caching, the API would reprocess your full history on every turn."

Call the re-sent context on turn `t` roughly `base + t * growth`. Summed over `N` turns, cumulative prompt tokens are

```text
N * base + growth * N * (N + 1) / 2 + output * N * (N - 1) / 2
```

when nothing compacts. The dominant term is quadratic in the number of turns. `closed_form_prompt_tokens` in `cc_usage_study.model` is that expression, and a test checks it against the simulation.

Compaction cuts the history back to a summary plus what Claude Code re-injects (system prompt, project `CLAUDE.md`, a few recently touched files, skill bodies within a cap). Later turns are cheaper. The summary call is not free, and the next turn rebuilds the conversation cache. The context-window docs list what survives. The prompt-caching docs say compaction invalidates the conversation layer on purpose, while a warm summary call can still read the old prefix from cache.

That trade is the whole study. Earlier compaction means more, smaller sawteeth. Later compaction means fewer sawteeth and a larger area under each one.

### Why caching changes the arithmetic

The token buckets do not overlap. Fresh `input_tokens`, `cache_creation_input_tokens`, and `cache_read_input_tokens` partition the prompt. Output is separate. Published multipliers relative to base input, from the pricing page:

| Operation | Multiplier |
| --- | --- |
| 5-minute cache write | 1.25x |
| 1-hour cache write | 2x |
| Cache read, most models | 0.1x |
| Cache read, Opus 5.5 and Sonnet 5.5 | 0.05x |
| Cache read, Fable 5.2, Mythos 5.2, Fable 5.1, Mythos 5.1 | 0.025x |

Claude Code requests the 1-hour TTL on the main conversation for subscription usage that is still inside the plan, and drops to five minutes once usage is billed to credits. Subagents, compaction, and most other side requests default to five minutes on either billing path. Source: [How Claude Code uses prompt caching](https://code.claude.com/docs/en/prompt-caching).

A large warm context is mostly cache reads. Shrinking it saves less money than the raw token count suggests. The simulation's priced total uses these multipliers. It is not a prediction of the subscription meter.

## What was verified about the setting

Checked against the official docs on 13 November 2026. Nothing in this section is inferred from the LinkedIn posts.

### Two knobs, not one slider labeled 95

[Environment variables](https://code.claude.com/docs/en/env-vars) define `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` as:

> Set the percentage (1-100) of the auto-compact window at which auto-compaction triggers. Use lower values like `50` to compact earlier; the variable can't raise the threshold, so values above the default percentage are ignored. It applies only in sessions that compact before the model's context limit. Applies to both main conversations and subagents.

The percentage is a fraction of the auto-compact window, not a free-floating "percent of the model" with a published default of 95. The docs do not publish the numeric default percentage. Setting 95, or 100, does nothing when that value sits above the default. A before/after that assumes the old behavior was "95 percent of the window" may be comparing 25 percent with the default, not with 95.

The window itself is a token count:

- `autoCompactWindow` in settings, from 100,000 to 1,000,000, capped at the model window. Default is unset, and Claude Code picks a window for the model. [Settings reference](https://code.claude.com/docs/en/settings-reference).
- `/autocompact 500k` saves a per-model window under `modelSettings`.
- `--autocompact` overrides the saved window for one launch. `claude --autocompact auto` returns to the tuned window.
- `CLAUDE_CODE_AUTO_COMPACT_WINDOW` outranks the command, the flag, and the setting, unless the value is ignored. A number with no suffix is a token count, so `200` means 200 tokens, not 200,000. Suffixed forms such as `200k` need v2.1.304 or later.

On a 200K context, 25 percent is 50,000 tokens and 10 percent is 20,000. Both are below the 100,000 floor of the window knob, so those two arms collapse to the same window. The percentage variable is what can still name 25 percent of a smaller window. The harness records the clamp.

`autoCompactEnabled` defaults to true. `DISABLE_AUTO_COMPACT=1` turns automatic compaction and idle compaction off. `DISABLE_COMPACT=1` also disables `/compact`.

### The published default trigger is about 967K, not "95 percent"

[Model configuration](https://code.claude.com/docs/en/model-config), section "Default auto-compact thresholds":

- With no window set, Claude Code compacts at the model's context limit, with these exceptions.
- Sonnet 4.6 and Opus 4.6 without extended context compact at the 200K boundary.
- `CLAUDE_CODE_DISABLE_1M_CONTEXT=1` holds native 1M models, such as Sonnet 5 and the Fable models, to the 200K boundary.
- Models that run with a native 1M window compact before the window fills, at about 967K tokens by default. The list is the Fable models, Sonnet 5 and later, Haiku 5.5, and Opus 4.7 and later.

About 967,000 / 1,000,000 is 96.7 percent. That is close to "95 percent or so." It is the docs' approximation, and this repo does not treat 967,000 as an exact constant. The [changelog](https://code.claude.com/docs/en/changelog) for v2.1.323 (12 November 2026) says Sonnet 5's default window moved to the full 1M context, so 1M sessions auto-compact at about 967K instead of about 934K. The same release stopped applying `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` to auto-memory extraction in Anthropic-hosted cloud sessions, and in some of those sessions to `/btw`. The main conversation still follows it.

`CLAUDE_CODE_MAX_OUTPUT_TOKENS` reduces the room left before auto-compaction, because a larger reserved output shrinks the usable window. Idle compaction is a separate path: on by default for interactive sessions on the 1-hour cache, once context reaches `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS` (default 200,000, floor 100,000) and the session has been idle for about an hour. [Settings reference](https://code.claude.com/docs/en/settings-reference).

How to set 25 percent for a real session, matching the post's wording:

```bash
# percentage of the current auto-compact window; cannot go above the default percentage
export CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=25
claude
```

Or in `~/.claude/settings.json`:

```json
{ "env": { "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "25" } }
```

How to set an absolute trigger of 250,000 tokens, which is 25 percent of a 1M window and is inside the documented window range:

```bash
claude --autocompact 250000
```

If both are set, read the precedence above before assuming you know the trigger. A percentage of 25 applied on top of a 250,000 token window is 62,500, not 250,000.

### Usage limits: what is counted

[How do usage and length limits work?](https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work) (Help Center, read 13 November 2026):

- Paid plans have a session limit that resets five hours after the first message of the window, and weekly limits that reset at a fixed time for the account.
- Usage depends on conversation length and complexity, features, model, and effort level. It is not described as a fixed message count or a fixed token quota.
- claude.ai, Claude Code, and Claude Desktop share one pool. Switching clients does not open a second allowance.
- Weekly limits apply across models. Some plans also have a separate weekly Fable limit.
- The page does not say whether a cache read counts the same as a fresh input token, less, or not at all. It does not publish a weight.

[Models, usage, and limits in Claude Code](https://support.claude.com/en/articles/14552983-models-usage-and-limits-in-claude-code) distinguishes subscription seats (a pool, then a hard stop) from API keys (pay per token, and `/cost` shows a running session estimate). The same page says every turn sends the conversation so far, the project context, and the new prompt, and that prompt caching bills the unchanged part at the cheaper cache-read rate. That is an API-price statement. It is not a formula for the five-hour bar.

`total_cost_usd` in the Agent SDK and in `claude -p` JSON output is a client-side estimate from a price table bundled with the client. The docs say it can drift from the bill. [Track cost and usage](https://code.claude.com/docs/en/agent-sdk/cost-tracking). Do not report it as the meter.

Third-party posts and GitHub issues have estimated meter weights, including a claim that the five-hour meter discounts cache reads and a suspicion that the weekly meter does not. Those are not Anthropic documentation. This study does not adopt a weight. If a before/after is going to speak to the limits, write down the percentages from Settings > Usage next to the four token buckets.

Length limits are the context window, a different thing. The Help Center also describes automatic context management in claude.ai when code execution is on: long chats get summarized, and "longer conversations that trigger automatic context management consume more of your usage limit." That summarizer is the chat product, not the Claude Code percentage setting.

### Transcript fields the analyzer understands

Claude Code stores sessions at `~/.claude/projects/**/*.jsonl` (or under `CLAUDE_CONFIG_DIR` if that is set). Assistant records carry `message.usage` with `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, and `cache_read_input_tokens`. A nested `cache_creation` object splits writes into `ephemeral_5m_input_tokens` and `ephemeral_1h_input_tokens`. The status line defines current context input as the sum of the three input buckets, not including output. [Status line](https://code.claude.com/docs/en/statusline). [Prompt caching](https://code.claude.com/docs/en/prompt-caching).

There is no official page, that this repo found, specifying that one API call is written as several JSONL lines. Public notes on the file format say a single `message.id` is repeated once per content block, with the same input and cache figures and a growing `output_tokens`. Summing every line over-counts. This analyzer keeps the last record for each `(session id, message id)`. That is a parser convention for an append-only log, not an Anthropic guarantee. The Agent SDK docs separately warn that per-step `output_tokens` on the streaming API can be a placeholder and that the result message holds the cumulative total. For a headless benchmark, prefer the result message, and prefer `modelUsage` over top-level `usage` because `usage` omits subagents while `modelUsage` and `total_cost_usd` include them.

Compaction counts only structural markers (`subtype` containing `compact`, `isCompactSummary`, `compactMetadata`). The parser does not read message text to guess. A summary that lacks those fields is missed.

### T3 Code

T3 Code is a third-party app that drives Claude through the Agent SDK. It is not the Claude desktop app and not the official terminal UI. Its provider doc sets "Auto-compact after" to an integer from 100,000 to 1,000,000, which is the window knob. [providers-claude.md](https://github.com/pingdotgg/t3code/blob/main/docs/user/providers-claude.md). Their issue 24649 reports an entrypoint difference in how the five-hour meter moved per token, later described as closed for a `t3-code` entrypoint on one Pro-plan check. That is their report. The protocol in `benchmarks/client_comparison.md` says what you can measure without taking it as a rate.

## What the model predicts

Run `python -m cc_usage_study.simulate`. The figures land in `outputs/figures/`, and `outputs/summary.json` is the source for the numbers below. They are consequences of the assumptions. They are not his usage.

Default long session:

| Assumption | Value |
| --- | --- |
| Context window | 1,000,000 |
| Productive turns | 120 |
| New tokens per turn | 20,000 |
| Output tokens per turn | 2,000 |
| System prompt + project context | 12,000 + 8,000 |
| Summary | 4,000 |
| Files re-read after compaction | 6,000 |
| Cache hit rate on the stable prefix | 0.95 |
| Cache read / 1-hour write / output multipliers | 0.1 / 2.0 / 5.0 |
| Rework turns per compaction | 0 |

The output multiplier of 5 matches the published output-to-input ratio for several current models (Opus 5.5 is 20/4, Sonnet 5.5 is 10/2). It is not universal. With caching on, the model assigns every prompt token to either a cache read or a cache write. Real calls also report a small uncached tail.

25 percent of the 1M window (250,000 tokens) against 95 percent (950,000):

| | 25 percent | 95 percent | ratio |
| --- | --- | --- | --- |
| Prompt tokens | 20,531,000 | 59,092,000 | 0.347 |
| API-equivalent, in base input tokens | 10,046,350 | 17,177,350 | 0.585 |
| Compactions | 11 | 2 | |

The 95 percent session sends about 2.9 times as many prompt tokens as the 25 percent session. The priced total is about 1.7 times as high, not 4 times. A naive 250/950 ratio is 0.263, about 3.8x, which is the "almost 4x" neighborhood. This scenario does not get there: the summary and the re-injected prefix are not zero, and the low trigger pays for eleven summary calls that each resend a full context.

Against the documented about-967K trigger, a 250,000 token threshold has raw ratio 0.346 and priced ratio 0.583. Same story, because 967K is close to 950K.

Other runs from the same file:

- Cache reads at 0.05x (Opus 5.5 and Sonnet 5.5 list price): raw ratio stays 0.347, priced ratio rises to 0.635. Cheaper reads make the large context less painful, so shrinking it saves less.
- Caching off: priced ratio 0.362, close to the raw ratio. That is the no-cache bound. Claude Code does not run there by default.
- At 10 percent, raw prompt tokens fall to 12,383,000, but the priced total is 9,665,830, only slightly under the 25 percent priced total. Compactions rise to 39. On a cache-priced bill, pushing past 25 percent buys little.
- Short session (30 turns, 8,000 new tokens and 2,000 output per turn): the 95 percent trigger never fires. Raw ratio 0.752 (3,901,000 against 5,190,000), one compaction against zero. The direction is still lower, and it is not a 4x effect.
- Rework: at about 7 extra repair turns after every compaction, the priced ratio crosses 1 while the raw ratio is still about 0.6. Lost context is how the saving disappears even though the sawtooth looks smaller.

Figures:

- `outputs/figures/cumulative_raw_tokens.png`
- `outputs/figures/cumulative_api_equivalent.png`
- `outputs/figures/threshold_sweep.png`
- `outputs/figures/context_sawtooth.png`
- `outputs/figures/sensitivity_cache_hit.png`
- `outputs/figures/sensitivity_rework.png`
- `outputs/figures/short_session.png`

## Run it

Python 3.11 or newer.

```bash
python3 -m pip install -e ".[dev]"
python3 -m pytest -q
python3 -m cc_usage_study.simulate --out outputs
python3 -m cc_usage_study.analyze fixtures/synthetic/projects --split-date 2026-10-01 --sessions
python3 -m cc_usage_study.benchmark --task median_bug --percents 95,25
```

`make test`, `make simulate`, `make analyze-fixture`, and `make benchmark-plan` wrap the same commands.

The fixture under `fixtures/synthetic/` is fake. The report sets `"synthetic": true`. A canary string in the fixture prompt text must not appear in the report. Do not publish fixture totals as usage.

The benchmark defaults to a dry run. `--execute` calls `claude` and spends quota. Read `benchmarks/protocol.md` first. The three scaffold tasks are short on purpose: they score a pass and a fail, and they do not fill a 1M window. A long task has to be added before a live grid says anything about compaction.

## What to run locally

The simulation and the tests run anywhere. The claims about his account only move if the following happens on his machine.

1. Note `claude --version` and the model. The about-967K default is for current native 1M models, and it moved for Sonnet 5 in v2.1.323.
2. Write down the exact keys in `~/.claude/settings.json`: `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`, `CLAUDE_CODE_AUTO_COMPACT_WINDOW`, `autoCompactWindow`, and any per-model `modelSettings` window. A percentage and a token window compound. Also note the date the 25 percent value was introduced. Without that date the split is a guess.
3. Analyze the transcripts. This prints aggregates only.

```bash
python3 -m cc_usage_study.analyze ~/.claude/projects \
  --split-date YYYY-MM-DD \
  --sessions \
  --output outputs/local-report.json
```

Compare `prompt_tokens_per_session_median` and `prompt_tokens_per_call`, and the four buckets, not the grand total. A lighter week looks like a saving either way. Subagent files are included and tagged. Project paths are hashed unless the path is the synthetic fixture. Prompt text is dropped, including on parse errors.

4. If list price is useful, the report estimates it only when the model is in the 13 November 2026 pricing snapshot and every cache write has a 5-minute versus 1-hour split. Otherwise the estimate is null. The snapshot lives in `cc_usage_study.pricing`. It will go stale. It is not the subscription bill.
5. For the limits themselves, copy the five-hour and weekly percentages from Settings > Usage for the same periods. The JSONL files do not contain them. An otherwise idle account is the only way a live benchmark's meter delta means anything.
6. For a controlled check, dry-run the harness, then a pilot: one task that actually reaches the high trigger, 25 percent against the default, both the window method and the percent method, three repeats. Full grid instructions, and the reasons not to run 90 sessions, are in `benchmarks/protocol.md`.
7. For the app versus CLI claim, follow `benchmarks/client_comparison.md`. Match the compaction setting before calling a difference a client effect. The desktop chat product does not write this JSONL format.

## Layout

```text
src/cc_usage_study/model.py        simulation
src/cc_usage_study/simulate.py     plots and summary.json
src/cc_usage_study/analyze.py      transcript aggregates
src/cc_usage_study/benchmark.py    dry-run and live harness
src/cc_usage_study/pricing.py      published price snapshot
benchmarks/protocol.md             controlled runs
benchmarks/client_comparison.md    desktop, CLI, T3 Code
benchmarks/tasks/                  three short scored tasks
fixtures/synthetic/                fake JSONL for the analyzer
docs/article-outline.md            draft outline for nevolin.be
outputs/                           generated figures and summary
```

## Caveats

- The long-session token sizes, other than the documented about-967K trigger, are assumptions chosen so that both triggers actually fire. A different growth rate changes the ratio. The code is the place to change them.
- No meter weight is implemented, because none is published.
- The percentage override cannot delay compaction. The 95 percent arm of a percent-method experiment may be identical to the default.
- Earlier compaction can lower tokens and still make the task worse. The benchmark scores tests for that reason. The rework curve is the simulation's version of the same risk.
- Headless `--bare` is the wrong mode for a subscription-limit question. It requires an API key and skips the subscription login.

## License

MIT. See `LICENSE`.
