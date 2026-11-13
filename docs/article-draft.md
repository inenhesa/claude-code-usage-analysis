# I said Claude Code used almost 4x less. Here is the part I can defend.

I posted that setting Claude Code's auto-compaction to 25% had cut my usage by almost four times. I had stopped running into the five-hour and weekly limits during my work.

Then I reposted it with a second claim: switching from the Claude app to T3 Code cut usage by "another 200%." The screenshot even says "take usage," which I will not quietly correct into a measured token claim.

The first statement was a personal observation, not a controlled experiment. The second contains bad arithmetic. You cannot reduce usage by more than 100%. I should have described what I noticed without attaching an unmeasured percentage. I am not replacing it with 2x, 3x, or any nicer-looking factor.

The useful question underneath is narrower: when does keeping less conversation history make a coding agent cheaper, without making the work worse?

I separated four things that are easy to mash into the word "usage": a token model, local transcripts, task quality, and the allowance meter. They are related. They are not interchangeable.

## Three numbers hiding behind "usage"

A long conversation carries its past into new model requests. One message I type can cause several of those requests, because each tool batch is another call. Anthropic's [prompt-caching guide](https://code.claude.com/docs/en/prompt-caching) describes the re-send.

If each request adds roughly the same new input, and nothing truncates the history, the cumulative prompt count grows with the square of the number of requests. That is request accounting. It is not a claim that every token is processed from scratch at full price.

Prompt caching changes the price. An unchanged prefix can be a cheap cache read. New material, cache writes, and generated output have different prices. A warm summary can still read the old prefix. The project context is rebuilt. The system prompt can stay cached. "Compaction invalidates the cache" is too blunt.

Then there is the subscription meter. Anthropic says length, complexity, model, effort, and features affect usage. It does not publish a formula from those buckets to the five-hour bar. The [allowance is shared](https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work) across claude.ai, Claude Code, and Claude Desktop.

| Quantity | What it tells us |
| --- | --- |
| Raw prompt tokens | How much input the requests carry, including cached input |
| API-equivalent cost | What those buckets imply at published prices |
| Allowance movement | How far the account's meter actually moved |

A 3x change in the first row does not establish a 3x change in either of the others.

## What 25% actually means

There is no universal setting called "the default 95%." My wording was approximate.

The current [model documentation](https://code.claude.com/docs/en/model-config) says native 1M sessions compact at about 967K by default. Other models differ. That is today's documented behavior, not necessarily what my installation did before the change.

`CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=25` asks for compaction at 25% of the selected auto-compact window, and it cannot raise the threshold. An absolute window is a separate control (`autoCompactWindow`, `/autocompact`, `--autocompact`, or `CLAUDE_CODE_AUTO_COMPACT_WINDOW`).

Set a 250,000-token window and also apply 25%, and the nominal budget in this study's model is 62,500, not 250,000. On that arm the raw-prompt total is 6.21 times smaller than about 967K, and the API-equivalent total is only 1.43 times smaller. Compared with 25% alone, the compounded arm costs more at API prices (ratio 0.70). Use one control. Then read the actual compaction events. A percentage in a status bar is not the boundary.

## A long session you can audit

The study's main simulation, S1, is 60 user turns of 8 model requests. These are not 60 messages I typed.

Each request adds 4,000 tokens of new input and 600 tokens of output. Startup context is 28,000. After a compaction the residual is 46,000 (system, project, a 3,000-token summary, 15,000 tokens of re-injected files) before the next 4,000 of new input. Caching is physical: a warm request reads the previous prompt and writes only the previous output plus the new tail. Reads are priced at 0.1x, 1-hour writes at 2x, output at 5x. No repair work, no long pauses.

The first prompts are 32,000, then 36,600, then 41,200. The 250k arm's first summary request carries 249,800 tokens. The about-967k arm keeps going until a summary request of 967,400.

| Simulated quantity | 250k | about 967k |
| --- | ---: | ---: |
| Raw prompt tokens, including summary requests | 72,881,600 | 222,160,000 |
| Compactions | 10 | 2 |
| API-equivalent units | 13,626,420 | 28,033,460 |

These are simulated counts, not my account. The about-967k session sends 3.05 times as many prompt tokens. Its API-equivalent total is 2.06 times as high. Neither number is four. Dividing 967 by 250 gives about 3.9, and that shortcut ignores the residual context, the summary calls, and the cache.

Current models do not all use a 0.1x read. At the published 0.05x rate (Opus 5.5 and Sonnet 5.5) the same session's API saving falls to 1.69. At 0.025x (Fable 5.1 and 5.2) it falls to 1.38. The raw-prompt ratio stays 3.05. Cheaper reads make a large context hurt less, so shrinking it saves less money. Prices: [the pricing page](https://platform.claude.com/docs/en/about-claude/pricing), snapshot 13 November 2026.

A grid of the same session puts the cheapest trigger near 120,000 tokens at 0.1x reads (2.35 times cheaper than about 967k) and near 160,000 at 0.05x (1.76 times). The closed form is `T* ≈ R + sqrt(2 * K * (g + o) / r)`, with R = 46,000. For the list prices most of us are on, the interesting region is roughly 150,000 to 200,000 tokens, not 25% of whatever the window happens to be. If your reads were free, compacting earlier would not help: the same S1 comparison at a zero read weight is 0.92, meaning the low trigger costs more.

## The bugfix where nothing changes

Twenty smaller requests, 4,000 new tokens and 1,000 output each, start at a 32,000 prompt and end at 127,000. Total raw prompt tokens: 1,590,000. Neither 250k nor about 967k fires. The API saving is 1.00.

A different short session, three user turns of ten calls, is also 1.00. Force a 100,000-token window on that one and the API-equivalent total gets worse (0.93) even though raw tokens fall (1.35). Early compaction can cost money on a task that was about to finish.

Request count is not the whole story. A short task that starts with a large attachment can still hit the low boundary.

At a completed, unrelated task, `/clear` is often the better comparison. It discards the conversation. It does not erase files, and the next request is not free. For unfinished related work, `/compact` with an explicit keep-list preserves continuity, and you should count the summary request.

## Caching already makes history cheap

Yes. That is why 2.06 is smaller than 3.05.

Timing still matters. In S4, four 75-minute breaks with idle compaction off raise the API saving to 2.28, because the high-trigger arm pays cold rewrites of a large context. Turn idle compaction on, which is the default on a recent interactive session once you are over 200k tokens, and the saving falls to 1.31. The high arm is compacted while the cache is still warm. A lot of the "25% saved me" story disappears on break-heavy days if the client was already doing that.

On a 5-minute TTL (usage credits, or an API key) with 7-minute gaps, the API saving is 2.60. That is the case where almost every user turn rewrites the context. It is relevant if I had already hit the limit and was on credits. It is not the default inside the plan.

There are public counterexamples to compacting out of habit. In an [HN thread](https://news.ycombinator.com/item?id=47739260), Boris Cherny pointed at large-context cache misses, skills, and background agents. [Rulestack](https://dev.to/rulestack/claude-codes-compact-cost-006-at-136k-tokens-with-a-warm-cache-9x-that-cold-and-clear-sent-85f) reports a warm-versus-cold compaction gap. [Learn LLM](https://learn-llm.com/p/raw-tokens-fell-3960-the-bill-went/) reports fewer tokens and a higher client-estimated cost when only a few follow-up questions remained. I have not reproduced those experiments. They are the right objection: a summary has a payback period.

## The agent forgets something important

Imagine an invoice migration with one rule: round only the final amount, never each line item.

The summary drops it. The agent writes a plausible patch. A test fails. It re-reads files, edits again, and runs more tests. Those requests count. So does review time.

In this simulator, about 15 extra repair requests of 8,000 tokens per compaction bring the API saving from 2.06 down to 1.03. Raw tokens are still lower (1.64). That is one chosen repair model, not a law of cognition, and not a universal "seven turns."

The metric that matters is cost per successfully completed task, with the invariant checked by a test the agent did not get to edit. Fewer tokens and a broken migration is a failed optimization.

I would put critical rules in project-root instructions and in executable tests before making summaries more aggressive. Anthropic documents [what survives compaction](https://code.claude.com/docs/en/context-window). Not every old file or nested rule survives intact.

## T3 Code versus the Desktop app

This needs its own experiment.

Claude Desktop chat, its Code tab, the official interactive `claude` CLI, and T3 Code are not four skins on an identical request. T3 Code drives Claude through the Agent SDK. Its [provider documentation](https://raw.githubusercontent.com/pingdotgg/t3code/main/docs/user/providers-claude.md) exposes an absolute "Auto-compact after" value and a separate idle-thread policy: When you return, Before the cache expires (beta), or Never.

A switch can change the model, the effort, the tools, the instructions, the idle policy, or the window. Any of those can change consumption. A desktop window does not inherently make requests expensive, and "every MCP schema is resent" is not a safe universal. Current [tool search](https://code.claude.com/docs/en/agent-sdk/tool-search) defers schemas on supported configurations. Measure `/context`.

There is counterevidence to a universal T3 advantage. A [T3 issue](https://github.com/pingdotgg/t3code/issues/24649) initially reported faster five-hour-meter consumption than the interactive CLI for similar tokens. A later small check reported parity. An upstream report says headless `claude -p` (entrypoint `sdk-cli`) can move the five-hour meter faster per token than interactive `cli`. These are version-specific reports, not an official formula. If anything, they point the other way from "the CLI client is automatically cheaper."

The fair comparison matches the effective window (`/context` reports `raw_max_tokens`), repeats comparable tasks in a counterbalanced order, and records token buckets where the transcripts exist. Desktop chat does not write this JSONL format. Those cells stay empty. They do not get guessed numbers.

T3 also writes titles, commit messages, and PR text in runs that may save no session history. A JSONL total can miss them. The meter will not.

## What my own logs still need to establish

[MEASURED_LOCAL_DATA: exact setting keys, effective window, date changed, model ids, client versions, plan, and effort.]

[MEASURED_LOCAL_DATA: before and after task counts, the four token buckets, compaction `preTokens`, entrypoint, and cost per successfully completed task. The historical split is observational.]

[MEASURED_LOCAL_DATA: five-hour and weekly start and end percentages for matched blocks, reset boundaries, and whether anything else used the account.]

[MEASURED_LOCAL_DATA: quality results, invariant failures, repair requests, human corrections, elapsed time, and the client comparison, or an explicit "not run."]

The analyzer emits aggregates, not prompt text. The source logs can still contain company code and tool output. I would run it only where that is allowed, and I would read the aggregate before it leaves the machine. If the switch happened more than the default 30 days of transcript retention ago, the "before" CLI logs may already be gone. Desktop and Cowork transcripts are kept.

## A practical playbook

Look at a fresh session with `/context`. Remove instructions and noisy tool output you do not need.

Use `/clear` for unrelated tasks. Keep a short handoff when continuity matters.

For genuinely long work, try one smaller window, not two overlapping controls. After checking for a percentage override:

```bash
claude --autocompact 250000
```

Keep the model and effort fixed. If you are on Opus 5.5 or Sonnet 5.5, the simulation says the priced optimum is closer to 160,000 than to 250,000, and closer to 120,000 if your reads are at the standard 0.1x. Treat that as a place to test, not as a prescription. Record the actual boundaries.

Check the result with tests. If repairs increase, move the trigger back up or put the invariant somewhere that survives a summary.

Measure the allowance separately, in percentage points, not as a guessed dollar conversion.

My useful claim is smaller than the LinkedIn post. Earlier compaction can cut carried context on long sessions, by something like 3 times in raw prompt tokens under these assumptions, and by something like 2 times at a standard cache-read price. Whether it saves an allowance, or the work, depends on caching, pauses, continuity, and the tasks that actually finish.

That is worth testing. It is not a universal 4x switch.

## Objections worth keeping

| Objection | Answer |
| --- | --- |
| You rediscovered a cheap cache read. | The priced gap is smaller than the token gap. A short remainder can lose money. S2b does. |
| The summary forgets the invariant. | Test it. Root instructions and executable checks beat a lower token graph. |
| Your percentage is impossible. | "Another 200%" is not a valid reduction. No replacement factor until it is measured. |
| That bar is not a token counter. | Agreed. Meter percentage points stay in their own column. |
| Your before and after was a quieter week. | Pair tasks. The historical split stays labeled observational. |
| Why not `/clear`? | Right tool for unrelated work. Wrong tool for an unfinished investigation. |
| T3 is guaranteed cheaper. | It is not. Some reports found the opposite direction on the five-hour meter. |
| Fifteen repair requests is universal. | No. It is one sensitivity run. |
| Your average hit rate hides pauses. | S4, S4b, and S4c are the pause cases. Idle compaction changes the answer. |
