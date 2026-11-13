# Stop Leaving Claude Code on Default (~967k)

*Finding the auto-compaction sweet spot for everyday developers, and separating it from Claude Desktop vs. CLI.*

## Two levers, kept apart

On native 1M models, Claude Code waits until **~967k tokens** before auto-compacting, so long threads drag huge histories turn after turn. This study isolates **two independent levers** that affect token burn and 5-hour/weekly limits:

- **Part 1. The auto-compaction sweet spot.** Where compacting earlier genuinely cuts real session cost (~41-57%) without hurting output quality, why ~967k is a poor default for long coding sessions, and which triggers backfire.
- **Part 2. Claude Desktop vs. the Claude CLI (and T3 Code).** How the client changes background traffic, the default boundary, log retention, and rate-limit burn, independently of compaction.

Numbers are from the caching model. Your own logs and a live run are separate, and the analyzer below is how you collect them.

## Best-practice sweet spot

Set **one** knob. On a long session that is the whole win.

| Your setup | Do this |
| --- | --- |
| Native 1M models (Opus 5.5, Sonnet 5.5, Opus 4.7 and later, Sonnet 5 and later) | One knob in the **150k-200k** band: `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=20` or `claude --autocompact 180000`. About **41% to 57%** cheaper on a long session, near 2x, without thrashing. |
| 200k models (`CLAUDE_CODE_DISABLE_1M_CONTEXT=1`, Sonnet 4.6, Opus 4.6) | **Do not use 25%.** It fires at 50k and costs **+227% more**. Stay near the default, or set a window at or above 120k. |
| Both knobs at once | **Never.** A 250k window plus 25% becomes **62.5k** and costs **+43% more** than 250k alone. |
| Short tasks and quick fixes | `/clear` between unrelated tasks. Auto-compaction saves **0%**. Forcing 100k adds **+8%**. |
| Rules the agent must not forget | Put them in root `CLAUDE.md`. A summary re-injects that file, so you do not pay a pile of repair turns. |

One dollar anchor, computed from the published list price times this model's units. It is **not** the subscription meter. In one 60-turn Sonnet 5 session at $2 per million input tokens, the ~967k default is **$56.07**, the 120k valley is **$23.90** (-57%), and 250k is **$27.25** (-51%). On Opus 5.5 at $4 per million, with 0.05x cache reads, **$68.16** falls to **$38.64** at the 160k valley (-43%).

The session behind those dollars is 60 messages, 8 model calls each, 4,000 new tokens and 600 of output per call, on a warm cache. The default compacts twice. At 250k it compacts 10 times. Same work.

| | Default, ~967k | 250k | Sweet spot |
| --- | ---: | ---: | ---: |
| Prompt tokens | 222,160,000 | 72,881,600 | lower still, at a higher compaction count |
| Sonnet 5 list price | $56.07 | $27.25 | $23.90 at 120k |
| Opus 5.5 list price | $68.16 | 0.05x reads, not the 0.1x row | $38.64 at 160k |
| Compactions, 967k vs 250k | 2 | 10 | more often at 120k, still cheaper |

Four long breaks with idle compaction left on, which recent Claude Code does once you pass 200k, shrink the gap to about 24%. The big thread gets summarized while the cache is still warm. Turn idle compaction off if the trigger you chose should be the only one.

If the summary drops a project rule and the agent spends about 15 repair calls putting it back, the saving falls to about 3%. That is why the rule belongs in root `CLAUDE.md` before you lower the trigger.

## Paste one line

On a 1M model, run one of these and leave the other unset:

```bash
CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=20 claude
```

```bash
claude --autocompact 180000
```

`20` is 20% of the window already in force. On a native 1M model that is 200k, the top of the band. `180000` sits in the middle of it. If a window is set and the percentage is also set, they multiply, and you have left the band. The first compaction's `preTokens` is the number that counts.

## The picture

![When compacting earlier saves money, and when it costs more](outputs/figures/scenario_comparison.png)

Teal is cheaper. Coral costs more. The top two bars are the valleys. The long red bar is 25% on a 200k model.

![Session cost versus the compaction trigger, with the 150k-200k band marked](outputs/figures/eoq_curve.png)

100% is leaving the default alone. The teal band is where to set a 1M model. Below 100k the curve turns up.

![Prompt size through a long session at two triggers](outputs/figures/context_sawtooth.png)

Same 60-turn session. Caching is why a 67% drop in prompt tokens becomes about a 51% drop in cost. Most of a warm prompt is a cheap re-read, so shrinking the history does not shrink the bill by the same factor.

The red bar is the one to show a teammate who set 25% on a 200k model. The teal band on the curve is the one to show everyone else. Dividing 967 by 250 suggests a 3.9x drop, and that shortcut skips the summary calls, the context you keep, and the cache.

## Part 2. Desktop, the CLI, and T3 Code

Switching apps is not a compaction result. Desktop chat does not even write the Claude Code transcript. The Code tab, the official CLI, and T3 Code do, and they do not keep those logs for the same length of time or fire the same quiet extra calls. Change the client and the trigger in the same week and you will credit the wrong one.

| Surface | JSONL logs | What sets compaction | Silent traffic |
| --- | --- | --- | --- |
| Claude Desktop chat | No | The chat product's own context management | The meter sees it. These files do not. |
| Desktop Code tab | Yes, kept at any age | Same settings as the CLI, but the Desktop launch environment wins | Prompt suggestions can be a background request |
| Official `claude` CLI | Yes, deleted after 30 days by default | Env, then flag, then `/autocompact`, then settings | Titles and other helper calls |
| T3 Code | Yes, under that app's config directory | "Auto-compact after" is a window. A percentage in user settings still stacks on top. | Titles and commit text may save no transcript. Suggestions default off. |

Hold the window fixed, then compare. Cards C1 through C6 in `benchmarks/client_comparison.md` are that experiment: same task, same boundary, client as the only change. A five-hour difference at the same token totals is a client finding. A different `raw_max_tokens` from `/context` means you did not match the trigger.

## Check your own logs in 10 seconds

Python 3.11 or newer. The analyzer prints aggregates only. It never copies prompt text. `--output` and `--summary` also print a one-screen scorecard on stderr.

```bash
python3 -m pip install -e ".[dev]"
python3 -m cc_usage_study.analyze ~/.claude/projects --split-date YYYY-MM-DD --sessions --output outputs/local-report.json
python3 -m cc_usage_study.simulate --out outputs
```

Replace the date with the day you changed the setting. The scorecard shows sessions, main calls, compactions, the cache-read share, prompt tokens per call, and the before/after change. CLI logs older than 30 days may already be gone. Desktop and Cowork transcripts are kept.

Read `preTokens` on the compaction events before you trust a label. If both knobs were set, expect a boundary near 62,500, not 250,000. The five-hour percentages live in Settings > Usage, not in the JSONL. `python3 -m pytest -q` checks the model against these charts. Company logs can still contain source code. The safe demo is `fixtures/synthetic/`.

## After the first week

Treat the first week as a check, not a victory lap.

- Prompt tokens per main call should drop if long threads were the bulk of the work. A week of tiny bugfixes can show no change, and that is the 0% bar, not a failed setting.
- Compactions jumping into the hundreds, with the per-call size barely down, means the trigger is too low. That is the 50k case.
- If tests start failing on rules the agent used to remember, move the trigger up or put the rule in root `CLAUDE.md`. A cheaper transcript and a broken task is a loss.
- Copy the five-hour and weekly percentages for the same blocks, with nothing else using the account. A list-price win that does not move the meter is still worth knowing. The meter formula is not published.
- If you also switched from Desktop to the CLI or to T3 Code, split those sessions by entrypoint before you credit the trigger. Part 2 is a different experiment.

## What's inside

| | |
| --- | --- |
| `docs/article-draft.md` | The essay: the sweet spot, the traps, and the client question |
| `docs/examples.md` | The arithmetic, request by request |
| `docs/reference.md` | Knobs, model windows, caching, idle compaction |
| `outputs/` | Every chart, plus the list-price dollars |
| `benchmarks/client_comparison.md` | Cards C1-C6 for Desktop vs CLI vs T3 |
| `fixtures/synthetic/` | Fake transcripts. Reports from them are marked synthetic. |

## License

MIT. See `LICENSE`.
