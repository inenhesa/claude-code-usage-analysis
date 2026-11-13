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


def list_price_anchor(by_name: dict, eoq: dict) -> dict:
    """Published API list price for one modeled long session. Not the 5-hour meter."""
    sonnet_rate = _require_weight_match("claude-sonnet-5", 0.1)
    opus_rate = _require_weight_match("claude-opus-5-5", 0.05)
    standard = eoq["simulated_best_10k_grid"]["0.1"]
    opus_reads = eoq["simulated_best_10k_grid"]["0.05"]
    sonnet_default = by_name["S1"]["api_hi"]
    sonnet_250k = by_name["S1"]["api_lo"]
    opus_default = by_name["S7-0.05"]["api_hi"]
    return {
        "label": "Published API list price applied to simulated base-input equivalents. Not a subscription meter.",
        "sonnet_5": {
            "model": "claude-sonnet-5",
            "input_usd_per_million": sonnet_rate,
            "cache_read_multiplier": 0.1,
            "default_967k_usd": _usd(sonnet_default, sonnet_rate),
            "trigger_250k_usd": _usd(sonnet_250k, sonnet_rate),
            "sweet_spot_trigger": standard["best_trigger"],
            "sweet_spot_usd": _usd(standard["best_api"], sonnet_rate),
        },
        "opus_5_5": {
            "model": "claude-opus-5-5",
            "input_usd_per_million": opus_rate,
            "cache_read_multiplier": 0.05,
            "default_967k_usd": _usd(opus_default, opus_rate),
            "sweet_spot_trigger": opus_reads["best_trigger"],
            "sweet_spot_usd": _usd(opus_reads["best_api"], opus_rate),
        },
    }


def build_summary() -> dict:
    scenarios = [run_scenario(scenario) for scenario in all_scenarios()]
    by_name = {row["name"]: row for row in scenarios}
    s1_hi = simulate(Assumptions())
    s1_lo = simulate(replace(Assumptions(), pct=0.25))
    eoq = eoq_grid()
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
        "list_price": list_price_anchor(by_name, eoq),
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
            "text.color": "#1a1a1a",
            "axes.labelcolor": "#1a1a1a",
            "xtick.color": "#1a1a1a",
            "ytick.color": "#1a1a1a",
            "axes.titlecolor": "#1a1a1a",
        }
    )
    navy = "#143d66"
    clay = "#c4511b"
    green = "#1f7a4d"
    ink = "#1a1a1a"
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
        ax.spines["left"].set_color("#d5d8dc")
        ax.spines["bottom"].set_color("#d5d8dc")
        ax.tick_params(length=0, pad=6)
        ax.grid(axis="y", color="#eef0f2", linewidth=1)
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
    ax.plot(_cumsum(hi, "raw_prompt"), color=navy, linewidth=2.2, label="about 967k")
    ax.plot(_cumsum(lo, "raw_prompt"), color=clay, linewidth=2.2, label="250k")
    title(ax, "Raw prompt tokens in one long session")
    ax.set_xlabel("Model request", labelpad=8)
    ax.set_ylabel("Cumulative prompt tokens", labelpad=8)
    ax.yaxis.set_major_formatter(millions)
    legend_side(ax)
    finish(fig, figures / "cumulative_raw_tokens.png")

    fig, ax = axes((9.4, 5.2))
    ax.plot(_cumsum(hi, "api"), color=navy, linewidth=2.2, label="about 967k")
    ax.plot(_cumsum(lo, "api"), color=clay, linewidth=2.2, label="250k")
    title(ax, "Same session, priced like the API")
    ax.set_xlabel("Model request", labelpad=8)
    ax.set_ylabel("Cumulative API-equivalent units", labelpad=8)
    ax.yaxis.set_major_formatter(millions)
    legend_side(ax)
    finish(fig, figures / "cumulative_api_equivalent.png")

    teal = "#0b6e6a"
    coral = "#e23b2f"
    token_drop = (1 - by_name["S1"]["prompt_lo"] / by_name["S1"]["prompt_hi"]) * 100
    cost_drop = (1 - by_name["S1"]["api_lo"] / by_name["S1"]["api_hi"]) * 100
    fig, ax = axes((10.2, 5.6))
    ax.fill_between(range(len(_context(hi))), _context(hi), color=navy, alpha=0.13, linewidth=0)
    ax.fill_between(range(len(_context(lo))), _context(lo), color=teal, alpha=0.16, linewidth=0)
    ax.plot(
        _context(hi),
        color=navy,
        linewidth=2.0,
        label=f"Default ~967k: compacts {hi.compactions} times",
    )
    ax.plot(
        _context(lo),
        color=teal,
        linewidth=2.0,
        label=f"Early trigger 250k: compacts {lo.compactions} times",
    )
    ax.set_title("Why 3x fewer tokens only saves about 2x in cost", loc="left", pad=36)
    ax.text(
        0.0,
        1.02,
        (
            "Prompt caching discounts re-reads by 90-95%. "
            f"A {token_drop:.0f}% token drop becomes a {cost_drop:.0f}% cost drop."
        ),
        transform=ax.transAxes,
        fontsize=12,
        color="#344054",
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
    ax.axvspan(70_000, 100_000, color="#fde8e6", zorder=0)
    ax.axvspan(150_000, 200_000, color="#e6f4f1", zorder=0)
    series = (
        ("0.1", "Standard 0.1x cache reads", navy),
        ("0.05", "Opus 5.5 / Sonnet 5.5 (0.05x reads)", clay),
        ("0.025", "Fable / Mythos (0.025x reads)", green),
    )
    valleys = []
    for key, label, color in series:
        block = summary["eoq"]["simulated_best_10k_grid"][key]
        points = block["curve"]
        relative = [point["api"] / baselines[key] * 100 for point in points]
        triggers = [point["trigger"] for point in points]
        ax.plot(triggers, relative, color=color, linewidth=2.4, label=label, zorder=3)
        best_relative = block["best_api"] / baselines[key] * 100
        ax.scatter([block["best_trigger"]], [best_relative], color=color, s=48, zorder=4)
        valleys.append((block["best_trigger"], best_relative, color))
    ax.axhline(100, color="#98a2b3", linestyle="--", linewidth=1.0, zorder=1)
    ax.axvline(250_000, color="#98a2b3", linestyle="--", linewidth=1.0, zorder=1)
    ax.text(
        392_000,
        101.4,
        "Default ~967k = 100% baseline",
        ha="right",
        va="bottom",
        fontsize=11,
        color="#667085",
    )
    ax.text(
        247_000,
        103.2,
        "25% of 1M",
        ha="right",
        va="bottom",
        fontsize=11,
        color="#667085",
    )
    ax.text(
        175_000,
        91,
        "Sweet spot\n150k-200k",
        ha="center",
        va="center",
        fontsize=12,
        color="#0f6d6c",
        zorder=4,
    )
    # 120k sits up and to the right, inside the bowl, off the falling blue slope.
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
    title(ax, "There is a sweet spot (and compacting too low turns the cost back up)")
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
        color="#b42318",
        annotation_clip=False,
    )
    ax.set_ylabel("Session cost, % of the ~967k default", labelpad=8)
    ax.xaxis.set_major_formatter(tokens)
    ax.set_xticks([100_000, 150_000, 200_000, 250_000, 300_000, 400_000])
    ax.set_ylim(33, 108)
    legend_side(ax)
    finish(fig, figures / "eoq_curve.png")

    def cost_change(row: dict) -> float:
        return (row["api_lo"] - row["api_hi"]) / row["api_hi"] * 100

    def change_label(change: float, saving: float) -> str:
        rounded = int(round(change))
        if rounded == 0:
            return "0% (never compacts)"
        if rounded > 0:
            return f"+{rounded}% MORE expensive"
        if saving >= 2:
            return f"{rounded}% ({saving:.1f}x cheaper)"
        if abs(rounded) <= 5:
            return f"{rounded}% (savings wiped out)"
        return f"{rounded}%"

    def sweet_spot(read_key: str, baseline_name: str, caption: str) -> tuple[str, float, float]:
        block = summary["eoq"]["simulated_best_10k_grid"][read_key]
        baseline = by_name[baseline_name]["api_hi"]
        change = (block["best_api"] - baseline) / baseline * 100
        saving = baseline / block["best_api"]
        trigger = int(block["best_trigger"] / 1000)
        return caption.format(trigger=trigger), change, saving

    sweet_rows = [
        sweet_spot("0.1", "S1", "Sweet spot: {trigger}k on standard reads"),
        sweet_spot("0.05", "S7-0.05", "Sweet spot: {trigger}k on Opus/Sonnet 5.5"),
    ]
    scenario_rows = [
        ("Long session, 250k vs ~967k", "S1"),
        ("Hour-long breaks, idle compact on", "S4b"),
        ("Rules forgotten, 15 repairs", "S3-15"),
        ("Short bugfix", "S2"),
        ("Short bugfix forced to 100k", "S2b"),
        ("Both knobs: 62.5k vs 250k alone", "S8b"),
        ("25% on a 200k model (50k)", "S6"),
    ]
    named_rows = [
        (label, cost_change(by_name[name]), by_name[name]["api_saving"]) for label, name in scenario_rows
    ]
    rows = sweet_rows + named_rows
    compare = [(label, None) for label, _, _ in rows]
    changes = [change for _, change, _ in rows]
    labels = [change_label(change, saving) for _, change, saving in rows]
    fig, ax = axes((11.8, 7.2))
    positions = list(range(len(compare)))
    colors = [teal if change <= 0 else coral for change in changes]
    ax.barh(positions, changes, color=colors, height=0.62, zorder=3)
    ax.axvline(0, color=ink, linewidth=1.1, zorder=2)
    ax.set_yticks(positions)
    ax.set_yticklabels([label for label, _ in compare])
    ax.invert_yaxis()
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color="#eef0f2", linewidth=1)
    label_x = max(changes) + 12
    for index, (change, text, color) in enumerate(zip(changes, labels, colors)):
        ax.text(label_x, index, text, va="center", ha="left", fontsize=12, color=color, clip_on=False)
    ax.set_xlim(min(changes) - 28, label_x + 148)
    ax.set_xlabel("Change in estimated API cost. Left of zero is cheaper. Right costs more.", labelpad=8)
    title(ax, "When compacting earlier saves money, and when it backfires")
    finish(fig, figures / "scenario_comparison.png")

    amnesia = [row for row in summary["scenarios"] if row["name"].startswith("S3-")]
    fig, ax = axes((9.4, 5.2))
    ks = [0, 3, 6, 10, 15]
    ax.plot(ks, [row["api_saving"] for row in amnesia], color=navy, marker="o", linewidth=2.2, label="API-equivalent")
    ax.plot(ks, [row["raw_prompt_saving"] for row in amnesia], color=clay, marker="o", linewidth=2.2, label="raw prompt")
    ax.axhline(1.0, color=ink, linewidth=1)
    title(ax, "Repair work can erase the priced saving")
    ax.set_xlabel("Extra 8k-tail requests after each compaction", labelpad=8)
    ax.set_ylabel("Higher trigger cost / lower trigger cost", labelpad=8)
    ax.set_xticks(ks)
    ax.legend(frameon=False, loc="upper right")
    finish(fig, figures / "amnesia_breakeven.png")
    # Same figure, kept under the older filename.
    fig, ax = axes((9.4, 5.2))
    ax.plot(ks, [row["api_saving"] for row in amnesia], color=navy, marker="o", linewidth=2.2, label="API-equivalent")
    ax.plot(ks, [row["raw_prompt_saving"] for row in amnesia], color=clay, marker="o", linewidth=2.2, label="raw prompt")
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
    bars = ax.bar(labels, heights, color=[navy, clay, green], width=0.62)
    ax.axhline(1.0, color=ink, linewidth=1)
    title(ax, "Pauses change the answer")
    ax.set_ylabel("API cost, 967k / 250k", labelpad=8)
    ax.set_ylim(0, max(heights) * 1.18)
    for bar, height in zip(bars, heights):
        ax.text(bar.get_x() + bar.get_width() / 2, height + 0.06, f"{height:.2f}x", ha="center", va="bottom", fontsize=11)
    finish(fig, figures / "ttl_cliff.png")

    compound = simulate(replace(Assumptions(), window=250_000, pct=0.25))
    fig, ax = axes((9.4, 5.2))
    ax.plot(_context(hi), color=navy, linewidth=1.6, label="about 967k")
    ax.plot(_context(compound), color=clay, linewidth=1.6, label="250k window and 25% (62.5k)")
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
        color=navy,
        marker="o",
        linewidth=2.2,
        label="API-equivalent",
    )
    ax.plot(
        [0.1, 0.05, 0.025],
        [by_name[name]["raw_prompt_saving"] for name in reads],
        color=clay,
        marker="o",
        linewidth=2.2,
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
    ax.plot(_cumsum(short_hi, "raw_prompt"), color=navy, linewidth=2.4, label="about 967k")
    ax.plot(_cumsum(short_lo, "raw_prompt"), color=clay, linewidth=2.4, linestyle=(0, (1, 1.4)), label="250k")
    title(ax, "A short bugfix never reaches either trigger")
    ax.set_xlabel("Model request", labelpad=8)
    ax.set_ylabel("Cumulative prompt tokens", labelpad=8)
    ax.yaxis.set_major_formatter(millions)
    ax.legend(frameon=False, loc="upper left")
    finish(fig, figures / "short_session.png")

    fig, ax = axes((9.4, 5.2))
    triggers = [row["trigger"] for row in curve]
    ax.plot(triggers, [row["api"] for row in curve], color=navy, linewidth=2.2)
    best = summary["eoq"]["simulated_best_10k_grid"]["0.1"]
    ax.scatter([best["best_trigger"]], [best["best_api"]], color=clay, s=46, zorder=4)
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
    s1 = summary["s1"]
    print("S1 about-967k vs 25% of 1M (physical caching)")
    print(
        f"  API {s1['api_hi']:.0f} vs {s1['api_lo']:.0f} (saving {s1['api_saving']:.2f}x)"
    )
    print(
        f"  raw prompt {s1['prompt_hi']:.0f} vs {s1['prompt_lo']:.0f} "
        f"(saving {s1['raw_prompt_saving']:.2f}x)"
    )
    print(
        f"  compactions {s1['compactions_hi']} vs {s1['compactions_lo']}"
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
