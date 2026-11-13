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


def build_summary() -> dict:
    scenarios = [run_scenario(scenario) for scenario in all_scenarios()]
    by_name = {row["name"]: row for row in scenarios}
    s1_hi = simulate(Assumptions())
    s1_lo = simulate(replace(Assumptions(), pct=0.25))
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
        "eoq": eoq_grid(),
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

    fig, ax = axes((9.4, 5.2))
    ax.plot(_context(hi), color=navy, linewidth=1.6, label="about 967k, 2 compactions")
    ax.plot(_context(lo), color=clay, linewidth=1.6, label="250k, 10 compactions")
    title(ax, "Context grows, then a summary cuts it back")
    ax.set_xlabel("Model request", labelpad=8)
    ax.set_ylabel("Prompt size", labelpad=8)
    ax.yaxis.set_major_formatter(tokens)
    ax.set_ylim(0, 1_150_000)
    legend_side(ax)
    finish(fig, figures / "context_sawtooth.png")

    curve = summary["eoq"]["simulated_best_10k_grid"]["0.1"]["curve"]
    fig, ax = axes((9.4, 5.4))
    series = (
        ("0.1", "reads at 0.1x", navy),
        ("0.05", "reads at 0.05x", clay),
        ("0.025", "reads at 0.025x", green),
    )
    for key, label, color in series:
        block = summary["eoq"]["simulated_best_10k_grid"][key]
        points = block["curve"]
        ax.plot(
            [p["trigger"] for p in points],
            [p["api"] for p in points],
            color=color,
            linewidth=2.2,
            label=label,
        )
        ax.scatter([block["best_trigger"]], [block["best_api"]], color=color, s=42, zorder=4)
    title(ax, "The cheap trigger is a valley, not 25% of 1M")
    ax.set_xlabel("Compaction trigger", labelpad=8)
    ax.set_ylabel("API-equivalent cost of the session", labelpad=8)
    ax.xaxis.set_major_formatter(tokens)
    ax.yaxis.set_major_formatter(millions)
    ax.set_xticks([100_000, 150_000, 200_000, 250_000, 300_000, 400_000])
    legend_side(ax)
    finish(fig, figures / "eoq_curve.png")

    compare = [
        ("Long session, 250k", "S1"),
        ("T3 window, about 217k", "S1b"),
        ("Short bugfix", "S2"),
        ("Bugfix forced to 100k", "S2b"),
        ("15 repairs per compact", "S3-15"),
        ("Idle compaction on", "S4b"),
        ("Both knobs, 62.5k", "S8"),
        ("62.5k versus 250k alone", "S8b"),
    ]
    fig, ax = axes((9.6, 5.8))
    values = [by_name[name]["api_saving"] for _, name in compare]
    colors = [navy if value >= 1 else clay for value in values]
    positions = list(range(len(compare)))
    ax.barh(positions, values, color=colors, height=0.62)
    ax.axvline(1.0, color=ink, linewidth=1)
    ax.set_yticks(positions)
    ax.set_yticklabels([label for label, _ in compare])
    ax.invert_yaxis()
    ax.set_xlim(0, 2.85)
    ax.set_xlabel("API cost of the higher trigger, divided by the lower one", labelpad=8)
    title(ax, "Earlier compaction helps only some sessions")
    for index, value in enumerate(values):
        ax.text(value + 0.06, index, f"{value:.2f}x", va="center", ha="left", fontsize=11, color=ink)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color="#eef0f2", linewidth=1)
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
