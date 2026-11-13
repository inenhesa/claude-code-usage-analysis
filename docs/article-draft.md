# Finding the Auto-Compaction Sweet Spot in Claude Code

On a native 1M model, Claude Code waits until about 967k tokens before it auto-compacts. A long coding thread spends the day re-sending that history. Prompt caching already discounts a repeated prefix by 90 to 95 percent, and every compaction has a fixed cost, so the cheapest trigger is a valley, not "as early as possible" and not the default.

This piece finds that valley across ordinary developer workflows. A second question stays in its own lane: what changes when you move between Claude Desktop, the official CLI, and T3 Code. The workflow table is the caching model. The live numbers come from 14 Claude Opus 5.5 sessions run on 13 November 2026 with a dedicated API key, $18.57 in total, and are stored in `outputs/live_experiments.json`.

## The setting to copy

For everyday deep work on a native 1M model, set **one** knob in the 150k-200k band:

```bash
CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=18 claude
```

```bash
claude --autocompact 180000
```

Neither fires exactly where it reads. Claude Code v2.1.325 (functions `tle` and `WX`) subtracts an output reserve of min(maxOutputTokens, 20,000) from the window, then a 13,000-token buffer. `--autocompact 180000` fires at 147,000. `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=18` on a 1M window fires at floor(980,000 x 0.18) = 176,400. The Opus 5.5 valley is flat enough that both land within a point of its floor. Do not set both. Stacking a window and a percentage multiplies: a 250,000 window with 25 fires at 57,500. The arithmetic is in `docs/reference.md`.

On a 200k-context model, including a native 1M model held there with `CLAUDE_CODE_DISABLE_1M_CONTEXT=1`, leave the default. Forcing the trigger down to 40k or 50k costs **227% more** in this model. A trigger near 120k is only about **6%** under the 200k default, which is not a reason to chase a low percentage.

One priced example, from the published list price times the simulated units, not from the five-hour meter. A 60-turn Sonnet 5 session at $2 per million input tokens is **$56.07** at the default and **$23.90** at the 120k valley. The same shape on Opus 5.5, $4 per million and 0.05x reads, is **$68.16** at the default and **$38.64** at 160k. Prices: [the pricing page](https://platform.claude.com/docs/en/about-claude/pricing), snapshot 13 November 2026.

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
| 200k-context model | near the 200k default | +227% if forced to 50k |

Two patterns matter more than any single cell. Heavier working memory pushes the valley up: 30,000 tokens re-injected after each summary, plus larger file reads, moves the cheapest trigger from 160k to **280k**. Losing rules after each summary pushes it further, to **360k**. The first fix is root `CLAUDE.md`, not a lower trigger. Anthropic documents [what survives compaction](https://code.claude.com/docs/en/context-window). Root `CLAUDE.md` comes back. A rule that lived only in the middle of the chat often does not.

A short bugfix is the other edge. Three user turns of ten calls never reach 180k, so that setting matches the default at **0%**. Force 100k and the estimated cost is **8% higher**, even though the raw token count falls. `/clear` is the right tool between unrelated tasks.

## What 14 live runs showed

The model says a low trigger pays on a long session. The live runs asked what it costs on a medium one.

Five arms ran the same 4-phase `LedgerKit v3.2` migration on Opus 5.5, effort medium, 5-minute cache. The default arm never compacted and peaked at 270,132 tokens.

| Arm | Fires at | Compactions | Visible | Hidden summary | Total |
| --- | ---: | ---: | ---: | ---: | ---: |
| Default 1M | 967,000 | 0 | $3.17 | $0.00 | $3.17 |
| 180k | 147,000 | 2 | $2.80 | $0.37 | $3.17 |
| 250,000 window + 25 | 57,500 | 6 | $2.73 | $0.87 | $3.60 |
| 140k | 107,000 | 4 | $3.22 | $0.52 | $3.74 |
| 100k | 67,000 | 7 | $2.96 | $1.01 | $3.97 |

Two things are not in the usual advice. First, the summary request each compaction sends writes no assistant row in the transcript. Claude Code's own `cost_state` total includes it, and the gap is $0.08-$0.34 per compaction. On 100k it is $1.01, 25.5% of the bill. Second, the visible column alone would tell you every low trigger saved money. The total says only 180k broke even, and the rest lost 14% to 25.2%. A session that tops out near 270k has not run long enough to repay its summaries.

Quality did not move. All five arms passed their tests and the functional checker and reported all 12 planted anomalies. Two arms trip an `INV-02` AST rule only because they routed a timestamp through the sanctioned `clock.now()`.

Short work points the same way. Three quick fixes in three fresh sessions, the same as `/clear` between them, cost $0.1507. The same three in one resumed session cost $0.1089, 27.7% less. The fresh sessions wrote 18,853 cache tokens at $5.00 per million to rebuild the same prefix. The warm one wrote 8,884 and re-read the rest at $0.20.

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
| Cached API cost | What those buckets imply at published prices |
| Allowance movement | How far the five-hour and weekly meters actually moved |

A large change in the first row does not establish the same change in either of the others. On the 60-turn Opus 5.5 / Sonnet 5.5 session, moving from about 967k to the 160k valley trims raw prompt tokens by about **77%** and estimated API cost by about **43%**. Caching is the whole reason those two numbers differ.

## A long session you can audit

The deep-feature baseline is 60 user turns of 8 model requests. A user turn is a message a person sends. A model request is one API call inside that turn.

Each request adds 4,000 tokens of new input and 600 tokens of output. Startup context is 28,000. After a compaction the residual is 46,000 (system, project, a 3,000-token summary, 15,000 tokens of re-injected files) before the next 4,000 of new input. A warm request reads the previous prompt and writes only the previous output plus the new tail. Reads are priced at 0.1x, 1-hour writes at 2x, output at 5x. No repair work, no long pauses.

The first prompts are 32,000, then 36,600, then 41,200. The default arm keeps going until a summary request of about 967,400 tokens. The 160k arm, at the 0.05x read price, compacts 19 times instead of 2.

At the standard 0.1x read price the same shape bottoms at **120k**, **57%** below the default. At 0.025x (Fable 5.1 and 5.2) the valley is higher and the saving is smaller, about 28% near 220k. Cheaper reads make a large context hurt less, so shrinking it saves less money. If reads were free, compacting earlier would not help.

The current [model documentation](https://code.claude.com/docs/en/model-config) says native 1M sessions compact at about 967k by default. Other models differ. `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` cannot raise the threshold. An absolute window is a separate control (`autoCompactWindow`, `/autocompact`, `--autocompact`, or `CLAUDE_CODE_AUTO_COMPACT_WINDOW`). Use one. Then read `preTokens` on the compaction event.

## Caching already makes history cheap

That is the 77% versus 43% gap above.

Timing still matters. Four 75-minute breaks with idle compaction off make the priced gap larger, because the high trigger pays to rewrite a huge cold context. Turn idle compaction on, which is the default on a recent interactive session once you are over 200k tokens, and the extra saving from also lowering the trigger falls to about **18%**, with the valley near **140k**. The high arm is compacted while the cache is still warm.

On a 5-minute cache (usage credits, or an API key) with 7-minute gaps, almost every user turn rewrites the context, and earlier compaction helps more. That is not the default inside the plan.

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
| Cold first request, tokens | 16,850 | 19,528 | 22,842 | 39,749 |
| Inline / deferred tools | 13 / 13 | 14 / 15 | 15 / 82 | 35 / 82 |
| System prompt, characters | 7,227 | 7,485 | 12,857 | 25,865 |
| `noisy_bughunt` fix | | $0.1145 | $0.1362 | $0.2057 |

T3 Code drives the same Claude Code v2.1.325 as the CLI, so the guess was that it would match. It does not. It adds 67 `mcp__t3-code__*` deferred tools, an inline `request_secret` tool, and 5,372 system-prompt characters, including `<runtime_info>` and `<pull_request_linking>` blocks. The cold prefix is 17% larger and the fix cost 19% more than the CLI's main-thread rows, or 9% more than the CLI's total with its background title and side calls. Desktop's Code tab bundles Claude Code v2.1.321 with browser preview, session, and terminal tools: 2.04 times the interactive CLI's cold prefix and 1.80 times its fix cost. All three fixed all three bugs.

A [T3 issue](https://github.com/pingdotgg/t3code/issues/24649) initially reported faster five-hour-meter consumption than the interactive CLI for similar tokens. A later small check reported parity. An upstream report says headless `claude -p` (entrypoint `sdk-cli`) can move the five-hour meter faster per token than interactive `cli`. These are version-specific reports, not an official formula. They do not say the CLI is automatically cheaper.

Match the effective window (`raw_max_tokens` from `/context`), repeat comparable tasks in both orders, and record token buckets where the transcripts exist. Desktop chat does not write this JSONL format. Those cells stay empty. They do not get guessed numbers.

T3 also writes titles, commit messages, and PR text in runs that may save no session history. A JSONL total can miss them. The meter will not. The report that `sdk-cli` and interactive `cli` can meter differently is [claude-code#97074](https://github.com/anthropics/claude-code/issues/97074).

## What a local log still has to show

Measured in this study, 13 November 2026: setting keys and effective windows (`--autocompact` 180k, 140k, 100k, and a 250,000 window with `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=25`), model `claude-opus-5-5`, effort medium, client versions (Claude CLI v2.1.325, T3 Code v0.1.9, Claude Desktop v5.1231.2), the four token buckets, every compaction `preTokens`, entrypoints, cost per completed task, test and invariant results. One repeat per arm.

Not measured: five-hour and weekly meter percentages, because every run used an API key with the 5-minute cache, not a subscription with the 1-hour cache. Long sessions past 300k tokens were modeled, not run. A local log of your own fills those in.

The analyzer emits aggregates, not prompt text. The source logs can still contain source code and tool output. Run it only where that is allowed, and read the aggregate before it leaves the machine. If the change was more than the default 30 days of transcript retention ago, the earlier CLI logs may already be gone. Desktop and Cowork transcripts are kept.

## A practical playbook

Look at a fresh session with `/context`. Remove instructions and noisy tool output you do not need.

Use `/clear` for unrelated tasks. For quick related fixes minutes apart, stay in the session: in the live runs that was 27.7% cheaper.

For genuinely long work on a 1M model, try one setting in the valley. Do not set both:

```bash
CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=18 claude
claude --autocompact 180000
```

If your sessions end under about 270k tokens, leave the default: in the live runs every lower trigger tied or lost once the hidden summary calls were counted. Keep the model and effort fixed. If the working set is huge, or the rules are not in root `CLAUDE.md`, the valley is higher than 180k. Treat the table as a place to test, not as a prescription. Record the actual boundaries.

Check the result with tests. If repairs increase, move the trigger back up or put the rule in root `CLAUDE.md`.

Measure the allowance separately, in percentage points, not as a guessed dollar conversion.

## Objections worth keeping

| Objection | Answer |
| --- | --- |
| You rediscovered a cheap cache read. | The priced gap is smaller than the token gap. A short task can lose money. Forcing 100k on a bugfix does. |
| The summary forgets the rule. | Test it. Root `CLAUDE.md` and executable checks beat a lower token graph. Eight repairs moved the valley to 360k. |
| That bar is not a token counter. | Agreed. Meter percentage points stay in their own column. |
| The before and after was a quieter week. | Pair tasks. A historical split stays labeled observational. |
| Why not `/clear`? | Right tool for unrelated work. Wrong tool for an unfinished investigation. |
| One client is guaranteed cheaper. | It is not. Some reports found the opposite direction on the five-hour meter. |
| Repair counts are universal. | No. Eight repairs and fifteen repairs are two sensitivity runs. |
| Pauses do not matter. | They do. With idle compaction on, the extra saving from a lower trigger is about 18%. |
| The transcript shows what compaction cost. | It does not. The summary request writes no assistant row. Use `cost_state` or OTel. In the live 100k arm the gap was 25.5% of the bill. |
| T3 Code is the CLI with a different window. | It wraps the same Claude Code, then adds 67 deferred tools and 5,372 prompt characters. The same fix cost 19% more. |
