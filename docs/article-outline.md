# Outline: Does Compacting Claude Code Earlier Actually Save Usage?

The essay is `docs/article-draft.md`. Numbers are outputs of the caching simulator in `outputs/summary.json`. They are not account measurements.

## 1. Open on the default, then the sweet spot

Native 1M models wait until about 967k. The goal is the trigger everyday developers should set, about 150k to 200k, and a separate track for Desktop versus CLI versus T3 Code. Name the two levers before any chart. Say that stacking a window and a percentage, or using 25% on a 200k model, raises the bill.

## 2. Three meanings of "usage"

Raw prompt tokens, cached API cost, and the unpublished five-hour meter. A factor on the first is not a factor on the third. Link the [usage-limits article](https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work) and the [pricing page](https://platform.claude.com/docs/en/about-claude/pricing).

Define a user turn versus a model request before the 60-by-8 figure.

## 3. What 25% refers to

Do not write "the default is 95%." Native 1M models compact at about 967k ([model config](https://code.claude.com/docs/en/model-config)). The percentage override cannot raise the threshold. A window plus a percentage compounds: 250,000 and 25% is 62,500, and that costs 43% more than 250k alone. On a 200k model, 25% is 50,000 and the estimated cost more than triples (+227%). T3's "Auto-compact after" is the window. A source comment says the reported threshold is about 33,000 below that window. Verify with `preTokens`.

## 4. Figures

Lead with the three shareable charts:

1. `outputs/figures/scenario_comparison.png`. Percent change in estimated API cost. Teal is cheaper, coral costs more.
2. `outputs/figures/eoq_curve.png`. Session cost as a percent of the ~967k default. Valleys at 120k (-57%), 160k (-43%), 220k (-28%).
3. `outputs/figures/context_sawtooth.png`. Why a 67% token drop is a 51% cost drop.

Supporting charts, same assumptions:

4. `outputs/figures/cumulative_raw_tokens.png` and `cumulative_api_equivalent.png`.
5. `outputs/figures/amnesia_breakeven.png` and `sensitivity_rework.png`. Fifteen repairs leave about a 3% gap.
6. `outputs/figures/ttl_cliff.png`. Idle compaction on cuts the gap to about 24%.
7. `outputs/figures/compounding_sawtooth.png`. 62,500 against about 967k.
8. `outputs/figures/short_session.png`. The lines match. Saving is 0%.
9. `outputs/figures/threshold_sweep.png` and `sensitivity_cache_hit.png`.

Print next to the first figure:

- Raw prompt tokens: 72,881,600 against 222,160,000. About 67% fewer. Not 4x.
- Estimated API cost: 13,626,420 against 28,033,460. About 51% lower at a 0.1x read, about 41% lower at 0.05x.
- Compactions: 10 against 2.
- Sweet spot: 120k (57% cheaper) at 0.1x, 160k (43% cheaper) at 0.05x, 220k (28% cheaper) at 0.025x. 25% of 1M is 250k, past the valley.

## 5. What a laptop must fill in

Slots, not guesses:

- [MEASURED_LOCAL_DATA: settings, date, model, entrypoint, version.]
- [MEASURED_LOCAL_DATA: main-stream buckets, prompt tokens per call, `preTokens`.]
- [MEASURED_LOCAL_DATA: meter percentage points for the same blocks.]
- [MEASURED_LOCAL_DATA: tests, repairs, elapsed time, or "not run."]

The analyzer does not see the meter.

## 6. The controlled run

Point at `benchmarks/protocol.md`. Smoke tasks do not reach 250k. Long tasks are generated from a seed, with hidden tests. `claude -p` is `sdk-cli`. Meter claims need the `tui` arm. No live numbers exist yet.

## 7. Clients, kept separate

`benchmarks/client_comparison.md`. Match the window with `/context`. Do not treat a client switch as a compaction result.

## 8. Caveats

- About 967k is today's documented approximation for native 1M models, not a permanent constant.
- Cold rewrites in the model are priced as cache writes, not as fresh input. `outputs/summary.json` says so.
- Haiku 5.5's long-prompt tier is per request. Fast-mode cache prices are not estimated.
- 4.7 and later models tokenize the same text into more tokens.
- Subagent files often reuse the parent's session id. Counting them inside the parent invents a ratio.
- The synthetic fixture is fake.

## 9. Close

On long sessions, a lower trigger cuts re-sent prompt tokens by about 3x here, and the list-price saving is closer to 2x. The valley is nearer 150k to 200k than to a blind 25%. If tests fail when the trigger drops, the token saving is real and the work is worse.
