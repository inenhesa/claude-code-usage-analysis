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

    figures = out / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    hi = simulate(Assumptions())
    lo = simulate(replace(Assumptions(), pct=0.25))

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(_cumsum(hi, "raw_prompt"), label="about 967k")
    ax.plot(_cumsum(lo, "raw_prompt"), label="25% of 1M (250k)")
    ax.set_title("S1 raw prompt tokens, physical prefix caching")
    ax.set_xlabel("Model request")
    ax.set_ylabel("Cumulative prompt tokens")
    ax.legend()
    fig.tight_layout()
    fig.savefig(figures / "cumulative_raw_tokens.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(_cumsum(hi, "api"), label="about 967k")
    ax.plot(_cumsum(lo, "api"), label="250k")
    ax.set_title("S1 API-equivalent units (reads at 0.1x, 1-hour writes at 2x)")
    ax.set_xlabel("Model request")
    ax.set_ylabel("Cumulative base-input-token units")
    ax.legend()
    fig.tight_layout()
    fig.savefig(figures / "cumulative_api_equivalent.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(_context(hi), label="about 967k", linewidth=1)
    ax.plot(_context(lo), label="250k", linewidth=1)
    ax.set_title("Context size at each request, S1")
    ax.set_xlabel("Model request")
    ax.set_ylabel("Prompt tokens")
    ax.legend()
    fig.tight_layout()
    fig.savefig(figures / "context_sawtooth.png")
    plt.close(fig)

    curve = summary["eoq"]["simulated_best_10k_grid"]["0.1"]["curve"]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for key, label in (("0.1", "reads 0.1x"), ("0.05", "reads 0.05x"), ("0.025", "reads 0.025x")):
        points = summary["eoq"]["simulated_best_10k_grid"][key]["curve"]
        ax.plot([p["trigger"] for p in points], [p["api"] for p in points], label=label)
    ax.set_title("API-equivalent cost versus trigger (EOQ sweep)")
    ax.set_xlabel("Trigger tokens")
    ax.set_ylabel("API-equivalent units")
    ax.legend()
    fig.tight_layout()
    fig.savefig(figures / "eoq_curve.png")
    plt.close(fig)

    amnesia = [row for row in summary["scenarios"] if row["name"].startswith("S3-")]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ks = [0, 3, 6, 10, 15]
    ax.plot(ks, [row["api_saving"] for row in amnesia], marker="o", label="API-equivalent")
    ax.plot(ks, [row["raw_prompt_saving"] for row in amnesia], marker="o", label="raw prompt")
    ax.axhline(1.0, color="black", linewidth=0.8)
    ax.set_title("Amnesia: extra rework calls per compaction, both arms")
    ax.set_xlabel("Rework calls of 8k tail after each compaction")
    ax.set_ylabel("High trigger cost / low trigger cost")
    ax.legend()
    fig.tight_layout()
    fig.savefig(figures / "amnesia_breakeven.png")
    fig.savefig(figures / "sensitivity_rework.png")
    plt.close(fig)

    names = ["S4", "S4b", "S4c"]
    labels = ["1h TTL, idle off", "1h TTL, idle on", "5m TTL, 7-min gaps"]
    by_name = {row["name"]: row for row in summary["scenarios"]}
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.bar(labels, [by_name[name]["api_saving"] for name in names])
    ax.axhline(1.0, color="black", linewidth=0.8)
    ax.set_title("TTL cliff: API saving of 967k versus 250k")
    ax.set_ylabel("High / low")
    fig.tight_layout()
    fig.savefig(figures / "ttl_cliff.png")
    plt.close(fig)

    compound = simulate(replace(Assumptions(), window=250_000, pct=0.25))
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(_context(hi), label="about 967k", linewidth=1)
    ax.plot(_context(compound), label="window 250k and 25% (62.5k)", linewidth=1)
    ax.set_title("Compounding: two knobs together are not a 250k experiment")
    ax.set_xlabel("Model request")
    ax.set_ylabel("Prompt tokens")
    ax.legend()
    fig.tight_layout()
    fig.savefig(figures / "compounding_sawtooth.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 4.5))
    reads = ["S7-0.1", "S7-0.05", "S7-0.025"]
    ax.plot(
        [0.1, 0.05, 0.025],
        [by_name[name]["api_saving"] for name in reads],
        marker="o",
        label="API-equivalent",
    )
    ax.plot(
        [0.1, 0.05, 0.025],
        [by_name[name]["raw_prompt_saving"] for name in reads],
        marker="o",
        label="raw prompt",
    )
    ax.set_title("Cheaper cache reads shrink the priced saving")
    ax.set_xlabel("Cache read multiplier")
    ax.set_ylabel("967k cost / 250k cost")
    ax.invert_xaxis()
    ax.legend()
    fig.tight_layout()
    fig.savefig(figures / "sensitivity_cache_hit.png")
    plt.close(fig)

    short_hi = simulate(Assumptions(user_turns=3, calls_per_turn=10))
    short_lo = simulate(replace(short_hi.assumptions, pct=0.25))
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(_cumsum(short_hi, "raw_prompt"), label="about 967k, no compaction")
    ax.plot(_cumsum(short_lo, "raw_prompt"), label="250k, also no compaction")
    ax.set_title("Short bugfix: both triggers do nothing, so the lines match")
    ax.set_xlabel("Model request")
    ax.set_ylabel("Cumulative prompt tokens")
    ax.legend()
    fig.tight_layout()
    fig.savefig(figures / "short_session.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 4.5))
    triggers = [row["trigger"] for row in curve]
    ax.plot(triggers, [row["api"] for row in curve])
    ax.set_title("Trigger sweep at the standard 0.1x read price")
    ax.set_xlabel("Trigger tokens")
    ax.set_ylabel("API-equivalent units")
    fig.tight_layout()
    fig.savefig(figures / "threshold_sweep.png")
    plt.close(fig)


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
