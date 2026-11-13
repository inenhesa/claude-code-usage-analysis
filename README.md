# Claude Code Compaction Is Not Free, and Your Client Changes the Bill

**14 live Claude Opus 5.5 runs. $18.57 of real API spend. Every number below is read from the raw session logs.**

The usual advice is "compact early, it saves tokens" and "every Claude Code frontend is the same Claude Code." Both are wrong in ways you can measure.

1. **Every auto-compaction sends a summary request that you never see in the transcript.** In 5 live 4-phase builds, those hidden calls cost **$0.08-$0.34 each**. On a 100k window they added **$1.01, or 25.5% of the whole bill**.
2. **The number you type is not where compaction fires.** Claude Code v2.1.325 subtracts a 20,000-token output reserve and a 13,000-token buffer, so `--autocompact 180000` fires at **147,000**. Stack a window and a percentage and they multiply: (250,000 - 20,000) x 0.25 = **57,500**, which is where the live session fired (57,993 and 57,044).
3. **A lower trigger only pays past a break-even session length.** On a build that peaked near 270k tokens, 180k tied the default (**$3.17 vs $3.17**), 140k cost **+18%**, 100k cost **+25.2%**, and the stacked knobs cost **+14%**.
4. **For quick back-to-back fixes, keep the session warm.** One `--resume` session beat three `/clear` sessions by **27.7%** ($0.1089 vs $0.1507). Opus 5.5 cache reads cost $0.20 per million tokens. Rebuilding the prefix costs $5.00.
5. **Claude CLI, T3 Code, and Claude Desktop are not interchangeable.** T3 Code wraps the same Claude Code v2.1.325, yet its cold start is **+17%** larger and the same bugfix cost **+19%** more. Claude Desktop's Code tab starts at **2.04 times** the CLI prefix and cost **1.80x** on the identical fix.

The long-session answer still stands: **for deep 1M work that would otherwise run to 300k-1M+ tokens, set one knob in the 150k-200k band**. That part comes from the caching model below, and the live runs show where it stops paying.

## Part 1. Compaction, measured live

![Visible vs hidden cost of five live compaction settings, and where each one actually fired](outputs/figures/live_compaction_arms.png)

Green is what the transcript shows. Red is the compaction-summary traffic that only appears in Claude Code's own `cost_state` total. The right panel puts every observed fire (red dots) next to the threshold predicted from the v2.1.325 binary (green line) and the number that was typed (dashed red).

Same task in every arm: **LedgerKit v3.2**, a 24-module migration split over 4 `--resume` phases, with the invariants only in a long architecture doc. Claude Opus 5.5, effort medium, 5-minute API cache.

| Arm | Fires at | Compactions | Visible | Hidden | Total | vs default |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Default 1M | 967,000 | 0 | $3.17 | $0.00 | **$3.17** | baseline |
| `--autocompact 180k` | 147,000 | 2 | $2.80 | $0.37 | **$3.17** | +0.1% |
| 250,000-token window + 0.25 override | 57,500 | 6 | $2.73 | $0.87 | **$3.60** | +14% |
| `--autocompact 140k` | 107,000 | 4 | $3.22 | $0.52 | **$3.74** | +18% |
| `--autocompact 100k` | 67,000 | 7 | $2.96 | $1.01 | **$3.97** | +25.2% |

Quality held everywhere. All 5 arms passed the visible tests and the functional checker, and all 5 reported **12/12** planted `CHECK_ANOMALY` rows in phases 1-3. The 180k and 100k arms show 4 `INV-02` hits only because they routed the timestamp through `clock.now()`, which the checker's AST rule also matches. None of the 5 calls `datetime.now()` in a module.

### The trigger math, from the binary

Read from Claude Code v2.1.325, functions `tle` and `WX`:

```text
effective_window = window - min(maxOutputTokens, 20000)
threshold        = effective_window - 13000                                    # no override
threshold        = min(floor(effective_window * pct / 100), effective_window - 13000)   # with CLAUDE_AUTOCOMPACT_PCT_OVERRIDE
```

| You set | It fires at |
| --- | ---: |
| nothing, 1M window | 967,000 |
| `--autocompact 180000` | 147,000 |
| `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=18`, 1M window | 176,400 |
| `--autocompact 140000` | 107,000 |
| `--autocompact 100000` | 67,000 |
| 250,000-token window and `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=25` | 57,500 |

`preTokens` on the `compact_boundary` row is logged by a different counter from the trigger check, so a fire can land a few thousand tokens under the line, or overshoot it after a large tool batch.

### `/clear` vs a warm session

![Three /clear sessions vs one warm session, and where the dollars go](outputs/figures/live_clear_vs_warm.png)

Three quick fixes back to back (`median_bug`, `slice_bug`, `keep_currency`). Red is three fresh sessions, the same as `/clear` between tasks. Green is one session resumed three times. Both arms pass every test.

| | 3 sessions with `/clear` | 1 warm session |
| --- | ---: | ---: |
| Total | $0.1507 | **$0.1089 (-27.7%)** |
| Cache-write tokens | 18,853 | 8,884 |
| Cache-read tokens | 137,507 | 158,381 |

Each fresh session rebuilds the same prefix at $5.00 per million tokens. The warm session re-reads it at $0.20. `/clear` is still right between unrelated work that is minutes apart. It is the wrong reflex between quick related fixes.

## Part 2. Three clients, one model, one bugfix

![Cold-start prefix and bugfix cost across Claude CLI, T3 Code, and Claude Desktop](outputs/figures/live_client_comparison.png)

Green is the official CLI. Light red is T3 Code. Deep red is Claude Desktop's Code tab. Same Opus 5.5, effort medium, identical 3-line `CLAUDE.md`, no user MCP servers.

| | CLI `-p` | CLI interactive | T3 Code | Desktop Code tab |
| --- | ---: | ---: | ---: | ---: |
| Cold first request, tokens | 16,850 | 19,528 | 22,842 (+17%) | 39,749 (2.04 times) |
| System prompt, characters | 7,227 | 7,485 | 12,857 | 25,865 |
| Inline tools | 13 | 14 | 15 | 35 |
| Deferred tools | 13 | 15 | 82 | 82 |
| Skills | 13 | 13 | 13 | 22 |
| `noisy_bughunt` fix, API list price | | $0.1145 main, $0.1247 with side calls | $0.1362 (+19%) | $0.2057 (1.80x) |
| Calls / cache-read tokens | | 6 / 130k | 6 / 139k | 8 / 318k |

All three clients fixed all 3 planted bugs, passed the visible and hidden tests, and left the decoy `relayd/metrics.py` untouched.

**T3 Code is not "just the CLI."** It runs Claude Code v2.1.325 through the Agent SDK, then adds 67 `mcp__t3-code__*` deferred tools, an inline `mcp__t3-code__request_secret` tool, and 5,372 more system-prompt characters, including `<runtime_info>` and `<pull_request_linking>` blocks. Against the CLI's total with its background calls, the fix still cost 9% more.

**Desktop's Code tab carries a whole app.** It bundles Claude Code v2.1.321 and sends 35 inline tools instead of 14, including 13 `Claude_Browser` preview tools, 6 `ccd_session` and `ccd_turn` tools, and a `terminal` reader. Add 69 MCP deferred tools (59 `mcp__ccd_*`, 6 scheduled-task, 4 terminal), 9 more skills, and 3.5 times the system prompt. Desktop writes no `cost_state` row, so its dollars are computed from the assistant rows and are a floor.

## The model: where the sweet spot sits by workflow

The live runs used a 5-minute API cache on a ~270k session. A subscription session uses a 1-hour cache, and real deep work often runs far longer. The caching simulator in `src/cc_usage_study/` sweeps the trigger for 8 workflows on a 1-hour cache.

![Cost change versus the default for eight developer workflows](outputs/figures/scenario_comparison.png)

Green is a sweet spot that beats the default. Red is a setting forced too low on a short task or a 200k model.

| Workflow | Sweet spot | Cost vs default | What to do |
| --- | --- | --- | --- |
| Deep feature work, standard reads (Sonnet 5, Opus 4.7) | **120k** | **-57%** | One knob in the band. $56.07 becomes $23.90. |
| Deep feature work, Opus 5.5 / Sonnet 5.5 | **160k** | **-43%** | Same band. $68.16 becomes $38.64. |
| Marathon refactor, smaller steps | **130k** | **-54%** | A little earlier when each step is small. |
| Heavy file reads, big working memory | **280k** | **-24%** | Do not copy a 120k trigger onto a huge working set. |
| Rules that live only in the chat | **360k** | **-24.9%** | Put them in root `CLAUDE.md` before you lower the trigger. |
| Workday with long breaks, idle compaction on | **140k** | **-18%** | Idle compaction already summarizes a warm cache. |
| Quick bugfix | **180k changes nothing** | **0%**, or **+8%** if forced to 100k | Keep related fixes warm. `/clear` between unrelated ones. |
| 200k-context model | **120k is only 6% under the default** | **+227%** if forced to 50k | Leave the default. A low trigger thrashes. |

List-price dollars for one 60-turn deep build, published input price times simulated units, not the five-hour meter:

| Model | Reads | Default, about 967k | Sweet spot |
| --- | --- | ---: | ---: |
| Sonnet 5, $2 / million input | 0.1x | $56.07 | $23.90 at 120k (**-57%**) |
| Opus 5.5, $4 / million input | 0.05x | $68.16 | $38.64 at 160k (**-43%**) |

![Three read-price curves, with the 150k-200k band marked](outputs/figures/eoq_curve.png)

100% is the ~967k default. The green band is the practical setting for a 1M model, and the red band below 100k is where it thrashes. Red is standard 0.1x reads, valley at 120k (**-57%**). Solid green is Opus 5.5 / Sonnet 5.5 at 0.05x, valley at 160k (**-43%**). Dashed green is the cheapest 0.025x reads, valley near 220k (**-28%**). The Opus 5.5 valley is flat: anywhere from 140k to 200k lands within one point of the minimum.

![Prompt size through a 60-turn session at the default and at 160k](outputs/figures/context_sawtooth.png)

Red is the default, green is the 160k sweet spot. The lower trigger cuts raw prompt tokens by about **77%** but estimated API cost by only about **43%**, because most of the tall red curve was a discounted re-read.

The model and the live runs agree on the mechanism. A summary has a payback period. The live builds peaked near 270k, short of the long sessions where the model finds its big savings, and they paid a hidden summary bill on top. Short sessions lose money with a low trigger. Long ones win.

## Copy-paste

For deep 1M work that would run past 300k tokens, pick **one**:

```bash
claude --autocompact 180000                 # fires at 147,000
CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=18 claude   # fires at 176,400 on a 1M window
```

Never both. For sessions that end under ~270k, leave the default. For quick related fixes, stay in one session.

Check your own logs, aggregates only, prompt text is never copied:

```bash
python3 -m pip install -e ".[dev]"
python3 -m cc_usage_study.analyze ~/.claude/projects --split-date YYYY-MM-DD --sessions --output outputs/local-report.json
python3 -m cc_usage_study.simulate --out outputs
python3 -m cc_usage_study.experiments figures --out outputs
python3 -m pytest -q
```

Read `preTokens` on your first `compact_boundary`. If it does not match the table above, your setting is not the one you think.

## Measured vs modeled

| Source | Cache | What it is |
| --- | --- | --- |
| Live runs, `outputs/live_experiments.json` | 5-minute API cache | 14 real Opus 5.5 sessions on a dedicated API key, 13 November 2026. Dollars are API list price. |
| Simulator, `outputs/summary.json` | 1-hour subscription cache | A caching model of 8 workflows. Not a measurement of any account. |
| Five-hour and weekly meter | n/a | Not measured. Anthropic does not publish the weights. Read it in Settings > Usage. |

## Footnotes

- Clients: Claude CLI v2.1.325 (`claude -p` and interactive), T3 Code v0.1.9 (Agent SDK wrapping Claude Code v2.1.325), Claude Desktop v5.1231.2 (Code tab, bundled Claude Code v2.1.321).
- Model `claude-opus-5-5`, effort `medium`, 1M context, `maxOutputTokens` 128,000.
- Opus 5.5 list prices per million tokens: $4 input, $5 5-minute cache write, $0.20 cache read, $20 output. Pricing snapshot 13 November 2026, [pricing page](https://platform.claude.com/docs/en/about-claude/pricing).
- "Hidden" cost is `cost_state.totalCostUSD` minus the deduplicated assistant rows in the same transcript. The default arm's gap is $0.00, so the gap appears only when a session compacts. The $0.08-$0.34 per compaction is each phase's gap divided by its compactions.
- `python3 -m cc_usage_study.experiments figures` redraws the three live charts from the committed JSON. The JSON holds numbers only: no prompt text, no tool output, no keys, no local paths.

## What's inside

| | |
| --- | --- |
| `outputs/live_experiments.json` | All 14 live runs, sanitized |
| `src/cc_usage_study/experiments.py` | Extractor and the three live charts |
| `benchmarks/protocol.md` | How the live runs were set up, and the trigger math |
| `benchmarks/client_comparison.md` | The measured client table and cards C1-C6 |
| `docs/article-draft.md` | The essay, workflow by workflow |
| `docs/reference.md` | Knobs, model windows, caching, and the traps |
| `docs/examples.md` | The arithmetic behind a single session |
| `fixtures/synthetic/` | Fake transcripts for the analyzer |

## License

MIT. See `LICENSE`.
