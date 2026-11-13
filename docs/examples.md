# Worked examples

Every count below is either a closed-form illustration or an output of `cc_usage_study.model.simulate` under named assumptions. None of it is a measured account, except the paragraphs marked as live or measured, which come from `outputs/live_experiments.json`. `k` means 1,000 tokens. A model request is one API call. A user turn can contain several model requests.

The long-session numbers are scenario S1 in `outputs/summary.json`: 60 user turns, 8 requests each, 4,000 tokens of new input and 600 tokens of output per request, 20,000 system tokens, 8,000 project tokens, 15,000 tokens re-injected after compaction, a 3,000-token summary, 1-hour TTL, no long gaps, no rework. Cache reads are priced at 0.1x, 1-hour writes at 2x, compaction tails at 1.25x, output at 5x. A warm request reads the previous prompt and writes the previous output plus the new tail.

## Example 1. The long session, 250k against about 967k

The first prompts, on either arm, before anything compacts:

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

Each step adds 4,600 tokens (4,000 of new input and 600 of output). The 250k arm's first summary request is the 49th model request in the trace (0-based index 48). Its prompt is 249,800 tokens. The next productive prompt is 50,000: the 46,000-token reset (system, project, summary, re-injected files) plus 4,000 of new input. The about-967k arm's first summary request has prompt 967,400, after 204 earlier requests, and the next productive prompt is also 50,000.

End of S1, from the simulator, not from 250/967:

| | 250k | about 967k | high / low |
| --- | ---: | ---: | ---: |
| Raw prompt tokens, including summary requests | 72,881,600 | 222,160,000 | 3.05 |
| API-equivalent units | 13,626,420 | 28,033,460 | 2.06 |
| Compactions | 10 | 2 | |
| Productive model requests | 480 | 480 | |

The high arm sends 3.05 times as many prompt tokens. Its API-equivalent total is 2.06 times as high. Dividing 967 by 250 gives 3.87, which ignores the reset context, the summary requests, and the fact that most of a warm prompt is a cache read.

```text
weighted units = fresh input + 1.25 * 5-minute writes + 2 * 1-hour writes
               + read_multiplier * cache reads + output_multiplier * output
```

Multiply by the model's base input price for a list-price estimate. Do not turn this line into the five-hour bar.

One control, not both:

```bash
# Percentage of whatever window this version selects. Cannot raise the default.
CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=25 claude

# Absolute window. Remove a percentage override first, or they compound.
claude --autocompact 250000
```

In T3 Code, Settings, Providers, Claude, Auto-compact after `250000` is the window. A source comment says the UI treats the threshold as that window minus about 33,000, and Claude Code v2.1.325 agrees: the window minus a 20,000 output reserve minus a 13,000 buffer, so `250000` fires at 217,000. Record `compactMetadata.preTokens` rather than the label.

## Example 2. Twenty requests where both triggers do nothing

Assumptions: 20 user turns, 1 request each, 4,000 new input, 1,000 output, same 28,000-token startup context. From `zero_effect_control` in `outputs/summary.json`:

- First prompt: 32,000
- Last prompt: 127,000
- Raw prompt tokens: 1,590,000
- Compactions at 250k and at about 967k: 0 and 0
- API saving: 1.00

The setting does nothing. A short session that starts with a huge attachment is a different scenario. S2 in the scenario table is the same lesson with 3 user turns of 10 calls: API saving 1.00. S2b forces a 100k window on that short session and the API-equivalent total gets worse (0.93). Early compaction is not free.

At a finished, unrelated task, `/clear` drops the conversation. It does not undo files, and the next request still pays for the system prompt and project context. For unfinished related work, `/compact` with an explicit keep-list is the comparison, and the summary request is part of the cost.

## Example 3. The compounding trap

```json
{
  "autoCompactWindow": 250000,
  "env": { "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "25" }
}
```

The percentage applies to the window. This model's trigger is 62,500, and it warns. Claude Code v2.1.325 applies it to the window after the 20,000 output reserve: floor(230,000 x 0.25) = 57,500. The live arm compacted 6 times on a 4-phase build and cost 14% more than the default, with $0.87 of that in summary requests the transcript does not show. S8 compares that trigger with about 967k: raw-prompt saving 6.21, API saving 1.43, compactions 158 against 2. S8b compares it with 25% alone (250k): API saving 0.70, so the compounded arm costs more at API prices than the percentage by itself. Raw tokens still look better (2.04). That is how a transcript-only reading flatters a thrash.

The 100k floor is a limit of the absolute window control. It does not force a percentage override back up to 100k. On a 200k context, percentage 25 is 50,000. S6: API saving 0.31 against the 200k boundary. The low arm compacts 476 times and costs more.

Use one control. Then read the boundaries.

## Example 4. Lost context

S3 adds the same number of extra 8,000-token repair requests after every compaction, on both arms. API saving versus the no-rework S1 baseline of 2.06:

| Extra repair requests per compaction | API saving | Raw-prompt saving |
| --- | ---: | ---: |
| 0 | 2.06 | 3.05 |
| 3 | 1.88 | 2.81 |
| 6 | 1.68 | 2.52 |
| 10 | 1.41 | 2.17 |
| 15 | 1.03 | 1.64 |

Around 15 repair requests, the API-equivalent gap is gone and raw tokens are still lower. This is the simulator's chosen repair model. It is not a measured property of a model, and it is not the old "seven turns" illustration, which used a different cache formula.

A concrete failure the benchmark can score: an invoice rule that says round only the final amount, half even, never each line. Put it in project-root `CLAUDE.md` and in a hidden test. A summary that drops it shows up as a failed test and as repair requests, not as a nicer token graph. Root `CLAUDE.md` is re-injected. Path-scoped rules and files over 5,000 tokens are not guaranteed to come back in full ([context window](https://code.claude.com/docs/en/context-window)).

## Example 5. Desktop, CLI, T3 Code

Measured on 13 November 2026, Opus 5.5, effort medium, identical `CLAUDE.md`, no user MCP servers. Cold first request: Claude CLI v2.1.325 `claude -p` 16,850 tokens, interactive 19,528, T3 Code v0.1.9 22,842, Claude Desktop v5.1231.2 Code tab 39,749. The same `noisy_bughunt` fix: CLI $0.1145 in main-thread rows ($0.1247 with background calls), T3 Code $0.1362, Desktop $0.2057. The full table is in `benchmarks/client_comparison.md`. Use your own numbers for your own setup.

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
