"""Run the compaction model and write plots plus a JSON summary.

The numbers are consequences of the assumptions in ``model.py``. They are not
measurements of a Claude account.
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import replace
from pathlib import Path

from cc_usage_study.model import (
    DEFAULT_ASSUMPTIONS,
    OFFICIAL_ABOUT_967K,
    SHORT_ASSUMPTIONS,
    Assumptions,
    SimulationResult,
    compare,
    simulate,
    with_threshold,
)

# Fractions of the context window used in the headline comparison.
# 0.95 is the informal figure from the LinkedIn post. It is not a published
# Claude Code default. The published approximation for native 1M models is
# about 967K tokens, reported separately.
THRESHOLD_FRACTIONS = (0.95, 0.75, 0.50, 0.25, 0.10)

COLORS = {
    0.95: "#b45309",
    0.75: "#c2410c",
    0.50: "#0369a1",
    0.25: "#047857",
    0.10: "#6d28d9",
}


def _series(result: SimulationResult) -> list[dict[str, float | int | str]]:
    prompt = 0
    cost_units = 0.0
    a = result.assumptions
    rows = []
    productive_seen = 0
    for call in result.calls:
        prompt += call.prompt_tokens
        cost_units += (
            call.input_tokens
            + call.cache_read_tokens * a.cache_read_multiplier
            + call.cache_write_tokens * a.cache_write_multiplier
            + call.output_tokens * a.output_multiplier
        )
        if call.kind == "turn":
            productive_seen += 1
        rows.append(
            {
                "kind": call.kind,
                "productive_turn": call.productive_turn,
                "prompt_tokens": call.prompt_tokens,
                "cumulative_prompt_tokens": prompt,
                "cumulative_api_equivalent": cost_units,
                "history_after": call.history_after,
                "productive_seen": productive_seen,
            }
        )
    return rows


def run_fraction_sweep(base: Assumptions, fractions: tuple[float, ...] = THRESHOLD_FRACTIONS) -> list[SimulationResult]:
    results = []
    for fraction in fractions:
        scenario = with_threshold(base, fraction=fraction)
        scenario = replace(scenario, label=f"{base.label or 'scenario'}@{fraction:.2f}")
        results.append(simulate(scenario))
    return results


def build_summary() -> dict:
    long_results = run_fraction_sweep(DEFAULT_ASSUMPTIONS)
    by_fraction = {f"{result.assumptions.threshold_fraction:.2f}": result.totals() for result in long_results}
    low = next(result for result in long_results if abs(result.assumptions.threshold_fraction - 0.25) < 1e-9)
    high = next(result for result in long_results if abs(result.assumptions.threshold_fraction - 0.95) < 1e-9)
    official_low = simulate(with_threshold(DEFAULT_ASSUMPTIONS, tokens=250_000))
    official_high = simulate(
        replace(with_threshold(DEFAULT_ASSUMPTIONS, tokens=OFFICIAL_ABOUT_967K), label="about_967k")
    )
    read_05 = replace(DEFAULT_ASSUMPTIONS, cache_read_multiplier=0.05, label="long_session_read_0.05")
    read_05_low = simulate(with_threshold(read_05, fraction=0.25))
    read_05_high = simulate(with_threshold(read_05, fraction=0.95))
    uncached = replace(DEFAULT_ASSUMPTIONS, caching=False, label="long_session_uncached")
    uncached_low = simulate(with_threshold(uncached, fraction=0.25))
    uncached_high = simulate(with_threshold(uncached, fraction=0.95))
    short_results = run_fraction_sweep(SHORT_ASSUMPTIONS)
    short_low = next(result for result in short_results if abs(result.assumptions.threshold_fraction - 0.25) < 1e-9)
    short_high = next(result for result in short_results if abs(result.assumptions.threshold_fraction - 0.95) < 1e-9)

    hit_rows = []
    for hit in (0.0, 0.25, 0.5, 0.75, 0.9, 0.95, 1.0):
        base = replace(DEFAULT_ASSUMPTIONS, cache_hit_rate=hit, label=f"hit_{hit}")
        pair_low = simulate(with_threshold(base, fraction=0.25))
        pair_high = simulate(with_threshold(base, fraction=0.95))
        hit_rows.append({"cache_hit_rate": hit, **compare(pair_low, pair_high)})

    rework_rows = []
    for rework in range(0, 9):
        base = replace(
            DEFAULT_ASSUMPTIONS,
            rework_turns_per_compaction=rework,
            label=f"rework_{rework}",
        )
        pair_low = simulate(with_threshold(base, fraction=0.25))
        pair_high = simulate(with_threshold(base, fraction=0.95))
        rework_rows.append({"rework_turns_per_compaction": rework, **compare(pair_low, pair_high)})

    return {
        "disclaimer": (
            "Model output under stated assumptions. Not a measurement of any "
            "Claude account, and not a prediction of the five-hour or weekly "
            "usage meter. Anthropic has not published meter weights."
        ),
        "default_assumptions": {
            "context_window": DEFAULT_ASSUMPTIONS.context_window,
            "turns": DEFAULT_ASSUMPTIONS.turns,
            "growth_per_turn": DEFAULT_ASSUMPTIONS.growth_per_turn,
            "output_per_turn": DEFAULT_ASSUMPTIONS.output_per_turn,
            "system_prompt_tokens": DEFAULT_ASSUMPTIONS.system_prompt_tokens,
            "project_context_tokens": DEFAULT_ASSUMPTIONS.project_context_tokens,
            "summary_tokens": DEFAULT_ASSUMPTIONS.summary_tokens,
            "reinjected_tokens": DEFAULT_ASSUMPTIONS.reinjected_tokens,
            "cache_hit_rate": DEFAULT_ASSUMPTIONS.cache_hit_rate,
            "cache_read_multiplier": DEFAULT_ASSUMPTIONS.cache_read_multiplier,
            "cache_write_multiplier": DEFAULT_ASSUMPTIONS.cache_write_multiplier,
            "output_multiplier": DEFAULT_ASSUMPTIONS.output_multiplier,
            "rework_turns_per_compaction": DEFAULT_ASSUMPTIONS.rework_turns_per_compaction,
            "notes": [
                "Token sizes except the documented about-967K trigger are assumptions.",
                "cache_read_multiplier 0.1 is the standard published cache-hit rate. Opus 5.5 and Sonnet 5.5 are 0.05. Fable 5.2 is 0.025.",
                "cache_write_multiplier 2.0 is the published 1-hour cache-write rate. Claude Code requests a 1-hour TTL on the main conversation for subscription usage that is still inside the plan.",
                "output_multiplier 5 matches the published output/input price ratio for several current models, including Opus 5.5 (20/4) and Sonnet 5.5 (10/2). It is not universal.",
                "With caching on, every prompt token is either a cache read or a cache write. Real calls also report a small uncached input_tokens tail.",
            ],
        },
        "long_session": {
            "by_threshold_fraction": by_fraction,
            "ratio_0.25_over_0.95": compare(low, high),
        },
        "about_967k_versus_250k": {
            "note": (
                "250000 is 25% of a 1M window. 967000 is the documentation's "
                "'about 967K' default trigger for native 1M models, not a 95% setting."
            ),
            "low": official_low.totals(),
            "high": official_high.totals(),
            "ratio_250k_over_967k": compare(official_low, official_high),
        },
        "long_session_cache_read_0.05": {
            "note": "Same assumptions, cache reads priced at 0.05x, the published Opus 5.5 and Sonnet 5.5 rate.",
            "ratio_0.25_over_0.95": compare(read_05_low, read_05_high),
        },
        "long_session_uncached": {
            "note": "Caching off. Every prompt token is billed as fresh input. This is a bound, not Claude Code's default.",
            "ratio_0.25_over_0.95": compare(uncached_low, uncached_high),
        },
        "short_session": {
            "assumptions": {
                "turns": SHORT_ASSUMPTIONS.turns,
                "growth_per_turn": SHORT_ASSUMPTIONS.growth_per_turn,
                "output_per_turn": SHORT_ASSUMPTIONS.output_per_turn,
            },
            "ratio_0.25_over_0.95": compare(short_low, short_high),
            "low": short_low.totals(),
            "high": short_high.totals(),
        },
        "sensitivity_cache_hit_rate": hit_rows,
        "sensitivity_rework": rework_rows,
    }


def _write_grid(path: Path, summary: dict) -> None:
    rows = []
    for key, totals in summary["long_session"]["by_threshold_fraction"].items():
        rows.append({"scenario": "long_session", "threshold_fraction": key, **_flat(totals)})
    with path.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = list(rows[0].keys())
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _flat(totals: dict) -> dict:
    flat = {}
    for key, value in totals.items():
        if key == "warnings":
            flat[key] = "; ".join(value)
        else:
            flat[key] = value
    return flat


def _write_series(path: Path, results: list[SimulationResult]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "threshold_fraction",
            "kind",
            "productive_turn",
            "productive_seen",
            "prompt_tokens",
            "cumulative_prompt_tokens",
            "cumulative_api_equivalent",
            "history_after",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            fraction = result.assumptions.threshold_fraction
            for row in _series(result):
                writer.writerow({"threshold_fraction": f"{fraction:.2f}", **row})


def write_figures(directory: Path, summary: dict) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg", force=False)
    import matplotlib.pyplot as plt

    directory.mkdir(parents=True, exist_ok=True)
    long_results = run_fraction_sweep(DEFAULT_ASSUMPTIONS)
    written: list[Path] = []

    def save(fig: object, name: str) -> None:
        target = directory / name
        fig.savefig(target, dpi=140)  # type: ignore[attr-defined]
        plt.close(fig)  # type: ignore[arg-type]
        written.append(target)

    fig, ax = plt.subplots(figsize=(8.2, 4.8))
    for result in long_results:
        fraction = result.assumptions.threshold_fraction
        rows = [row for row in _series(result) if row["kind"] == "turn"]
        ax.plot(
            [row["productive_seen"] for row in rows],
            [row["cumulative_prompt_tokens"] / 1_000_000 for row in rows],
            color=COLORS.get(fraction, "#334155"),
            label=f"{fraction:.0%} of window",
            linewidth=2,
        )
    ax.set_xlabel("Productive turns")
    ax.set_ylabel("Cumulative prompt tokens (millions)")
    ax.set_title("Long session: raw prompt tokens vs compaction threshold")
    ax.legend(frameon=False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    save(fig, "cumulative_raw_tokens.png")

    fig, ax = plt.subplots(figsize=(8.2, 4.8))
    for result in long_results:
        fraction = result.assumptions.threshold_fraction
        rows = [row for row in _series(result) if row["kind"] == "turn"]
        ax.plot(
            [row["productive_seen"] for row in rows],
            [row["cumulative_api_equivalent"] / 1_000_000 for row in rows],
            color=COLORS.get(fraction, "#334155"),
            label=f"{fraction:.0%} of window",
            linewidth=2,
        )
    ax.set_xlabel("Productive turns")
    ax.set_ylabel("API-equivalent cost (millions of base input tokens)")
    ax.set_title("Same session, priced with cache reads at 0.1x and 1-hour writes at 2x")
    ax.legend(frameon=False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    save(fig, "cumulative_api_equivalent.png")

    fig, axes = plt.subplots(1, 2, figsize=(8.6, 4.4))
    fractions = []
    prompts = []
    costs = []
    for result in long_results:
        fractions.append(result.assumptions.threshold_fraction * 100)
        prompts.append(result.total_prompt_tokens / 1_000_000)
        costs.append(result.api_equivalent_cost() / 1_000_000)
    axes[0].bar([str(int(item)) for item in fractions], prompts, color="#0f766e")
    axes[0].set_xlabel("Threshold, percent of 1M window")
    axes[0].set_ylabel("Prompt tokens (millions)")
    axes[0].set_title("Raw prompt total")
    axes[1].bar([str(int(item)) for item in fractions], costs, color="#1d4ed8")
    axes[1].set_xlabel("Threshold, percent of 1M window")
    axes[1].set_ylabel("API-equivalent (millions of base tokens)")
    axes[1].set_title("Cache-priced total")
    for axis in axes:
        axis.spines["top"].set_visible(False)
        axis.spines["right"].set_visible(False)
    fig.tight_layout()
    save(fig, "threshold_sweep.png")

    fig, ax = plt.subplots(figsize=(8.2, 4.8))
    for fraction in (0.95, 0.25):
        result = next(item for item in long_results if abs(item.assumptions.threshold_fraction - fraction) < 1e-9)
        rows = [row for row in _series(result) if row["kind"] == "turn"]
        ax.plot(
            [row["productive_seen"] for row in rows],
            [row["history_after"] / 1_000 for row in rows],
            color=COLORS[fraction],
            label=f"{fraction:.0%} threshold",
            linewidth=2,
        )
    ax.set_xlabel("Productive turns")
    ax.set_ylabel("Context after the turn (thousands of tokens)")
    ax.set_title("Context size sawtooth at 25% and 95%")
    ax.legend(frameon=False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    save(fig, "context_sawtooth.png")

    fig, ax = plt.subplots(figsize=(8.2, 4.8))
    hits = summary["sensitivity_cache_hit_rate"]
    ax.plot(
        [row["cache_hit_rate"] for row in hits],
        [row["prompt_tokens_ratio"] for row in hits],
        color="#0f766e",
        marker="o",
        label="Raw prompt tokens",
    )
    ax.plot(
        [row["cache_hit_rate"] for row in hits],
        [row["api_equivalent_ratio"] for row in hits],
        color="#1d4ed8",
        marker="o",
        label="API-equivalent cost",
    )
    ax.axhline(1.0, color="#94a3b8", linewidth=1)
    ax.set_xlabel("Cache hit rate on the stable prefix")
    ax.set_ylabel("25% threshold divided by 95% threshold")
    ax.set_title("Savings survive cache hits in the raw total, and shrink in the price")
    ax.set_ylim(0, max(1.15, max(row["api_equivalent_ratio"] for row in hits) * 1.15))
    ax.legend(frameon=False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    save(fig, "sensitivity_cache_hit.png")

    fig, ax = plt.subplots(figsize=(8.2, 4.8))
    rework = summary["sensitivity_rework"]
    ax.plot(
        [row["rework_turns_per_compaction"] for row in rework],
        [row["prompt_tokens_ratio"] for row in rework],
        color="#0f766e",
        marker="o",
        label="Raw prompt tokens",
    )
    ax.plot(
        [row["rework_turns_per_compaction"] for row in rework],
        [row["api_equivalent_ratio"] for row in rework],
        color="#1d4ed8",
        marker="o",
        label="API-equivalent cost",
    )
    ax.axhline(1.0, color="#94a3b8", linewidth=1)
    ax.set_xlabel("Extra rework turns after every compaction")
    ax.set_ylabel("25% threshold divided by 95% threshold")
    ax.set_title("Rework can spend the savings from compacting earlier")
    ax.legend(frameon=False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    save(fig, "sensitivity_rework.png")

    short_results = run_fraction_sweep(SHORT_ASSUMPTIONS)
    fig, ax = plt.subplots(figsize=(8.2, 4.8))
    labels = []
    values = []
    colors = []
    for result in short_results:
        fraction = result.assumptions.threshold_fraction
        labels.append(f"{fraction:.0%}")
        values.append(result.total_prompt_tokens / 1_000_000)
        colors.append(COLORS.get(fraction, "#334155"))
    ax.bar(labels, values, color=colors)
    ax.set_xlabel("Threshold")
    ax.set_ylabel("Prompt tokens (millions)")
    ax.set_title("Short session: 95% never compacts, and the raw-token gap is much smaller")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    save(fig, "short_session.png")
    return written


def write_outputs(out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = build_summary()
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    _write_grid(out_dir / "grid.csv", summary)
    _write_series(out_dir / "series_long.csv", run_fraction_sweep(DEFAULT_ASSUMPTIONS))
    figures = write_figures(out_dir / "figures", summary)
    summary["figures"] = [str(path) for path in figures]
    return summary


def _format_report(summary: dict) -> str:
    long_ratio = summary["long_session"]["ratio_0.25_over_0.95"]
    official = summary["about_967k_versus_250k"]["ratio_250k_over_967k"]
    short_ratio = summary["short_session"]["ratio_0.25_over_0.95"]
    read_05 = summary["long_session_cache_read_0.05"]["ratio_0.25_over_0.95"]
    lines = [
        "Model results (assumptions, not measurements)",
        "",
        "Long session, 25% of 1M vs 95% of 1M:",
        f"  prompt tokens: {long_ratio['prompt_tokens_low']:,} vs {long_ratio['prompt_tokens_high']:,} "
        f"(ratio {long_ratio['prompt_tokens_ratio']:.3f})",
        f"  API-equivalent: {long_ratio['api_equivalent_low']:,.0f} vs {long_ratio['api_equivalent_high']:,.0f} "
        f"(ratio {long_ratio['api_equivalent_ratio']:.3f})",
        f"  compactions: {long_ratio['compaction_count_low']} vs {long_ratio['compaction_count_high']}",
        "",
        "250k vs the documented about-967k trigger:",
        f"  prompt ratio {official['prompt_tokens_ratio']:.3f}, "
        f"API-equivalent ratio {official['api_equivalent_ratio']:.3f}",
        "",
        "Same long session with cache reads at 0.05x (Opus 5.5 / Sonnet 5.5 list price):",
        f"  API-equivalent ratio {read_05['api_equivalent_ratio']:.3f} "
        f"(raw prompt ratio stays {read_05['prompt_tokens_ratio']:.3f})",
        "",
        "Short session, 25% vs 95%:",
        f"  prompt ratio {short_ratio['prompt_tokens_ratio']:.3f} "
        f"(compactions {short_ratio['compaction_count_low']} vs {short_ratio['compaction_count_high']})",
        "",
        summary["disclaimer"],
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Simulate cumulative tokens vs compaction threshold and write plots.")
    parser.add_argument("--out", type=Path, default=Path("outputs"), help="Directory for summary.json, csv, and figures")
    args = parser.parse_args(argv)
    summary = write_outputs(args.out)
    print(_format_report(summary))
    print(f"\nWrote {args.out / 'summary.json'} and figures under {args.out / 'figures'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
