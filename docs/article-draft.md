# Finding the Auto-Compaction Sweet Spot in Claude Code

On a native 1M model, Claude Code waits until about 967k tokens before it auto-compacts. A long coding thread spends the day re-sending that history. Prompt caching already discounts a repeated prefix by 90 to 95 percent, and every compaction has a fixed cost, so the cheapest trigger is a valley, not "as early as possible" and not the default.

This piece finds that valley across ordinary developer workflows. A second question stays in its own lane: what changes when you move between Claude Desktop, the official CLI, and T3 Code. The numbers are the caching model. Slots marked `[MEASURED_LOCAL_DATA: ...]` stay empty until a real log is plugged in.

## The setting to copy

For everyday deep work on a native 1M model, set **one** knob in the 150k-200k band:

```bash
CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=18 claude
```

```bash
claude --autocompact 180000
```

`18` is 18% of a 1M window, which is 180k. `180000` is the window itself. Do not set both. Stacking a window and a percentage compounds, and the result can sit far below the valley. The arithmetic is in `docs/reference.md`.

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

A [T3 issue](https://github.com/pingdotgg/t3code/issues/24649) initially reported faster five-hour-meter consumption than the interactive CLI for similar tokens. A later small check reported parity. An upstream report says headless `claude -p` (entrypoint `sdk-cli`) can move the five-hour meter faster per token than interactive `cli`. These are version-specific reports, not an official formula. They do not say the CLI is automatically cheaper.

Match the effective window (`raw_max_tokens` from `/context`), repeat comparable tasks in both orders, and record token buckets where the transcripts exist. Desktop chat does not write this JSONL format. Those cells stay empty. They do not get guessed numbers.

T3 also writes titles, commit messages, and PR text in runs that may save no session history. A JSONL total can miss them. The meter will not. The report that `sdk-cli` and interactive `cli` can meter differently is [claude-code#97074](https://github.com/anthropics/claude-code/issues/97074).

## What a local log still has to show

[MEASURED_LOCAL_DATA: exact setting keys, effective window, date changed, model ids, client versions, plan, and effort.]

[MEASURED_LOCAL_DATA: before and after task counts, the four token buckets, compaction `preTokens`, entrypoint, and cost per successfully completed task. The historical split is observational.]

[MEASURED_LOCAL_DATA: five-hour and weekly start and end percentages for matched blocks, reset boundaries, and whether anything else used the account.]

[MEASURED_LOCAL_DATA: quality results, invariant failures, repair requests, human corrections, elapsed time, and the client comparison, or an explicit "not run."]

The analyzer emits aggregates, not prompt text. The source logs can still contain source code and tool output. Run it only where that is allowed, and read the aggregate before it leaves the machine. If the change was more than the default 30 days of transcript retention ago, the earlier CLI logs may already be gone. Desktop and Cowork transcripts are kept.

## A practical playbook

Look at a fresh session with `/context`. Remove instructions and noisy tool output you do not need.

Use `/clear` for unrelated tasks. Keep a short handoff when continuity matters.

For genuinely long work on a 1M model, try one setting in the valley. Do not set both:

```bash
CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=18 claude
claude --autocompact 180000
```

Keep the model and effort fixed. If the working set is huge, or the rules are not in root `CLAUDE.md`, the valley is higher than 180k. Treat the table as a place to test, not as a prescription. Record the actual boundaries.

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
