# Outline: Finding the Auto-Compaction Sweet Spot in Claude Code

The essay is `docs/article-draft.md`. Numbers are outputs of the caching simulator in `outputs/summary.json`, including `use_cases`. They are not account measurements.

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
2. `outputs/figures/eoq_curve.png`. Session cost as a percent of the ~967k default. Valleys at 120k (-57%), 160k (-43%), 220k (-28%). The teal band is 150k-200k. There is no marker for a fixed fraction of 1M.
3. `outputs/figures/context_sawtooth.png`. Default against the 160k sweet spot. The subtitle is computed from those two runs.

Supporting charts, same assumptions:

4. `outputs/figures/cumulative_raw_tokens.png` and `cumulative_api_equivalent.png`.
5. `outputs/figures/amnesia_breakeven.png` and `sensitivity_rework.png`.
6. `outputs/figures/ttl_cliff.png`.
7. `outputs/figures/compounding_sawtooth.png`. The stacked-knob case, explained in the reference.
8. `outputs/figures/short_session.png`. The lines match. Saving is 0%.
9. `outputs/figures/threshold_sweep.png` and `sensitivity_cache_hit.png`.

## 5. Quality and the client

Eight repairs move the valley to 360k. Fifteen repairs are a second sensitivity run, not a law. Cost per finished task is the metric. Root `CLAUDE.md` and executable tests come before a lower trigger.

Part 2 holds the window fixed and compares clients. Cards C1 through C6. `sdk-cli` versus interactive `cli` is a meter question, not a token question. Cite [claude-code#97074](https://github.com/anthropics/claude-code/issues/97074) and [t3code#24649](https://github.com/pingdotgg/t3code/issues/24649).

## 6. What a real log still has to fill in

`[MEASURED_LOCAL_DATA]` slots: setting keys, effective window, model, client version, plan, effort, task counts, token buckets, `preTokens`, meter percentage points, and quality. Do not invent them.
