# Worked examples

Every count below is either a closed-form illustration or an output of `cc_usage_study.model.simulate` under named assumptions. None of it is a measured account, except the paragraphs marked as live or measured, which come from `outputs/live_experiments.json`. `k` means 1,000 tokens. A model request is one API call. A user turn can contain several model requests.

The long-session numbers are scenario S1 in `outputs/summary.json`: 60 user turns, 8 requests each, 4,000 tokens of new input and 600 tokens of output per request, 20,000 system tokens, 8,000 project tokens, 15,000 tokens re-injected after compaction, a 3,000-token summary, 1-hour TTL, no long gaps, no rework. Cache reads are priced at 0.1x, 1-hour writes at 2x, compaction tails at 1.25x, output at 5x. A warm request reads the previous prompt and writes the previous output plus the new tail.

## Example 1. The long session, a 245k fire against the 967k default

`CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=25` on a 1M window fires at floor(980,000 x 0.25) = 245,000 in Claude Code v2.1.325, not at a quarter of 1M. The first prompts, on either arm, before anything compacts:

| Request | Prompt tokens |
| --- | ---: |
| 1 | 32,000 |
| 2 | 36,600 |
| 3 | 41,200 |
| 4 | 45,800 |
| 5 | 50,400 |
| 6 | 55,000 |
| 7 | 59,600 |
| 8 | 64,200 |

Each step adds 4,600 tokens (4,000 of new input and 600 of output). The 245k arm's first summary request is the 48th model request in the trace (0-based index 47). Its prompt is 245,200 tokens. The next productive prompt is 50,000: the 46,000-token reset (system, project, summary, re-injected files) plus 4,000 of new input. The 967k arm's first summary request has prompt 967,400, after 204 earlier requests, and the next productive prompt is also 50,000.

End of S1, from the simulator:

| | 245k fire | 967k default | default / 245k |
| --- | ---: | ---: | ---: |
| Raw prompt tokens, including summary requests | 72,371,600 | 222,160,000 | 3.07 times |
| Cache-weighted units (0.1x reads) | 13,640,520 | 28,033,460 | 2.06 times |
| Compactions | 11 | 2 | |
| Productive model requests | 480 | 480 | |

The default arm sends 3.07 times as many prompt tokens but only 2.06 times the cache-weighted usage, because most of a warm prompt is a cheap cache read. The ratio of the two triggers is irrelevant: it ignores the reset context, the summary requests, and the read discount.

```text
cache-weighted units = fresh input + 1.25 * 5-minute writes + 2 * 1-hour writes
                     + read_multiplier * cache reads + output_multiplier * output
```

Do not turn this line into the five-hour bar. Anthropic does not publish the meter weights.

One control, not both:

```bash
# Percentage of the effective window (window minus 20,000). Cannot raise the default.
CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=25 claude     # fires at 245,000 on 1M

# Absolute window. Remove a percentage override first, or they compound.
claude --autocompact 250000                   # fires at 217,000
```

In T3 Code, Settings, Providers, Claude, Auto-compact after `250000` is the window. A source comment says the UI treats the threshold as that window minus about 33,000, and Claude Code v2.1.325 agrees: the window minus a 20,000 output reserve minus a 13,000 buffer, so `250000` fires at 217,000 (scenario S1b: 2.15 times less cache-weighted usage than the default on S1). Record `compactMetadata.preTokens` rather than the label.

## Example 2. Twenty requests where both triggers do nothing

Assumptions: 20 user turns, 1 request each, 4,000 new input, 1,000 output, same 28,000-token startup context. From `zero_effect_control` in `outputs/summary.json`:

- First prompt: 32,000
- Last prompt: 127,000
- Raw prompt tokens: 1,590,000
- Compactions at a 245k fire and at the 967k default: 0 and 0
- Usage ratio: 1.00

The setting does nothing. A short session that starts with a huge attachment is a different scenario. S2 in the scenario table is the same lesson with 3 user turns of 10 calls: ratio 1.00. S2b forces `--autocompact 100000` (fires at 67,000) on that short session and cache-weighted usage rises 42% (ratio 0.70). Early compaction is not free.

At a finished, unrelated task, `/clear` drops the conversation. It does not undo files, and the next request still pays for the system prompt and project context. For unfinished related work, `/compact` with an explicit keep-list is the comparison, and the summary request is part of the cost.

## Example 3. The compounding trap

```json
{
  "autoCompactWindow": 250000,
  "env": { "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "25" }
}
```

The percentage applies to the window after the 20,000 output reserve. Claude Code v2.1.325 and this model both fire at floor(230,000 x 0.25) = 57,500, and the model warns. The naive reading, 0.25 x 250k = 62.5k, is wrong. The live arm compacted 6 times on a 4-phase build and used 13.6% more than the default, and 24.3% of its usage was summary requests the transcript does not show. S8 compares that trigger with the 967k default over a long session: 5.79 times fewer raw prompt tokens, but only 1.12 times less cache-weighted usage, with 237 compactions against 2. S8b compares it with the 25 percent override alone (245k): ratio 0.55, so the compounded arm uses 1.83 times as much as the percentage by itself, while raw tokens still look 1.89 times better. That is how a transcript-only reading flatters a thrash.

The 100k floor is a limit of the absolute window control. It does not force a percentage override back up. On a 200k model, percentage 25 fires at floor(180,000 x 0.25) = 45,000, under the ~46k post-compaction reset. S6: the low arm compacts 476 times and uses 3.39 times the 167,000 default.

Use one control. Then read the boundaries.

## Example 4. Lost context

S3 adds the same number of extra 8,000-token repair requests after every compaction, on both arms. Default usage divided by 245k-fire usage, against the no-rework S1 baseline of 2.06:

| Extra repair requests per compaction | Cache-weighted ratio | Raw-prompt ratio |
| --- | ---: | ---: |
| 0 | 2.06 | 3.07 |
| 3 | 1.89 | 2.84 |
| 6 | 1.69 | 2.56 |
| 10 | 1.39 | 2.16 |
| 15 | 0.99 | 1.61 |

Around 15 repair requests, the cache-weighted gap is gone and raw tokens are still lower. This is the simulator's chosen repair model. It is not a measured property of a model, and it is not the old "seven turns" illustration, which used a different cache formula.

A concrete failure the benchmark can score: an invoice rule that says round only the final amount, half even, never each line. Put it in project-root `CLAUDE.md` and in a hidden test. A summary that drops it shows up as a failed test and as repair requests, not as a nicer token graph. Root `CLAUDE.md` is re-injected. Path-scoped rules and files over 5,000 tokens are not guaranteed to come back in full ([context window](https://code.claude.com/docs/en/context-window)).

## Example 5. Desktop, CLI, T3 Code

Measured on 13 November 2026, Opus 5.5, effort medium, identical `CLAUDE.md`, no user MCP servers, n=1 each. First request size: Claude CLI v2.1.325 `claude -p` 16,850 tokens, interactive 19,528, T3 Code v0.1.9 22,842, Claude Desktop v5.1231.2 Code tab 39,749. Those requests read 63% to 68% of the prefix from a warm shared cache, so they are first-request sizes, not true cold starts. The same `noisy_bughunt` fix, cache-weighted, against the CLI's main-thread rows: T3 Code 1.19x, Desktop 1.80x (1.71x to 1.92x across read weights, 2.39x in raw tokens). The CLI's background calls add 9% on top of its own main rows. The full table is in `benchmarks/client_comparison.md`. Use your own numbers for your own setup.

| Surface | What to verify | Where tokens might exist |
| --- | --- | --- |
| Claude Desktop chat | Attachments, connectors, model, effort | Not the Claude Code JSONL root |
| Desktop Code tab | Version, launch environment, session ids | Claude Code transcripts where persisted |
| Official interactive `claude` | Tools, MCP, window, idle policy | `~/.claude/projects`, entrypoint `cli` |
| `claude -p` | Same, plus the harness flags | Entrypoint `sdk-cli`. A meter delta here is not an interactive result |
| T3 Code | Config dir, inherited env, Auto-compact after, idle mode | That instance's `CLAUDE_CONFIG_DIR` |

T3's idle choices, from its provider doc, are When you return, Before the cache expires (beta), and Never. Match them or label them a confounder. Prompt suggestions are off by default in T3. Text-generation runs for titles and commit messages can be absent from JSONL. None of those, by themselves, is a measured 2x.

For each completed task record fresh input, 5-minute writes, 1-hour writes, cache reads, and output. Record meter movement in percentage points, with no other account usage in between. The live runs used an API key, so their meter cells are empty.

A matched-token difference in the meter supports a version-specific allowance observation. It does not by itself mean one client sent fewer tokens. See `benchmarks/client_comparison.md`.
