# Review of the method: what holds, what to qualify, what changed

This is a critical read of the study and its analyzer, plus the fixes that came with it. It contains no data from any real user: only method, reasoning and the checks you can run on your own transcripts.

## Where the study is right

- **Cache-weighted usage beats raw tokens.** A cache read is billed at 0.025x to 0.1x of input, depending on the model, a write at 1.25x (5 minutes) or 2x (1 hour). Two runs that move the same tokens can differ several times in cost. This is the study's strongest point.
- **The analyzer's accounting is sound.** It dedupes streamed chunks on `(message.id, requestId)` and keeps the most complete record, includes subagent and sidechain files, prices the 5-minute and 1-hour write split separately, refuses to price an unknown model or a missing TTL split, and takes Haiku's long-prompt tier per call. The price table was re-checked against the current rate card for the Claude 5 family, Claude 4.x and Haiku 4.5 and matches.
- **Compaction is not free.** The study measures the summary call, which only shows up in Claude Code's own cost line, and the rewrite of the cache after each compaction.

## Where it overreaches or needs a qualifier

1. **The headline multiple is conditional on a 5-minute cache.** The 10x "coffee break" figure came from runs on the 5-minute TTL with 10 to 13 minute breaks. Clients that use the 1-hour TTL show no such cliff until the break passes an hour, and then the write is 2x, not 1.25x. The README says this in one sentence; it belongs next to the headline. Check your own TTL: the analyzer reports `cache_creation_5m_tokens` and `cache_creation_1h_tokens` per period.
2. **A multiple against a tiny baseline inflates.** The fresh-start comparison is a small bugfix. The ratio shrinks as the next task grows, while the absolute extra cost per carried token does not. Report the absolute units next to every ratio.
3. **Sample size.** Most conditions ran once, a few two or three times, on one model and small tasks. The non-monotonic compaction ranking in the README is itself a sign of noise. Treat "lower trigger loses" as a hypothesis the model supports, not a measurement.
4. **Rules are written as facts, some are modeled.** "About 43% less" and "~3x" come from the simulator. `cc_usage_study.validate` now scores the simulator against every live run: its caching arithmetic holds once the task shape is realistic (10 of 10 short-task runs within a quarter, most within 5%), but the published constants overstate a fresh start about 4.5x, because they ignore the system prompt already cached by earlier sessions, so every modeled resume-vs-fresh ratio is too small. The long-session compaction model gets the sign right for 3 of 4 settings but not the size, and prices a summary call at 0.59x the measured cost.
5. **The meter is not the bill.** Units are API list price. Subscription limits use weights Anthropic does not publish, so a subscription user can rank two settings differently.
6. **Observational before/after claims.** The analyzer's own headline was "prompt tokens per call, after versus before", which is the raw-token trap the study warns against. A model switch, a client switch or a TTL change in the same window moves the number with no link to the setting under test. Fixed below.
7. **Windows and timezones.** `by_day` groups on UTC while `--split-tz` applies to the period split only. Equal-length windows matter, and old transcripts may already be pruned by Claude Code's retention setting, which biases a long "before" window toward whatever survived.

## Compaction trigger: why two analyses can disagree

A common shortcut treats average context size per turn as the cost proxy and concludes that compacting at a low fraction of the window cuts input several times over. The study finds a different answer. Both are partly right:

- The shortcut is right that long contexts are re-read every turn and that a smaller context is cheaper per turn.
- It ignores the summary call, the cache rewrite after each compaction, and the read weight. Cache reads are the cheap part, so most of the "saved" tokens were cheap to begin with. Whether an earlier trigger wins depends on the model's read weight (0.025x, 0.05x or 0.1x) and on pauses. At cheap read weights the study finds a tie or a loss on a mid-length session; at 0.1x some trigger settings gain and others tie.
- The study is right on the sign of the effect and the read-weight dependence. It is weaker on size (n=1 per arm, no pauses).

Use the study's reading: moving the trigger from near-full to a mid-range value is plausible and low-risk, going much lower is not supported, and either claim needs your own measurement.

## What changed in this branch

- `analyze`: the before/after block now carries `weighted_main` (list-price cost per priced call and per session, with the priced share reported and unpriced calls counted; cost is missing, never zero, when nothing could be priced) and `ttl_expired_rebuilds_main`. The scorecard labels the raw token delta as raw and prints the cost delta beside it, or says why not when fewer than 95% of calls on either side could be priced. The comparison note names the confounders.
- `checks.py`: reconciliation checks that compare totals derived by different routes (per model, day, entrypoint, version, role, session, record count minus duplicates, period coverage) and warnings for data-quality limits (cache-write split mismatch, output placeholders, mixed tokenizers, unpriced or partly priced models). A failure is a grouping or bookkeeping bug, not a finding. The checks re-sum the same deduplicated calls, so a wrong dedup key needs the dedup tests, not these checks.
- `pipeline.py`: one command runs analyze, checks, simulate, figures, and verifies that the regenerated model summary equals the committed `outputs/summary.json` (the CSVs and figures are regenerated, not compared).
- Tests for the checks (an injected grouping bug and a corrupted record count), the cost block, the scorecard wording, and the pipeline.

## Run it on your own data

```bash
python3 -m pip install -e ".[dev]"
python3 -m cc_usage_study.pipeline                                        # synthetic fixtures
python3 -m cc_usage_study.pipeline --projects ~/.claude/projects --split-date YYYY-MM-DD --out ./local-out
```

Keep `--out` outside git when the input is real. Compare equal-length windows, split by model, entrypoint and TTL, and read `weighted_main` before the raw token line.

## Still open

- Reconcile the analyzer's USD estimate with a billing source for the same window. The checks cannot prove the total matches an invoice.
- Recalibrate the simulator's task constants (shared cached prefix, calls per task) and re-derive the modeled tables; `outputs/validation.json` shows the direction and size of the change.
- Fix the compaction model's summary-call cost and its between-request trigger check, then re-score it against the live arms.
- Replay real pause patterns through the simulator and compare its rebuild count with `cache_rebuilds_main`.
- Repeat the headline runs on the 1-hour TTL and with larger next tasks, with n of at least 5.
