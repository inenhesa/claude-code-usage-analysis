# Claude Code compaction study

A reproducible look at two anecdotal claims from Ilja Nevolin's Claude Code setup, posted on LinkedIn.

## Status

1. Personal observation, not a controlled experiment: auto-compaction at 25% "cut usage by almost 4x," and a repost said switching from the Claude app to T3 Code "cuts take usage by another 200%." A reduction cannot exceed 100%. The intended ratio was not recorded. This repo does not replace 200% with 2x, 3x, or any other factor.
2. Simulated long session (S1, physical prefix caching): the about-967k trigger sends 3.05 times the raw prompt tokens of a 250k trigger (222,160,000 vs 72,881,600).
3. The same session at published API price shape (cache reads 0.1x, 1-hour writes 2x, output 5x): 2.06 times the cost (28,033,460 vs 13,626,420 base-input units). Not 4 times.
4. Local effect on Ilja's transcripts: [MEASURED_LOCAL_DATA: not run here].
5. Task quality after compaction: [MEASURED_LOCAL_DATA: not run here].
6. Five-hour and weekly meter: no published formula. The simulated optimum is about 120,000 tokens at a 0.1x read price and about 160,000 at 0.05x (Opus 5.5 and Sonnet 5.5 list price), not a blind 25% of a 1M window.

A user turn is a message a person sends. A model request is one API call inside that turn, including tool batches. S1 is 60 user turns of 8 model requests each. Assumptions are in `outputs/summary.json`. They are not a measurement.

## Questions

1. On a long session, does an earlier auto-compaction trigger reduce cumulative prompt tokens, once summary requests are counted?
2. How much of that reduction survives prompt caching at published prices?
3. Does the same ratio show up in the five-hour and weekly meters?
4. When does lost context cost more than the smaller prompts save?
5. Is an app versus CLI (T3 Code) difference a compaction effect, a client effect, or a different mix of work?

## Hypotheses

- H1. On sessions that cross the high trigger more than once, raw prompt tokens fall as the trigger falls. In S1 the factor is 3.05, not 4.
- H2. At published cache prices the gap is smaller (2.06 in S1) because a warm prefix is a cache read.
- H3. If cache reads were free, earlier compaction would not help (S1 `h_zero` saving 0.92, meaning the low trigger costs more).
- H4. About 15 extra 8k-tail repair requests per compaction erase the API-equivalent gap in this scenario (S3). That is one chosen repair model.
- H5. A short session that never reaches 250k shows no difference (S2). Forcing a 100k window on that same short session makes the API-equivalent total worse (S2b, 0.93).
- H6. Desktop chat, the Desktop Code tab, the official CLI, and T3 Code are different clients. A lower total after switching apps is not, by itself, a compaction result.

## Mechanism

Each model request re-sends the context. The prompt-caching docs say the API would otherwise reprocess the full history on every turn. If each request adds roughly the same new input, and nothing compacts, cumulative prompt tokens grow with the square of the number of requests:

```text
N * base + growth * N * (N + 1) / 2 + output * N * (N - 1) / 2
```

`growth` is new input only. Previous output is a separate term. `closed_form_prompt_tokens` is that sum, and a test checks it against a session that never compacts. Real sessions are not this curve forever: compaction, idle compaction, and cache expiry cut it.

Compaction replaces history with a summary. The summary request can still read a warm prefix. The project-context layer is rebuilt. The system prompt can stay cached. The next requests are smaller. You can also lose a decision and spend requests putting it back.

Physical caching in this model: a warm request reads the entire previous prompt and writes the previous output plus the new tail. It does not rewrite 5% of the whole prefix on every call. A cold request, after the TTL, rewrites the prefix at that call's cache-write rate (2x on a 1-hour main conversation, 1.25x on a compaction, which is in the 5-minute bucket). A 1.0x fresh-input reading of a cold prefix would differ. The choice is labeled in `outputs/summary.json`.

## What the docs say the setting is

Checked 13 November 2026.

Two knobs. [`CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`](https://code.claude.com/docs/en/env-vars) is a percentage from 1 to 100 of the auto-compact window. It cannot raise the threshold. The docs do not publish the default percentage. The other knob is a token window from 100,000 to 1,000,000: `/autocompact`, `--autocompact`, `autoCompactWindow`, or `CLAUDE_CODE_AUTO_COMPACT_WINDOW` ([settings](https://code.claude.com/docs/en/settings-reference), [model config](https://code.claude.com/docs/en/model-config)). The environment variable outranks the flag and the setting.

Native 1M models compact at about 967K by default ([model config](https://code.claude.com/docs/en/model-config)). That is close to "95 percent or so." It is the docs' approximation, not a setting named 95. [Changelog v2.1.323](https://code.claude.com/docs/en/changelog) (12 November 2026) moved Sonnet 5's default window to the full 1M, so those sessions compact at about 967K instead of about 934K.

Setting both knobs compounds. In this model, a 250,000 window plus a 25% override fires at 62,500 and warns. S8: that arm uses 6.21 times fewer raw prompt tokens than about 967k, but only 1.43 times less API-equivalent cost, and it costs more than 25% alone (S8b, API ratio 0.70). Check `compactMetadata.preTokens` before believing a UI label. Whether production subtracts anything else after the percentage is unverified.

T3 Code's "Auto-compact after" writes the window integer. A source comment in `ClaudeAdapterV2.ts` (commit 65e4272, read 13 November 2026) says Claude Code compacts about 33,000 tokens short of the window, so a 250,000 window is reported as about 217,000. That comment is not an Anthropic spec. This model subtracts 33,000 only when you set `reserve=33000` (scenario S1b: API saving 2.15, raw 3.39). `1,000,000 - 33,000 = 967,000` matches the documented approximation. That match is an inference.

Idle compaction is on by default for an interactive session on the 1-hour cache once context is at least 200,000 and the session has been idle for about an hour ([settings](https://code.claude.com/docs/en/settings-reference), [changelog 2.1.299](https://code.claude.com/docs/en/changelog)). S4b turns the S1 API saving from 2.28 (idle off, four 75-minute breaks) down to 1.31.

[Usage limits](https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work): five-hour session limit, weekly limits, one pool shared by claude.ai, Claude Code, and Claude Desktop. No published weight for fresh tokens versus cache reads versus messages. Cache reads are cheaper on the API bill ([prompt caching](https://code.claude.com/docs/en/prompt-caching), [pricing](https://platform.claude.com/docs/en/about-claude/pricing)). `total_cost_usd` is a client-side estimate ([cost tracking](https://code.claude.com/docs/en/agent-sdk/cost-tracking)).

## What the model predicts

`python3 -m cc_usage_study.simulate --out outputs` writes `outputs/summary.json` and the figures. Headline S1, 60 user turns, 8 requests each, 4,000 new tokens and 600 output tokens per request, 1-hour TTL, no long gaps, no rework:

| | 250k (25% of 1M) | about 967k | high / low |
| --- | ---: | ---: | ---: |
| Raw prompt tokens | 72,881,600 | 222,160,000 | 3.05 |
| API-equivalent units | 13,626,420 | 28,033,460 | 2.06 |
| Compactions | 10 | 2 | |

Other rows in the same file, high trigger divided by low trigger:

| Scenario | API | Raw prompt | What it is |
| --- | ---: | ---: | --- |
| S1b T3 window 250k with the 33k comment (217k) | 2.15 | 3.39 | Same session, lower effective trigger |
| S1c marathon, 150 turns, 2k tail | 2.35 | 3.18 | Longer, smaller tails |
| S2 short bugfix, 3 turns, 10 calls | 1.00 | 1.00 | Neither trigger fires |
| S2b that bugfix at a 100k window | 0.93 | 1.35 | Early compaction costs more at API prices |
| S3, 15 rework calls of 8k per compaction | 1.03 | 1.64 | API gap is gone |
| S4b four 75-minute breaks, idle compaction on | 1.31 | 1.77 | Default idle path removes most of the gap |
| S4c 5-minute TTL, 7-minute gaps | 2.60 | 3.05 | Usage credits or an API key |
| S6 200k context, percentage 25 (50k) | 0.31 | 1.26 | Thrash. API cost goes up |
| S7 reads at 0.05x | 1.69 | 3.05 | Opus 5.5 and Sonnet 5.5 list price |
| S7 reads at 0.025x | 1.38 | 3.05 | Fable 5.1 and 5.2 list price |
| S8 window 250k and percentage 25 (62.5k) | 1.43 | 6.21 | Looks huge in raw tokens |

EOQ form used here: `T* ≈ R + sqrt(2 * K * (g + o) / r)`, with R = 46,000 and K = 70,250 at a 0.1x read. Closed-form estimates: 126,393 (0.1x), 158,880 (0.05x), 205,060 (0.025x). A 10k grid of the same session bottoms at 120,000 (2.35x vs 967k), 160,000 (1.76x), and 220,000 (1.39x). Cheaper reads mean you should compact later. 25% of 1M is 250,000, which is past these optima for the prices above.

Figures: `outputs/figures/cumulative_raw_tokens.png`, `cumulative_api_equivalent.png`, `context_sawtooth.png`, `eoq_curve.png`, `amnesia_breakeven.png`, `ttl_cliff.png`, `compounding_sawtooth.png`, `threshold_sweep.png`, `sensitivity_cache_hit.png`, `sensitivity_rework.png`, `short_session.png`.

Worked arithmetic is in `docs/examples.md`. The article draft is `docs/article-draft.md`.

## Run it

Python 3.11 or newer.

```bash
python3 -m pip install -e ".[dev]"
python3 -m pytest -q
python3 -m cc_usage_study.simulate --out outputs
python3 fixtures/synthetic/generate_fixtures.py
python3 -m cc_usage_study.analyze fixtures/synthetic/projects --split-date 2026-10-01 --sessions --output outputs/synthetic-report.json
python3 -m cc_usage_study.benchmark --task median_bug --percents 95,25
```

`make test`, `make simulate`, `make analyze-fixture`, and `make benchmark-plan` wrap the same commands. The benchmark defaults to a dry run. `--execute` calls `claude` and spends quota. Do not use it for this study's claims until you have read `benchmarks/protocol.md`. The three smoke tasks are too short to reach 250k. The long tasks are generated from a seed. See `benchmarks/generators/`.

The fixture is fake. Reports set `"synthetic": true`. A canary string in the fixture must not appear in the report.

## What to run on the machine that has the transcripts

1. Note `claude --version`, the model, and the date the setting changed.
2. Record `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`, `CLAUDE_CODE_AUTO_COMPACT_WINDOW`, and `autoCompactWindow`. If both a window and a percentage are set, they compound. Also note T3's "Auto-compact after" and its idle-thread mode (When you return, Before the cache expires, or Never).
3. If that date is older than `cleanupPeriodDays` (30 by default), CLI transcripts from the "before" period may already be gone. Desktop and Cowork transcripts are kept.

```bash
python3 -m cc_usage_study.analyze ~/.claude/projects \
  --split-date YYYY-MM-DD \
  --split-tz Europe/Lisbon \
  --sessions \
  --output outputs/local-report.json
```

Compare main-stream prompt tokens per call, the four buckets, `compaction_markers.pre_tokens`, and `by_entrypoint`. Subagent files often reuse the parent's `sessionId`. The analyzer keeps them in a separate stream. It dedupes globally on `(message.id, requestId)`, counts only `compact_boundary`, and drops `<synthetic>` and `isApiErrorMessage` lines. Prompt text is not copied. Project paths are hashed unless the path is this fixture.

4. List price is estimated per request. Haiku 5.5's long tier applies when that request's prompt is over 100,000 tokens, not when the week's sum is. Fast-mode cache prices are not estimated. If the before and after periods use different tokenizer generations (4.7 and later versus earlier), the report warns that the same text is about 30% more tokens on the newer models. That warning is the pricing page's note, not a measurement of these logs.
5. Copy five-hour and weekly percentages from Settings > Usage, or from `/usage` and a status-line `rate_limits` logger, for the same blocks. The JSONL files do not contain the meter. `five_hour_blocks` in the report are token weights under several labeled schemes, including a third-party client-side attribution (`attribution_v2_1_317`) that is not the server meter.
6. For a controlled check, dry-run the harness, then a pilot on `ledger_migration` at scale S. Full rules are in `benchmarks/protocol.md`. `claude -p` is entrypoint `sdk-cli`. A meter delta from that entrypoint is not an interactive-CLI result. The `tui` method exists for that comparison.
7. Client comparison: `benchmarks/client_comparison.md`.

## Layout

```text
src/cc_usage_study/model.py        physical-caching simulation
src/cc_usage_study/scenarios.py    S1 to S8
src/cc_usage_study/simulate.py     summary.json and figures
src/cc_usage_study/analyze.py      transcript aggregates
src/cc_usage_study/benchmark.py    dry-run harness
src/cc_usage_study/pricing.py      2026-11-13 price snapshot
benchmarks/protocol.md             controlled runs
benchmarks/client_comparison.md    desktop, CLI, T3 Code
benchmarks/generators/             long-horizon tasks
fixtures/synthetic/                fake JSONL, including edge cases
docs/examples.md                   worked examples
docs/article-draft.md              nevolin.be draft
docs/article-outline.md            outline
outputs/                           generated figures and summary
```

## Caveats

- Token sizes other than the documented about-967K trigger are assumptions.
- No meter weight is implemented, because none is published.
- The percentage override cannot delay compaction. A "95 percent" arm may be identical to the default.
- Earlier compaction can lower tokens and still make the task worse.
- Headless `--bare` is the wrong mode for a subscription-limit question.
- Company transcripts can contain source code and secrets even when the report does not. Run the analyzer only where that is allowed.

## License

MIT. See `LICENSE`.
