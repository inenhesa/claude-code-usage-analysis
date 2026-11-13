# Finding the Claude Code Auto-Compaction Sweet Spot

*Where auto-compaction cuts real cost across developer workflows, when it backfires, and how to separate Claude Desktop vs. CLI.*

## Why ~967k is rarely the right default

On a native 1M model, Claude Code waits until about **967k tokens** before it auto-compacts. A long coding thread re-sends that history on every model call. Prompt caching makes the re-read cheap, but not free, so the cost still has a valley. For everyday deep work that valley sits in **150k-200k**, not at the default.

Set **one** knob there:

```bash
CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=18 claude
```

```bash
claude --autocompact 180000
```

`18` is 18% of a 1M window, which lands at 180k. Do not set both. The table below is where that single choice should move, by workflow. Figures are this repo's caching model. Your logs are a separate measurement.

## Sweet spot by use case

| Workflow | Sweet spot | Cost vs default | What to do |
| --- | --- | --- | --- |
| Deep feature work, standard reads (Sonnet 5, Opus 4.7) | **120k** | **-57%** | One knob in the band. $56.07 becomes $23.90. |
| Deep feature work, Opus 5.5 / Sonnet 5.5 | **160k** | **-43%** | Same band. $68.16 becomes $38.64. |
| Marathon refactor, smaller steps | **130k** | **-54%** | A little earlier than 180k when each step is small. |
| Heavy file reads, big working memory | **280k** | **-24%** | Do not copy a 120k trigger onto a huge working set. |
| Rules that live only in the chat | **360k** | **-24.9%** | Put them in root `CLAUDE.md` before you lower the trigger. |
| Workday with long breaks, idle compaction on | **140k** | **-18%** | Idle compaction already summarizes a warm cache. |
| Quick bugfix | **180k changes nothing** | **0%**, or **+8%** if forced to 100k | `/clear` between unrelated tasks. |
| 200k-context model | **120k is only 6% under the default** | **+227%** if forced to 50k | Leave the default. A low trigger thrashes. |

### List-price example

One 60-turn deep feature build. The dollars are the published input price times the simulated units. They are **not** the five-hour or weekly meter.

| Model | Reads | Default, about 967k | Sweet spot |
| --- | --- | ---: | ---: |
| Sonnet 5, $2 / million input | 0.1x | $56.07 | $23.90 at 120k (**-57%**) |
| Opus 5.5, $4 / million input | 0.05x | $68.16 | $38.64 at 160k (**-43%**) |

Sonnet 5.5 uses the same 0.05x read price as Opus 5.5, so it shares the 160k valley. Only the dollar scale changes, because its input price is lower.

## Why the valley moves

A warm call re-reads the previous prompt at the cache-read price and writes only the new tail. Compaction throws that warm prefix away and pays to rebuild it. Too late, and you keep paying for a huge read. Too early, and you pay the rebuild on a loop.

- Cheaper cache reads move the valley up and shrink the saving: 120k at 0.1x, 160k at 0.05x, about 220k at 0.025x.
- Bigger file reads, with 30k tokens kept after every summary, move it to **280k**. Rebuilding a large working set is expensive, so you compact less often.
- Eight repair calls after every summary, the cost of rules that were not pinned, move it to **360k**. Put those rules in root `CLAUDE.md` first.
- Idle compaction, on by default after a long pause once the thread is past 200k, already summarizes the default. Also setting 140k then saves **18%**, not the **43%** of an uninterrupted session.
- A three-turn bugfix never reaches 180k, so that setting costs **0%**. 150k can still fire once. Forcing 100k costs **8%** more. Use `/clear`.
- On a 200k model the default is already close. 120k saves about **6%**. Forcing 40k or 50k costs about **227%** more.

180k is the line to paste because it sits in the deep-work band and does not touch a short bugfix. Move it when the work in the table is different.

## The picture

![Cost change versus the default for eight developer workflows](outputs/figures/scenario_comparison.png)

Teal is the discovered sweet spot, cheaper than leaving the default alone. Coral is what happens when a short task or a 200k model is forced too low.

![Three read-price curves, with the 150k-200k band marked](outputs/figures/eoq_curve.png)

100% is the ~967k default. The teal band is the practical setting for a 1M model. Below 100k the curve turns up. The blue valley is 120k (**-57%**). The clay valley is 160k (**-43%**). The green valley, the cheapest reads, is about 220k and only about **28%** under the default.

![Prompt size through a 60-turn session at the default and at 160k](outputs/figures/context_sawtooth.png)

Same deep session on Opus 5.5 / Sonnet 5.5 read prices. The 160k trigger cuts raw prompt tokens by about **77%** and estimated API cost by about **43%**. Most of the tall blue line was a discounted re-read, which is why those two percents are not the same.

## What the model does not claim

It does not read an account, and it does not predict the five-hour or weekly bar. Anthropic has not published those weights. A cold rewrite is priced as a cache write: 1.25x on a compaction, 2x when a one-hour cache expires. A different price for that rewrite would move the dollars. The valley would still be far below 967k on a long 1M session.

The check that belongs to you is the analyzer below, `preTokens` on the first compaction, and Settings > Usage for the bar itself.

## Part 2. Desktop, the CLI, and T3 Code

The client is a different lever. Desktop chat does not write Claude Code transcripts. The Code tab, the official CLI, and T3 Code do, and they do not keep logs for the same length of time or fire the same quiet extra calls. Change the app and the trigger in one week and you will credit the wrong one.

| Surface | JSONL logs | What sets compaction | Silent traffic |
| --- | --- | --- | --- |
| Claude Desktop chat | No | The chat product's own context handling | The meter sees it. These files do not. |
| Desktop Code tab | Yes, kept | Same settings as the CLI. The Desktop launch environment wins. | Prompt suggestions can run in the background. |
| Official `claude` CLI | Yes, 30-day cleanup by default | Env, then flag, then `/autocompact`, then settings | Titles and other helper calls |
| T3 Code | Yes, under its own config directory | "Auto-compact after" is a window. A percentage in user settings still stacks. | Titles and commit text may leave no transcript. |

Hold the trigger fixed, then compare. Cards C1 through C6 in `benchmarks/client_comparison.md` are that experiment. A meter gap at the same token totals is a client finding. A different `raw_max_tokens` from `/context` means the triggers were not the same.

## Check your own logs in 10 seconds

Python 3.11 or newer. Aggregates only. Prompt text is never copied. `--output` and `--summary` print a scorecard on stderr.

```bash
python3 -m pip install -e ".[dev]"
python3 -m cc_usage_study.analyze ~/.claude/projects --split-date YYYY-MM-DD --sessions --output outputs/local-report.json
python3 -m cc_usage_study.simulate --out outputs
```

Replace the date with the day you changed the setting. Read `preTokens` on the first compaction before you trust a label. The five-hour percentages live in Settings > Usage, not in the file. `python3 -m pytest -q` checks the model. Company logs can still contain source code. The safe demo is `fixtures/synthetic/`.

## What's inside

| | |
| --- | --- |
| `docs/article-draft.md` | The essay, workflow by workflow |
| `docs/examples.md` | The arithmetic behind a single session |
| `docs/reference.md` | Knobs, model windows, caching, and the traps |
| `outputs/` | Charts, sweet spots, and the list-price dollars |
| `benchmarks/client_comparison.md` | Cards C1-C6 |
| `fixtures/synthetic/` | Fake transcripts. Reports are marked synthetic. |

## License

MIT. See `LICENSE`.
