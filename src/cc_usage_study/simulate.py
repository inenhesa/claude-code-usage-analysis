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
    Assumptions,
    eoq_trigger,
    fixed_compaction_cost,
    saving,
    simulate,
)
from cc_usage_study.pricing import PRICES_USD_PER_MILLION
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
    """Sweep triggers. Window minimum is 100k, so lower triggers use pct."""
    base = Assumptions()
    k, reset = fixed_compaction_cost(base)
    growth = base.tail + base.output_per_call
    estimates = {}
    simulated = {}
    for read_mult in (0.1, 0.05, 0.025):
        estimates[str(read_mult)] = eoq_trigger(
            reset_tokens=reset,
            fixed_compaction_cost=k if read_mult == base.cache_read_multiplier else _k_for(base, read_mult),
            growth_per_call=growth,
            read_multiplier=read_mult,
        )
        best_trigger = None
        best_cost = None
        curve = []
        for trigger in range(70_000, 401_000, 10_000):
            assumptions = replace(
                base,
                pct=trigger / base.context_window,
                cache_read_multiplier=read_mult,
            )
            result = simulate(assumptions)
            cost = result.cost("api")
            curve.append({"trigger": result.trigger_tokens, "api": cost, "compactions": result.compactions})
            if best_cost is None or cost < best_cost:
                best_cost = cost
                best_trigger = result.trigger_tokens
        hi = simulate(replace(base, cache_read_multiplier=read_mult))
        lo = simulate(
            replace(base, pct=best_trigger / base.context_window, cache_read_multiplier=read_mult)
        )
        simulated[str(read_mult)] = {
            "best_trigger": best_trigger,
            "best_api": best_cost,
            "saving_vs_967k": saving(hi, lo, "api"),
            "curve": curve,
        }
    return {
        "formula": "T* ≈ R + sqrt(2 * K * (g + o) / r)",
        "R": reset,
        "growth_per_call": growth,
        "K_at_read_0.1": k,
        "estimate": estimates,
        "simulated_best_10k_grid": simulated,
        "note": (
            "The closed form treats compaction overhead K as constant. "
            "The grid is the same session as S1 with the trigger moved. "
            "For read multipliers 0.05 and 0.025, K is recomputed with that r. "
            "Optimum near 150k-200k is the 0.05x (Opus 5.5 / Sonnet 5.5 list price) region, "
            "not a reason to pick 25% of a 1M window."
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


def _require_weight_match(model: str, read_multiplier: float) -> float:
    """Return the published base input price, in USD per million tokens.

    The simulator prices a session in base-input equivalents: cache writes at
    1.25x and 2x, output at 5x, reads at the scenario's multiplier. Multiplying
    those units by the base input price is the list-price bill only when the
    published row has those same ratios.
    """
    base, write_5m, write_1h, read, output = PRICES_USD_PER_MILLION[model]
    ratios = {
        "5-minute write": write_5m / base,
        "1-hour write": write_1h / base,
        "cache read": read / base,
        "output": output / base,
    }
    expected = {
        "5-minute write": 1.25,
        "1-hour write": 2.0,
        "cache read": read_multiplier,
        "output": 5.0,
    }
    for name, actual in ratios.items():
        if abs(actual - expected[name]) > 1e-9:
            raise ValueError(f"{model} {name} ratio {actual} does not match the simulator ({expected[name]})")
    return base


def _usd(api_units: float, usd_per_million: float) -> float:
    return round(api_units * usd_per_million / 1_000_000, 2)


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


def use_case_sweet_spots() -> list[dict]:
    """Best trigger for each everyday workflow. Every figure is from simulate()."""
    wide = list(range(50_000, 460_000, 10_000)) + [967_000]
    small_model = list(range(40_000, 210_000, 10_000))
    gaps = ((12, 75.0), (24, 75.0), (36, 75.0), (48, 75.0))
    specs = [
        {
            "id": "deep_0_1",
            "workflow": "Deep feature build, standard reads",
            "base": Assumptions(cache_read_multiplier=0.1),
            "triggers": wide,
            "chart_kind": "sweet",
            "chart_template": "Deep coding (0.1x reads) -> {k}k sweet spot",
            "practice": "One knob near {k}k. Sonnet 5 and Opus 4.7 read prices.",
        },
        {
            "id": "deep_0_05",
            "workflow": "Deep feature build, Opus 5.5 / Sonnet 5.5",
            "base": Assumptions(cache_read_multiplier=0.05),
            "triggers": wide,
            "chart_kind": "sweet",
            "chart_template": "Deep coding (Opus/Sonnet 5.5) -> {k}k sweet spot",
            "practice": "One knob near {k}k. This is the 150k-200k band.",
        },
        {
            "id": "marathon_0_05",
            "workflow": "Marathon refactor, 150 turns",
            "base": Assumptions(user_turns=150, tail=2_000, cache_read_multiplier=0.05),
            "triggers": wide,
            "chart_kind": "sweet",
            "chart_template": "Marathon refactor (150 turns) -> {k}k sweet spot",
            "practice": "Longer work still wants the same band, near {k}k.",
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
            "chart_template": "Heavy file reads / big memory -> {k}k sweet spot",
            "practice": "Heavier memory moves the valley to {k}k. Do not copy a tiny trigger.",
        },
        {
            "id": "unpinned_rules_0_05",
            "workflow": "Rules only in the chat, 8 repairs per compact",
            "base": Assumptions(rework_calls=8, rework_tail=8_000, cache_read_multiplier=0.05),
            "triggers": wide,
            "chart_kind": "sweet",
            "chart_template": "Unpinned rules (8 repairs/compact) -> {k}k sweet spot",
            "practice": "Put rules in root CLAUDE.md first. Without that, the valley jumps to {k}k.",
        },
        {
            "id": "breaks_idle_0_05",
            "workflow": "Workday with four long breaks, idle compaction on",
            "base": Assumptions(
                long_gaps=gaps,
                idle_compaction=True,
                cache_read_multiplier=0.05,
            ),
            "triggers": wide,
            "chart_kind": "sweet",
            "chart_template": "Long coffee breaks (idle compact on) -> {k}k sweet spot",
            "practice": "Idle compaction already summarizes a big warm cache. Valley is {k}k.",
        },
        {
            "id": "bugfix",
            "workflow": "Quick bugfix, one pull request",
            "base": Assumptions(user_turns=3, calls_per_turn=10),
            "triggers": wide,
            "chart_kind": "caution",
            "caution_trigger": 100_000,
            "chart_template": "Quick bugfix forced to 100k (180k matches default)",
            "practice": "Use /clear between unrelated tasks. Forcing 100k costs more. 180k changes nothing.",
        },
        {
            "id": "context_200k",
            "workflow": "200k-context model",
            "base": Assumptions(context_window=200_000),
            "triggers": small_model,
            "chart_kind": "caution",
            "caution_trigger": 50_000,
            "chart_template": "200k model forced down to 50k",
            "practice": "Leave the default. A 40k or 50k trigger thrashes.",
        },
    ]
    rows = []
    for spec in specs:
        base = spec["base"]
        default = _session_cost(_at_trigger(base, None))
        points = [_session_cost(_at_trigger(base, trigger)) for trigger in spec["triggers"]]
        # The model's own default may already be in the list. Keep one copy.
        by_trigger = {point["trigger"]: point for point in points}
        by_trigger[default["trigger"]] = default
        best = _pick_best(list(by_trigger.values()))
        row = {
            "id": spec["id"],
            "workflow": spec["workflow"],
            "best_trigger": best["trigger"],
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
        else:
            caution = _session_cost(_at_trigger(base, spec["caution_trigger"]))
            row["caution_trigger"] = caution["trigger"]
            row["caution_api"] = caution["api"]
            row["caution_change_pct"] = _change(caution["api"], default["api"])
            row["chart_label"] = spec["chart_template"]
            row["chart_change_pct"] = row["caution_change_pct"]
            if spec["id"] == "context_200k":
                low = _session_cost(_at_trigger(base, 40_000))
                row["trigger_40k_change_pct"] = _change(low["api"], default["api"])
        rows.append(row)
    return rows


def list_price_anchor(use_cases: list[dict]) -> dict:
    """Published API list price for the deep-feature sweet spots. Not the 5-hour meter."""
    by_id = {row["id"]: row for row in use_cases}
    sonnet_rate = _require_weight_match("claude-sonnet-5", 0.1)
    opus_rate = _require_weight_match("claude-opus-5-5", 0.05)
    standard = by_id["deep_0_1"]
    opus = by_id["deep_0_05"]
    return {
        "label": "Published API list price applied to simulated base-input equivalents. Not a subscription meter.",
        "sonnet_5": {
            "model": "claude-sonnet-5",
            "input_usd_per_million": sonnet_rate,
            "cache_read_multiplier": 0.1,
            "default_967k_usd": _usd(standard["default_api"], sonnet_rate),
            "sweet_spot_trigger": standard["best_trigger"],
            "sweet_spot_usd": _usd(standard["best_api"], sonnet_rate),
        },
        "opus_5_5": {
            "model": "claude-opus-5-5",
            "input_usd_per_million": opus_rate,
            "cache_read_multiplier": 0.05,
            "default_967k_usd": _usd(opus["default_api"], opus_rate),
            "sweet_spot_trigger": opus["best_trigger"],
            "sweet_spot_usd": _usd(opus["best_api"], opus_rate),
        },
    }


def build_summary() -> dict:
    scenarios = [run_scenario(scenario) for scenario in all_scenarios()]
    by_name = {row["name"]: row for row in scenarios}
    s1_hi = simulate(Assumptions())
    s1_lo = simulate(replace(Assumptions(), pct=0.25))
    eoq = eoq_grid()
    use_cases = use_case_sweet_spots()
    return {
        "disclaimer": (
            "Model output under stated assumptions. Not a measurement of any Claude account, "
            "and not a prediction of the five-hour or weekly usage meter. "
            "Anthropic has not published meter weights. "
            "Cold rewrites are priced as cache writes at the call's write rate "
            "(1.25x on compaction, 2x on a 1-hour main conversation), matching the "
            "reference call-level simulator. A 1.0x fresh-input reading of a cold prefix "
            "would differ."
        ),
        "caching": "physical prefix: a warm call reads the previous prompt and writes previous output plus the new tail",
        "scenarios": scenarios,
        "s1": by_name["S1"],
        "s1_totals": {"hi": s1_hi.totals(), "lo": s1_lo.totals()},
        "zero_effect_control": zero_effect_control(),
        "eoq": eoq,
        "use_cases": use_cases,
        "list_price": list_price_anchor(use_cases),
    }


def write_figures(summary: dict, out: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

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
        }
    )
    # Plain green and red only. Green is the cheaper setting or the baseline that
    # wins; red is the default that costs more, or the setting that backfires.
    green = "#1a7f37"
    green_light = "#2da44e"
    red = "#cf222e"
    red_dark = "#a40e26"
    red_soft = "#ff8182"
    ink = "#1f2328"
    muted = "#59636e"
    by_name = {row["name"]: row for row in summary["scenarios"]}
    figures = out / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    hi = simulate(Assumptions())
    lo = simulate(replace(Assumptions(), pct=0.25))

    def axes(figsize: tuple[float, float]):
        fig, ax = plt.subplots(figsize=figsize)
        fig.patch.set_facecolor("white")
        ax.set_facecolor("white")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("#d0d7de")
        ax.spines["bottom"].set_color("#d0d7de")
        ax.tick_params(length=0, pad=6)
        ax.grid(axis="y", color="#eaeef2", linewidth=1)
        ax.set_axisbelow(True)
        return fig, ax

    def finish(fig, path: Path) -> None:
        fig.savefig(path, dpi=160, bbox_inches="tight", pad_inches=0.45, facecolor="white")
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

    def legend_side(ax) -> None:
        ax.legend(frameon=False, loc="center left", bbox_to_anchor=(1.02, 0.5), borderaxespad=0)

    fig, ax = axes((9.4, 5.2))
    ax.plot(_cumsum(hi, "raw_prompt"), color=red, linewidth=2.2, label="about 967k")
    ax.plot(_cumsum(lo, "raw_prompt"), color=green, linewidth=2.2, label="250k")
    title(ax, "Raw prompt tokens in one long session")
    ax.set_xlabel("Model request", labelpad=8)
    ax.set_ylabel("Cumulative prompt tokens", labelpad=8)
    ax.yaxis.set_major_formatter(millions)
    legend_side(ax)
    finish(fig, figures / "cumulative_raw_tokens.png")

    fig, ax = axes((9.4, 5.2))
    ax.plot(_cumsum(hi, "api"), color=red, linewidth=2.2, label="about 967k")
    ax.plot(_cumsum(lo, "api"), color=green, linewidth=2.2, label="250k")
    title(ax, "Same session, priced like the API")
    ax.set_xlabel("Model request", labelpad=8)
    ax.set_ylabel("Cumulative API-equivalent units", labelpad=8)
    ax.yaxis.set_major_formatter(millions)
    legend_side(ax)
    finish(fig, figures / "cumulative_api_equivalent.png")

    deep_reads = next(row for row in summary["use_cases"] if row["id"] == "deep_0_05")
    sweet_trigger = deep_reads["best_trigger"]
    sweet_run = simulate(_at_trigger(Assumptions(cache_read_multiplier=0.05), sweet_trigger))
    default_run = simulate(_at_trigger(Assumptions(cache_read_multiplier=0.05), None))
    token_drop = (1 - sweet_run.totals()["prompt_tokens"] / default_run.totals()["prompt_tokens"]) * 100
    cost_drop = (1 - sweet_run.cost("api") / default_run.cost("api")) * 100
    fig, ax = axes((10.2, 5.6))
    ax.fill_between(range(len(_context(default_run))), _context(default_run), color=red, alpha=0.10, linewidth=0)
    ax.fill_between(range(len(_context(sweet_run))), _context(sweet_run), color=green, alpha=0.16, linewidth=0)
    ax.plot(
        _context(default_run),
        color=red,
        linewidth=2.0,
        label=f"Default ~967k: compacts {default_run.compactions} times",
    )
    ax.plot(
        _context(sweet_run),
        color=green,
        linewidth=2.0,
        label=f"Sweet spot {sweet_trigger // 1000}k: compacts {sweet_run.compactions} times",
    )
    ax.set_title(
        f"Default (~967k) vs. the {sweet_trigger // 1000}k sweet spot in one 60-turn session",
        loc="left",
        pad=36,
    )
    ax.text(
        0.0,
        1.02,
        (
            "Cutting peak context trims raw prompt tokens by "
            f"~{token_drop:.0f}% and estimated API cost by ~{cost_drop:.0f}% "
            "(caching makes re-reads 90-95% cheaper)."
        ),
        transform=ax.transAxes,
        fontsize=11,
        color=muted,
        clip_on=False,
    )
    ax.set_xlabel("Model request", labelpad=8)
    ax.set_ylabel("Prompt size", labelpad=8)
    ax.yaxis.set_major_formatter(tokens)
    ax.set_ylim(0, 1_180_000)
    legend_side(ax)
    finish(fig, figures / "context_sawtooth.png")

    curve = summary["eoq"]["simulated_best_10k_grid"]["0.1"]["curve"]
    baselines = {
        "0.1": by_name["S1"]["api_hi"],
        "0.05": by_name["S7-0.05"]["api_hi"],
        "0.025": by_name["S7-0.025"]["api_hi"],
    }
    fig, ax = axes((10.6, 6.1))
    ax.axvspan(70_000, 100_000, color="#ffebe9", zorder=0)
    ax.axvspan(150_000, 200_000, color="#dafbe1", zorder=0)
    # Red for the priciest reads, solid green for Opus 5.5 / Sonnet 5.5, light
    # dashed green for the cheapest reads.
    series = (
        ("0.1", "Standard 0.1x cache reads", red, "-"),
        ("0.05", "Opus 5.5 / Sonnet 5.5 (0.05x reads)", green, "-"),
        ("0.025", "Fable / Mythos (0.025x reads)", green_light, (0, (5, 2))),
    )
    valleys = []
    for key, label, color, style in series:
        block = summary["eoq"]["simulated_best_10k_grid"][key]
        points = block["curve"]
        relative = [point["api"] / baselines[key] * 100 for point in points]
        triggers = [point["trigger"] for point in points]
        ax.plot(triggers, relative, color=color, linewidth=2.4, linestyle=style, label=label, zorder=3)
        best_relative = block["best_api"] / baselines[key] * 100
        ax.scatter([block["best_trigger"]], [best_relative], color=color, s=48, zorder=4)
        valleys.append((block["best_trigger"], best_relative, color))
    ax.axhline(100, color="#8c959f", linestyle="--", linewidth=1.0, zorder=1)
    ax.text(
        392_000,
        101.4,
        "Default ~967k = 100% baseline",
        ha="right",
        va="bottom",
        fontsize=11,
        color=muted,
    )
    ax.text(
        175_000,
        91,
        "Sweet spot\n150k-200k",
        ha="center",
        va="center",
        fontsize=12,
        color=green,
        zorder=4,
    )
    # 120k sits up and to the right, inside the bowl, off the falling red slope.
    # 160k and 220k sit above their dots, under the sweet-spot caption.
    callouts = (
        ((8, 12), "left", "bottom"),
        ((0, 16), "center", "bottom"),
        ((0, 14), "center", "bottom"),
    )
    for (trigger, relative, color), (offset, ha, va) in zip(valleys, callouts):
        cut = 100 - relative
        ax.annotate(
            f"{trigger / 1000:.0f}k (-{cut:.0f}%)",
            xy=(trigger, relative),
            xytext=offset,
            textcoords="offset points",
            ha=ha,
            va=va,
            fontsize=12,
            color=color,
            annotation_clip=False,
        )
    title(ax, "Where the auto-compaction sweet spot sits across Claude models")
    ax.set_xlabel("Compaction trigger", labelpad=32)
    ax.annotate(
        "Too low (<100k): thrashing",
        xy=(85_000, 0),
        xycoords=("data", "axes fraction"),
        xytext=(0, -28),
        textcoords="offset points",
        ha="center",
        va="top",
        fontsize=11,
        color=red,
        annotation_clip=False,
    )
    ax.set_ylabel("Session cost, % of the ~967k default", labelpad=8)
    ax.xaxis.set_major_formatter(tokens)
    ax.set_xticks([100_000, 150_000, 200_000, 300_000, 400_000])
    ax.set_ylim(33, 108)
    legend_side(ax)
    finish(fig, figures / "eoq_curve.png")

    def change_label(change: float) -> str:
        rounded = int(round(change))
        if rounded == 0:
            return "0%"
        if rounded > 0:
            return f"+{rounded}% MORE expensive"
        times = 100.0 / (100.0 + change)
        if times >= 2:
            return f"{rounded}% ({times:.1f}x cheaper)"
        return f"{rounded}%"

    compare = [(row["chart_label"], None) for row in summary["use_cases"]]
    changes = [row["chart_change_pct"] for row in summary["use_cases"]]
    labels = [change_label(change) for change in changes]
    fig, ax = axes((11.8, 7.2))
    positions = list(range(len(compare)))
    colors = [green if change <= 0 else red for change in changes]
    ax.barh(positions, changes, color=colors, height=0.62, zorder=3)
    ax.axvline(0, color=ink, linewidth=1.1, zorder=2)
    ax.set_yticks(positions)
    ax.set_yticklabels([label for label, _ in compare])
    ax.invert_yaxis()
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color="#eaeef2", linewidth=1)
    label_x = max(changes) + 12
    for index, (change, text, color) in enumerate(zip(changes, labels, colors)):
        ax.text(label_x, index, text, va="center", ha="left", fontsize=12, color=color, clip_on=False)
    ax.set_xlim(min(changes) - 28, label_x + 148)
    ax.set_xlabel("Change in estimated API cost. Left of zero is cheaper. Right costs more.", labelpad=8)
    title(ax, "Sweet spot by workflow, versus leaving the default alone")
    finish(fig, figures / "scenario_comparison.png")

    amnesia = [row for row in summary["scenarios"] if row["name"].startswith("S3-")]
    fig, ax = axes((9.4, 5.2))
    ks = [0, 3, 6, 10, 15]
    ax.plot(ks, [row["api_saving"] for row in amnesia], color=green, marker="o", linewidth=2.2, label="API-equivalent")
    ax.plot(ks, [row["raw_prompt_saving"] for row in amnesia], color=red, marker="o", linewidth=2.2, linestyle=(0, (4, 2)), label="raw prompt")
    ax.axhline(1.0, color=ink, linewidth=1)
    title(ax, "Repair work can erase the priced saving")
    ax.set_xlabel("Extra 8k-tail requests after each compaction", labelpad=8)
    ax.set_ylabel("Higher trigger cost / lower trigger cost", labelpad=8)
    ax.set_xticks(ks)
    ax.legend(frameon=False, loc="upper right")
    finish(fig, figures / "amnesia_breakeven.png")
    # Same figure, kept under the older filename.
    fig, ax = axes((9.4, 5.2))
    ax.plot(ks, [row["api_saving"] for row in amnesia], color=green, marker="o", linewidth=2.2, label="API-equivalent")
    ax.plot(ks, [row["raw_prompt_saving"] for row in amnesia], color=red, marker="o", linewidth=2.2, linestyle=(0, (4, 2)), label="raw prompt")
    ax.axhline(1.0, color=ink, linewidth=1)
    title(ax, "Repair work can erase the priced saving")
    ax.set_xlabel("Extra 8k-tail requests after each compaction", labelpad=8)
    ax.set_ylabel("Higher trigger cost / lower trigger cost", labelpad=8)
    ax.set_xticks(ks)
    ax.legend(frameon=False, loc="upper right")
    finish(fig, figures / "sensitivity_rework.png")

    names = ["S4", "S4b", "S4c"]
    labels = ["1h cache\nidle off", "1h cache\nidle on", "5m cache\n7-minute gaps"]
    fig, ax = axes((9.4, 5.2))
    heights = [by_name[name]["api_saving"] for name in names]
    bars = ax.bar(labels, heights, color=[green if height >= 1 else red for height in heights], width=0.62)
    ax.axhline(1.0, color=ink, linewidth=1)
    title(ax, "Pauses change the answer")
    ax.set_ylabel("API cost, 967k / 250k", labelpad=8)
    ax.set_ylim(0, max(heights) * 1.18)
    for bar, height in zip(bars, heights):
        ax.text(bar.get_x() + bar.get_width() / 2, height + 0.06, f"{height:.2f}x", ha="center", va="bottom", fontsize=11)
    finish(fig, figures / "ttl_cliff.png")

    compound = simulate(replace(Assumptions(), window=250_000, pct=0.25))
    fig, ax = axes((9.4, 5.2))
    ax.plot(_context(hi), color=green, linewidth=1.6, label="about 967k")
    ax.plot(_context(compound), color=red, linewidth=1.6, label="250k window and 0.25 override\n(model 62.5k, live CLI 57.5k)")
    title(ax, "Two knobs together are not a 250k experiment")
    ax.set_xlabel("Model request", labelpad=8)
    ax.set_ylabel("Prompt size", labelpad=8)
    ax.yaxis.set_major_formatter(tokens)
    ax.set_ylim(0, 1_150_000)
    legend_side(ax)
    finish(fig, figures / "compounding_sawtooth.png")

    fig, ax = axes((9.4, 5.2))
    reads = ["S7-0.1", "S7-0.05", "S7-0.025"]
    ax.plot(
        [0.1, 0.05, 0.025],
        [by_name[name]["api_saving"] for name in reads],
        color=green,
        marker="o",
        linewidth=2.2,
        label="API-equivalent",
    )
    ax.plot(
        [0.1, 0.05, 0.025],
        [by_name[name]["raw_prompt_saving"] for name in reads],
        color=red,
        marker="o",
        linewidth=2.2,
        linestyle=(0, (4, 2)),
        label="raw prompt",
    )
    title(ax, "Cheaper cache reads shrink the priced saving")
    ax.set_xlabel("Cache-read price, as a fraction of input", labelpad=8)
    ax.set_ylabel("967k cost / 250k cost", labelpad=8)
    ax.set_xticks([0.025, 0.05, 0.1])
    ax.legend(frameon=False, loc="upper left")
    finish(fig, figures / "sensitivity_cache_hit.png")

    short_hi = simulate(Assumptions(user_turns=3, calls_per_turn=10))
    short_lo = simulate(replace(short_hi.assumptions, pct=0.25))
    fig, ax = axes((9.4, 5.2))
    ax.plot(_cumsum(short_hi, "raw_prompt"), color=red, linewidth=2.4, label="about 967k")
    ax.plot(_cumsum(short_lo, "raw_prompt"), color=green, linewidth=2.4, linestyle=(0, (1, 1.4)), label="250k")
    title(ax, "A short bugfix never reaches either trigger")
    ax.set_xlabel("Model request", labelpad=8)
    ax.set_ylabel("Cumulative prompt tokens", labelpad=8)
    ax.yaxis.set_major_formatter(millions)
    ax.legend(frameon=False, loc="upper left")
    finish(fig, figures / "short_session.png")

    fig, ax = axes((9.4, 5.2))
    triggers = [row["trigger"] for row in curve]
    ax.plot(triggers, [row["api"] for row in curve], color=green_light, linewidth=2.2)
    best = summary["eoq"]["simulated_best_10k_grid"]["0.1"]
    ax.scatter([best["best_trigger"]], [best["best_api"]], color=green, s=60, zorder=4)
    title(ax, "Standard 0.1x reads, cheapest near 120k")
    ax.set_xlabel("Compaction trigger", labelpad=8)
    ax.set_ylabel("API-equivalent cost of the session", labelpad=8)
    ax.xaxis.set_major_formatter(tokens)
    ax.yaxis.set_major_formatter(millions)
    ax.set_xticks([100_000, 150_000, 200_000, 250_000, 300_000, 400_000])
    finish(fig, figures / "threshold_sweep.png")


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
    print("Use-case sweet spots (cost change versus that workflow's default):")
    for row in summary["use_cases"]:
        print(
            f"  {row['id']}: best {row['best_trigger']} "
            f"({row['cost_change_pct']:+.1f}%) chart {row['chart_change_pct']:+.1f}% "
            f"[{row['chart_label']}]"
        )
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
