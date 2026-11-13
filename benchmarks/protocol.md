# Controlled benchmark protocol

This is the procedure for asking whether a lower auto-compaction threshold changes token use, wall time, and task success on a fixed task. The harness in `src/cc_usage_study/benchmark.py` builds the commands. It does not, by itself, answer the question. A live run spends quota.

Read the README section on the two configuration knobs before changing anything on a machine you use for real work.

## Question

On one model, one effort level, and one frozen task, how do total tokens, cache reads, wall time, and tests-passing change when the compaction trigger moves?

Two knobs exist, and they are not the same experiment:

1. Window arm. Set the auto-compact window, in tokens, with `CLAUDE_CODE_AUTO_COMPACT_WINDOW` and `--autocompact`. The documented range is 100,000 to 1,000,000, capped at the model window. This is the control the docs describe as "how full the context gets before compaction."
2. Percent arm. Set `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` and pass `--autocompact auto`, so the window stays on the model default and the percentage scales that window. This is the control that matches the wording of the 25% setting. Values above the default percentage are ignored, and the docs do not publish that percentage. On current native 1M models the observed default trigger is about 967K tokens, so a 95% arm may be identical to the default.

Run both. If they disagree, the percentage story and the token-window story are different claims.

## What is held fixed

- Claude Code version. Record `claude --version` on every batch. The about-967K figure is from the current model-config page, and the Sonnet 5 window moved in v2.1.323 (12 November 2026).
- Model and effort. Pass `--model` explicitly. Do not change effort mid-run. A model switch or, on many models, an effort change starts a new cache.
- Task snapshot. Each arm copies the task directory to a fresh work directory.
- Tools and permissions. The harness uses `--permission-mode acceptEdits`, `--allowedTools Bash,Read,Edit`, and by default `--permission-prompts none` (that flag needs v2.1.259 or later; drop it with `--no-permission-prompts-flag` on older builds).
- No bare mode. `--bare` does not use the subscription login and it shortens the system prompt. Leave it off if the question is the five-hour limit.
- Plugins, MCP servers, and personal `CLAUDE.md` are a confound. Prefer a task directory that has none, and be aware that a normal `claude -p` still loads user-level configuration. Record that configuration in the lab notebook.

## What is measured

From the stream-json result message, per arm:

- `usage`: top-level loop only. Subagent tokens are absent.
- `modelUsage`: top-level loop plus subagents, per model.
- `total_cost_usd`: client-side list-price estimate. Not the subscription meter.
- `num_turns`
- compaction markers: records whose `subtype` contains `compact`. This can under-count if a build writes the summary without that marker.
- wall time, measured by the harness
- tests passing, from the task's `score` command
- extra files outside the task's `editable` list, a crude quality check for "the model wrote around the bug"

The harness drops message text. It keeps token fields only.

The five-hour and weekly percentages are not in the transcript. Read them from Settings > Usage on claude.ai, or from the Claude Code usage screen, immediately before the first arm and immediately after the last arm. Do this on an account that is otherwise idle. Write the percentages in the lab notebook next to the token totals. That pair is the only observation that speaks to "I stopped hitting the limit."

## Scaffold tasks, and why they are not enough

`benchmarks/tasks/` has three short bugs:

| Task | Pass condition | Context risk |
| --- | --- | --- |
| `median_bug` | `median` averages the two middle values for an even length | low |
| `slice_bug` | `last_n` keeps `n` items | low |
| `keep_currency` | tax rounds half up, and `CURRENCY` stays `EUR` | the currency rule is only in the prompt |

These tasks exist so the harness can be scored without a network call. They are too short to reach 25% of a 1M window, let alone 95%. A run on them can still show a difference in startup tokens, and it can show that the command line is accepted. It cannot show the sawtooth from the simulation.

A task that can speak to the claim needs enough tool output to cross the higher trigger. Add one as a directory with `task.json`, `prompt.txt`, the code, and a pytest file. Point `score` at that pytest. Keep the prompt fixed. Put any constraint you care about losing (a name, a forbidden import, a file that must not change) in the prompt and in a test, the way `keep_currency` does.

Suggested shape for a long task: a multi-file bug whose test fails until several files are edited, plus a written constraint that is easy to violate if the summary drops it. Run it once at the default trigger and confirm, from the compaction marker count or from context size in the transcript, that compaction actually fired. If it did not, the arm is not a compaction experiment.

## Repeats and order

Run a pilot before a grid. One task, two triggers (the default, and 25%), both methods, three repeats. That is 12 live sessions. Randomize the order, or alternate, so a warm prompt cache or a usage-limit backoff does not line up with one trigger.

The full grid in the harness is five percents, two methods, three tasks, and three repeats: 90 sessions. Do not start there. The thing under study is a quota. A 90-session grid can consume the limit you are trying to measure.

Cache state matters. The docs say parallel sessions in the same directory can share a prefix, and a compaction invalidates the conversation layer. Use a fresh copy of the task per arm, which this harness does, and leave a gap longer than the cache TTL if you need cold-start numbers. On a subscription, inside the plan, the main conversation TTL is one hour. After the limit, Claude Code drops that to five minutes.

## Commands

Dry run, the default. Prints the plan and does not launch Claude:

```bash
python -m cc_usage_study.benchmark --task median_bug --percents 95,25 --output outputs/benchmark-plan.json
```

Pilot, which spends quota:

```bash
python -m cc_usage_study.benchmark --execute \
  --task median_bug \
  --percents 95,25 \
  --methods window,pct \
  --repeats 3 \
  --model sonnet \
  --output outputs/benchmark-pilot.json
```

Check the plan's `preflight` list. In particular, remove or account for `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`, `CLAUDE_CODE_AUTO_COMPACT_WINDOW`, and `autoCompactWindow` in `~/.claude/settings.json`. A valid window variable outranks `--autocompact`. Managed settings outrank the overlay, and the harness cannot see them.

`--no-session-persistence` keeps the run out of `~/.claude/projects`. Leave it off if you want the log analyzer to see the same sessions. The result JSON is the benchmark record either way.

## How to read a result

For each method separately, compare 25% with 95% (or with the untouched default, if the 95% percent-arm was ignored):

- Sum of `modelUsage` input, output, cache read, and cache creation. Prefer this over top-level `usage` once the task starts subagents.
- Cache-read share: cache read divided by (input + cache read + cache creation).
- Tests passed, and whether `CURRENCY` or the equivalent constraint held.
- Wall time.
- Compaction markers. Zero markers means the threshold was not reached. Do not interpret a token difference in that run as a compaction effect.
- The meter percentages you wrote down. A token ratio and a meter ratio are different outcomes. Report both. Do not convert one into the other with a weight Anthropic has not published.

## Stop rules

Stop the batch if the five-hour meter crosses a level you still need for other work. A failed or partial batch is data about cost. It is not a reason to switch the account onto usage credits mid-comparison, because crossing into credits also changes the cache TTL.
