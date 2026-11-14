"""Run the physical-caching model and write summary.json plus figures.

Every number in the summary is a model output under the assumptions in
cc_usage_study.scenarios. It is not a measurement and not a usage-meter prediction.
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import replace
from pathlib import Path

from cc_usage_study.model import (
    LIVE_MEAN_COMPACTION_UNITS,
    Assumptions,
    eoq_trigger,
    fixed_compaction_cost,
    saving,
    simulate,
    summary_call_units,
)
from cc_usage_study.scenarios import all_scenarios, run_scenario


def _cumsum(result, scheme: str) -> list[float]:
    total = 0.0
    series = []
    # Cost of a prefix of the trace by reusing call fields directly.
    read_mult = result.assumptions.cache_read_multiplier
    out_mult = result.assumptions.output_multiplier
    for call in result.calls:
        if scheme == "raw_prompt":
            total += call.prompt_tokens
        else:
            write_cost = call.cache_write_tokens * call.write_multiplier
            total += (
                call.input_tokens
                + call.cache_read_tokens * read_mult
                + write_cost
                + call.output_tokens * out_mult
            )
        series.append(total)
    return series


def _context(result) -> list[int]:
    return [call.prompt_tokens for call in result.calls]


def eoq_grid() -> dict:
    """Sweep the fire point with ``default_trigger`` (a fire point, not a setting).

    To get a fire point T on a 1M model, type ``--autocompact T + 33000``.
    """
    base = Assumptions()
    k, reset = fixed_compaction_cost(base)
    growth = base.tail + base.output_per_call
    estimates = {}
    calibrated = {}
    simulated = {}
    for read_mult in (0.1, 0.05, 0.025):
        k_r = _k_for(base, read_mult)
        estimates[str(read_mult)] = eoq_trigger(
            reset_tokens=reset,
            fixed_compaction_cost=k_r,
            growth_per_call=growth,
            read_multiplier=read_mult,
        )
        # Live calibration: the modeled summary call at a 107k fire is short of
        # the live mean by this much, so K rises by the same amount.
        model_call = summary_call_units(replace(base, cache_read_multiplier=read_mult), 107_000)
        shift = LIVE_MEAN_COMPACTION_UNITS - model_call
        calibrated[str(read_mult)] = {
            "model_summary_call_units_at_107k": model_call,
            "live_mean_compaction_units": LIVE_MEAN_COMPACTION_UNITS,
            "K_model": k_r,
            "K_live_calibrated": k_r + shift,
            "estimate_model": estimates[str(read_mult)],
            "estimate_live_calibrated": eoq_trigger(
                reset_tokens=reset,
                fixed_compaction_cost=k_r + shift,
                growth_per_call=growth,
                read_multiplier=read_mult,
            ),
        }
        calibrated[str(read_mult)]["valley_shift_tokens"] = (
            calibrated[str(read_mult)]["estimate_live_calibrated"] - estimates[str(read_mult)]
        )
        best_trigger = None
        best_cost = None
        curve = []
        for trigger in range(50_000, 401_000, 10_000):
            result = simulate(_at_trigger(replace(base, cache_read_multiplier=read_mult), trigger))
            cost = result.cost("api")
            curve.append({"trigger": result.trigger_tokens, "api": cost, "compactions": result.compactions})
            if best_cost is None or cost < best_cost:
                best_cost = cost
                best_trigger = result.trigger_tokens
        hi = simulate(replace(base, cache_read_multiplier=read_mult))
        lo = simulate(_at_trigger(replace(base, cache_read_multiplier=read_mult), best_trigger))
        simulated[str(read_mult)] = {
            "best_trigger": best_trigger,
            "setting_to_type": best_trigger + 33_000,
            "best_api": best_cost,
            "default_api": hi.cost("api"),
            "saving_vs_967k": saving(hi, lo, "api"),
            "change_vs_967k_pct": _change(best_cost, hi.cost("api")),
            "curve": curve,
        }
    # Same 0.05x grid with the summary call raised to the live mean.
    live_base = replace(
        base,
        cache_read_multiplier=0.05,
        compact_instr=base.compact_instr
        + round(
            (LIVE_MEAN_COMPACTION_UNITS - calibrated["0.05"]["model_summary_call_units_at_107k"])
            / base.cache_write_5m
        ),
    )
    live_points = [
        (trigger, simulate(_at_trigger(live_base, trigger)).cost("api"))
        for trigger in range(50_000, 401_000, 10_000)
    ]
    live_best = min(live_points, key=lambda item: item[1])
    live_default = simulate(live_base).cost("api")
    return {
        "formula": "T* = R + sqrt(2 * K * (g + o) / r)",
        "R": reset,
        "growth_per_call": growth,
        "K_at_read_0.1": k,
        "estimate": estimates,
        "live_calibrated": calibrated,
        "simulated_live_calibrated_0.05": {
            "compact_instr": live_base.compact_instr,
            "best_trigger": live_best[0],
            "setting_to_type": live_best[0] + 33_000,
            "change_vs_967k_pct": _change(live_best[1], live_default),
        },
        "simulated_best_10k_grid": simulated,
        "note": (
            "Triggers are fire points. Type 33,000 more with --autocompact. "
            "K equals the overhead simulate() charges per compaction, so the closed form and the trace agree. "
            "The live Opus 5.5 compactions averaged about 36.6k cache-weighted units against about 21.6k for the "
            "modeled summary call at a 107k fire, so the live-calibrated K is about 15k higher and the valley "
            "moves roughly 10k to the right. Optimum near 150k-200k is the 0.05x (Opus 5.5 / Sonnet 5.5) region."
        ),
    }


def _k_for(assumptions: Assumptions, read_mult: float) -> float:
    adjusted = replace(assumptions, cache_read_multiplier=read_mult)
    k, _reset = fixed_compaction_cost(adjusted)
    return k


def zero_effect_control() -> dict:
    """Twenty single-call turns that stay under both triggers."""
    assumptions = Assumptions(
        label="zero_effect",
        user_turns=20,
        calls_per_turn=1,
        tail=4_000,
        output_per_call=1_000,
    )
    hi = simulate(assumptions)
    lo = simulate(replace(assumptions, pct=0.25))
    return {
        "note": "Neither trigger fires. The setting does nothing.",
        "trigger_hi": hi.trigger_tokens,
        "trigger_lo": lo.trigger_tokens,
        "compactions_hi": hi.compactions,
        "compactions_lo": lo.compactions,
        "prompt_tokens": hi.totals()["prompt_tokens"],
        "api_saving": saving(hi, lo, "api"),
        "first_prompt": hi.calls[0].prompt_tokens,
        "last_prompt": hi.calls[-1].prompt_tokens,
    }


def _at_trigger(base: Assumptions, trigger: int | None) -> Assumptions:
    """Pin the compaction trigger without using the absolute-window floor.

    ``window`` cannot be set below 100,000. ``default_trigger`` can, which is
    how a sweep reaches the thrash zone. None leaves the model's own default.
    """
    return replace(base, window=None, pct=None, default_trigger=trigger)


def _session_cost(assumptions: Assumptions) -> dict:
    result = simulate(assumptions)
    totals = result.totals()
    return {
        "trigger": result.trigger_tokens,
        "api": result.cost("api"),
        "prompt_tokens": totals["prompt_tokens"],
        "compactions": result.compactions,
    }


def _pick_best(points: list[dict]) -> dict:
    """Lowest API cost. Ties prefer 180k, then anything in 150k-200k."""
    lowest = min(point["api"] for point in points)
    tied = [point for point in points if point["api"] <= lowest * (1 + 1e-12)]
    exact = [point for point in tied if point["trigger"] == 180_000]
    if exact:
        return exact[0]
    band = [point for point in tied if 150_000 <= point["trigger"] <= 200_000]
    if band:
        return min(band, key=lambda point: abs(point["trigger"] - 180_000))
    return min(tied, key=lambda point: (point["api"], -point["trigger"]))


def _change(alternative: float, baseline: float) -> float:
    return (alternative - baseline) / baseline * 100.0


# ----------------------------------------------------------------------------- the pause tax
#
# A small, explicit next-task model. Every number is exact prefix-cache
# arithmetic, cross-checked against simulate() in the tests.
#
# TASK_SYSTEM is the shared system prompt and tools (the live CLI's first
# request was about 17k in total). TASK_PROJECT is the session-specific part
# (CLAUDE.md, memory, git status). A hot --resume skips rewriting it: the
# live 1B run saved about 5.0k write tokens per avoided /clear.
TASK_SYSTEM = 12_000
TASK_PROJECT = 5_000
TASK_CALLS = 5
TASK_TAIL = 2_000
TASK_OUTPUT = 600
# One re-orientation (exploration) call a fresh session makes and a resumed
# session can skip: reads a ~23k warm prefix, writes a 1.9k tool result plus
# the previous 600-token answer, and answers in 600 tokens.
EXPLORE_WRITE = 2_500
EXPLORE_OUTPUT = 600
EXPLORE_READ = 23_250
OUTPUT_WEIGHT = 5.0
WRITE_5M = 1.25
WRITE_1H = 2.0
LIVE_HOT_SAVED_WRITE_TOKENS = 4_985  # (18,853 - 8,884) / 2, live 1B run
READ_MULTS = (0.1, 0.05, 0.025)


def _ttl_write(ttl_min: float) -> float:
    return WRITE_1H if ttl_min >= 60 else WRITE_5M


def task_units(
    *,
    cached_prefix: int,
    cold_prefix: int,
    write_mult: float,
    read_mult: float,
    calls: int = TASK_CALLS,
    tail: int = TASK_TAIL,
    output: int = TASK_OUTPUT,
) -> float:
    """Cache-weighted units of one ``calls``-request task.

    The first request reads ``cached_prefix`` warm and writes ``cold_prefix``
    plus its tail. Each later request reads the previous prompt and writes
    the previous answer plus a new tail.
    """
    prompt = cached_prefix + cold_prefix + tail
    total = cached_prefix * read_mult + (cold_prefix + tail) * write_mult + output * OUTPUT_WEIGHT
    for _ in range(calls - 1):
        total += prompt * read_mult + (output + tail) * write_mult + output * OUTPUT_WEIGHT
        prompt += output + tail
    return total


def exploration_call_units(write_mult: float, read_mult: float) -> float:
    return EXPLORE_WRITE * write_mult + EXPLORE_OUTPUT * OUTPUT_WEIGHT + EXPLORE_READ * read_mult


def price_of_a_pause() -> list[dict]:
    """One carried history token after the TTL (rewritten) vs a warm read."""
    rows = []
    for ttl, write in (("5m", WRITE_5M), ("1h", WRITE_1H)):
        for read_mult in (0.1, 0.05):
            rows.append(
                {
                    "ttl": ttl,
                    "read_multiplier": read_mult,
                    "write_multiplier": write,
                    "times_a_warm_read": write / read_mult,
                }
            )
    return rows


def stale_resume_vs_clear(
    history_grid: tuple[int, ...] = tuple(range(0, 200_001, 5_000)),
    read_mults: tuple[float, ...] = READ_MULTS,
) -> dict:
    """``--resume`` after the cache expired, vs ``/clear`` and a fresh session.

    Both arms are cold after the pause. The stale arm rewrites the carried
    history H once and re-reads it on each later call, so it pays
    H x (w + (calls - 1) x r) extra. The fresh arm may need ``saved`` extra
    exploration calls to re-orient. Crossover H* = saved x V / slope.
    """
    out: dict = {
        "task": {
            "calls": TASK_CALLS,
            "system_tokens": TASK_SYSTEM,
            "project_tokens": TASK_PROJECT,
            "tail_tokens": TASK_TAIL,
            "output_tokens": TASK_OUTPUT,
        },
        "exploration_call": {
            "write_tokens": EXPLORE_WRITE,
            "output_tokens": EXPLORE_OUTPUT,
            "read_tokens": EXPLORE_READ,
        },
    }
    for ttl_name, ttl in (("5m", 5.0), ("1h", 60.0)):
        write = _ttl_write(ttl)
        block = {}
        for read_mult in read_mults:
            fresh = task_units(cached_prefix=0, cold_prefix=TASK_SYSTEM + TASK_PROJECT, write_mult=write, read_mult=read_mult)
            slope = write + (TASK_CALLS - 1) * read_mult
            explore = exploration_call_units(write, read_mult)
            arms = {}
            for saved in (0, 2):
                clear = fresh + saved * explore
                curve = []
                for history in history_grid:
                    stale = task_units(
                        cached_prefix=0,
                        cold_prefix=TASK_SYSTEM + TASK_PROJECT + history,
                        write_mult=write,
                        read_mult=read_mult,
                    )
                    curve.append({"history": history, "stale_pct_of_clear": stale / clear * 100})
                arms[f"saved_calls_{saved}"] = {
                    "clear_units": clear,
                    "crossover_history_tokens": saved * explore / slope,
                    "at_30k_pct": (fresh + slope * 30_000) / clear * 100,
                    "at_60k_pct": (fresh + slope * 60_000) / clear * 100,
                    "at_120k_pct": (fresh + slope * 120_000) / clear * 100,
                    "curve": curve,
                }
            block[str(read_mult)] = {
                "write_multiplier": write,
                "fresh_task_units": fresh,
                "slope_units_per_history_token": slope,
                "exploration_call_units": explore,
                "rule_of_thumb_tokens_per_saved_call": explore / slope,
                **arms,
            }
        out[ttl_name] = block
    out["note"] = (
        "Stale resume pays only if carried history is under about 5k tokens per exploration call it saves. "
        "With no saved calls it always loses."
    )
    return out


def hot_resume_crossover(
    history_grid: tuple[int, ...] = tuple(range(0, 200_001, 5_000)),
    read_mults: tuple[float, ...] = READ_MULTS,
) -> dict:
    """``--resume`` inside the cache window, vs ``/clear`` and a fresh session.

    The fresh arm reads the shared system prefix warm but rewrites the
    session-specific prefix (about 5.0k tokens, as in the live 1B run). The
    hot arm reads everything warm and pays r x H on every remaining call.
    Hot wins while H x remaining calls < saved writes x (w - r) / r.
    """
    out: dict = {"live_saved_write_tokens_per_avoided_clear": LIVE_HOT_SAVED_WRITE_TOKENS}
    for ttl_name, ttl in (("5m", 5.0), ("1h", 60.0)):
        write = _ttl_write(ttl)
        block = {}
        for read_mult in read_mults:
            fresh = task_units(cached_prefix=TASK_SYSTEM, cold_prefix=TASK_PROJECT, write_mult=write, read_mult=read_mult)
            saved_units = TASK_PROJECT * (write - read_mult)
            token_calls = saved_units / read_mult
            curve = []
            for history in history_grid:
                hot = task_units(
                    cached_prefix=TASK_SYSTEM + TASK_PROJECT + history,
                    cold_prefix=0,
                    write_mult=write,
                    read_mult=read_mult,
                )
                curve.append({"history": history, "hot_pct_of_fresh": hot / fresh * 100})
            block[str(read_mult)] = {
                "fresh_task_units": fresh,
                "saved_write_units_per_avoided_clear": TASK_PROJECT * write,
                "net_saved_units": saved_units,
                "crossover_token_calls": token_calls,
                "crossover_history_tokens": token_calls / TASK_CALLS,
                "curve": curve,
            }
        out[ttl_name] = block
    out["note"] = (
        "Hot resume wins only while carried history x remaining calls stays under about 120k token-calls "
        "at 0.05x reads (about 57k at 0.1x). The live -27.7% came from a tiny history (peak context 20,265)."
    )
    return out


def _cron_assumptions(ttl_min: float, read_mult: float, runs: int, gap_min: float) -> Assumptions:
    return Assumptions(
        label="cron",
        user_turns=runs,
        calls_per_turn=TASK_CALLS,
        tail=TASK_TAIL,
        output_per_call=TASK_OUTPUT,
        system_prompt_tokens=TASK_SYSTEM,
        project_context_tokens=TASK_PROJECT,
        gap_min=gap_min,
        ttl_min=ttl_min,
        cache_read_multiplier=read_mult,
        idle_compaction=True,
        # claude -p exits after each run, so idle compaction never runs.
        process_alive_through_gaps=False,
    )


def cron_jobs(runs: int = 12, gap_min: float = 61.0, read_mult: float = 0.05) -> dict:
    """Scheduled ``claude -p`` runs: ``--resume`` every run vs a fresh session every run.

    Runs are spaced just past the cache window, so every resume is a cold
    rebuild of the whole carried history. On the 1-hour cache a run that
    starts inside the hour would read warm instead.
    """
    out: dict = {"runs": runs, "gap_min": gap_min, "read_multiplier": read_mult}
    for ttl_name, ttl in (("1h", 60.0), ("5m", 5.0)):
        resume = simulate(_cron_assumptions(ttl, read_mult, runs, gap_min))
        single = simulate(_cron_assumptions(ttl, read_mult, 1, gap_min))
        fresh_units = single.cost("api") * runs
        resume_units = resume.cost("api")
        out[ttl_name] = {
            "resume_units": resume_units,
            "fresh_units": fresh_units,
            "fresh_pct_of_resume": fresh_units / resume_units * 100,
            "resume_vs_fresh_multiplier": resume_units / fresh_units,
            "resume_change_vs_fresh_pct": _change(resume_units, fresh_units),
            "resumes": runs - 1,
            "cold_rebuilds": resume.cold_rebuilds,
            "compactions": resume.total_compactions,
            "final_prompt_tokens": resume.calls[-1].prompt_tokens,
        }
    return out


# ----------------------------------------------------------------------------- workflows


def use_case_sweet_spots() -> list[dict]:
    """Best fire point for each everyday workflow, plus the pause traps.

    Every figure is from simulate() or the exact next-task model above.
    ``best_trigger`` is where compaction fires. ``setting_to_type`` is the
    ``--autocompact`` value that fires there (33,000 higher).
    """
    wide = list(range(50_000, 460_000, 10_000)) + [967_000]
    small_model = list(range(50_000, 170_000, 10_000))
    gaps = ((12, 75.0), (24, 75.0), (36, 75.0), (48, 75.0))
    specs = [
        {
            "id": "deep_0_1",
            "workflow": "Deep feature build, standard 0.1x reads",
            "base": Assumptions(cache_read_multiplier=0.1),
            "triggers": wide,
            "chart_kind": "sweet",
            "chart_template": "Deep build, 0.1x reads: fire at {k}k",
            "practice": "One knob, firing near {k}k. Sonnet 5 and Opus 4.7 read weights.",
        },
        {
            "id": "deep_0_05",
            "workflow": "Deep feature build, Opus 5.5 / Sonnet 5.5",
            "base": Assumptions(cache_read_multiplier=0.05),
            "triggers": wide,
            "chart_kind": "sweet",
            "chart_template": "Deep build, Opus 5.5: fire at {k}k",
            "practice": "One knob, firing near {k}k. This is the 150k-200k band.",
        },
        {
            "id": "marathon_0_05",
            "workflow": "Marathon refactor, 150 turns",
            "base": Assumptions(user_turns=150, tail=2_000, cache_read_multiplier=0.05),
            "triggers": wide,
            "chart_kind": "sweet",
            "chart_template": "Marathon refactor, 150 turns: fire at {k}k",
            "practice": "Longer work with small steps wants a slightly earlier fire, near {k}k.",
        },
        {
            "id": "heavy_memory_0_05",
            "workflow": "Heavy file reads, big working memory",
            "base": Assumptions(
                tail=10_000,
                reinjected_tokens=30_000,
                user_turns=40,
                calls_per_turn=6,
                cache_read_multiplier=0.05,
            ),
            "triggers": wide,
            "chart_kind": "sweet",
            "chart_template": "Heavy file reads, big memory: fire at {k}k",
            "practice": "Heavier memory moves the valley to {k}k. Do not copy a small trigger.",
        },
        {
            "id": "unpinned_rules_0_05",
            "workflow": "Rules only in the chat, 8 repairs per compact",
            "base": Assumptions(rework_calls=8, rework_tail=8_000, cache_read_multiplier=0.05),
            "triggers": wide,
            "chart_kind": "sweet",
            "chart_template": "Rules only in chat (8 repairs): fire at {k}k",
            "practice": "Put rules in root CLAUDE.md first. Without that, the valley jumps to {k}k.",
        },
        {
            "id": "breaks_idle_0_05",
            "workflow": "Workday with four long breaks, idle compaction ran",
            "base": Assumptions(long_gaps=gaps, idle_compaction=True, cache_read_multiplier=0.05),
            "triggers": wide,
            "chart_kind": "sweet",
            "chart_template": "Long breaks, idle compaction ran: fire at {k}k",
            "practice": "Idle compaction already summarized the big warm cache. Valley is {k}k.",
        },
        {
            "id": "bugfix",
            "workflow": "Quick bugfix, one pull request",
            "base": Assumptions(user_turns=3, calls_per_turn=10),
            "triggers": wide,
            "chart_kind": "caution",
            "caution": {"trigger": 100_000},
            "chart_template": "Quick bugfix forced to fire at 100k",
            "practice": "Nothing to tune: no trigger is reached. Forcing 100k costs more.",
        },
        {
            "id": "context_200k",
            "workflow": "200k-context model",
            "base": Assumptions(context_window=200_000),
            "triggers": small_model,
            "chart_kind": "caution",
            # CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=25 on a 200k model: floor(180,000 x 0.25).
            "caution": {"pct": 0.25},
            "chart_template": "200k model, override 25 (fires at 45k)",
            "practice": "Leave the default (167,000). A 45k fire sits under the ~46k reset and thrashes.",
        },
        {
            "id": "breaks_lapsed_0_05",
            "workflow": "Lunch break / laptop sleep, idle compaction lapsed",
            "base": Assumptions(
                long_gaps=gaps,
                idle_compaction=True,
                process_alive_through_gaps=False,
                cache_read_multiplier=0.05,
            ),
            "triggers": wide,
            "chart_kind": "penalty",
            "penalty_vs": Assumptions(long_gaps=gaps, idle_compaction=True, cache_read_multiplier=0.05),
            "penalty_vs_label": "the same day with idle compaction running",
            "chart_template": "Lunch breaks, laptop slept: idle compaction lapsed",
            "practice": "Run /compact before you leave, or /clear with a handoff when you return. Best fire point {k}k.",
        },
        {
            "id": "gaps_5m_0_05",
            "workflow": "API or credits, 7-minute gaps past the 5m cache",
            "base": Assumptions(ttl_min=5, gap_min=7, cache_read_multiplier=0.05),
            "triggers": wide,
            "chart_kind": "penalty",
            "penalty_vs": Assumptions(ttl_min=5, gap_min=2, cache_read_multiplier=0.05),
            "penalty_vs_label": "the same session with 2-minute gaps",
            "chart_template": "API key, 7-minute gaps past the 5m cache",
            "practice": "Every turn rebuilds the whole prefix. Reply inside 5 minutes or fire early, near {k}k.",
        },
    ]
    rows = []
    for spec in specs:
        base = spec["base"]
        default = _session_cost(_at_trigger(base, None))
        points = [_session_cost(_at_trigger(base, trigger)) for trigger in spec["triggers"]]
        by_trigger = {point["trigger"]: point for point in points}
        by_trigger[default["trigger"]] = default
        best = _pick_best(list(by_trigger.values()))
        row = {
            "id": spec["id"],
            "workflow": spec["workflow"],
            "chart_kind": spec["chart_kind"],
            "best_trigger": best["trigger"],
            "setting_to_type": best["trigger"] + 33_000,
            "setting_to_type_pct": round(best["trigger"] / (base.context_window - 20_000) * 100, 1),
            "default_trigger": default["trigger"],
            "best_api": best["api"],
            "default_api": default["api"],
            "best_prompt_tokens": best["prompt_tokens"],
            "default_prompt_tokens": default["prompt_tokens"],
            "compactions_at_best": best["compactions"],
            "compactions_at_default": default["compactions"],
            "cost_change_pct": _change(best["api"], default["api"]),
            "prompt_change_pct": _change(best["prompt_tokens"], default["prompt_tokens"]),
            "practice": spec["practice"].format(k=best["trigger"] // 1000),
        }
        if spec["chart_kind"] == "sweet":
            row["chart_label"] = spec["chart_template"].format(k=best["trigger"] // 1000)
            row["chart_change_pct"] = row["cost_change_pct"]
        elif spec["chart_kind"] == "caution":
            if "pct" in spec["caution"]:
                caution = _session_cost(replace(base, pct=spec["caution"]["pct"]))
                row["caution_setting"] = f"CLAUDE_AUTOCOMPACT_PCT_OVERRIDE={int(spec['caution']['pct'] * 100)}"
            else:
                caution = _session_cost(_at_trigger(base, spec["caution"]["trigger"]))
                row["caution_setting"] = f"fire point {spec['caution']['trigger']}"
            row["caution_trigger"] = caution["trigger"]
            row["caution_api"] = caution["api"]
            row["caution_compactions"] = caution["compactions"]
            row["caution_change_pct"] = _change(caution["api"], default["api"])
            row["chart_label"] = spec["chart_template"]
            row["chart_change_pct"] = row["caution_change_pct"]
        else:
            good = _session_cost(_at_trigger(spec["penalty_vs"], None))
            row["penalty_vs"] = spec["penalty_vs_label"]
            row["penalty_baseline_api"] = good["api"]
            row["chart_label"] = spec["chart_template"]
            row["chart_change_pct"] = _change(default["api"], good["api"])
        rows.append(row)

    stale = stale_resume_vs_clear(history_grid=(60_000,))["5m"]["0.05"]["saved_calls_2"]
    rows.append(
        {
            "id": "stale_resume_60k",
            "workflow": "Stale --resume after a pause with 60k history, vs /clear",
            "chart_kind": "penalty",
            "best_trigger": None,
            "chart_label": "Stale --resume, 60k history, vs /clear",
            "chart_change_pct": stale["curve"][0]["stale_pct_of_clear"] - 100,
            "penalty_vs": "/clear plus a 3-line handoff, with 2 extra exploration calls",
            "practice": "Past the cache window, /clear and paste a short handoff unless history is under about 10k.",
        }
    )
    cron = cron_jobs()["1h"]
    rows.append(
        {
            "id": "cron_hourly_resume",
            "workflow": "Hourly claude -p cron with --resume vs fresh runs",
            "chart_kind": "penalty",
            "best_trigger": None,
            "chart_label": "Hourly claude -p with --resume, vs fresh runs",
            "chart_change_pct": cron["resume_change_vs_fresh_pct"],
            "penalty_vs": "a fresh session every run, state kept in a file",
            "practice": "Start every scheduled run fresh. Keep state in a file, not in --resume.",
        }
    )
    return rows


def build_summary() -> dict:
    scenarios = [run_scenario(scenario) for scenario in all_scenarios()]
    by_name = {row["name"]: row for row in scenarios}
    s1_hi = simulate(Assumptions())
    s1_lo = simulate(replace(Assumptions(), pct=0.25))
    eoq = eoq_grid()
    use_cases = use_case_sweet_spots()
    idle_off = by_name["S4"]["api_hi"]
    idle_on = by_name["S4b"]["api_hi"]
    return {
        "disclaimer": (
            "Model output under stated assumptions. Not a measurement of any Claude account, "
            "and not a prediction of the five-hour or weekly usage meter. "
            "Anthropic has not published meter weights. "
            "All usage is in cache-weighted units: input 1, 5-minute write 1.25, 1-hour write 2, "
            "cache read 0.05 on Opus 5.5 (0.1 and 0.025 as sensitivity), output 5."
        ),
        "units": {
            "name": "cache-weighted units",
            "weights_opus_5_5": {"input": 1, "cache_write_5m": 1.25, "cache_write_1h": 2, "cache_read": 0.05, "output": 5},
            "read_sensitivity": [0.1, 0.05, 0.025],
        },
        "caching": "physical prefix: a warm call reads the previous prompt and writes previous output plus the new tail",
        "trigger_formula": (
            "Claude Code v2.1.325 tle + WX: effective_window = window - min(maxOutputTokens, 20000); "
            "threshold = effective_window - 13000, or min(floor(effective_window * pct), effective_window - 13000)"
        ),
        "scenarios": scenarios,
        "s1": by_name["S1"],
        "s1_totals": {"hi": s1_hi.totals(), "lo": s1_lo.totals()},
        "idle_lapse": {
            "default_idle_lapsed_units": idle_off,
            "default_idle_ran_units": idle_on,
            "lapsed_vs_ran_multiplier": idle_off / idle_on,
        },
        "zero_effect_control": zero_effect_control(),
        "eoq": eoq,
        "use_cases": use_cases,
        "pause_tax": {
            "price_of_a_pause": price_of_a_pause(),
            "stale_resume_vs_clear": stale_resume_vs_clear(),
            "hot_resume_crossover": hot_resume_crossover(),
            "cron_jobs": cron_jobs(),
        },
    }


def _live_ttl_points(out: Path) -> list[dict]:
    """Measured TTL points, if the live runner's results have been merged."""
    for path in (out / "live_experiments.json", Path(__file__).resolve().parents[2] / "outputs" / "live_experiments.json"):
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            runs = data.get("ttl_runs") or {}
            return list(runs.get("points") or [])
    return []


FOOTER = "Weights 1 / 1.25 / 0.05 / 5 (Opus 5.5). Range across models in README."


def write_figures(summary: dict, out: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 12,
            "axes.titlesize": 15,
            "axes.labelsize": 12,
            "xtick.labelsize": 11,
            "ytick.labelsize": 11,
            "legend.fontsize": 11,
            "figure.facecolor": "white",
            "savefig.facecolor": "white",
            "savefig.edgecolor": "white",
            "axes.facecolor": "white",
            "text.color": "#1f2328",
            "axes.labelcolor": "#1f2328",
            "xtick.color": "#1f2328",
            "ytick.color": "#1f2328",
            "axes.titlecolor": "#1f2328",
            "text.parse_math": False,
        }
    )
    # Two hues only. Green is the cheaper setting or the habit that wins; red is
    # the default that costs more, or the trap. Series differ by alpha, hatch,
    # or line style, never by a third hue.
    green = "#1a7f37"
    red = "#cf222e"
    ink = "#1f2328"
    muted = "#59636e"
    axis_grey = "#d0d7de"
    grid_grey = "#eaeef2"
    green_tint = "#dafbe1"
    red_tint = "#ffebe9"
    by_name = {row["name"]: row for row in summary["scenarios"]}
    figures = out / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    stale_file = figures / "ttl_cliff.png"
    if stale_file.exists():
        stale_file.unlink()
    hi = simulate(Assumptions())
    lo = simulate(replace(Assumptions(), pct=0.25))

    def axes(figsize: tuple[float, float]):
        fig, ax = plt.subplots(figsize=figsize)
        fig.patch.set_facecolor("white")
        ax.set_facecolor("white")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color(axis_grey)
        ax.spines["bottom"].set_color(axis_grey)
        ax.tick_params(length=0, pad=6)
        ax.grid(axis="y", color=grid_grey, linewidth=1)
        ax.set_axisbelow(True)
        return fig, ax

    def footer(fig, text: str = FOOTER) -> None:
        fig.text(0.01, -0.02, text, ha="left", va="top", fontsize=9.5, color=muted)

    def finish(fig, path: Path, note: str | None = FOOTER) -> None:
        if note:
            footer(fig, note)
        fig.savefig(path, dpi=160, bbox_inches="tight", pad_inches=0.35, facecolor="white")
        plt.close(fig)

    def title(ax, text: str) -> None:
        ax.set_title(text, loc="left", pad=14)

    def tokens(value, _pos):
        if abs(value) >= 1_000_000:
            scaled = value / 1_000_000
            text = f"{scaled:.1f}".rstrip("0").rstrip(".")
            return f"{text}M"
        return f"{value / 1000:.0f}k"

    def millions(value, _pos):
        return f"{value / 1_000_000:.0f}M"

    def percent(value, _pos):
        return f"{value:.0f}%"

    def signed_percent(value, _pos):
        return "0" if abs(value) < 1e-9 else f"{value:+.0f}%"

    hi_label = f"Default, fires at {hi.trigger_tokens // 1000}k"
    lo_label = f"Override 25 on 1M, fires at {lo.trigger_tokens // 1000}k"

    # Raw prompt tokens.
    fig, ax = axes((9.4, 5.2))
    ax.plot(_cumsum(hi, "raw_prompt"), color=red, linewidth=2.2, label=hi_label)
    ax.plot(_cumsum(lo, "raw_prompt"), color=green, linewidth=2.2, label=lo_label)
    title(ax, "Raw prompt tokens in one long session")
    ax.set_xlabel("Model request", labelpad=8)
    ax.set_ylabel("Cumulative prompt tokens (raw)", labelpad=8)
    ax.yaxis.set_major_formatter(millions)
    ax.legend(frameon=False, loc="upper left")
    finish(fig, figures / "cumulative_raw_tokens.png", "Raw tokens, no cache weighting. Modeled 60-turn session, 1-hour cache.")

    # Cache-weighted usage.
    fig, ax = axes((9.4, 5.2))
    ax.plot(_cumsum(hi, "api"), color=red, linewidth=2.2, label=hi_label)
    ax.plot(_cumsum(lo, "api"), color=green, linewidth=2.2, label=lo_label)
    ratio = hi.cost("api") / lo.cost("api")
    title(ax, f"Same session, cache-weighted: the gap shrinks to {ratio:.2f}x")
    ax.set_xlabel("Model request", labelpad=8)
    ax.set_ylabel("Cumulative cache-weighted units", labelpad=8)
    ax.yaxis.set_major_formatter(millions)
    ax.legend(frameon=False, loc="upper left")
    finish(fig, figures / "cumulative_api_equivalent.png", "Weights 1 / 1.25 / 2 / 0.1 / 5 (0.1x reads). Range across models in README.")

    # Sawtooth at the Opus 5.5 sweet spot.
    deep_reads = next(row for row in summary["use_cases"] if row["id"] == "deep_0_05")
    sweet_trigger = deep_reads["best_trigger"]
    sweet_run = simulate(_at_trigger(Assumptions(cache_read_multiplier=0.05), sweet_trigger))
    default_run = simulate(_at_trigger(Assumptions(cache_read_multiplier=0.05), None))
    token_drop = (1 - sweet_run.totals()["prompt_tokens"] / default_run.totals()["prompt_tokens"]) * 100
    cost_drop = (1 - sweet_run.cost("api") / default_run.cost("api")) * 100
    fig, ax = axes((10.2, 5.6))
    ax.fill_between(range(len(_context(default_run))), _context(default_run), color=red, alpha=0.10, linewidth=0)
    ax.fill_between(range(len(_context(sweet_run))), _context(sweet_run), color=green, alpha=0.16, linewidth=0)
    ax.plot(_context(default_run), color=red, linewidth=2.0,
            label=f"Default, fires at 967k: {default_run.compactions} compactions")
    ax.plot(_context(sweet_run), color=green, linewidth=2.0,
            label=f"Fires at {sweet_trigger // 1000}k (type {(sweet_trigger + 33_000) // 1000}k): "
                  f"{sweet_run.compactions} compactions")
    ax.set_title(f"Default vs a {sweet_trigger // 1000}k fire point in one 60-turn session", loc="left", pad=34)
    ax.text(0.0, 1.02,
            f"Raw prompt tokens fall {token_drop:.0f}%. Cache-weighted usage falls {cost_drop:.0f}%, "
            "because most of the tall red curve was a cheap warm read.",
            transform=ax.transAxes, fontsize=11, color=muted, clip_on=False)
    ax.set_xlabel("Model request", labelpad=8)
    ax.set_ylabel("Prompt size, tokens", labelpad=8)
    ax.yaxis.set_major_formatter(tokens)
    ax.set_ylim(0, 1_180_000)
    ax.legend(frameon=False, loc="upper left")
    finish(fig, figures / "context_sawtooth.png")

    # EOQ curves: all green, red only for the zone below 100k.
    grid = summary["eoq"]["simulated_best_10k_grid"]
    fig, ax = axes((10.8, 6.2))
    ax.axvspan(46_000, 100_000, color=red, alpha=0.08, zorder=0, linewidth=0)
    ax.axvspan(150_000, 200_000, color=green_tint, zorder=0, linewidth=0)
    series = (
        ("0.05", "Opus 5.5 / Sonnet 5.5, 0.05x reads", "-"),
        ("0.1", "Sonnet 5 / Opus 4.7, 0.1x reads", (0, (6, 3))),
        ("0.025", "Fable / Mythos, 0.025x reads", (0, (1, 2))),
    )
    for key, label, style in series:
        block = grid[key]
        base_cost = block["default_api"]
        triggers = [point["trigger"] for point in block["curve"]]
        relative = [point["api"] / base_cost * 100 for point in block["curve"]]
        ax.plot(triggers, relative, color=green, linewidth=2.6 if style == "-" else 2.2, linestyle=style,
                label=label, zorder=3)
        best_relative = block["best_api"] / base_cost * 100
        ax.scatter([block["best_trigger"]], [best_relative], color=green, s=52, zorder=4, edgecolor="white")
        ax.annotate(f"{block['best_trigger'] // 1000}k ({best_relative - 100:+.0f}%)",
                    xy=(block["best_trigger"], best_relative), xytext=(0, -20), textcoords="offset points",
                    ha="center", va="top", fontsize=11.5, color=green, fontweight="bold")
    ax.axhline(100, color=ink, linewidth=1.0, zorder=1)
    ax.text(398_000, 98.5, "Default, fires at 967k = 100%", ha="right", va="top", fontsize=11, color=ink)
    ax.text(175_000, 126, "Fire here\n150k-200k", ha="center", va="center", fontsize=12, color=green,
            fontweight="bold")
    title(ax, "Where auto-compaction should fire, across Claude read weights")
    ax.set_xlabel("Where compaction fires (the number you type is 33k higher)", labelpad=8)
    ax.set_ylabel("Usage, % of default (cache-weighted)", labelpad=8)
    ax.xaxis.set_major_formatter(tokens)
    ax.yaxis.set_major_formatter(percent)
    ax.set_xticks([50_000, 100_000, 150_000, 200_000, 300_000, 400_000])
    ax.set_xlim(46_000, 402_000)
    low = min(min(p["api"] / grid[k]["default_api"] * 100 for p in grid[k]["curve"]) for k in grid)
    high = max(max(p["api"] / grid[k]["default_api"] * 100 for p in grid[k]["curve"]) for k in grid)
    ax.set_ylim(max(0, low - 12), max(108, min(high + 4, 140)))
    handles, labels_ = ax.get_legend_handles_labels()
    handles.append(Patch(facecolor=red, alpha=0.18, edgecolor="none"))
    labels_.append("under 100k: summaries cost more than they save")
    ax.legend(handles, labels_, frameon=True, facecolor="white", edgecolor=axis_grey, framealpha=1,
              loc="upper right", fontsize=10.5)
    finish(fig, figures / "eoq_curve.png",
           "Modeled 60-turn session, 1-hour cache. Each curve is % of its own default. Weights 1 / 1.25 / 2 / r / 5.")

    # Workflow comparison, sorted, with clipped outliers.
    clip = 100.0
    rows = sorted(summary["use_cases"], key=lambda row: row["chart_change_pct"])
    changes = [row["chart_change_pct"] for row in rows]
    shown = [min(change, clip) for change in changes]
    fig, ax = axes((12.4, 8.0))
    positions = list(range(len(rows)))
    for index, (row, change, width) in enumerate(zip(rows, changes, shown)):
        color = green if change <= 0 else red
        hatch = "////" if row["chart_kind"] == "penalty" else None
        ax.barh(index, width, color=color if hatch is None else "white", edgecolor=color, hatch=hatch,
                linewidth=1.4, height=0.62, zorder=3)
        if change > clip:
            # Break marks at the clip, then the true size.
            ax.plot([clip - 4, clip - 1], [index - 0.36, index + 0.36], color="white", linewidth=5, zorder=4)
            ax.plot([clip - 4, clip - 1], [index - 0.36, index + 0.36], color=ink, linewidth=1.0, zorder=5)
            text = f"{change / 100 + 1:.2f}x ({change:+.0f}%)"
        else:
            text = f"{change:+.0f}%" if abs(change) >= 0.5 else "0%"
        offset = 2.5 if change > 0 else -2.5
        ax.text(width + offset, index, text, va="center", ha="left" if change > 0 else "right", fontsize=11.5,
                color=color, fontweight="bold", zorder=6)
    ax.axvline(0, color=ink, linewidth=1.1, zorder=2)
    ax.set_yticks(positions)
    ax.set_yticklabels([row["chart_label"] for row in rows])
    ax.invert_yaxis()
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color=grid_grey, linewidth=1)
    ax.set_xlim(min(changes) - 16, clip + 36)
    ax.set_xticks([-60, -40, -20, 0, 20, 40, 60, 80, 100])
    ax.xaxis.set_major_formatter(signed_percent)
    ax.set_xlabel("Usage change, % (cache-weighted). Left of zero uses less.", labelpad=8)
    title(ax, "Fire point by workflow, and the pause traps")
    ax.legend(
        handles=[
            Patch(facecolor=green, edgecolor=green, label="best fire point vs the default"),
            Patch(facecolor=red, edgecolor=red, label="setting forced too low vs the default"),
            Patch(facecolor="white", edgecolor=red, hatch="////", label="pause trap vs the fix"),
        ],
        frameon=True, facecolor="white", edgecolor=axis_grey, framealpha=1, loc="upper right", fontsize=10.5,
    )
    finish(fig, figures / "scenario_comparison.png",
           "Bars past +100% are cut; the label gives the full size. " + FOOTER)

    # Rework sensitivity.
    amnesia = [row for row in summary["scenarios"] if row["name"].startswith("S3-")]
    ks = [0, 3, 6, 10, 15]
    for name in ("amnesia_breakeven.png", "sensitivity_rework.png"):
        fig, ax = axes((9.4, 5.2))
        ax.plot(ks, [row["api_saving"] for row in amnesia], color=green, marker="o", linewidth=2.2,
                label="cache-weighted usage")
        ax.plot(ks, [row["raw_prompt_saving"] for row in amnesia], color=red, marker="o", linewidth=2.2,
                linestyle=(0, (4, 2)), label="raw prompt tokens")
        ax.axhline(1.0, color=ink, linewidth=1)
        title(ax, "Repair work after each compaction erases the saving")
        ax.set_xlabel("Extra 8k-tail requests after each compaction", labelpad=8)
        ax.set_ylabel("Default usage / 245k-fire usage", labelpad=8)
        ax.set_xticks(ks)
        ax.legend(frameon=False, loc="upper right")
        finish(fig, figures / name, "Above 1 the lower fire point wins. Weights 1 / 1.25 / 2 / 0.1 / 5.")

    # TTL chart 1: the price of a pause.
    pause = summary["pause_tax"]["price_of_a_pause"]
    fig, ax = axes((10.2, 5.8))
    labels = ["Warm read"] + [f"{row['ttl']} cache\n{row['read_multiplier']:g}x reads" for row in pause]
    heights = [1.0] + [row["times_a_warm_read"] for row in pause]
    colors = [green] + [red] * len(pause)
    alphas = [1.0] + [0.55 if row["read_multiplier"] == 0.1 else 1.0 for row in pause]
    for index, (height, color, alpha) in enumerate(zip(heights, colors, alphas)):
        ax.bar(index, height, color=color, alpha=alpha, width=0.62, zorder=3)
        label = "1x" if index == 0 else f"{height:g}x"
        ax.text(index, height + 0.8, label, ha="center", va="bottom", fontsize=13, fontweight="bold", color=color)
    ax.axhline(1.0, color=green, linewidth=1.2, zorder=2)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels)
    ax.set_ylim(0, max(heights) * 1.15)
    ax.set_ylabel("Cost of 1 carried history token, x a warm read", labelpad=8)
    title(ax, "After the cache expires, every old token is bought again")
    finish(fig, figures / "ttl_price_of_a_pause.png",
           "Rewrite weight over read weight: 1.25 or 2 against 0.1 or 0.05. The 5m cache is API keys and credits; "
           "1h is a subscription.")

    # TTL chart 2: resume crossover.
    stale = summary["pause_tax"]["stale_resume_vs_clear"]["5m"]["0.05"]
    stale_1h = summary["pause_tax"]["stale_resume_vs_clear"]["1h"]["0.05"]
    hot = summary["pause_tax"]["hot_resume_crossover"]["5m"]["0.05"]
    fig, ax = axes((10.8, 6.2))
    hx = [point["history"] for point in hot["curve"]]
    ax.plot(hx, [point["hot_pct_of_fresh"] for point in hot["curve"]], color=green, linewidth=2.6,
            label="hot --resume (cache warm)")
    sx = [point["history"] for point in stale["saved_calls_2"]["curve"]]
    ax.plot(sx, [point["stale_pct_of_clear"] for point in stale["saved_calls_2"]["curve"]], color=red,
            linewidth=2.6, label="stale --resume, 2 calls saved")
    ax.plot(sx, [point["stale_pct_of_clear"] for point in stale["saved_calls_0"]["curve"]], color=red,
            linewidth=1.8, linestyle=(0, (5, 3)), alpha=0.7, label="stale --resume, none saved")
    ax.axhline(100, color=ink, linewidth=1.2, zorder=2)
    ax.text(198_000, 103, "/clear + fresh session = 100%", ha="right", va="bottom", fontsize=11, color=ink)
    cross_stale = stale["saved_calls_2"]["crossover_history_tokens"]
    cross_hot = hot["crossover_history_tokens"]
    ax.scatter([cross_stale], [100], color=red, s=60, zorder=5, edgecolor="white")
    ax.annotate(f"stale stops\npaying at {cross_stale / 1000:.0f}k", xy=(cross_stale, 100), xytext=(4_000, 245),
                textcoords="data", fontsize=11, color=red, ha="left", va="bottom",
                arrowprops={"arrowstyle": "-", "color": red, "linewidth": 1.0})
    ax.scatter([cross_hot], [100], color=green, s=60, zorder=5, edgecolor="white")
    ax.annotate(f"hot stops paying at {cross_hot / 1000:.0f}k\n(history x 5 calls = {hot['crossover_token_calls'] / 1000:.0f}k)",
                xy=(cross_hot, 100), xytext=(20, -46), textcoords="offset points", fontsize=11, color=green,
                ha="left", arrowprops={"arrowstyle": "-", "color": green, "linewidth": 1.0})
    live = _live_ttl_points(out)
    top = max(point["stale_pct_of_clear"] for point in stale["saved_calls_0"]["curve"]) * 1.06
    ax.set_ylim(0, top)
    hot_live = [point for point in live if point.get("kind") == "hot"]
    stale_live = [point for point in live if point.get("kind") != "hot"]
    for point in hot_live:
        ax.scatter([point["history_tokens"]], [point["pct_of_fresh"]], color=green, marker="D", s=70,
                   zorder=6, edgecolor=ink, linewidth=0.8)
        ax.annotate(f"hot, measured live: {point['pct_of_fresh']:.0f}%", xy=(point["history_tokens"], point["pct_of_fresh"]),
                    xytext=(104_000, 55), textcoords="data", fontsize=10.5, color=green, ha="left", va="center",
                    arrowprops={"arrowstyle": "-", "color": green, "linewidth": 1.0})
    if stale_live:
        # Measured stale points sit far above the modeled lines (smaller task, cheaper fresh start),
        # so they are drawn as an arrow off the top of the chart instead of squashing the model.
        xs = [point["history_tokens"] for point in stale_live]
        ys = [point["pct_of_fresh"] for point in stale_live]
        x_mid = sum(xs) / len(xs)
        ax.annotate("", xy=(x_mid, top), xytext=(x_mid, top * 0.80), textcoords="data",
                    arrowprops={"arrowstyle": "-|>", "color": red, "linewidth": 2.0, "mutation_scale": 18})
        ax.text(x_mid + 3_000, top * 0.86,
                f"stale, measured live: {min(ys):.0f}% to {max(ys):.0f}%\n(off the scale, 3 of 3 runs)",
                ha="left", va="center", fontsize=10.5, color=red)
    if live:
        ax.scatter([], [], color=green, marker="D", edgecolor=ink, s=60, label="measured live (Opus 5.5)")
    ax.set_xlim(0, 200_000)
    ax.xaxis.set_major_formatter(tokens)
    ax.yaxis.set_major_formatter(percent)
    ax.set_xlabel("History carried into the next task, tokens", labelpad=8)
    ax.set_ylabel("Next-task usage, % of a fresh session (cache-weighted)", labelpad=8)
    title(ax, "Resume only while the cache is warm and the history is small")
    ax.legend(frameon=True, facecolor="white", edgecolor=axis_grey, framealpha=1, loc="upper left", fontsize=10.5)
    finish(fig, figures / "resume_crossover.png",
           f"5-call next task, 5m cache, 0.05x reads. On the 1h cache stale resume stops paying at "
           f"{stale_1h['saved_calls_2']['crossover_history_tokens'] / 1000:.1f}k. Lines modeled. "
           + ("Live points: n=1 each, a\nsmaller task than the model's, so the fresh start is cheaper and the gap bigger."
              if live else "Live check pending."))

    # TTL chart 3: cron.
    cron = summary["pause_tax"]["cron_jobs"]
    fig, ax = axes((10.2, 5.8))
    groups = [("1h", "1-hour cache\n(subscription)"), ("5m", "5-minute cache\n(API key, credits)")]
    width = 0.34
    for index, (key, label) in enumerate(groups):
        block = cron[key]
        ax.bar(index - width / 2 - 0.02, 100, width=width, color=red, zorder=3)
        ax.bar(index + width / 2 + 0.02, block["fresh_pct_of_resume"], width=width, color=green, zorder=3)
        ax.text(index - width / 2 - 0.02, 102, "100%", ha="center", va="bottom", fontsize=13, color=red,
                fontweight="bold")
        ax.text(index + width / 2 + 0.02, block["fresh_pct_of_resume"] + 2,
                f"{block['fresh_pct_of_resume']:.0f}%", ha="center", va="bottom", fontsize=13, color=green,
                fontweight="bold")
        ax.text(index - width / 2 - 0.02, 50, f"{block['cold_rebuilds']} of {block['resumes']}\nresumes\nrebuilt cold",
                ha="center", va="center", fontsize=10.5, color="white")
    ax.set_xticks(range(len(groups)))
    ax.set_xticklabels([label for _key, label in groups])
    ax.set_xlim(-0.6, 1.6)
    ax.set_ylim(0, 118)
    ax.yaxis.set_major_formatter(percent)
    ax.set_ylabel("Usage of 12 runs, % of --resume (cache-weighted)", labelpad=8)
    title(ax, "Scheduled claude -p: start fresh every run")
    ax.legend(handles=[Patch(color=red, label="--resume every run"), Patch(color=green, label="fresh session every run")],
              frameon=False, loc="upper right", ncol=2, bbox_to_anchor=(1.0, 1.02))
    finish(fig, figures / "cron_resume_vs_fresh.png",
           "12 runs, 61 minutes apart, 5-call task each. Modeled; the cold rebuild itself was measured live. "
           "Weights 1 / 1.25 / 2 / 0.05 / 5 (Opus 5.5).")

    # Two knobs together.
    compound = simulate(replace(Assumptions(), window=250_000, pct=0.25))
    fig, ax = axes((9.4, 5.2))
    ax.plot(_context(hi), color=green, linewidth=1.6, label=hi_label)
    ax.plot(_context(compound), color=red, linewidth=1.6,
            label=f"250k window + override 25:\nfires at {compound.trigger_tokens:,} ({compound.compactions} compactions)")
    title(ax, "Two knobs together multiply into a thrash")
    ax.set_xlabel("Model request", labelpad=8)
    ax.set_ylabel("Prompt size, tokens", labelpad=8)
    ax.yaxis.set_major_formatter(tokens)
    ax.set_ylim(0, 1_150_000)
    ax.legend(frameon=False, loc="upper right")
    finish(fig, figures / "compounding_sawtooth.png", "floor((250,000 - 20,000) x 0.25) = 57,500, as in the live arm.")

    # Read-weight sensitivity.
    fig, ax = axes((9.4, 5.2))
    reads = ["S7-0.1", "S7-0.05", "S7-0.025"]
    ax.plot([0.1, 0.05, 0.025], [by_name[name]["api_saving"] for name in reads], color=green, marker="o",
            linewidth=2.2, label="cache-weighted usage")
    ax.plot([0.1, 0.05, 0.025], [by_name[name]["raw_prompt_saving"] for name in reads], color=red, marker="o",
            linewidth=2.2, linestyle=(0, (4, 2)), label="raw prompt tokens")
    title(ax, "Cheaper cache reads shrink the saving")
    ax.set_xlabel("Cache-read weight, as a fraction of input", labelpad=8)
    ax.set_ylabel("Default usage / 245k-fire usage", labelpad=8)
    ax.set_xticks([0.025, 0.05, 0.1])
    ax.set_ylim(1.0, 3.4)
    ax.legend(frameon=False, loc="upper left", bbox_to_anchor=(0.0, 0.86))
    finish(fig, figures / "sensitivity_cache_hit.png", "Above 1 the lower fire point wins.")

    # Short session.
    short_hi = simulate(Assumptions(user_turns=3, calls_per_turn=10))
    short_lo = simulate(replace(short_hi.assumptions, pct=0.25))
    fig, ax = axes((9.4, 5.2))
    ax.plot(_cumsum(short_hi, "raw_prompt"), color=red, linewidth=2.4, label=hi_label)
    ax.plot(_cumsum(short_lo, "raw_prompt"), color=green, linewidth=2.4, linestyle=(0, (1, 1.4)), label=lo_label)
    title(ax, "A short bugfix never reaches either trigger")
    ax.set_xlabel("Model request", labelpad=8)
    ax.set_ylabel("Cumulative prompt tokens (raw)", labelpad=8)
    ax.yaxis.set_major_formatter(millions)
    ax.legend(frameon=False, loc="upper left")
    finish(fig, figures / "short_session.png", "The two lines overlap: neither setting does anything.")

    # Threshold sweep, 0.1x reads.
    curve = grid["0.1"]["curve"]
    fig, ax = axes((9.4, 5.2))
    ax.plot([row["trigger"] for row in curve], [row["api"] / grid["0.1"]["default_api"] * 100 for row in curve],
            color=green, linewidth=2.2)
    best = grid["0.1"]
    ax.scatter([best["best_trigger"]], [best["best_api"] / best["default_api"] * 100], color=green, s=60, zorder=4)
    ax.axhline(100, color=ink, linewidth=1.0)
    title(ax, f"Standard 0.1x reads: lowest near a {best['best_trigger'] // 1000}k fire point")
    ax.set_xlabel("Where compaction fires (type 33k more)", labelpad=8)
    ax.set_ylabel("Usage, % of default (cache-weighted)", labelpad=8)
    ax.xaxis.set_major_formatter(tokens)
    ax.yaxis.set_major_formatter(percent)
    ax.set_xticks([50_000, 100_000, 150_000, 200_000, 250_000, 300_000, 400_000])
    finish(fig, figures / "threshold_sweep.png", "Weights 1 / 1.25 / 2 / 0.1 / 5. Modeled 60-turn session.")


def write_outputs(out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    summary = build_summary()
    # Drop the long EOQ curves from the on-disk summary? Keep them; they document the plot.
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    with (out / "grid.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "name",
                "trigger_hi",
                "trigger_lo",
                "compactions_hi",
                "compactions_lo",
                "api_hi",
                "api_lo",
                "api_saving",
                "raw_prompt_saving",
                "read_full_saving",
                "h_zero_saving",
            ],
        )
        writer.writeheader()
        for row in summary["scenarios"]:
            writer.writerow({key: row[key] for key in writer.fieldnames})
    write_figures(summary, out)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the compaction simulation and write plots.")
    parser.add_argument("--out", type=Path, default=Path("outputs"))
    args = parser.parse_args(argv)
    summary = write_outputs(args.out)
    print("Use cases (cache-weighted usage change; best is a fire point):")
    for row in summary["use_cases"]:
        best = f"best {row['best_trigger']} " if row.get("best_trigger") else ""
        print(f"  {row['id']}: {best}chart {row['chart_change_pct']:+.1f}% [{row['chart_label']}]")
    best = summary["eoq"]["simulated_best_10k_grid"]
    print(
        "EOQ grid best triggers: "
        + ", ".join(f"r={key} -> {row['best_trigger']}" for key, row in best.items())
    )
    print(summary["disclaimer"])
    print(f"Wrote {args.out / 'summary.json'} and figures under {args.out / 'figures'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
