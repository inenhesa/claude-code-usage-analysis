# Finding the Auto-Compaction Sweet Spot in Claude Code

On a native 1M model, Claude Code waits until about 967k tokens before it auto-compacts. A long coding thread spends the day re-sending that history. Prompt caching already discounts a repeated prefix by 90 to 95 percent, and every compaction has a fixed cost, so the cheapest trigger is a valley, not "as early as possible" and not the default.

This piece finds that valley across ordinary developer workflows. A second question stays in its own lane: what changes when you move between Claude Desktop, the official CLI, and T3 Code. The workflow table is the caching model. The live numbers come from 14 Claude Opus 5.5 sessions run on 13 November 2026 with a dedicated API key, n=1 per arm, and are stored in `outputs/live_experiments.json`. Every change is cache-weighted usage: tokens weighted the way the API meters them, on Opus 5.5 `1 / 1.25 / 2 / 0.05 / 5` (input, 5-minute write, 1-hour write, read, output). Where a model's read weight changes the answer, the 0.1x and 0.025x cases are given too.

## The setting to copy

For everyday deep work on a native 1M model, set **one** knob in the 150k-200k band:

```bash
CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=18 claude
```

```bash
claude --autocompact 180000
```

Neither fires exactly where it reads. Claude Code v2.1.325 (functions `tle` and `WX`) subtracts an output reserve of min(maxOutputTokens, 20,000) from the window, then a 13,000-token buffer. `--autocompact 180000` fires at 147,000. `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=18` on a 1M window fires at floor(980,000 x 0.18) = 176,400. The Opus 5.5 valley is flat enough that both land within a point of its floor: `--autocompact 180000` is -43.2% in the model against a -43.3% minimum. Do not set both. Stacking a window and a percentage multiplies: a 250,000 window with 25 fires at 57,500. The arithmetic is in `docs/reference.md`.

On a 200k-context model, including a native 1M model held there with `CLAUDE_CODE_DISABLE_1M_CONTEXT=1`, leave the default. The default already fires at 167,000. `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=25` fires at 45,000, under the 46,000 a summary leaves behind, and the session thrashes: **+239%** in this model.

These are cache-weighted units from the simulator, not the five-hour meter. A 60-turn deep-work session at 0.1x reads uses **57%** less at the 120k valley than at the default. The same shape at Opus 5.5's 0.05x reads uses **43%** less at 160k. Weights: [the pricing page](https://platform.claude.com/docs/en/about-claude/pricing), snapshot 13 November 2026.

## Before any trigger: the pause tax

The setting matters less than when you come back. We measured it: a session built up to about 97k tokens, left alone for 10 to 13 minutes on a 5-minute cache, then resumed for a quick bugfix, used **about 10 times** as much as doing the same fix in a fresh session after `/clear` (9.8 to 9.9 times on Opus 5.5, in 3 of 3 pairs). The resumed session's first call wrote 86k to 88k tokens back into the cache. The fresh one wrote about 5,500. Resumed after 3.3 minutes instead, with the cache still warm, the same kind of session read 96,730 tokens from the cache and wrote 743, and used 6.5 times less than the stale resume, on almost the same number of raw tokens.

Claude Code caches the prompt prefix for **5 minutes** on an API key or credits and **1 hour** on a subscription. Inside that window a resumed session re-reads its history at the read weight. After it, every old token is written again at the write weight: 12.5 to 40 times a warm read, depending on the cache and the model.

In the model, a 5-call next task resumed after the cache expired loses to `/clear` plus a three-line handoff once history passes about **10k tokens** (8.3k on the 1-hour cache), even if the fresh session would have needed 2 extra exploration calls. It is **+41%** at 30k of history, **2.02 times** at 60k, and **3.25 times** at 120k. With no exploration calls saved, the stale resume always loses. Inside the window the picture flips for small histories: a hot resume skips rewriting about 5,000 tokens of session prefix (the live run saved 4,985 per avoided `/clear`) and wins while history times remaining calls stays under about 120k token-calls at 0.05x reads.

Scheduled `claude -p --resume` jobs are the extreme. Twelve runs an hour apart rebuild their whole growing history cold every time, and fresh runs use **33%** to **35%** of it. The 1-hour case assumes each run starts more than an hour after the previous one ended.

The live check agrees with the model's mechanism and is harsher in size, because the live fix was small, so the fresh start was cheap. Even warm, carrying 97k of history into an unrelated fix used 1.5 times a fresh start; the model predicted about 1.4 times at that size. The extra cost per carried token was about 1.4 units in all three pairs. These are single runs per condition (one warm run, three stale and fresh pairs) on one model and one kind of task. The cron and 1-hour numbers are still modeled.

## Where the valley actually sits

Every row is the same physical-cache simulator, sweeping the trigger and keeping the cheapest point.

| Workflow | Sweet spot | Cost vs that workflow's default |
| --- | ---: | ---: |
| Deep feature build, 0.1x reads (Sonnet 5, Opus 4.7) | 120k | -57% |
| Deep feature build, 0.05x reads (Opus 5.5, Sonnet 5.5) | 160k | -43% |
| Marathon refactor, 150 turns, smaller steps | 130k | -54% |
| Heavy file reads and a large retained context | 280k | -24% |
| Rules that live only in the chat, 8 repairs per compact | 360k | -24.9% |
| Four long breaks, idle compaction on | 140k | -18% |
| Quick bugfix | 180k does nothing | 0%, or +8% if forced to 100k |
| 200k-context model | leave the 167,000 default | +239% with override 25 (fires at 45,000) |

Two patterns matter more than any single cell. Heavier working memory pushes the valley up: 30,000 tokens re-injected after each summary, plus larger file reads, moves the cheapest trigger from 160k to **280k**. Losing rules after each summary pushes it further, to **360k**. The first fix is root `CLAUDE.md`, not a lower trigger. Anthropic documents [what survives compaction](https://code.claude.com/docs/en/context-window). Root `CLAUDE.md` comes back. A rule that lived only in the middle of the chat often does not.

A short bugfix is the other edge. Three user turns of ten calls never reach 180k, so that setting matches the default at **0%**. Force 100k and the estimated cost is **8% higher**, even though the raw token count falls. `/clear` is the right tool between unrelated tasks.

## What 14 live runs showed

The model says a low trigger pays on a long session. The live runs asked what it costs on a medium one.

Five arms ran the same 4-phase `LedgerKit v3.2` migration on Opus 5.5, effort medium, 5-minute cache. The default arm never compacted and peaked at 270,132 tokens.

| Arm | Fires at | Compactions | Usage, default = 100 | Hidden share | Raw tokens vs default | At 0.1x reads |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Default 1M | 967,000 | 0 | 100 | 0% | 6.12M tokens | 100 |
| 180k | 147,000 | 2 | 100 (tie, +0.1%) | 11.8% | -48% | -13.6% |
| 250,000 window + 25 | 57,500 | 6 | +13.6% | 24.3% | -70% | -10.2% |
| 140k | 107,000 | 4 | +18.1% | 13.9% | -42% | +1.0% |
| 100k | 67,000 | 7 | +25.2% | 25.5% | -63% | +0.1% |

Three things are not in the usual advice. First, the summary request each compaction sends writes no assistant row in the transcript. Claude Code's own `cost_state` total includes it. Across 19 compactions it averaged about 36.6k units each, and on the 100k arm it was 25.5% of the usage. Second, the transcript alone undercounts every compacting arm, by exactly that hidden share, so it makes a low trigger look better than it is. Counting the hidden calls, only 180k tied and the rest used 13.6% to 25.2% more. Third, fewer tokens is not less usage: the 100k arm sent 63% fewer raw tokens.

Read the tie carefully. It is n=1, on a session that tops out near 270k, with no pause past the 5-minute cache. One such pause at ~260k of history would add roughly 39% to the default arm. The ranking also depends on the read weight: at 0.1x reads the 180k arm is 13.6% under the default. And two arms fail the repo's own validity gate, because their first `preTokens` landed more than 15% past the line (180k at +15.7%, the compound arm at +55.5%).

Quality did not move. All five arms passed their tests and the functional checker and reported all 12 planted anomalies. Two arms trip an `INV-02` AST rule only because they routed a timestamp through the sanctioned `clock.now()`.

Short work points the same way while the cache is warm. Three quick fixes in three fresh sessions, the same as `/clear` between them, used 37.7k units. The same three in one resumed session used 27.2k, **27.7% less** (21.1% to 32.0% across read weights), on 7.0% more raw tokens. The fresh sessions wrote 18,853 cache tokens at the 1.25x weight to rebuild the same prefix. The warm one wrote 8,884 and re-read the rest at 0.05x. Its context peaked at 20,265 tokens, which is why the pause tax above did not bite.

## Two levers, not one story

Part 1 is the trigger. Part 2 is the client. Desktop chat, the Desktop Code tab, `claude` in the terminal, and T3 Code do not send the same background traffic, keep logs for the same length of time, or expose the same boundary control. Change both in one week and you cannot tell which one moved the meter. The client protocol is `benchmarks/client_comparison.md`.

## Three things hiding behind "usage"

One message you type can cause several model requests, because each tool batch is another call. Anthropic's [prompt-caching guide](https://code.claude.com/docs/en/prompt-caching) describes the re-send.

If each request adds roughly the same new input, and nothing truncates the history, cumulative prompt tokens grow with the square of the request count. That is request accounting. It is not a claim that every token is processed from scratch at full price.

Caching changes the price. An unchanged prefix can be a cheap cache read. A warm summary can still read the old prefix. The project context is rebuilt. The system prompt can stay cached. "Compaction invalidates the cache" is too blunt.

Then there is the subscription meter. Anthropic says length, complexity, model, effort, and features affect usage. It does not publish a formula from those buckets to the five-hour bar. The [allowance is shared](https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work) across claude.ai, Claude Code, and Claude Desktop.

| Quantity | What it tells you |
| --- | --- |
| Raw prompt tokens | How much input the requests carry, including cached input |
| Cache-weighted usage | What those buckets weigh at the published per-bucket weights |
| Allowance movement | How far the five-hour and weekly meters actually moved |

A large change in the first row does not establish the same change in either of the others. On the 60-turn Opus 5.5 / Sonnet 5.5 session, moving from about 967k to the 160k valley trims raw prompt tokens by about **77%** and cache-weighted usage by about **43%**. Caching is the whole reason those two numbers differ.

## A long session you can audit

The deep-feature baseline is 60 user turns of 8 model requests. A user turn is a message a person sends. A model request is one API call inside that turn.

Each request adds 4,000 tokens of new input and 600 tokens of output. Startup context is 28,000. After a compaction the residual is 46,000 (system, project, a 3,000-token summary, 15,000 tokens of re-injected files) before the next 4,000 of new input. A warm request reads the previous prompt and writes only the previous output plus the new tail. Reads are priced at 0.1x, 1-hour writes at 2x, output at 5x. No repair work, no long pauses.

The first prompts are 32,000, then 36,600, then 41,200. The default arm keeps going until a summary request of about 967,400 tokens. The 160k arm, at the 0.05x read price, compacts 19 times instead of 2.

At the standard 0.1x read price the same shape bottoms at **120k**, **57%** below the default. At 0.025x (Fable 5.1 and 5.2) the valley is higher and the saving is smaller, about 28% near 220k. Cheaper reads make a large context hurt less, so shrinking it saves less money. If reads were free, compacting earlier would not help.

The current [model documentation](https://code.claude.com/docs/en/model-config) says native 1M sessions compact at about 967k by default. Other models differ. `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` cannot raise the threshold. An absolute window is a separate control (`autoCompactWindow`, `/autocompact`, `--autocompact`, or `CLAUDE_CODE_AUTO_COMPACT_WINDOW`). Use one. Then read `preTokens` on the compaction event.

## Caching already makes history cheap

That is the 77% versus 43% gap above.

Timing still matters. With four 75-minute breaks and idle compaction running, the extra saving from also lowering the trigger falls to about **18%**, with the valley near **140k**, because the high arm is compacted while the cache is still warm. Idle compaction needs four things at once: `idleCompaction` on, the 1-hour cache, the Claude Code process still alive through the break, and at least `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS` of context (200,000 by default, 100,000 at the lowest). Close the laptop over lunch and none of it runs: the same day costs **76% more**, because each return rewrites the full cold context. And `--autocompact 180000` fires at 147,000, so the session never reaches the 200,000 minimum. Pair it with `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS=100000` if you rely on idle compaction.

On a 5-minute cache (usage credits, or an API key) with 7-minute gaps, almost every user turn rewrites the context: **+213%** against answering within 2 minutes. Earlier compaction helps more there.

When you run `/compact` matters as much. Measured live on Opus 5.5 with the same ~97k-token session and the same quick fix: `/compact` right away, while the cache was warm, then a 6.5 to 11 minute break, cost **2.5 times** a fresh `/clear` start in total (n=3), compact call included. Taking the break first and compacting after cost **9.3 and 10.2 times** (n=2), about the same as just resuming, because the summary request sends the whole cold history again. Anthropic's [cost guide](https://code.claude.com/docs/en/costs) also suggests compacting before you step away, and its [prompt caching page](https://code.claude.com/docs/en/prompt-caching) lists which requests get the 5-minute or 1-hour TTL.

There are public counterexamples to compacting out of habit. In an [HN thread](https://news.ycombinator.com/item?id=47739260), Boris Cherny pointed at large-context cache misses, skills, and background agents. [Rulestack](https://dev.to/rulestack/claude-codes-compact-cost-006-at-136k-tokens-with-a-warm-cache-9x-that-cold-and-clear-sent-85f) reports a warm-versus-cold compaction gap. [Learn LLM](https://learn-llm.com/p/raw-tokens-fell-3960-the-bill-went/) reports fewer tokens and a higher client-estimated cost when only a few follow-up questions remained. Those experiments are not reproduced here. They are the right objection: a summary has a payback period.

## The agent forgets something important

Imagine an invoice migration with one rule: round only the final amount, never each line item.

The summary drops it. The agent writes a plausible patch. A test fails. It re-reads files, edits again, and runs more tests. Those requests count.

With eight repair requests of 8,000 tokens after every compaction, the cheapest trigger in this model jumps to **360k**, and the saving against the default shrinks to about **24.9%**. A separate sensitivity run, fifteen repairs of the same size, nearly wipes a more aggressive comparison down to a **3%** gap. Neither number is a law of how models think.

The number that matters is cost per finished task, with the rule checked by a test the agent did not get to edit. Fewer tokens and a broken migration is a failed optimization.

## Part 2: Desktop, the CLI, and T3 Code

A client switch is not a compaction result. Test it with the window held fixed.

Claude Desktop chat, its Code tab, the official interactive `claude` CLI, and T3 Code are not four skins on an identical request. T3 Code drives Claude through the Agent SDK. Its [provider documentation](https://raw.githubusercontent.com/pingdotgg/t3code/main/docs/user/providers-claude.md) exposes an absolute "Auto-compact after" value and a separate idle-thread policy: When you return, Before the cache expires (beta), or Never.

A switch can change the model, the effort, the tools, the instructions, the idle policy, or the window. Any of those can change consumption. Measure `/context` before you compare.

The live comparison held the model, effort, `CLAUDE.md`, and MCP setup fixed, and measured what each client sends.

| | CLI `-p` | CLI interactive | T3 Code v0.1.9 | Desktop v5.1231.2 Code tab |
| --- | ---: | ---: | ---: | ---: |
| First request, tokens | 16,850 | 19,528 | 22,842 | 39,749 |
| Inline / deferred tools | 13 / 13 | 14 / 15 | 15 / 82 | 35 / 82 |
| System prompt, characters | 7,227 | 7,485 | 12,857 | 25,865 |
| `noisy_bughunt` fix, usage vs CLI | | 1.00x | 1.19x | 1.80x |

T3 Code drives the same Claude Code v2.1.325 as the CLI, so the guess was that it would match. It does not. It adds 67 `mcp__t3-code__*` deferred tools, an inline `request_secret` tool, and 5,372 system-prompt characters, including `<runtime_info>` and `<pull_request_linking>` blocks. The first request is 17% larger and the fix used 1.19 times the CLI's main-thread rows, or 1.09 times the CLI's total with its background title and side calls. Desktop's Code tab bundles Claude Code v2.1.321 with browser preview, session, and terminal tools: 2.04 times the interactive CLI's first request and 1.80 times its fix usage (2.39 times in raw tokens). All three fixed all three bugs. None of these first requests was a true cold start: each read 63% to 68% of its prefix from a warm shared cache.

A [T3 issue](https://github.com/pingdotgg/t3code/issues/24649) initially reported faster five-hour-meter consumption than the interactive CLI for similar tokens. A later small check reported parity. An upstream report says headless `claude -p` (entrypoint `sdk-cli`) can move the five-hour meter faster per token than interactive `cli`. These are version-specific reports, not an official formula. They do not say the CLI is automatically cheaper.

Match the effective window (`raw_max_tokens` from `/context`), repeat comparable tasks in both orders, and record token buckets where the transcripts exist. Desktop chat does not write this JSONL format. Those cells stay empty. They do not get guessed numbers.

T3 also writes titles, commit messages, and PR text in runs that may save no session history. A JSONL total can miss them. The meter will not. The report that `sdk-cli` and interactive `cli` can meter differently is [claude-code#97074](https://github.com/anthropics/claude-code/issues/97074).

## What a local log still has to show

Measured in this study, 13 November 2026: setting keys and effective windows (`--autocompact` 180k, 140k, 100k, and a 250,000 window with `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=25`), model `claude-opus-5-5`, effort medium, client versions (Claude CLI v2.1.325, T3 Code v0.1.9, Claude Desktop v5.1231.2), the four token buckets, every compaction `preTokens`, entrypoints, cost per completed task, test and invariant results. One repeat per arm.

Not measured: five-hour and weekly meter percentages, because every run used an API key with the 5-minute cache, not a subscription with the 1-hour cache. Long sessions past 300k tokens, the 1-hour cache, and scheduled jobs were modeled, not run. The 5-minute pause tax was run on 14 November 2026: one warm resume and three stale and fresh pairs, n=1 each. A local log of your own fills in the rest.

The analyzer emits aggregates, not prompt text. The source logs can still contain source code and tool output. Run it only where that is allowed, and read the aggregate before it leaves the machine. If the change was more than the default 30 days of transcript retention ago, the earlier CLI logs may already be gone. Desktop and Cowork transcripts are kept.

## A practical playbook

Look at a fresh session with `/context`. Remove instructions and noisy tool output you do not need.

Use `/clear` for unrelated tasks. For quick related fixes minutes apart, stay in the session: in the live runs that used 27.7% less. After a pause longer than the cache window, `/clear` and paste a three-line handoff instead of resuming: in the live runs, resuming after a 10-minute break used about 10 times as much.

For genuinely long work on a 1M model, try one setting in the valley. Do not set both:

```bash
CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=18 claude
claude --autocompact 180000
```

If your sessions end under about 270k tokens and you do not pause past the cache window, leave the default: in the live runs every lower trigger tied or lost once the hidden summary calls were counted. Keep the model and effort fixed. If the working set is huge, or the rules are not in root `CLAUDE.md`, the valley is higher than 180k. Treat the table as a place to test, not as a prescription. Record the actual boundaries.

Check the result with tests. If repairs increase, move the trigger back up or put the rule in root `CLAUDE.md`.

Measure the allowance separately, in percentage points, not as a guessed dollar conversion.

## Objections worth keeping

| Objection | Answer |
| --- | --- |
| You rediscovered a cheap cache read. | The usage gap is smaller than the token gap. A short task can lose. Forcing 100k on a bugfix does. |
| The summary forgets the rule. | Test it. Root `CLAUDE.md` and executable checks beat a lower token graph. Eight repairs moved the valley to 360k. |
| That bar is not a token counter. | Agreed. Meter percentage points stay in their own column. |
| The before and after was a quieter week. | Pair tasks. A historical split stays labeled observational. |
| Why not `/clear`? | Right tool for unrelated work. Wrong tool for an unfinished investigation: there, `/compact` before the break (2.5 times a fresh start, measured) beats resuming cold (about 10 times). |
| One client is guaranteed cheaper. | It is not. Some reports found the opposite direction on the five-hour meter. |
| Repair counts are universal. | No. Eight repairs and fifteen repairs are two sensitivity runs. |
| Pauses do not matter. | They matter most. Measured live, resuming a 97k session after a 10-minute break used about 10 times a fresh start, and a lapsed idle compaction adds 76% to a day in the model. |
| The transcript shows what compaction cost. | It does not. The summary request writes no assistant row. Use `cost_state` or OTel. In the live 100k arm the gap was 25.5% of the bill. |
| T3 Code is the CLI with a different window. | It wraps the same Claude Code, then adds 67 deferred tools and 5,372 prompt characters. The same fix used 1.19 times as much. |
