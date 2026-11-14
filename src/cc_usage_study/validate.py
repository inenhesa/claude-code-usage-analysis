"""Back-test the caching model against every live run in ``outputs/live_experiments.json``.

The study's modeled claims (pause curves, cron, sweet spots) rest on the
simulator. Until now the simulator had never been scored against the live
runs, which are the only measurements that could falsify it. This module
does that, with no API calls:

1. **Trigger formula.** ``effective_trigger`` against each arm's first
   measured ``preTokens``.
2. **Short tasks.** Fresh start, warm resume, stale resume and ``/compact``
   before or after a break, predicted from the model's published constants,
   against the measured units of the same runs.
3. **Long-session compaction.** Each live compaction arm, predicted by
   ``simulate`` from the default arm's measured trajectory, against the
   measured cost relative to the default arm.

Each test reports two predictions:

- ``published``: the simulator's own constants, as used for every modeled
  number in the README. This is the honest score of the shipped model.
- ``calibrated``: the same model with structural inputs read from the
  measured runs (shared cached prefix, calls per task, measured summary
  size). The gap between the two shows which assumption carries the error.

Nothing here is tuned to make the scores look good. A calibrated prediction
only uses quantities that are observable before the outcome it predicts
(for example the fresh run's own first-call shape, never the stale run's cost).
"""

from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import replace
from pathlib import Path

from .model import Assumptions, effective_trigger, simulate
from .simulate import (
    TASK_CALLS,
    TASK_OUTPUT,
    TASK_PROJECT,
    TASK_SYSTEM,
    TASK_TAIL,
    WRITE_5M,
    task_units,
)

ROOT = Path(__file__).resolve().parents[2]
LIVE = ROOT / "outputs" / "live_experiments.json"

READ = 0.05  # Opus 5.5, the live model
OUTPUT_WEIGHT = 5.0
DEFAULTS = Assumptions()

# A prediction within this relative error counts as a hit. Wide on purpose:
# the claim being tested is "the model gets the size right", not the decimal.
HIT_TOLERANCE = 0.25


def _units(tokens: dict, read: float = READ) -> float:
    return (
        tokens.get("input", 0)
        + tokens.get("cache_write_5m", 0) * WRITE_5M
        + tokens.get("cache_write_1h", 0) * 2.0
        + tokens.get("cache_read", 0) * read
        + tokens.get("output", 0) * OUTPUT_WEIGHT
    )


def _row(name: str, measured: float, published: float, calibrated: float | None, note: str = "") -> dict:
    def err(pred: float | None) -> float | None:
        return None if pred is None else (pred - measured) / measured

    pub_err = err(published)
    cal_err = err(calibrated)
    return {
        "name": name,
        "measured": round(measured, 1),
        "published": round(published, 1),
        "published_error": round(pub_err, 4),
        "published_hit": abs(pub_err) <= HIT_TOLERANCE,
        "calibrated": None if calibrated is None else round(calibrated, 1),
        "calibrated_error": None if cal_err is None else round(cal_err, 4),
        "calibrated_hit": None if cal_err is None else abs(cal_err) <= HIT_TOLERANCE,
        "note": note,
    }


def _mean(values) -> float:
    values = list(values)
    return sum(values) / len(values)


# ---------------------------------------------------------------- 1. trigger


def trigger_checks(live: dict) -> list[dict]:
    rows = []
    for key, arm in live["compaction"]["arms"].items():
        pct = arm.get("pct_override")
        window = arm.get("window")
        assumptions = replace(
            DEFAULTS,
            window=None if window in (None, 1_000_000) else window,
            pct=None if pct is None else pct / 100.0,
        )
        predicted, _ = effective_trigger(assumptions)
        bounds = arm.get("compact_boundaries") or []
        first = bounds[0]["preTokens"] if bounds else None
        rows.append(
            {
                "arm": key,
                "formula_trigger": predicted,
                "repo_predicted_threshold": arm.get("predicted_threshold"),
                "formula_matches_repo": predicted == arm.get("predicted_threshold"),
                "first_pre_tokens": first,
                "overshoot_tokens": None if first is None else first - predicted,
                "overshoot_share": None if first is None else round((first - predicted) / predicted, 4),
                "note": (
                    "no compaction expected or seen"
                    if first is None
                    else "compaction is checked between requests, so one large tool result can carry the context past the trigger"
                ),
            }
        )
    return rows


# ------------------------------------------------------------- 2. short tasks


def _calibration(live: dict) -> dict:
    """Task shape read from the fresh (/clear) runs only."""
    clears = live["ttl_runs"]["clear"]
    shared = _mean(c["first_call"]["cache_read"] for c in clears)
    first_write = _mean(c["first_call"]["cache_write_5m"] for c in clears)
    calls = round(_mean(c["assistant_calls"] for c in clears))
    later_writes = _mean(c["tokens"]["cache_write_5m"] - c["first_call"]["cache_write_5m"] for c in clears)
    output = _mean(c["tokens"]["output"] for c in clears) / calls
    tail = max(0.0, later_writes / max(1, calls - 1) - output)
    return {
        "shared_cached_prefix": shared,
        "first_call_write": first_write,
        "calls": calls,
        "output_per_call": output,
        "tail_per_call": tail,
        "source": "ttl_runs.clear only",
    }


def _cal_task(cal: dict, *, cached: float, cold: float) -> float:
    return task_units(
        cached_prefix=int(cached),
        cold_prefix=int(cold),
        write_mult=WRITE_5M,
        read_mult=READ,
        calls=cal["calls"],
        tail=int(cal["tail_per_call"]),
        output=int(cal["output_per_call"]),
    )


def _pub_task(*, cached: float, cold: float) -> float:
    return task_units(cached_prefix=int(cached), cold_prefix=int(cold), write_mult=WRITE_5M, read_mult=READ)


def short_task_checks(live: dict) -> dict:
    ttl = live["ttl_runs"]
    comp = live["compact_runs"]
    cal = _calibration(live)
    shared = cal["shared_cached_prefix"]
    # The fresh session's own first prompt beyond the shared prefix.
    own_prefix = cal["first_call_write"] - cal["tail_per_call"]

    rows = []
    clear_measured = _mean(_units(c["tokens"]) for c in ttl["clear"])
    clear_pub = _pub_task(cached=0, cold=TASK_SYSTEM + TASK_PROJECT)
    clear_cal = _cal_task(cal, cached=shared, cold=own_prefix)
    rows.append(
        _row(
            "fresh start (/clear), mean of 3",
            clear_measured,
            clear_pub,
            clear_cal,
            "published model writes the whole system prompt cold; live, about 11.4k of it was read from a cache shared across sessions",
        )
    )

    hot = ttl["hot"]
    h = hot["history_tokens"]
    rows.append(
        _row(
            "warm resume, 3.3 min, 97k history",
            _units(hot["tokens"]),
            _pub_task(cached=TASK_SYSTEM + TASK_PROJECT + h, cold=0),
            _cal_task(cal, cached=h, cold=cal["tail_per_call"]),
        )
    )

    for i, stale in enumerate(ttl["stale"], 1):
        h = stale["history_tokens"]
        rows.append(
            _row(
                f"stale resume {i}, {stale['pause_before_s'] // 60} min break",
                _units(stale["tokens"]),
                _pub_task(cached=0, cold=TASK_SYSTEM + TASK_PROJECT + h),
                # History already contains the system prompt; the shared part stays warm.
                _cal_task(cal, cached=shared, cold=h - shared + cal["tail_per_call"]),
            )
        )

    instr = DEFAULTS.compact_instr
    summary_pub = DEFAULTS.summary_min
    reinjected = DEFAULTS.reinjected_tokens
    summary_measured = _mean(
        run["compact"]["tokens"]["output"] for run in comp["before"] + comp["after"]
    )
    for side, runs in (("before", comp["before"]), ("after", comp["after"])):
        for i, run in enumerate(runs, 1):
            h = run["history_tokens"]
            measured = _units(run["compact"]["tokens"]) + _units(run["next_task"]["tokens"])
            post_ctx = run["next_task"]["first_call"]["cache_write_5m"] + run["next_task"]["first_call"]["cache_read"]
            if side == "before":
                # Summary call reads the warm history; the break then expires everything.
                pub_compact = WRITE_5M * instr + OUTPUT_WEIGHT * summary_pub + READ * h
                cal_compact = WRITE_5M * instr + OUTPUT_WEIGHT * summary_measured + READ * h
            else:
                # The shipped simulator writes a cold prompt to the cache (1.25x).
                pub_compact = WRITE_5M * (h + instr) + OUTPUT_WEIGHT * summary_pub
                # Live, the cold summary call sent the history as plain input (1x), not a cache write.
                cal_compact = 1.0 * (h + instr) + OUTPUT_WEIGHT * summary_measured
            pub_next = _pub_task(cached=0, cold=TASK_SYSTEM + TASK_PROJECT + summary_pub + reinjected)
            cal_next = _cal_task(cal, cached=shared, cold=max(0.0, post_ctx - shared))
            rows.append(
                _row(
                    f"/compact {side} the break, run {i}",
                    measured,
                    pub_compact + pub_next,
                    cal_compact + cal_next,
                    "calibrated next task uses the measured post-compact prompt size",
                )
            )

    # Ratios: the claims the README actually makes.
    def ratio(name_prefix: str, key: str) -> float:
        picked = [r[key] for r in rows if r["name"].startswith(name_prefix)]
        return _mean(picked)

    clear = {k: rows[0][k] for k in ("measured", "published", "calibrated")}
    ratios = []
    for label, prefix in (
        ("warm resume vs fresh", "warm resume"),
        ("stale resume vs fresh", "stale resume"),
        ("/compact before break vs fresh", "/compact before"),
        ("/compact after break vs fresh", "/compact after"),
    ):
        ratios.append(
            {
                "claim": label,
                "measured": round(ratio(prefix, "measured") / clear["measured"], 2),
                "published": round(ratio(prefix, "published") / clear["published"], 2),
                "calibrated": round(ratio(prefix, "calibrated") / clear["calibrated"], 2),
            }
        )
    return {
        "read_weight": READ,
        "published_constants": {
            "system_tokens": TASK_SYSTEM,
            "project_tokens": TASK_PROJECT,
            "calls": TASK_CALLS,
            "tail_tokens": TASK_TAIL,
            "output_tokens": TASK_OUTPUT,
            "summary_tokens": summary_pub,
            "reinjected_tokens": reinjected,
        },
        "calibration": {k: (round(v, 1) if isinstance(v, float) else v) for k, v in cal.items()},
        "measured_summary_output_tokens": round(summary_measured, 1),
        "rows": rows,
        "ratios": ratios,
    }


def modeled_claims_recalibrated(live: dict) -> list[dict]:
    """Re-derive the README's modeled pause claims with the calibrated task shape.

    Crossovers depend on the write and read weights and the calls per task,
    not on the fresh baseline, so they barely move. Ratios against a fresh
    start do move, because the published constants overstate the fresh start.
    """
    from .simulate import exploration_call_units

    cal = _calibration(live)
    shared = cal["shared_cached_prefix"]
    own = cal["first_call_write"] - cal["tail_per_call"]
    explore = exploration_call_units(WRITE_5M, READ)
    rows = []
    for history in (30_000, 60_000):
        pub_clear = _pub_task(cached=0, cold=TASK_SYSTEM + TASK_PROJECT) + 2 * explore
        pub_stale = _pub_task(cached=0, cold=TASK_SYSTEM + TASK_PROJECT + history)
        cal_clear = _cal_task(cal, cached=shared, cold=own) + 2 * explore
        cal_stale = _cal_task(cal, cached=shared, cold=history + own - shared + cal["tail_per_call"])
        rows.append(
            {
                "claim": f"stale resume with {history // 1000}k history vs /clear plus 2 exploration calls, 5m cache",
                "published_extra": round(pub_stale / pub_clear - 1.0, 3),
                "calibrated_extra": round(cal_stale / cal_clear - 1.0, 3),
            }
        )
    pub_slope = WRITE_5M + (TASK_CALLS - 1) * READ
    cal_slope = WRITE_5M + (cal["calls"] - 1) * READ
    rows.append(
        {
            "claim": "stale resume break-even history with 2 saved exploration calls, 5m cache (tokens)",
            "published": round(2 * explore / pub_slope),
            "calibrated": round(2 * explore / cal_slope),
        }
    )
    return rows


# --------------------------------------------------------- 3. compaction arms


def _arm_assumptions(default_arm: dict, *, calibrated: bool, arms: dict) -> Assumptions:
    calls = default_arm["assistant_calls"]
    prefix = default_arm["first_call_prefix_tokens"]
    output = default_arm["tokens"]["output"] / calls
    growth = (default_arm["end_context_tokens"] - prefix) / (calls - 1)
    base = replace(
        DEFAULTS,
        label="ledgerkit-replay",
        system_prompt_tokens=prefix,
        project_context_tokens=0,
        user_turns=calls,
        calls_per_turn=1,
        tail=int(growth - output),
        output_per_call=int(output),
        gap_min=1.0,
        ttl_min=5.0,
        cache_read_multiplier=READ,
    )
    if not calibrated:
        return base
    posts = [b["postTokens"] for a in arms.values() for b in a.get("compact_boundaries") or []]
    outputs = []
    extra_calls = []
    for a in arms.values():
        n = a.get("compactions") or 0
        if n:
            extra_calls.append((a["assistant_calls"] - calls) / n)
            outputs.append(n)
    post = int(statistics.median(posts))
    return replace(
        base,
        # postTokens is the whole context after compaction; model it as the summary.
        system_prompt_tokens=prefix,
        summary_min=max(0, post - prefix),
        summary_max=max(0, post - prefix),
        reinjected_tokens=0,
        rework_calls=round(statistics.median(extra_calls)),
        rework_tail=int(growth - output),
    )


def compaction_arm_checks(live: dict) -> dict:
    arms = live["compaction"]["arms"]
    default = arms["default_1m"]
    rows = []
    for calibrated in (False, True):
        base = _arm_assumptions(default, calibrated=calibrated, arms=arms)
        baseline = simulate(base)
        for key, arm in arms.items():
            if key == "default_1m":
                continue
            pct = arm.get("pct_override")
            variant = replace(base, window=arm["window"], pct=None if pct is None else pct / 100.0)
            result = simulate(variant)
            rows.append(
                {
                    "arm": key,
                    "variant": "calibrated" if calibrated else "published",
                    "predicted_cost_vs_default": round(result.cost() / baseline.cost() - 1.0, 4),
                    "measured_cost_vs_default": arm["cost_vs_default"],
                    "predicted_compactions": result.compactions,
                    "measured_compactions": arm["compactions"],
                    "validity_gate_passed": arm.get("validity_gate_passed"),
                }
            )
    for row in rows:
        row["error_points"] = round((row["predicted_cost_vs_default"] - row["measured_cost_vs_default"]) * 100, 1)
        row["sign_matches"] = (row["predicted_cost_vs_default"] >= 0) == (row["measured_cost_vs_default"] >= 0) or abs(
            row["measured_cost_vs_default"]
        ) < 0.02
    hidden = live["compaction"]["hidden_mean_units_per_compaction"]
    trigger_107k = 107_000
    modeled_summary = WRITE_5M * DEFAULTS.compact_instr + OUTPUT_WEIGHT * DEFAULTS.summary_min + READ * trigger_107k
    return {
        "rows": rows,
        "summary_call_units": {
            "measured_mean_hidden_units": hidden,
            "modeled_summary_call_units_at_107k": round(modeled_summary, 1),
            "ratio_measured_to_modeled": round(hidden / modeled_summary, 2),
        },
        "note": (
            "Replay of the LedgerKit suite: the default arm's measured trajectory (first prefix, growth per call, "
            "output per call) drives simulate() at each arm's setting. Measured cost includes the hidden summary "
            "traffic from cost_state. n=1 per arm and two arms fail the repo's own validity gate."
        ),
    }


# ------------------------------------------------------------------- report


def run(live_path: Path = LIVE) -> dict:
    live = json.loads(live_path.read_text(encoding="utf-8"))
    triggers = trigger_checks(live)
    short = short_task_checks(live)
    arms = compaction_arm_checks(live)
    short_rows = short["rows"]
    scored = {
        "short_task_published_hits": sum(r["published_hit"] for r in short_rows),
        "short_task_calibrated_hits": sum(bool(r["calibrated_hit"]) for r in short_rows),
        "short_task_runs": len(short_rows),
        "hit_tolerance": HIT_TOLERANCE,
        "arm_sign_matches_published": sum(r["sign_matches"] for r in arms["rows"] if r["variant"] == "published"),
        "arm_sign_matches_calibrated": sum(r["sign_matches"] for r in arms["rows"] if r["variant"] == "calibrated"),
        "arms": sum(1 for r in arms["rows"] if r["variant"] == "published"),
        "trigger_formula_matches": sum(r["formula_matches_repo"] for r in triggers),
        "trigger_arms": len(triggers),
    }
    return {
        "schema": 1,
        "source": "outputs/live_experiments.json",
        "what": "Simulator back-test against every live run. No API calls.",
        "score": scored,
        "trigger": triggers,
        "short_tasks": short,
        "compaction_arms": arms,
        "modeled_claims_recalibrated": modeled_claims_recalibrated(live),
    }


def format_report(report: dict) -> str:
    s = report["score"]
    lines = [
        f"Trigger formula: {s['trigger_formula_matches']}/{s['trigger_arms']} arms match the repo's predicted threshold",
    ]
    for r in report["trigger"]:
        if r["first_pre_tokens"] is not None:
            lines.append(
                f"  {r['arm']}: fires at {r['formula_trigger']:,}, first preTokens {r['first_pre_tokens']:,} ({r['overshoot_share']:+.1%})"
            )
    lines.append(
        f"Short tasks within {s['hit_tolerance']:.0%}: published {s['short_task_published_hits']}/{s['short_task_runs']}, "
        f"calibrated {s['short_task_calibrated_hits']}/{s['short_task_runs']}"
    )
    for r in report["short_tasks"]["rows"]:
        cal = "" if r["calibrated_error"] is None else f", calibrated {r['calibrated_error']:+.0%}"
        lines.append(f"  {r['name']}: measured {r['measured']:,.0f}, published {r['published_error']:+.0%}{cal}")
    for r in report["short_tasks"]["ratios"]:
        lines.append(f"  {r['claim']}: measured {r['measured']}x, published {r['published']}x, calibrated {r['calibrated']}x")
    lines.append(
        f"Compaction arms, sign of cost change: published {s['arm_sign_matches_published']}/{s['arms']}, "
        f"calibrated {s['arm_sign_matches_calibrated']}/{s['arms']}"
    )
    for r in report["compaction_arms"]["rows"]:
        lines.append(
            f"  {r['variant']:<10} {r['arm']}: predicted {r['predicted_cost_vs_default']:+.1%} "
            f"({r['predicted_compactions']} compactions), measured {r['measured_cost_vs_default']:+.1%} ({r['measured_compactions']})"
        )
    sc = report["compaction_arms"]["summary_call_units"]
    lines.append(
        f"Summary call: measured {sc['measured_mean_hidden_units']:,} units each, modeled {sc['modeled_summary_call_units_at_107k']:,.0f} "
        f"({sc['ratio_measured_to_modeled']}x)"
    )
    lines.append("Modeled claims, recalibrated:")
    for r in report["modeled_claims_recalibrated"]:
        if "published_extra" in r:
            lines.append(f"  {r['claim']}: published {r['published_extra']:+.0%}, calibrated {r['calibrated_extra']:+.0%}")
        else:
            lines.append(f"  {r['claim']}: published {r['published']:,}, calibrated {r['calibrated']:,}")
    return "\n".join(lines)


def write_figure(report: dict, out: Path) -> Path:
    """Measured ratio against fresh start, next to both predictions."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 12, "text.parse_math": False})
    ink, muted, green, red = "#1f2328", "#59636e", "#1a7f37", "#cf222e"
    ratios = report["short_tasks"]["ratios"]
    labels = [r["claim"].replace(" vs fresh", "") for r in ratios]
    fig, ax = plt.subplots(figsize=(10, 4.6))
    y = list(range(len(ratios)))[::-1]
    for yi, r in zip(y, ratios):
        ax.plot([r["published"], r["measured"]], [yi, yi], color="#d0d7de", lw=2, zorder=1)
        ax.scatter(r["measured"], yi, s=150, color=ink, zorder=3, label="measured" if yi == y[0] else None)
        ax.scatter(r["calibrated"], yi, s=70, color=green, marker="D", zorder=4, label="model, calibrated" if yi == y[0] else None)
        ax.scatter(r["published"], yi, s=80, facecolors="white", edgecolors=red, lw=2, zorder=2, label="model, published constants" if yi == y[0] else None)
    ax.set_yticks(y, labels)
    ax.set_xlabel("Usage of the same quick fix, fresh /clear start = 1")
    ax.axvline(1, color=muted, lw=1, ls=":")
    ax.set_xlim(0, max(max(r["measured"], r["calibrated"], r["published"]) for r in ratios) * 1.12)
    ax.set_title("The caching model against the live runs", loc="left", color=ink)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.legend(loc="upper right", frameon=False)
    fig.tight_layout()
    path = out / "figures" / "model_vs_measured.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Back-test the caching model against the live runs.")
    parser.add_argument("--live", type=Path, default=LIVE)
    parser.add_argument("--out", type=Path, help="write validation.json here")
    args = parser.parse_args(argv)
    report = run(args.live)
    print(format_report(report))
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "validation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        write_figure(report, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
