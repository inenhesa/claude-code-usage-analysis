# Outline: Finding the Auto-Compaction Sweet Spot in Claude Code

The essay is `docs/article-draft.md`. Workflow numbers are outputs of the caching simulator in `outputs/summary.json`, including `use_cases`. Live numbers are the 14 Opus 5.5 runs in `outputs/live_experiments.json`, 13 November 2026, $18.57 of API spend. Neither is a subscription-meter measurement.

## 1. Open on the default, then the valley

Native 1M models wait until about 967k. Everyday deep work bottoms near 120k at standard read prices and near 160k at Opus 5.5 / Sonnet 5.5 read prices. The copy-paste setting is one knob at 180k. Name the client track (Desktop, CLI, T3 Code) before any chart, and keep it separate.

## 2. Three meanings of "usage"

Raw prompt tokens, cached API cost, and the unpublished five-hour meter. A large change in the first is not the same change in the third. Link the [usage-limits article](https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work) and the [pricing page](https://platform.claude.com/docs/en/about-claude/pricing).

Define a user turn versus a model request before the 60-by-8 figure. On that session at 0.05x reads, 160k trims prompt tokens by about 77% and estimated cost by about 43%.

## 3. The use-case table

Lead with the eight workflows in `summary["use_cases"]`:

- Deep feature work: 120k (-57%) at 0.1x reads, 160k (-43%) at 0.05x reads.
- Marathon refactor with smaller steps: 130k (-54%).
- Heavy file reads and a large retained context: 280k (-24%). Heavier memory moves the valley up.
- Rules only in the chat, eight repairs per compact: 360k (about 24.9% under the default). Root `CLAUDE.md` comes first.
- Four long breaks with idle compaction on: 140k, and only about 18% under the default, because idle compaction already fired.
- Quick bugfix: 180k matches the default at 0%. Forcing 100k costs about 8% more.
- 200k-context model: the default is already close. Forcing 40k or 50k costs about 227% more. A 120k trigger is only about 6% under the default.

List-price dollars, not the meter: Sonnet 5 is $56.07 at the default and $23.90 at 120k. Opus 5.5 is $68.16 at the default and $38.64 at 160k.

The two-knob trap and the percentage math on a 200k window stay in `docs/reference.md`. Do not make them the opening.

## 4. Figures

Lead with the three shareable charts:

1. `outputs/figures/scenario_comparison.png`. Each workflow's sweet spot, or the penalty for forcing a low trigger.
2. `outputs/figures/eoq_curve.png`. Session cost as a percent of the ~967k default. Valleys at 120k (-57%), 160k (-43%), 220k (-28%). The green band is 150k-200k and the red band marks the thrash zone below 100k. Red is 0.1x reads, solid green 0.05x, dashed green 0.025x. There is no marker for a fixed fraction of 1M.
3. `outputs/figures/context_sawtooth.png`. Default (red) against the 160k sweet spot (green). The subtitle is computed from those two runs.

Live charts, from `python3 -m cc_usage_study.experiments figures`:

- `outputs/figures/live_compaction_arms.png`. Visible assistant cost (green) plus hidden compaction-summary cost (red) for 5 arms, and observed `preTokens` (red dots) against the `tle` + `WX` threshold (green).
- `outputs/figures/live_clear_vs_warm.png`. Three `/clear` sessions (red, $0.1507) against one warm session (green, $0.1089).
- `outputs/figures/live_client_comparison.png`. Cold prefix and bugfix cost: CLI in green, T3 Code in light red, Desktop in deep red.

Supporting charts, same assumptions:

4. `outputs/figures/cumulative_raw_tokens.png` and `cumulative_api_equivalent.png`.
5. `outputs/figures/amnesia_breakeven.png` and `sensitivity_rework.png`.
6. `outputs/figures/ttl_cliff.png`.
7. `outputs/figures/compounding_sawtooth.png`. The stacked-knob case, explained in the reference.
8. `outputs/figures/short_session.png`. The lines match. Saving is 0%.
9. `outputs/figures/threshold_sweep.png` and `sensitivity_cache_hit.png`.

## 5. Quality and the client

Eight repairs move the valley to 360k. Fifteen repairs are a second sensitivity run, not a law. Cost per finished task is the metric. Root `CLAUDE.md` and executable tests come before a lower trigger.

Part 2 holds the window fixed and compares clients. Cards C1 and C3 are measured: T3 Code v0.1.9 +17% cold prefix and +19% bugfix cost over Claude CLI v2.1.325, Claude Desktop v5.1231.2 2.04 times and 1.80 times. C2, C4, C5, and C6 are not run. `sdk-cli` versus interactive `cli` is a meter question, not a token question. Cite [claude-code#97074](https://github.com/anthropics/claude-code/issues/97074) and [t3code#24649](https://github.com/pingdotgg/t3code/issues/24649).

## 6. The live section

Lead Part 1's live section with the hidden summary bill: $0.08-$0.34 per compaction, $1.01 (25.5%) on the 100k arm. Then the binary math: window minus min(maxOutputTokens, 20,000) minus 13,000, so 180k fires at 147,000, and a 250,000 window with 25 fires at 57,500. Then break-even: on a ~270k session 180k ties at $3.17 and lower triggers lose 14% to 25.2%. Then `/clear` against a warm session, -27.7%.

## 7. What is still unmeasured

Five-hour and weekly meter percentage points, subscription 1-hour cache behavior, sessions past 300k tokens, repeats, and cards C2, C4, C5, C6. Do not invent them.
