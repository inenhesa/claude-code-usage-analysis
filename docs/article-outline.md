# Outline: fewer tokens is not less usage, and a pause can cost more than the work

The essay is `docs/article-draft.md`. Modeled numbers are in `outputs/summary.json` (`use_cases`, `pause_tax`, `eoq`). Live numbers are the 14 Opus 5.5 runs in `outputs/live_experiments.json`, 13 November 2026, n=1 per arm. Every change is cache-weighted usage at the Opus 5.5 weights `1 / 1.25 / 2 / 0.05 / 5`, with 0.1x and 0.025x reads as the sensitivity range. Neither source is a subscription-meter measurement.

## 1. Open on the measured surprise

The 100k arm sent 63% fewer raw tokens and used 25.2% more. A warm session saved 27.7% against `/clear`. Then the measured turn: a 97k session resumed after a 10-minute break used about 10 times a fresh `/clear` start on the same fix (3 of 3 pairs, n=1 each).

## 2. Three meanings of "usage"

Raw prompt tokens, cache-weighted usage, and the unpublished five-hour meter. A large change in the first is not the same change in the second, and the third is not measured here. Link the [usage-limits article](https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work) and the [pricing page](https://platform.claude.com/docs/en/about-claude/pricing).

## 3. The pause tax

- One old token after a pause costs 12.5 to 40 times a warm read (rewrite weight over read weight).
- Stale resume loses to `/clear` plus a handoff past about 10k of history (8.3k on the 1-hour cache), with 2 exploration calls saved. +41% at 30k, 2.02 times at 60k, 3.25 times at 120k.
- Hot resume wins while history times remaining calls stays under about 120k token-calls at 0.05x reads.
- Hourly `claude -p --resume`: fresh runs use 33% to 35% of it.
- Measured live, 14 November 2026, Opus 5.5, 5-minute cache: stale resume after 10+ minutes used 9.8 to 9.9 times a fresh start (3 pairs). Warm resume after 3.3 minutes, same 97k history, used 1.5 times a fresh start and 6.5 times less than stale. First stale call rewrote 86k to 88k tokens; the warm one read 96,730 and wrote 743.

## 4. Compaction, measured live

Five arms, one 4-phase migration, no pauses. Default 100, 180k 100 (tie), compound +13.6%, 140k +18.1%, 100k +25.2%. Hidden summary share 11.8% to 25.5%. At 0.1x reads 180k is 13.6% under the default. Two arms fail the 15% `preTokens` gate. The tie assumes no pause past the cache window.

## 5. The trigger math

`window - min(maxOutputTokens, 20000) - 13000`: 180000 fires at 147,000, override 18 at 176,400, a 250,000 window with 25 at 57,500. The four idle-compaction conditions, and why `--autocompact 180000` needs `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS=100000` to keep idle compaction.

## 6. Clients

First request: T3 Code +17%, Desktop Code tab 2.04 times the interactive CLI. Bugfix usage: T3 1.19x, Desktop 1.80x. Every first request read 63% to 68% from a warm cache, so these are not cold starts. C2, C4, C5, and C6 are not run. Cite [claude-code#97074](https://github.com/anthropics/claude-code/issues/97074) and [t3code#24649](https://github.com/pingdotgg/t3code/issues/24649).

## 7. The model's sweet spots

Deep work 120k (-57%) at 0.1x reads and 160k (-43%) on Opus 5.5. Marathon 130k (-54%). Heavy memory 280k (-24%). Rules only in the chat 360k (-24.9%). Long breaks with idle compaction 140k (-18%). Bugfix: leave it, forcing 100k is +8%. 200k model: leave it, override 25 is +239%. Pause traps: lapsed idle +76%, 5-minute gaps +213%.

## 8. Figures

Lead charts: `ttl_price_of_a_pause.png`, `resume_crossover.png`, `cron_resume_vs_fresh.png`, `live_compaction_arms.png`, `live_clear_vs_warm.png`, `live_client_comparison.png`, `scenario_comparison.png`, `eoq_curve.png`, `context_sawtooth.png`. Supporting: `cumulative_raw_tokens.png`, `cumulative_api_equivalent.png`, `amnesia_breakeven.png`, `sensitivity_rework.png`, `compounding_sawtooth.png`, `short_session.png`, `threshold_sweep.png`, `sensitivity_cache_hit.png`. Two hues only: green for the better option, red for the worse.

## 9. What is still unmeasured

Five-hour and weekly meter movement, the subscription 1-hour cache, sessions past 300k tokens, the 1-hour pause tax live, repeats beyond n=1, and cards C2, C4, C5, C6. Do not invent them.
