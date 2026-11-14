# Your Claude Code coffee break costs 10x

**Come back to a big session after a 10-minute break, resume it, and the next fix uses about 10 times what a fresh `/clear` start uses.** Measured on real Claude Opus 5.5 runs, on the 5-minute prompt cache, with about 97k tokens of history.

![Same quick bugfix on a 97k-token session: fresh start 1x, resume after 3 minutes 1.5x, /compact then a break 2.5x, resume after a 10-minute break 10x, /compact after the break 10x](outputs/figures/hero_coffee_break.png)

## The rules

Each rule says how strong its evidence is: **measured** (live runs, with n), **(modeled), validated** (the simulator, back-tested against the live runs), or **(modeled) only** (not yet confirmed by a measurement).

| Situation | Do this | Evidence |
| --- | --- | --- |
| Leaving for a break | [`/compact`](https://code.claude.com/docs/en/commands) first. The next fix used 2.5x a fresh start, compact call included, not 10x. | Measured, n=3 |
| Back after a break | [`/clear`](https://code.claude.com/docs/en/commands) and paste a 3-line handoff. Resuming used about 10x, and so did `/compact` after the break. | Measured, n=3 and n=2 |
| Quick related fixes back to back | Stay in the session. It used 28% less than `/clear` between each, while history stayed small (about 20k). | Measured, n=1 |
| Scheduled `claude -p` jobs | Fresh session every run, state in a file. `--resume` costs about 3x. | (modeled), validated; the real penalty is likely larger (see [validation](#does-the-model-hold-up)) |
| Long session headed past 300k tokens | `claude --autocompact 180000`. | (modeled) only. Live, it tied the default on a 270k session; the model's "43% less" is not confirmed |
| Choosing a client | The same fix used 1.19x in T3 Code and 1.80x in Claude Desktop's Code tab, against the plain CLI. | Measured, n=1 per client |

### Does this apply to you?

The 10x depends on how long your [prompt cache](https://code.claude.com/docs/en/prompt-caching) lives and how cheap cache reads are on your model.

| Your setup | Cache lifetime | What a "break" is | What to expect |
| --- | --- | --- | --- |
| API key or usage credits | 5 minutes | Anything over 5 minutes | The measured 10x (breaks of 10 to 13 minutes; a break just past 5 minutes was not measured) |
| Subscription within its [usage limits](https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-max-or-ultra-plan) | 1 hour | Over an hour | No cliff for short breaks. After an hour, carried history is rewritten at 2x instead of 1.25x: about 11x for the same fix (modeled with the calibrated task shape; not measured) |
| Model with 0.1x reads (Sonnet 5, Opus 4.7) | either | as above | Stale resume 9.0 times |
| Model with 0.025x reads (Fable, Mythos) | either | as above | Stale resume 10.4 times |

A subscription's five-hour and weekly meter uses weights Anthropic does not publish, so it can rank two habits differently from the list-price units used here.

## Copy-paste

```bash
# Cron and CI: start fresh every run, keep state in a file
claude -p "$(cat .agent/handoff.md) Continue the nightly triage." --output-format json
```

```text
# Before a break
/compact Keep: the open issue, files touched, test command

# Back after a break, new task
/clear
Goal: fix the currency rounding in invoices.
Done: tests in tests/test_invoice.py reproduce it.
Next: patch money.py, run the suite.
```

**Contents:** [how we measured](#how-we-measured) · [the coffee-break test](#1-the-coffee-break-tax) · [`/compact`](#compact-before-or-after-the-break) · [does the model hold up](#does-the-model-hold-up) · [compaction](#2-compaction-measured-live) · [clients](#4-three-clients-one-bugfix) · [limits](#honest-limits) · [reproduce](#reproduce)

---

## How we measured

| Experiment | Runs | Conditions | Data |
| --- | --- | --- | --- |
| Stale vs warm resume vs fresh start | 3 stale and fresh pairs, 1 warm | Opus 5.5, 5-minute cache, 97k history, breaks of 3.3 and 10 to 13 minutes, 3 small fixes | `ttl_runs` |
| `/compact` before or after a break | 3 before, 2 after | Same sessions and fixes as above | `compact_runs` |
| Warm session vs `/clear` between fixes | 1 per arm | Same 3 fixes, history under 21k | `clear_vs_warm` |
| Compaction trigger settings | 1 per arm, 5 arms | 24-module migration over 4 phases, about 270k peak, no long pauses | `compaction` |
| Three clients, one bugfix | 1 per client | Identical 3-line `CLAUDE.md`, no user MCP servers | `client_comparison` |

Everything is in `outputs/live_experiments.json` (numbers only, no prompts or paths). Modeled numbers come from the caching simulator in `src/cc_usage_study/` and are in `outputs/summary.json`. No live API calls are needed to check any of it.

> **Units.** "Usage" is **cache-weighted usage**: tokens weighted the way the API bills them, on Opus 5.5 `1 / 1.25 / 2 / 0.05 / 5` (input / 5-minute cache write / 1-hour cache write / cache read / output). Raw token counts mislead: a warm and a stale resume below moved the same 294k tokens and differed 6.5x in usage.

### 1. The coffee-break tax

Claude Code [caches the prompt prefix](https://code.claude.com/docs/en/prompt-caching) ([API caching docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). Inside the cache window a resumed session re-reads its history at the cheap read weight. After the window, every old token is **written to the cache again** at full write weight.

We built a session to about 97k tokens, walked away, and resumed it for a quick bugfix. Each time we also ran the same fix in a brand-new session. Three different fixes, all passed their tests.

| Same quick fix | Fresh (`/clear`) | Resumed after 3.3 min | Resumed after 10 to 13 min |
| --- | ---: | ---: | ---: |
| Usage, fresh = 1 | **1** | **1.5x** | **9.8x to 9.9x** (3 of 3) |
| Across models' read weights | 1 | 1.0x to 2.3x | 9.0x to 10.5x |
| First call: tokens written to cache | about 5,500 | 743 | 86,178 to 88,229 |
| Raw tokens for the whole fix | about 52,000 | 293,834 | 294,158 to 301,452 |

- Even warm, 97k of history was too much to drag into an unrelated small fix.
- The multiple is against a small fix. A bigger next task would shrink it, because the fresh session's own work would weigh more. The extra cost per carried token (about 1.4 units) does not shrink.

#### `/compact` before or after the break

Headless it is `claude -p "/compact Keep: the 3 issues found, files touched, test command" --resume SESSION`; the transcript gets a `compact_boundary` with `trigger: manual`.

| Same quick fix, fresh `/clear` = 1 | `/compact` call | Next fix | Total |
| --- | ---: | ---: | ---: |
| `/compact` right away (cache warm), then a 6.5 to 11 min break, n=3 | 1.1 to 1.2 | 1.3 | **2.5x** (2.49 to 2.53) |
| Break first (cache cold), then `/compact`, n=2 | 8.0 to 8.8 | 1.2 to 1.5 | **9.3x and 10.2x** |
| Plain resume after the break | none | 9.8 to 9.9 | **9.8x to 9.9x** |

- **Before the break it is cheap:** the summary call reads the 96k history from the warm cache.
- **After the break it saves nothing on the next fix:** the summary call sends the whole cold history again, as 87k to 90k uncached input tokens. It only pays on later turns.
- The compact call is not in the transcript as an assistant row; its tokens come from Claude Code's `cost_state`.

![One carried history token after the cache expires costs 12.5 to 40 times a warm read](outputs/figures/ttl_price_of_a_pause.png)

![Next-task usage against carried history: hot resume, stale resume, and a fresh session](outputs/figures/resume_crossover.png)

![Twelve hourly claude -p runs: --resume every run against a fresh session every run](outputs/figures/cron_resume_vs_fresh.png)

Scheduled jobs are the worst case (modeled): twelve `claude -p` runs spaced past the cache window, where every `--resume` rebuilds the whole growing history cold. Fresh runs use 33% of that on the 1-hour cache and 35% on the 5-minute cache.

A stale resume loses to a fresh start once history passes **about 10k tokens**, even if the fresh session needs 2 extra exploration calls (model, validated: 10.8k with the measured task shape). The diamonds are the live runs. Note the curve's percentages use the published constants, which understate the penalty; see below.

### Does the model hold up?

The modeled rules (scheduled jobs, pause traps, sweet spots) rest on the simulator. `python -m cc_usage_study.validate` scores it against every live run, with no API calls. Two predictions per run: the **published** constants that produced the modeled numbers in this README, and a **calibrated** model whose task shape is read from the fresh `/clear` runs only (calls per task, the cached prefix shared across sessions, tokens per call).

![The caching model against the live runs: published constants understate every ratio, the calibrated model lands on the measurements](outputs/figures/model_vs_measured.png)

| Same quick fix, fresh = 1 | Measured | Model, published | Model, calibrated |
| --- | ---: | ---: | ---: |
| Warm resume | 1.51 | 1.07 | 1.47 |
| Stale resume | 9.87 | 3.53 | 9.80 |
| `/compact` before the break | 2.49 | 1.84 | 2.60 |
| `/compact` after the break | 9.64 | 3.96 | 10.24 |
| Runs within a quarter of the measured usage | | 0 of 10 | 10 of 10 |

- **The caching arithmetic holds.** With a realistic task shape the model lands within 10% on every short-task run. The fresh baseline is fitted, and the `/compact` rows use each run's measured post-compact prompt size; the stale and warm rows are predictions.
- **The published constants were wrong in one place that matters.** They assume a fresh session writes its whole system prompt to the cache. Live, about 11.4k tokens of it was already cached by earlier sessions, so a fresh start cost 4.5x less than modeled. Every modeled "resume vs fresh" ratio is therefore **too small**: at 60k of history a stale resume costs +189% against `/clear` plus 2 exploration calls, not the published +102%. The break-even history barely moves (10.1k to 10.8k).
- **`/compact` after a break** sends the cold history as plain input (1x), not as a cache write (1.25x) as the simulator assumes.
- **Long-session compaction is not validated.** Replaying the migration task, the model gets the sign right for 3 of 4 trigger settings but not the size: it predicts about -1% for `--autocompact 140000` where the live run measured +18.1%. It prices a summary call at 21.6k units against a measured 36.6k (1.7x), and it misses how often compaction fires (3 against 4 at 140k, 10 to 14 against 6 on the stacked setting), partly because live, one large tool result can carry the context up to 56% past the trigger before the check runs. Treat the sweet-spot section below as a hypothesis.

Full scores per run: `outputs/validation.json`.

### 2. Compaction, measured live

![Usage of five live compaction settings, visible vs hidden, and where each one actually fired](outputs/figures/live_compaction_arms.png)

Same task in every arm: a 24-module migration over 4 `--resume` phases. Opus 5.5, effort medium, 5-minute cache, no long pauses, n=1 per arm. Green is what the transcript shows; red is summary traffic that only appears in Claude Code's own `cost_state` total.

- **Fewer tokens is not less usage.** The 100k arm sent 63% fewer raw tokens (2.27M against 6.12M) and used 25.2% more. Most of the default's tokens were cheap cache reads.
- **Part of the cost is invisible.** Summary calls were 11.8% to 25.5% of each compacting arm, about 36.6k units per compaction on average.
- **The ranking depends on read weight.** At 0.1x reads the 180k arm comes out 13.6% under the default. The tie also assumes no pauses.
- Quality held everywhere: all 5 arms passed the tests and the functional checker and reported 12 of 12 planted anomalies.

<details>
<summary>Per-arm table</summary>

| Arm | Fires at | Compactions | Usage, default = 100 | Hidden share | Raw tokens vs default | At 0.1x reads | At 0.025x reads | 15% `preTokens` gate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Default 1M | 967,000 | 0 | **100** | 0% | 6.12M tokens | 100 | 100 | no fire |
| `--autocompact 180000` | 147,000 | 2 | **100 (tie, +0.1%)** | 11.8% | -48% | -13.6% | +11.6% | fails (+15.7%) |
| 250,000 window + override 25 | 57,500 | 6 | **+13.6%** | 24.3% | -70% | -10.2% | +33.5% | fails (+55.5%) |
| `--autocompact 140000` | 107,000 | 4 | **+18.1%** | 13.9% | -42% | +1.0% | +32.5% | passes |
| `--autocompact 100000` | 67,000 | 7 | **+25.2%** | 25.5% | -63% | +0.1% | +46.2% | passes |

The ordering is not monotonic at n=1 (57,500 beat 107,000 and 67,000), so "a lower trigger pays past a break-even length" is a model claim.

</details>

### 3. `/clear` vs a warm session

![Three /clear sessions vs one warm session, and the raw tokens behind each](outputs/figures/live_clear_vs_warm.png)

Three quick fixes back to back, n=1. Three fresh sessions against one session resumed inside the cache window. The warm session read 7.0% more raw tokens but rebuilt 53% fewer, so it used **27.7% less** (21.1% to 32.0% by model). Context peaked at 20,265 tokens: this only holds while history is small.

### 4. Three clients, one bugfix

![First request size and bugfix usage across Claude CLI, T3 Code, and Claude Desktop](outputs/figures/live_client_comparison.png)

Same Opus 5.5, effort medium, identical 3-line `CLAUDE.md`, no user MCP servers, n=1 per client. All three fixed all 3 planted bugs.

| | CLI interactive | T3 Code | Desktop Code tab |
| --- | ---: | ---: | ---: |
| First request, tokens | 19,528 | 22,842 | 39,749 |
| Inline / deferred tools | 14 / 15 | 15 / 82 | 35 / 82 |
| Fix usage vs CLI | 1.00x | **1.19x** (1.17x to 1.20x by model) | **1.80x** (1.71x to 1.92x by model) |

T3 Code runs Claude Code through the Agent SDK and adds 67 deferred tools plus a longer system prompt. Desktop's Code tab carries a whole app (35 inline tools, 3.5 times the system prompt) and writes no `cost_state` row, so its number is a floor. Full table: `benchmarks/client_comparison.md`.

### 5. Where compaction actually fires

Read from the Claude Code v2.1.325 binary. The number you type is not where it fires: **subtract 33,000, and never stack two knobs.** The formula matched the repo's predicted threshold on all 5 live arms; the first live compaction fired 0.3% to 55.5% past it, because the check runs between requests.

```text
effective_window = window - min(maxOutputTokens, 20000)
threshold        = effective_window - 13000                                    # no override
threshold        = min(floor(effective_window * pct / 100), effective_window - 13000)   # with CLAUDE_AUTOCOMPACT_PCT_OVERRIDE
```

| You set | It fires at |
| --- | ---: |
| nothing, 1M window | 967,000 |
| nothing, 200k model | 167,000 |
| `--autocompact 180000` | 147,000 |
| `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=18`, 1M window | 176,400 |
| `--autocompact 100000` | 67,000 |
| 250,000-token window and `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=25` | 57,500 |

**Idle compaction** summarizes a big session at about 90% of the cache window, before it expires. It only runs when all of these hold: `idleCompaction` is on (default since v2.1.299), the session is on the **1-hour** cache (never on API keys), the **process is still alive** (not a sleeping laptop or a finished `claude -p`), and context is at least `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS` (**200,000** by default, 100,000 at the lowest).

### 6. Sweet spots by workflow (model only)

The compaction model failed its back-test on size (see [validation](#does-the-model-hold-up)), so read these as hypotheses to test on your own sessions, not as savings.

![Usage change by workflow: best fire points in green, settings forced too low in red, pause traps hatched](outputs/figures/scenario_comparison.png)

<details>
<summary>Workflow and pause-trap tables (modeled, published constants)</summary>

| Workflow | Best fire point | You type | Usage vs that workflow's default |
| --- | ---: | ---: | ---: |
| Deep feature work, 0.1x reads (Sonnet 5, Opus 4.7) | 120k | 153,000 | -57% |
| Deep feature work, Opus 5.5 / Sonnet 5.5 | 160k | 193,000 | -43% |
| Marathon refactor, 150 turns, smaller steps | 130k | 163,000 | -54% |
| Heavy file reads, big working memory | 280k | 313,000 | -24% |
| Rules that live only in the chat, 8 repairs per compact | 360k | 393,000 | -24.9% |
| Workday with long breaks, idle compaction ran | 140k | 173,000 | -18% |
| Quick bugfix | no trigger is reached | leave it | 0%, or +8% if forced to fire at 100k |
| 200k-context model | leave the 167,000 default | leave it | +239% with override 25 (fires at 45,000 and thrashes) |

| Trap | Against | Usage |
| --- | --- | ---: |
| Lunch breaks, laptop slept, idle compaction lapsed | the same day with idle compaction running | +76% |
| API key, 7-minute gaps past the 5-minute cache | the same session answered within 2 minutes | +213% |
| Stale `--resume` with 60k of history | `/clear` plus a handoff, 2 extra exploration calls | +102% (+189% calibrated) |
| Hourly `claude -p` with `--resume` | a fresh session every run | +201% |

</details>

![Usage against the fire point for three read weights, with the 150k-200k band marked](outputs/figures/eoq_curve.png)

![Prompt size through a 60-turn session at the default and at a 160k fire point](outputs/figures/context_sawtooth.png)

## Honest limits

- **Small n.** Most conditions ran once; the coffee-break and `/compact` tests ran 2 to 3 times. Ranges are reported, not confidence intervals.
- **One model, small tasks, one cache lifetime.** All live runs used Opus 5.5 on the 5-minute cache. 1-hour cache numbers are modeled.
- **Ratios against a small fix inflate.** The 10x is against a 3-call fix; the extra cost per carried token is the portable number.
- **The meter is not the bill.** Units are API list price. Subscription limits use unpublished weights.
- **The compaction model is unvalidated on size.** See [validation](#does-the-model-hold-up).
- **Two of five compaction arms fail the repo's own validity gate.**

More on method and open questions: [FINDINGS.md](FINDINGS.md).

## Reproduce

```bash
python3 -m pip install -e ".[dev]"
python3 -m cc_usage_study.pipeline        # analyze, check, simulate, figures, validate, verify; one command
python3 -m pytest -q
```

Run the analyzer on your own transcripts, keeping the output outside git:

```bash
python3 -m cc_usage_study.pipeline --projects ~/.claude/projects --split-date YYYY-MM-DD --out ./local-out
```

The analyzer copies aggregates only, never prompt text. Read `preTokens` on your first `compact_boundary`: if it does not match the table in section 5, your setting is not the one you think.

<details>
<summary>Setup and what's inside</summary>

- Clients: Claude CLI v2.1.325, T3 Code v0.1.9 (Agent SDK wrapping Claude Code v2.1.325), Claude Desktop v5.1231.2 (Code tab, bundled Claude Code v2.1.321).
- Model `claude-opus-5-5`, effort `medium`, 1M context, `maxOutputTokens` 128,000.
- Cache-weighted units are the logged list-price cost divided by the Opus 5.5 input price. Pricing snapshot 13 November 2026, [pricing page](https://platform.claude.com/docs/en/about-claude/pricing).
- "Hidden" usage is `cost_state.totalCostUSD` minus the deduplicated assistant rows in the same transcript.

| | |
| --- | --- |
| `outputs/live_experiments.json` | All live runs, sanitized |
| `outputs/summary.json` | Every modeled number |
| `outputs/validation.json` | The model scored against every live run |
| `src/cc_usage_study/` | Extractor, caching model, simulator, validation, charts |
| `benchmarks/protocol.md` | How the live runs were set up, and the validity gate |
| `benchmarks/client_comparison.md` | The full client table |
| `docs/reference.md` | Knobs, model windows, caching, the traps |
| `docs/examples.md` | The arithmetic behind a single session |
| `docs/article-draft.md` | The long-form essay |

</details>

### Anthropic docs

- [Prompt caching in Claude Code](https://code.claude.com/docs/en/prompt-caching): which requests get the 5-minute or 1-hour TTL, and how to choose it
- [Prompt caching on the API](https://platform.claude.com/docs/en/build-with-claude/prompt-caching): cache lifetime, 1-hour cache, write and read multipliers ([pricing](https://platform.claude.com/docs/en/about-claude/pricing))
- [Manage costs in Claude Code](https://code.claude.com/docs/en/costs) and [context window](https://code.claude.com/docs/en/context-window)
- [Commands](https://code.claude.com/docs/en/commands): `/compact`, `/clear`, `/usage`
- [Claude Code with a Pro, Max or Ultra plan](https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-max-or-ultra-plan) (five-hour and weekly limits) and [usage limit best practices](https://support.claude.com/en/articles/9797557-usage-limit-best-practices)
- [API rate limits](https://platform.claude.com/docs/en/api/rate-limits)

## Credits

Co-produced by Grok 4.7 and Claude Fable 5.2: AI-assisted research and writing, with Fable 5.2 reviewing and critiquing the analysis. Live experiments ran on Claude Opus 5.5.

## License

MIT. See `LICENSE`.
