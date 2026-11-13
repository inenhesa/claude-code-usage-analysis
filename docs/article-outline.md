# Outline: does compacting Claude Code earlier actually save usage?

Draft for nevolin.be. The article itself is `docs/article-draft.md`. Numbers below are outputs of the physical-caching simulator in `outputs/summary.json`, scenario S1 unless named otherwise. They are not account measurements.

## 1. Open on the correction

Quote both posts. "Almost 4x" is one person's observation. "Another 200%" is not a valid reduction. Do not invent a replacement ratio. State the question: when is a smaller history cheaper without making the work worse?

## 2. Three accountings

Raw prompt tokens, API-equivalent cost at published prices, and the five-hour and weekly meters. A factor on the first is not a factor on the third. Link the [usage-limits article](https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work) and the [pricing page](https://platform.claude.com/docs/en/about-claude/pricing).

Define a user turn versus a model request before any 60-by-8 figure.

## 3. What 25% refers to

Do not write "the default is 95%." Native 1M models compact at about 967K ([model config](https://code.claude.com/docs/en/model-config)). The percentage override cannot raise the threshold. A window plus a percentage compounds: 250,000 and 25% is 62,500 in this model (S8). T3's "Auto-compact after" is the window. A source comment says the reported threshold is about 33,000 below that window (S1b uses 217,000). Verify with `preTokens`.

## 4. Figures, with the assumptions under the first one

1. `outputs/figures/cumulative_raw_tokens.png`. S1 cumulative prompt tokens.
2. `outputs/figures/cumulative_api_equivalent.png`. Same session, priced.
3. `outputs/figures/context_sawtooth.png`. Prompt size at 250k and at about 967k.
3b. `outputs/figures/scenario_comparison.png`. API cost ratio for the eight headline cases.
4. `outputs/figures/eoq_curve.png`. Cost versus trigger at read prices 0.1, 0.05, and 0.025.
5. `outputs/figures/amnesia_breakeven.png`. S3 repair sweep.
6. `outputs/figures/ttl_cliff.png`. Idle off, idle on, 5-minute TTL.
7. `outputs/figures/compounding_sawtooth.png`. 62,500 against about 967k.
8. `outputs/figures/short_session.png`. S2, the lines match.
9. `outputs/figures/threshold_sweep.png`. The 0.1x slice of the EOQ grid.
10. `outputs/figures/sensitivity_cache_hit.png`. Read-price sensitivity.
11. `outputs/figures/sensitivity_rework.png`. Same data as the amnesia figure.

Print next to the first figure, S1:

- Raw prompt tokens: 72,881,600 against 222,160,000. Ratio 3.05. About 67% fewer raw prompt tokens at 250k. Not 4x.
- API-equivalent units: 13,626,420 against 28,033,460. Ratio 2.06.
- Compactions: 10 against 2.
- Grid optima: 120,000 tokens at 0.1x (saving 2.35 versus 967k), 160,000 at 0.05x (1.76), 220,000 at 0.025x (1.39). Formula estimates 126,393, 158,880, and 205,060. Say the practical band for current list prices is about 150k to 200k, and that 25% of 1M (250k) is past it.
- S3 at 15 repair requests: API ratio 1.03.
- S4b idle on: API ratio 1.31.
- S2: API ratio 1.00. S2b: 0.93.
- S8 raw 6.21, API 1.43. S8b API 0.70 versus 250k alone.

## 5. What the laptop must fill in

Slots, not guesses:

- [MEASURED_LOCAL_DATA: settings, date, model, entrypoint, version.]
- [MEASURED_LOCAL_DATA: main-stream buckets, per-call prompt tokens, `preTokens` histogram.]
- [MEASURED_LOCAL_DATA: meter percentage points for the same blocks.]
- [MEASURED_LOCAL_DATA: tests, repairs, elapsed time, or "not run."]

The analyzer does not see the meter. `five_hour_blocks` are labeled weights, including a third-party attribution scheme that is not the server meter.

## 6. The controlled run, as a protocol, not a result

Point at `benchmarks/protocol.md`. Smoke tasks do not reach 250k. Long tasks are `ledger_migration`, `noisy_bughunt`, and `steered_feature`, generated from a seed, with hidden tests and checkers. Placement of the invariant (CLAUDE.md, prompt, doc, PreCompact hook) is the quality variable. `claude -p` is `sdk-cli`. Meter claims need the `tui` arm. No live numbers exist yet.

## 7. The client switch, separate

`benchmarks/client_comparison.md`. Matched window via `/context`. T3 idle policy and invisible text-generation runs. Counterbalanced order. Do not claim T3 is cheaper. Some public reports say the SDK entrypoint used more of the five-hour meter per token.

## 8. Caveats to print

- One setup, two changes at once (threshold and client).
- About 967K is a documented approximation, not a historical constant for the week of the posts.
- Cold rewrites in the model are priced as cache writes, not as 1.0x fresh input. The summary file says so.
- Haiku 5.5's long-prompt tier is per request. Fast-mode cache prices are not in the estimator.
- 4.7 and later models tokenize the same text into more tokens. A version change can look like a usage change.
- Subagent lines often carry the parent's session id. Counting them inside the parent, or counting a compaction twice, will invent a ratio.
- The synthetic fixture is fake.

## 9. Close on the narrow claim

If the laptop split and a long-task pilot agree with S1, the honest sentence is: on long sessions, a lower trigger reduces re-sent prompt tokens by something on the order of 3x in this scenario, the list-price saving is closer to 2x, the priced optimum is nearer 150k to 200k than to 25% of 1M, and the meter is a separate measurement.

If prompt tokens per completed task do not move, the 4x was workload, model, client, or the meter.

If tests fail when the trigger drops, the token saving is real and the work is worse.
