# Outline: does compacting Claude Code earlier actually save usage?

Draft for a post on nevolin.be. Plain structure. Nothing here is a result from Ilja's account. The figures are from the simulation in this repo, under assumptions that the post has to show.

Working title: Compacting Claude Code at 25 percent. A smaller context, not a free 4x.

Alternate: I set auto-compaction to 25 percent. Here is what that can and cannot change.

## 1. The claim, in the form it was made

Two posts, from one work setup, not from a controlled run:

- Setting auto-compaction to 25 percent, against a default described as "95 percent or so," cut Claude Code usage by almost 4x. The five-hour limit and the weekly limit stopped binding.
- Switching from the Claude app to the Claude CLI, through T3 Code, "cuts token usage by another 200 percent." The screenshot text says "take usage," which reads as a typo for token usage. "Another 200 percent" is not a defined ratio. The post should not repeat it as one.

Say up front that both are anecdotes. The post is the attempt to see whether they generalize, and why they might.

## 2. The mechanism, before any number

Each Claude Code turn re-sends the whole context. The docs say this directly: the model does not remember between requests, so the request carries the system prompt, the project context, every prior message and tool result, and the new message. If every turn adds roughly the same amount of new material, the bill for re-sending history grows with the square of the number of turns. That is the reason a long session feels expensive out of proportion to the last thing you typed.

Compaction replaces that history with a summary. You pay for the summary call. You lose detail, and Claude may re-read files or redo work. In exchange, later turns send a smaller prompt.

Prompt caching changes the price of the re-send. The unchanged prefix is billed as a cache read, at 0.1x the base input price on most models, 0.05x on Opus 5.5 and Sonnet 5.5, and 0.025x on Fable 5.2 (Anthropic pricing page, snapshot 13 November 2026). A 1-hour cache write is 2x, and a 5-minute write is 1.25x. On a subscription, inside the plan, Claude Code asks for the 1-hour TTL on the main conversation. Compaction invalidates the conversation prefix, so the turn after a compaction rebuilds that cache.

The subscription limits are a third accounting, and Anthropic has not published it. The five-hour and weekly limits are real, they are shared by claude.ai, Claude Code, and Claude Desktop, and they depend on length, model, features, and effort. They are not documented as a function of these token buckets. A post that treats "tokens" and "I hit the limit" as the same number is ahead of the docs.

## 3. What "25 percent" actually refers to

Do not write "the default is 95 percent" as a fact. Checked against the Claude Code docs on 13 November 2026:

- Native 1M models (Fable, Sonnet 5 and later, Haiku 5.5, Opus 4.7 and later) auto-compact at about 967K tokens by default. That is about 96.7 percent of 1M, which is close to "95 percent or so," and it is an approximation in the docs, not a setting named 95.
- `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` sets a percentage from 1 to 100 of the auto-compact window. Lower values compact earlier. The variable cannot raise the threshold. Values above the default percentage are ignored. The docs do not publish that default percentage.
- The other knob is the window in tokens: `/autocompact`, `--autocompact`, `autoCompactWindow`, or `CLAUDE_CODE_AUTO_COMPACT_WINDOW`, from 100,000 to 1,000,000. On a 200K window, 25 percent and 10 percent both land on the 100,000 floor if you use this knob. They are not separate experiments there.
- T3 Code's own setting, "Auto-compact after," is that token count, not the percentage variable.

The post should show both knobs and say which one was changed. If the notes only say "25 percent," the matching control is the percentage variable, and the 95 percent end of the comparison may be a no-op.

## 4. What the simulation predicts, and the figure list

All of these are outputs of `python -m cc_usage_study.simulate`, committed under `outputs/`. Assumptions, also in the README: 1M window, 120 productive turns, 20,000 new tokens per turn, 2,000 output tokens per turn, a 20,000-token re-injected prefix, a 4,000-token summary, 6,000 tokens of re-read files, cache hit rate 0.95 on the stable prefix, cache reads at 0.1x, 1-hour writes at 2x, output at 5x the base input price, no rework. None of those token sizes except the about-967K trigger is a measurement.

Figures to include:

1. `outputs/figures/cumulative_raw_tokens.png`. Cumulative prompt tokens (fresh input plus cache reads plus cache writes) against turns, one line per threshold. This is the quadratic versus the sawtooth.
2. `outputs/figures/cumulative_api_equivalent.png`. The same sessions, priced with the cache multipliers. The lines sit closer together.
3. `outputs/figures/threshold_sweep.png`. End-of-session totals at 95, 75, 50, 25, and 10 percent. Raw tokens keep falling. The priced total flattens.
4. `outputs/figures/context_sawtooth.png`. Context size over the session at 25 and 95 percent.
5. `outputs/figures/sensitivity_cache_hit.png`. The 25/95 ratio against cache hit rate. The raw ratio does not move. The priced ratio does.
6. `outputs/figures/sensitivity_rework.png`. The same ratio against extra turns spent repairing lost context.
7. `outputs/figures/short_session.png`. Thirty quieter turns. The 95 percent line never compacts, and the gap shrinks.

Numbers to print next to the figures, for the default long session, 25 percent of 1M against 95 percent of 1M:

- Raw prompt tokens: 20,531,000 against 59,092,000. Ratio 0.347. The 95 percent session sends about 2.9 times as many prompt tokens. The factor of 4 from the posts is larger than this scenario.
- API-equivalent cost, in units of one base input token: 10,046,350 against 17,177,350. Ratio 0.585. The priced total at 95 percent is about 1.7 times the 25 percent total.
- Compactions: 11 against 2.
- The same comparison against the documented about-967K trigger, using a 250,000 token threshold: raw ratio 0.346, priced ratio 0.583. Almost the same, because 967K is close to 95 percent of 1M.
- With cache reads at 0.05x, the Opus 5.5 and Sonnet 5.5 list price, the raw ratio stays 0.347 and the priced ratio rises to 0.635. Cheaper reads make a large context hurt less, so shrinking it saves less money.
- With no cache at all, the priced ratio is 0.362, close to the raw ratio. That is the world the quadratic story assumes. It is not how Claude Code is billed.
- At 10 percent the raw total falls further, to 12,383,000, but the priced total is 9,665,830, only a little under the 25 percent priced total of 10,046,350. Thirty-nine compactions to get there. Past a point, earlier compaction is mostly extra summary calls.
- In the short session, 25 percent uses 3,901,000 prompt tokens against 5,190,000, ratio 0.752, with one compaction against zero. The direction is still "less," and the size is nowhere near 4x.
- Rework: with about 7 extra repair turns after every compaction, the priced ratio crosses 1. Raw tokens can still be lower while the priced total is not. Lost context is the way the trick loses.

A line the post can use, if it stays tied to these assumptions: the direction of the anecdote is what the model produces on a long session, and the factor of 4 is larger than this scenario. The factor of 4 remains possible for a different growth rate, a different meter, or a workload that was previously sitting at the top of the window on every turn. It is not the default prediction.

Say, in the same section, what would move the prediction to a true 4x. Raw tokens scale toward the ratio of the thresholds only when the residual summary is small, compaction calls are a small share, and the session cycles many times. 250/950 is 0.26, about 3.8x. This scenario lands at 0.35 because the summary and the re-injected prefix are not zero, and because the low threshold pays for eleven full-context summary calls.

## 5. What has to be measured on the laptop

The simulation does not know his sessions. The analyzer does, and only he can run it, because the transcripts are local and the tool is written to keep prompt text out of the report.

- Point it at `~/.claude/projects` and split on the date the setting changed.
- Compare prompt tokens per session and per call, not the raw weekly sum. A quiet week after the change looks like a saving either way.
- Report the four buckets separately: input, output, cache creation, cache read. Also the 5-minute and 1-hour write split when the transcript has it.
- Count structural compaction markers. Older files may summarize without a marker the parser recognizes. Say so if the count is zero and the context size still sawtooths.
- Put the Settings > Usage percentages beside the token table for the same dates. If tokens fall 3x and the meter falls 4x, or 1.2x, that is the interesting cell. Do not fill it in with a guessed weight.

Claims the laptop data can verify:

- After the change, median prompt tokens per session fell, on a similar kind of work.
- Compactions per session rose.
- Cache-read share of the prompt fell or the prompts got shorter. Both are consistent with earlier compaction. They are not, by themselves, a meter result.
- The five-hour bar and the weekly bar stopped filling. This sentence requires the meter notes. The JSONL files do not contain it.

Claims the laptop data cannot verify:

- That the same would happen on another codebase, model, or client.
- That T3 Code is cheaper than the Claude desktop chat app. Different transcript roots, and the chat app has no such root. See `benchmarks/client_comparison.md`.
- A quality regression. Token files do not say whether the summary forgot the constraint.

## 6. The controlled run, in one section

Describe the protocol, do not paste it. Fixed tasks, fresh directory per arm, model and effort pinned, version recorded, meter recorded on an idle account, window arm and percent arm both run, pilot of 25 against the default before any large grid. Success is tests passing, plus the constraint checks, plus the token buckets from `modelUsage` (it includes subagents; top-level `usage` does not).

Be explicit that the three tasks shipped with the repo are too short to force a compaction at 95 percent of 1M. They prove the harness scores a pass and a fail. A long task has to be added before this section has a number.

Also be explicit that `total_cost_usd` is a client-side estimate.

## 7. The client switch, as a separate claim

Keep it short. Same allowance, different clients. T3 Code is not the Claude desktop app and not the official TUI. Its compaction control is a token count. A public issue on the T3 Code repo reports that the Agent SDK entrypoint has, at times, moved the five-hour meter faster than the interactive CLI for the same tokens, with a later note that a `t3-code` entrypoint matched the CLI on one check. That is their measurement. If the post mentions it, label it that way, and do not treat a single Pro-plan check as a rate.

The measurable version of this claim is in `benchmarks/client_comparison.md`: same tasks, meter notes for every block, transcripts where they exist, compaction settings matched before calling a difference a client effect.

## 8. Caveats to print, not to hide in a footnote

- One person's sessions at work. Tooling, repo size, and which model the company allows all move the result.
- "Almost 4x" and "another 200 percent" were not measured with the buckets above. The post should not upgrade them to findings.
- The default is not published as 95 percent. About 967K on native 1M models is the published approximation, as of the docs read for this repo.
- The percentage override cannot delay compaction. A before period that was "already at the default" may not have been at 95 percent of the window.
- Cache prices and the subscription meter are different. The model predicts a smaller saving on the priced total than on raw tokens. The meter could be either, or neither.
- Earlier compaction can throw away a decision the model still needed. The rework plot is the warning. The currency task is a toy version of the same failure.
- Compaction itself costs a full-context summary call, and on a warm cache that call is mostly a cache read plus the summary output. On a cold cache it reprocesses the history. The model includes the call. A real session that compacts after an hour idle pays the cold version.
- Subagents have their own context. A transcript total that ignores them under-counts. Prefer `modelUsage` in the benchmark and include subagent files in the analyzer. They are tagged `subagent`.
- Prices in the estimator are a snapshot of the Anthropic pricing page on 13 November 2026. They will go stale. The estimator refuses a price when the model is unknown or the cache-write TTL split is missing, rather than guessing.
- The synthetic fixture is fake. Its report is marked synthetic. Do not paste it into the post as usage.

## 9. A closing that matches the evidence

If the laptop split and a long-task pilot agree with the long-session simulation, the honest conclusion is narrow: on long sessions, a lower trigger reduces re-sent prompt tokens by something on the order of the threshold ratio, the list-price saving is smaller because of caching, and the limit he felt is a separate measurement he still has to write down.

If the laptop split shows no change in prompt tokens per session, the 4x was workload, model, or the meter, and the post should say that.

If tests start failing when the trigger drops, the token saving is real and the work is worse. That is a result, not a footnote.

## Suggested section order for the published post

1. The two posts, quoted, with the date and the fact that they are one setup.
2. Why re-sending the context makes long sessions expensive.
3. What the docs actually say the setting does.
4. The simulation figures, with the assumptions in a table under the first figure.
5. The before/after from `~/.claude/projects`, four buckets, per session.
6. The meter notes beside that table.
7. The pilot benchmark, or a clear statement that it has not been run yet.
8. The client comparison, mostly as an open measurement, plus the T3 Code caveat.
9. When earlier compaction is the wrong tool: short sessions, high cache hit rate, and work that cannot survive a summary.
