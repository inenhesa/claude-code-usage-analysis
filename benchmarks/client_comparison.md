# Desktop app, Claude Code CLI, and T3 Code

The second claim is that moving from the Claude app to the Claude CLI, via T3 Code, cuts usage further. This is a client comparison. It is not another compaction-threshold comparison, and the transcript analyzer cannot run it on its own.

## What the products are

- Claude Desktop, the chat app, is a different product from Claude Code. Its long-chat behavior is the Help Center's "automatic context management" (summarize earlier messages when code execution is on). The same Help Center page says longer chats that trigger that summarizer consume more of the usage limit. That is a different mechanism from Claude Code compaction, and it is not evidence about the 25% setting.
- The Code tab in Claude Desktop runs Claude Code. Those sessions can write the same `~/.claude/projects/**/*.jsonl` transcripts as the terminal.
- The Claude Code CLI is the terminal `claude` process.
- T3 Code is a separate desktop app (pingdotgg/t3code) that drives Claude through the Agent SDK. It is not the Claude desktop app, and it is not the interactive CLI. Its own provider doc exposes "Auto-compact after" as an integer token count from 100,000 to 1,000,000, which is the window knob, not `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`. Source: https://github.com/pingdotgg/t3code/blob/main/docs/user/providers-claude.md (read 13 November 2026).

The phrase in the post, "Claude CLI (T3 Code)", names T3 Code as the client. A comparison of T3 Code against the official CLI is a third contrast, not the one the sentence states.

## What is measurable

| Quantity | Where it shows up | Usable for which contrast |
| --- | --- | --- |
| Five-hour and weekly meter | Settings > Usage. Official, shared by claude.ai, Claude Code, and Claude Desktop. | Any client, if the account is otherwise idle during the window |
| Four token buckets | `~/.claude/projects/**/*.jsonl` for Claude Code, the Code tab, and Agent SDK hosts that persist transcripts. T3 Code's own usage screen also reads those files. | CLI, Code tab, T3 Code. Not the desktop chat product |
| List-price estimate | `/cost`, result `total_cost_usd`, T3 Code's usage view. All client-side. | Same clients as the transcripts. Not the meter |
| Task success | Tests you chose | Only where you can replay a fixed task |
| Which client sent the request | Not a documented field in the JSONL usage object. T3 Code has discussed entrypoint labels (`cli`, `sdk-ts`, `t3-code`) in a GitHub issue, which is their report, not an Anthropic spec. | Only if you record the client yourself |

## What is not measurable from the files alone

- The meter weight of a cache read versus a fresh input token. Not published. A GitHub issue and a third-party blog claim the five-hour meter discounts cache reads and have speculated about the weekly meter. Those are reports, not a rule this study can cite as fact.
- Desktop chat token totals. They are not in `~/.claude/projects`.
- Whether two clients "used 200% less." That phrase is not a defined ratio. Report a ratio of meter movement, and separately a ratio of each token bucket, over the same tasks.

## A protocol that can actually be run

Do this on one account, one model, one week, with no other Claude use during the measured blocks.

1. Pick a fixed list of tasks you can score, including at least one long enough to compact. The scaffold tasks in this repo are the short end of that list.
2. Block A, Claude Desktop chat. Do the tasks. Record the meter at the start and end of the block. You will not get JSONL token buckets. Record wall time and whether the tests passed. This block is the fuzzy one. Treat it as a meter observation, not a token observation.
3. Block B, Claude Desktop Code tab, if that is what "Claude App" meant. Same tasks, fresh checkouts. Record the meter and keep the transcripts. Tag the block in your notes with the session ids, because the analyzer will not know the client.
4. Block C, `claude` in a terminal, same settings, compaction left at the default. Meter plus transcripts.
5. Block D, T3 Code, same model, and write down the Auto-compact value it is set to. If that value is not the default, this block confounds client and threshold. Set it to the same trigger as block C before you call the result a client effect.
6. Run the analyzer on the transcript roots for B, C, and D separately (copy the session files for each block into their own directory, or pass the session files). Compare prompt tokens per task, not the weekly sum, unless the task list was identical.

## A finding that would support the claim, and one that would not

Support, still modest: on the same tasks, block D moves the five-hour meter less than block A, and the token buckets that do exist are also lower, and the compaction settings were the same.

Not support: the meter moved less because the tasks were shorter, the model was smaller, or T3 Code was already compacting at a lower window. Also not support: tokens matched and only the meter moved. That can be an entrypoint difference. T3 Code's issue 24649 (https://github.com/pingdotgg/t3code/issues/24649, read 13 November 2026) reports that Agent SDK and `claude -p` traffic used more of the five-hour meter per token than the interactive CLI, and a later note on that issue says a `t3-code` entrypoint matched the interactive rate on one Pro-plan check. Weekly burn was described there as unchanged. Anthropic has not published this as a rate. If you see it, write it down as an observation and do not generalize from one account.

## Practical note

The Help Center says every surface draws on the same pool. Switching clients does not give you a second allowance. Any saving is a change in how fast that shared pool moves.
