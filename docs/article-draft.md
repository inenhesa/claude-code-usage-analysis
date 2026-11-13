# Does Compacting Claude Code Earlier Actually Save Usage?

On a native 1M model, Claude Code waits until about 967k tokens before it auto-compacts. A long coding thread spends the day re-sending that history. This piece is about the trigger that cuts that cost for everyday work, and it keeps a second question in its own lane: what changes when you move between Claude Desktop, the official CLI, and T3 Code.

Prompt caching already discounts a repeated prefix by 90 to 95 percent, and every compaction has a fixed cost. Earlier is cheaper on a long session. Too early, or on a short bugfix, and the bill goes up. The numbers below are the caching model. Slots marked `[MEASURED_LOCAL_DATA: ...]` stay empty until a real log is plugged in.

## The sweet spot to copy

For Opus 5.5 and Sonnet 5.5, published cache reads are 0.05 times input, and the cheapest trigger in this model is about 160k: 43% below leaving the default alone. At the standard 0.1x read price the valley is about 120k, 57% below the default. A practical band for most people on a 1M model is 150k to 200k. Set one knob, not both:

```bash
CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=20 claude
claude --autocompact 180000
```

`20` is 20% of the current window. On a native 1M model that is 200k, the top of the band. `180000` sits inside it. A 250k window plus a 25% override is not 250k. In this model it is 62,500, it compacts 158 times instead of 10, and it costs 43% more than 250k alone.

On a 200k model, including a native 1M model held there with `CLAUDE_CODE_DISABLE_1M_CONTEXT=1`, do not use 25%. That fires at 50k. In this model the session costs 227% more than leaving the trigger at 200k. Stay near the default, or set an absolute window at or above 120k.

One priced example, from the published list price times the simulated units, not from the five-hour meter. A 60-turn Sonnet 5 session at $2 per million input tokens is $56.07 at the default and $23.90 at the 120k valley. The same shape on Opus 5.5, $4 per million and 0.05x reads, is $68.16 at the default and $38.64 at 160k.

## Two levers, not one story

Part 1 is the auto-compaction trigger: where earlier compaction cuts a long session, and which settings thrash. Part 2 is the client. Desktop chat, the Desktop Code tab, `claude` in the terminal, and T3 Code do not send the same background traffic, keep logs for the same length of time, or expose the same boundary control. Change both in one week and you cannot tell which one moved the meter. The client protocol is `benchmarks/client_comparison.md`.

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

A 3x change in the first row does not establish a 3x change in either of the others.

## What 25% actually sets

There is no published setting called "the default 95%."

The current [model documentation](https://code.claude.com/docs/en/model-config) says native 1M sessions compact at about 967k by default. Other models differ.

`CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=25` asks for compaction at 25% of the selected auto-compact window. It cannot raise the threshold. An absolute window is a separate control (`autoCompactWindow`, `/autocompact`, `--autocompact`, or `CLAUDE_CODE_AUTO_COMPACT_WINDOW`).

Set a 250,000-token window **and** apply 25%, and the trigger in this model is 62,500, not 250,000. That arm compacts 158 times instead of 10. Against the default it still looks cheaper. Against 250k alone it costs **43% more**. Use one control. Then read `preTokens` on the compaction event. A percentage in a status bar is not the boundary.

On a 200k-context model, 25% is 50,000 tokens. In this model that session costs **227% more** than leaving the trigger at 200k. The token graph still looks smaller. The bill does not.

## A long session you can audit

The baseline is 60 user turns of 8 model requests. A user turn is a message a person sends. A model request is one API call inside that turn.

Each request adds 4,000 tokens of new input and 600 tokens of output. Startup context is 28,000. After a compaction the residual is 46,000 (system, project, a 3,000-token summary, 15,000 tokens of re-injected files) before the next 4,000 of new input. A warm request reads the previous prompt and writes only the previous output plus the new tail. Reads are priced at 0.1x, 1-hour writes at 2x, output at 5x. No repair work, no long pauses.

The first prompts are 32,000, then 36,600, then 41,200. The 250k arm's first summary request carries 249,800 tokens. The about-967k arm keeps going until a summary request of 967,400.

| Simulated quantity | 250k | about 967k |
| --- | ---: | ---: |
| Raw prompt tokens, including summary requests | 72,881,600 | 222,160,000 |
| Compactions | 10 | 2 |
| Estimated API cost (base-input units) | 13,626,420 | 28,033,460 |

The about-967k session sends 3.05 times as many prompt tokens: **67% fewer** at 250k. Its estimated API cost is 2.06 times as high: **51% lower** at 250k. Neither number is four. Dividing 967 by 250 gives about 3.9, and that shortcut ignores the residual context, the summary calls, and the cache.

At the published 0.05x read price (Opus 5.5 and Sonnet 5.5) the same session's cost gap falls to **41%**. At 0.025x (Fable 5.1 and 5.2) it falls further, to about 1.38 times. The raw-prompt gap stays 3.05 times. Cheaper reads make a large context hurt less, so shrinking it saves less money. Prices: [the pricing page](https://platform.claude.com/docs/en/about-claude/pricing), snapshot 13 November 2026.

A grid of the same session puts the cheapest trigger near **120,000** tokens at 0.1x reads (**57% cheaper** than about 967k) and near **160,000** at 0.05x (**43% cheaper**). For the list prices most people are on, the interesting region is roughly 150,000 to 200,000 tokens, not 25% of whatever the window happens to be. If reads were free, compacting earlier would not help: the same comparison at a zero read weight costs more at the lower trigger.

## The bugfix where nothing changes

Twenty smaller requests, 4,000 new tokens and 1,000 of output each, start at a 32,000 prompt and end at 127,000. Total raw prompt tokens: 1,590,000. Neither 250k nor about 967k fires. Saving: **0%**.

A different short session, three user turns of ten calls, is also 0%. Force a 100,000-token window on that one and the estimated API cost is **8% higher**, even though raw tokens fall. Early compaction can cost money on a task that was about to finish.

At a completed, unrelated task, `/clear` is the better move. It discards the conversation. It does not erase files, and the next request still pays for the system prompt and the project context. For unfinished related work, `/compact` with an explicit keep-list preserves continuity, and the summary request is part of the cost.

## Caching already makes history cheap

That is why 51% is smaller than 67%.

Timing still matters. Four 75-minute breaks with idle compaction off make the priced gap larger, because the high trigger pays to rewrite a huge cold context. Turn idle compaction on, which is the default on a recent interactive session once you are over 200k tokens, and the saving falls to about **24%**. The high arm is compacted while the cache is still warm.

On a 5-minute cache (usage credits, or an API key) with 7-minute gaps, almost every user turn rewrites the context, and early compaction helps more. That is not the default inside the plan.

There are public counterexamples to compacting out of habit. In an [HN thread](https://news.ycombinator.com/item?id=47739260), Boris Cherny pointed at large-context cache misses, skills, and background agents. [Rulestack](https://dev.to/rulestack/claude-codes-compact-cost-006-at-136k-tokens-with-a-warm-cache-9x-that-cold-and-clear-sent-85f) reports a warm-versus-cold compaction gap. [Learn LLM](https://learn-llm.com/p/raw-tokens-fell-3960-the-bill-went/) reports fewer tokens and a higher client-estimated cost when only a few follow-up questions remained. Those experiments are not reproduced here. They are the right objection: a summary has a payback period.

## The agent forgets something important

Imagine an invoice migration with one rule: round only the final amount, never each line item.

The summary drops it. The agent writes a plausible patch. A test fails. It re-reads files, edits again, and runs more tests. Those requests count.

In this simulator, about 15 extra repair requests of 8,000 tokens per compaction bring the cost gap from 51% down to **3%**. Raw tokens are still lower. That is one chosen repair model, not a law of how models think.

The number that matters is cost per finished task, with the rule checked by a test the agent did not get to edit. Fewer tokens and a broken migration is a failed optimization.

Put rules that must survive in project-root `CLAUDE.md` and in executable tests before you make summaries more aggressive. Anthropic documents [what survives compaction](https://code.claude.com/docs/en/context-window). Root `CLAUDE.md` comes back. A rule that lived only in the middle of the chat often does not.

## Part 2: Desktop, the CLI, and T3 Code

A client switch is not a compaction result. Test it with the window held fixed.

Claude Desktop chat, its Code tab, the official interactive `claude` CLI, and T3 Code are not four skins on an identical request. T3 Code drives Claude through the Agent SDK. Its [provider documentation](https://raw.githubusercontent.com/pingdotgg/t3code/main/docs/user/providers-claude.md) exposes an absolute "Auto-compact after" value and a separate idle-thread policy: When you return, Before the cache expires (beta), or Never.

A switch can change the model, the effort, the tools, the instructions, the idle policy, or the window. Any of those can change consumption. Measure `/context` before you compare.

A [T3 issue](https://github.com/pingdotgg/t3code/issues/24649) initially reported faster five-hour-meter consumption than the interactive CLI for similar tokens. A later small check reported parity. An upstream report says headless `claude -p` (entrypoint `sdk-cli`) can move the five-hour meter faster per token than interactive `cli`. These are version-specific reports, not an official formula. They do not say "the CLI is automatically cheaper."

Match the effective window (`raw_max_tokens` from `/context`), repeat comparable tasks in both orders, and record token buckets where the transcripts exist. Desktop chat does not write this JSONL format. Those cells stay empty. They do not get guessed numbers.

T3 also writes titles, commit messages, and PR text in runs that may save no session history. A JSONL total can miss them. The meter will not.

## What a local log still has to show

[MEASURED_LOCAL_DATA: exact setting keys, effective window, date changed, model ids, client versions, plan, and effort.]

[MEASURED_LOCAL_DATA: before and after task counts, the four token buckets, compaction `preTokens`, entrypoint, and cost per successfully completed task. The historical split is observational.]

[MEASURED_LOCAL_DATA: five-hour and weekly start and end percentages for matched blocks, reset boundaries, and whether anything else used the account.]

[MEASURED_LOCAL_DATA: quality results, invariant failures, repair requests, human corrections, elapsed time, and the client comparison, or an explicit "not run."]

The analyzer emits aggregates, not prompt text. The source logs can still contain source code and tool output. Run it only where that is allowed, and read the aggregate before it leaves the machine. If the change was more than the default 30 days of transcript retention ago, the earlier CLI logs may already be gone. Desktop and Cowork transcripts are kept.

## A practical playbook

Look at a fresh session with `/context`. Remove instructions and noisy tool output you do not need.

Use `/clear` for unrelated tasks. Keep a short handoff when continuity matters.

For genuinely long work, try one smaller window, not two overlapping controls. On a 1M model, either of these is in the valley. Do not set both:

```bash
CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=20 claude
claude --autocompact 180000
```

Keep the model and effort fixed. If you are on Opus 5.5 or Sonnet 5.5, the simulation says the priced optimum is closer to 160,000 than to 250,000, and closer to 120,000 at the standard 0.1x read price. Treat that as a place to test, not as a prescription. Record the actual boundaries.

Check the result with tests. If repairs increase, move the trigger back up or put the rule in root `CLAUDE.md`.

Measure the allowance separately, in percentage points, not as a guessed dollar conversion.

On long sessions, a lower trigger cuts re-sent prompt tokens by about 3x in this scenario, and the list-price saving is closer to 2x. The priced optimum is nearer 150k to 200k than to 25% of 1M. The meter is a separate measurement. It is not a universal 4x switch.

## Objections worth keeping

| Objection | Answer |
| --- | --- |
| You rediscovered a cheap cache read. | The priced gap is smaller than the token gap. A short task can lose money. Forcing 100k on a bugfix does. |
| The summary forgets the rule. | Test it. Root `CLAUDE.md` and executable checks beat a lower token graph. |
| That bar is not a token counter. | Agreed. Meter percentage points stay in their own column. |
| The before and after was a quieter week. | Pair tasks. A historical split stays labeled observational. |
| Why not `/clear`? | Right tool for unrelated work. Wrong tool for an unfinished investigation. |
| One client is guaranteed cheaper. | It is not. Some reports found the opposite direction on the five-hour meter. |
| Fifteen repair requests is universal. | No. It is one sensitivity run. The saving fell to 3%. |
| Pauses do not matter. | They do. Idle compaction, which is on by default, cuts this scenario's saving to about 24%. |
