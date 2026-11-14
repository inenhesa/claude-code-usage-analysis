"""Back-test the caching model against every live run in ``outputs/live_experiments.json``.

The study's modeled claims (pause curves, cron, sweet spots) rest on the
simulator. Until now the simulator had never been scored against the live
runs, which are the only measurements that could falsify it. This module
does that, with no API calls:

1. **Trigger overshoot.** Where each live compaction actually fired, against
   the formula's trigger.
2. **Short tasks.** Fresh start, warm resume, stale resume and ``/compact``
   before or after a break, against the measured units of the same runs.
3. **Long-session compaction.** Each live compaction arm, replayed through
   ``simulate`` from the default arm's measured trajectory, against the
   measured cost relative to the default arm.

Two model variants:

- ``published``: the simulator's own constants, as used for every modeled
  number in the README. This is the honest score of the shipped model.
- ``calibrated``: the same arithmetic with the task shape read from the
  measured runs.

Every short-task row carries a ``role`` so the score cannot flatter itself:

- ``fit``: the fresh (/clear) runs. The calibration is read from them, so
  their calibrated error is near zero by construction.
- ``prediction``: warm and stale resumes. The calibrated value uses only the
  fresh runs' shape plus the history size known before the run. These are the
  only rows that test the model out of sample.
- ``explained``: ``/compact`` runs. The calibrated value uses that run's own
  measured summary output and post-compact prompt size, and the observation
  (from these same runs) that a cold summary call sends history as plain
  input. They show the arithmetic is consistent, not that it predicts.

The compaction-arm ``calibrated`` variant is fitted on the same arms it
scores (summary size and extra calls per compaction) and is labelled in-sample.
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
    WRITE_1H,
    WRITE_5M,
    exploration_call_units,
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
# A compaction-arm prediction within this many percentage points counts as close.
ARM_CLOSE_POINTS = 5.0


def _units(tokens: dict, read: float = READ) -> float:
    return (
        tokens.get("input", 0)
        + tokens.get("cache_write_5m", 0) * WRITE_5M
        + tokens.get("cache_write_1h", 0) * WRITE_1H
        + tokens.get("cache_read", 0) * read
        + tokens.get("output", 0) * OUTPUT_WEIGHT
    )


def _row(name: str, role: str, measured: float, published: float, calibrated: float, note: str = "") -> dict:
    pub_err = (published - measured) / measured
    cal_err = (calibrated - measured) / measured
    return {
        "name": name,
        "role": role,
        "measured": round(measured, 1),
        "published": round(published, 1),
        "published_error": round(pub_err, 4),
        "published_hit": abs(pub_err) <= HIT_TOLERANCE,
        "calibrated": round(calibrated, 1),
        "calibrated_error": round(cal_err, 4),
        "calibrated_hit": abs(cal_err) <= HIT_TOLERANCE,
        "note": note,
    }


def _mean(values) -> float:
    values = list(values)
    return sum(values) / len(values)


# ---------------------------------------------------------------- 1. trigger


def trigger_checks(live: dict) -> list[dict]:
    """Where the first compaction fired against the formula's trigger.

    The formula itself is read from the binary; comparing it with the repo's
    own predicted threshold would compare it with itself, so only the
    measured overshoot is reported.
    """
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
                "first_pre_tokens": first,
                "overshoot_tokens": None if first is None else first - predicted,
                "overshoot_share": None if first is None else round((first - predicted) / predicted, 4),
                "validity_gate_passed": arm.get("validity_gate_passed"),
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
        "own_prefix": first_write - tail,
        "calls": calls,
        "output_per_call": output,
        "tail_per_call": tail,
        "source": "ttl_runs.clear only",
    }


def _cal_task(cal: dict, *, cached: float, cold: float, write: float = WRITE_5M) -> float:
    return task_units(
        cached_prefix=int(cached),
        cold_prefix=int(cold),
        write_mult=write,
        read_mult=READ,
        calls=cal["calls"],
        tail=int(cal["tail_per_call"]),
        output=int(cal["output_per_call"]),
    )


def _pub_task(*, cached: float, cold: float, write: float = WRITE_5M) -> float:
    return task_units(cached_prefix=int(cached), cold_prefix=int(cold), write_mult=write, read_mult=READ)


def _cal_fresh(cal: dict, write: float = WRITE_5M) -> float:
    return _cal_task(cal, cached=cal["shared_cached_prefix"], cold=cal["own_prefix"], write=write)


def _cal_stale(cal: dict, extra_history: float, write: float = WRITE_5M) -> float:
    """Stale resume carrying ``extra_history`` tokens beyond a fresh session's own prompt."""
    return _cal_task(
        cal, cached=cal["shared_cached_prefix"], cold=cal["own_prefix"] + extra_history, write=write
    )


def fresh_start_decomposition(cal: dict, measured_fresh: float) -> dict:
    """Which published assumption carries the fresh-start error."""
    shared = cal["shared_cached_prefix"]
    published = _pub_task(cached=0, cold=TASK_SYSTEM + TASK_PROJECT)
    with_shared_prefix = _pub_task(cached=shared, cold=TASK_SYSTEM + TASK_PROJECT - shared)
    with_task_shape = _cal_task(cal, cached=0, cold=TASK_SYSTEM + TASK_PROJECT)
    calibrated = _cal_fresh(cal)
    return {
        "measured": round(measured_fresh, 1),
        "published_times_measured": round(published / measured_fresh, 2),
        "published_plus_shared_cached_prefix_times_measured": round(with_shared_prefix / measured_fresh, 2),
        "published_plus_measured_task_shape_times_measured": round(with_task_shape / measured_fresh, 2),
        "calibrated_times_measured": round(calibrated / measured_fresh, 2),
        "note": (
            "Task shape is calls per task, tail and output per call (published 5 calls, 2,000-token tails, "
            "600 output; measured 3 calls, about 180-token tails, about 160 output). It carries more of the "
            "error than the shared cached prefix."
        ),
    }


def short_task_checks(live: dict) -> dict:
    ttl = live["ttl_runs"]
    comp = live["compact_runs"]
    cal = _calibration(live)
    shared = cal["shared_cached_prefix"]
    fresh_prompt = shared + cal["own_prefix"]

    rows = []
    clear_measured = _mean(_units(c["tokens"]) for c in ttl["clear"])
    rows.append(
        _row(
            "fresh start (/clear), mean of 3",
            "fit",
            clear_measured,
            _pub_task(cached=0, cold=TASK_SYSTEM + TASK_PROJECT),
            _cal_fresh(cal),
            "calibration source; calibrated error is zero by construction",
        )
    )

    hot = ttl["hot"]
    rows.append(
        _row(
            "warm resume, 3.3 min, 97k history",
            "prediction",
            _units(hot["tokens"]),
            _pub_task(cached=TASK_SYSTEM + TASK_PROJECT + hot["history_tokens"], cold=0),
            _cal_task(cal, cached=hot["history_tokens"], cold=cal["tail_per_call"]),
        )
    )

    for i, stale in enumerate(ttl["stale"], 1):
        h = stale["history_tokens"]  # includes the system prompt
        rows.append(
            _row(
                f"stale resume {i}, {stale['pause_before_s'] // 60} min break",
                "prediction",
                _units(stale["tokens"]),
                _pub_task(cached=0, cold=TASK_SYSTEM + TASK_PROJECT + h),
                _cal_stale(cal, h - fresh_prompt + cal["tail_per_call"]),
            )
        )

    instr = DEFAULTS.compact_instr
    summary_pub = DEFAULTS.summary_min
    reinjected = DEFAULTS.reinjected_tokens
    for side, runs in (("before", comp["before"]), ("after", comp["after"])):
        for i, run in enumerate(runs, 1):
            h = run["history_tokens"]
            summary_out = run["compact"]["tokens"]["output"]
            measured = _units(run["compact"]["tokens"]) + _units(run["next_task"]["tokens"])
            post_ctx = run["next_task"]["first_call"]["cache_write_5m"] + run["next_task"]["first_call"]["cache_read"]
            if side == "before":
                # Summary call reads the warm history; the break then expires everything.
                pub_compact = WRITE_5M * instr + OUTPUT_WEIGHT * summary_pub + READ * h
                cal_compact = WRITE_5M * instr + OUTPUT_WEIGHT * summary_out + READ * h
            else:
                # The shipped simulator writes a cold prompt to the cache (1.25x).
                pub_compact = WRITE_5M * (h + instr) + OUTPUT_WEIGHT * summary_pub
                # Observed in these runs: the cold summary call sends history as plain input (1x).
                cal_compact = 1.0 * (h + instr) + OUTPUT_WEIGHT * summary_out
            pub_next = _pub_task(cached=0, cold=TASK_SYSTEM + TASK_PROJECT + summary_pub + reinjected)
            cal_next = _cal_task(cal, cached=shared, cold=max(0.0, post_ctx - shared))
            rows.append(
                _row(
                    f"/compact {side} the break, run {i}",
                    "explained",
                    measured,
                    pub_compact + pub_next,
                    cal_compact + cal_next,
                    "uses this run's measured summary output and post-compact prompt size",
                )
            )

    def mean_of(prefix: str, key: str) -> float:
        return _mean(r[key] for r in rows if r["name"].startswith(prefix))

    ratios = []
    for label, prefix, role in (
        ("warm resume vs fresh", "warm resume", "prediction"),
        ("stale resume vs fresh", "stale resume", "prediction"),
        ("/compact before break vs fresh", "/compact before", "explained"),
        ("/compact after break vs fresh", "/compact after", "explained"),
    ):
        ratios.append(
            {
                "claim": label,
                "role": role,
                "measured": round(mean_of(prefix, "measured") / rows[0]["measured"], 2),
                "published": round(mean_of(prefix, "published") / rows[0]["published"], 2),
                "calibrated": round(mean_of(prefix, "calibrated") / rows[0]["calibrated"], 2),
            }
        )
    return {
        "read_weight": READ,
        "baseline": "mean of the 3 fresh runs (the data file's paired headline uses each pair instead)",
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
        "fresh_start_decomposition": fresh_start_decomposition(cal, clear_measured),
        "rows": rows,
        "ratios": ratios,
    }


def modeled_claims_recalibrated(live: dict) -> list[dict]:
    """Re-derive the README's modeled pause claims with the calibrated task shape.

    ``history`` is what a resumed session carries beyond a fresh session's own
    prompt, the same definition the published model uses.
    """
    cal = _calibration(live)
    rows = []
    for ttl_name, write in (("5m", WRITE_5M), ("1h", WRITE_1H)):
        explore = exploration_call_units(write, READ)
        for history in (30_000, 60_000):
            pub_clear = _pub_task(cached=0, cold=TASK_SYSTEM + TASK_PROJECT, write=write) + 2 * explore
            pub_stale = _pub_task(cached=0, cold=TASK_SYSTEM + TASK_PROJECT + history, write=write)
            cal_clear = _cal_fresh(cal, write) + 2 * explore
            cal_stale = _cal_stale(cal, history, write)
            rows.append(
                {
                    "claim": f"stale resume with {history // 1000}k history vs /clear plus 2 exploration calls, {ttl_name} cache",
                    "published_extra": round(pub_stale / pub_clear - 1.0, 3),
                    "calibrated_extra": round(cal_stale / cal_clear - 1.0, 3),
                }
            )
        pub_slope = write + (TASK_CALLS - 1) * READ
        cal_slope = write + (cal["calls"] - 1) * READ
        rows.append(
            {
                "claim": f"stale resume break-even history with 2 saved exploration calls, {ttl_name} cache (tokens)",
                "published": round(2 * explore / pub_slope),
                "calibrated": round(2 * explore / cal_slope),
            }
        )
    # The measured stale runs, re-weighted to a 1-hour cache: same tokens, 2x writes.
    ttl = live["ttl_runs"]
    h = _mean(s["history_tokens"] for s in ttl["stale"])
    extra = h - (cal["shared_cached_prefix"] + cal["own_prefix"]) + cal["tail_per_call"]
    rows.append(
        {
            "claim": "stale resume vs fresh at the measured 97k history, 1h cache, past the hour (modeled, not measured)",
            "calibrated_times_fresh": round(_cal_stale(cal, extra, WRITE_1H) / _cal_fresh(cal, WRITE_1H), 2),
            "calibrated_times_fresh_5m": round(_cal_stale(cal, extra) / _cal_fresh(cal), 2),
        }
    )
    return rows


# --------------------------------------------------------- 3. compaction arms


def _arm_assumptions(default_arm: dict) -> Assumptions:
    calls = default_arm["assistant_calls"]
    prefix = default_arm["first_call_prefix_tokens"]
    output = default_arm["tokens"]["output"] / calls
    growth = (default_arm["end_context_tokens"] - prefix) / (calls - 1)
    return replace(
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


def _growth_profile(arms: dict) -> dict:
    """Why a uniform-growth replay cannot place compactions: calls per compaction cycle."""
    out = {}
    for key, arm in arms.items():
        idx = [b["after_call_index"] for b in arm.get("compact_boundaries") or []]
        if len(idx) > 1:
            gaps = [b - a for a, b in zip(idx, idx[1:])]
            out[key] = {"calls_between_compactions": gaps, "min": min(gaps), "max": max(gaps)}
    return out


def compaction_arm_checks(live: dict) -> dict:
    arms = live["compaction"]["arms"]
    default = arms["default_1m"]
    base = _arm_assumptions(default)
    baseline = simulate(base)
    rows = []
    for key, arm in arms.items():
        if key == "default_1m":
            continue
        pct = arm.get("pct_override")
        variant = replace(base, window=arm["window"], pct=None if pct is None else pct / 100.0)
        result = simulate(variant)
        predicted = result.cost() / baseline.cost() - 1.0
        measured = arm["cost_vs_default"]
        rows.append(
            {
                "arm": key,
                "predicted_cost_vs_default": round(predicted, 4),
                "measured_cost_vs_default": measured,
                "error_points": round((predicted - measured) * 100, 1),
                "sign_matches": (predicted > 0) == (measured > 0),
                "within_points": abs(predicted - measured) * 100 <= ARM_CLOSE_POINTS,
                "predicted_compactions": result.compactions,
                "measured_compactions": arm["compactions"],
                "validity_gate_passed": arm.get("validity_gate_passed"),
            }
        )
    hidden = live["compaction"]["hidden_mean_units_per_compaction"]
    trigger_107k = 107_000
    modeled_summary = WRITE_5M * DEFAULTS.compact_instr + OUTPUT_WEIGHT * DEFAULTS.summary_min + READ * trigger_107k
    posts = [b["postTokens"] for a in arms.values() for b in a.get("compact_boundaries") or []]
    return {
        "rows": rows,
        "growth_profile": _growth_profile(arms),
        "post_compact_tokens": {
            "median": int(statistics.median(posts)),
            "min": min(posts),
            "max": max(posts),
            "note": "postTokens excludes the system prefix; the next call is about prefix + postTokens + the new turn",
        },
        "summary_call_units": {
            "measured_mean_hidden_units": hidden,
            "modeled_summary_call_units_at_107k": round(modeled_summary, 1),
            "ratio_modeled_to_measured": round(modeled_summary / hidden, 2),
            "note": (
                "Measured is all hidden traffic per compaction (cost_state minus assistant rows), pooled across "
                "triggers from 57.5k to 147k; modeled is one summary call at 107k."
            ),
        },
        "note": (
            "Replay of the migration suite: the default arm's measured trajectory (first prefix, mean growth per "
            "call, output per call) drives simulate() at each arm's setting. Measured cost includes the hidden "
            "summary traffic from cost_state. Real growth is lumpy (see growth_profile), so a uniform-growth "
            "replay misplaces compactions; fitting summary size and rework calls on these same arms made the "
            "error larger, not smaller, so no calibrated variant is reported. n=1 per arm and two arms fail the "
            "repo's own validity gate."
        ),
    }


# ------------------------------------------------------------------- report


def run(live_path: Path = LIVE) -> dict:
    live = json.loads(live_path.read_text(encoding="utf-8"))
    triggers = trigger_checks(live)
    short = short_task_checks(live)
    arms = compaction_arm_checks(live)
    rows = short["rows"]

    def hits(role: str, key: str) -> str:
        chosen = [r for r in rows if r["role"] == role]
        return f"{sum(r[key] for r in chosen)}/{len(chosen)}"

    def arm_count(key: str) -> str:
        return f"{sum(r[key] for r in arms['rows'])}/{len(arms['rows'])}"

    predictions = [r for r in rows if r["role"] == "prediction"]
    scored = {
        "hit_tolerance": HIT_TOLERANCE,
        "predictions_published_hits": hits("prediction", "published_hit"),
        "predictions_calibrated_hits": hits("prediction", "calibrated_hit"),
        "predictions_calibrated_max_abs_error": round(max(abs(r["calibrated_error"]) for r in predictions), 4),
        "explained_published_hits": hits("explained", "published_hit"),
        "explained_calibrated_hits": hits("explained", "calibrated_hit"),
        "arm_sign_published": arm_count("sign_matches"),
        "arm_within_points_published": arm_count("within_points"),
        "arm_close_points": ARM_CLOSE_POINTS,
    }
    return {
        "schema": 2,
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
    lines = ["Trigger overshoot (first live compaction against the formula):"]
    for r in report["trigger"]:
        if r["first_pre_tokens"] is not None:
            gate = "" if r["validity_gate_passed"] else ", fails validity gate"
            lines.append(
                f"  {r['arm']}: fires at {r['formula_trigger']:,}, first preTokens {r['first_pre_tokens']:,} "
                f"({r['overshoot_share']:+.1%}{gate})"
            )
    lines.append(
        f"Short-task predictions within {s['hit_tolerance']:.0%}: published {s['predictions_published_hits']}, "
        f"calibrated {s['predictions_calibrated_hits']} (max error {s['predictions_calibrated_max_abs_error']:.1%})"
    )
    for r in report["short_tasks"]["rows"]:
        lines.append(
            f"  [{r['role']}] {r['name']}: measured {r['measured']:,.0f}, "
            f"published {r['published_error']:+.0%}, calibrated {r['calibrated_error']:+.0%}"
        )
    for r in report["short_tasks"]["ratios"]:
        lines.append(
            f"  {r['claim']}: measured {r['measured']}x, published {r['published']}x, calibrated {r['calibrated']}x"
        )
    d = report["short_tasks"]["fresh_start_decomposition"]
    lines.append(
        f"Fresh start, modeled / measured: published {d['published_times_measured']}, "
        f"+ shared prefix {d['published_plus_shared_cached_prefix_times_measured']}, "
        f"+ task shape {d['published_plus_measured_task_shape_times_measured']}, calibrated {d['calibrated_times_measured']}"
    )
    lines.append(
        f"Compaction arms (published model): sign {s['arm_sign_published']}, "
        f"within {s['arm_close_points']:.0f} points {s['arm_within_points_published']}"
    )
    for r in report["compaction_arms"]["rows"]:
        lines.append(
            f"  {r['arm']}: predicted {r['predicted_cost_vs_default']:+.1%} "
            f"({r['predicted_compactions']} compactions), measured {r['measured_cost_vs_default']:+.1%} ({r['measured_compactions']})"
        )
    sc = report["compaction_arms"]["summary_call_units"]
    lines.append(
        f"Summary call: modeled {sc['modeled_summary_call_units_at_107k']:,.0f} units, measured hidden "
        f"{sc['measured_mean_hidden_units']:,} per compaction ({sc['ratio_modeled_to_measured']}x)"
    )
    lines.append("Modeled claims, recalibrated:")
    for r in report["modeled_claims_recalibrated"]:
        if "published_extra" in r:
            lines.append(f"  {r['claim']}: published {r['published_extra']:+.0%}, calibrated {r['calibrated_extra']:+.0%}")
        elif "published" in r:
            lines.append(f"  {r['claim']}: published {r['published']:,}, calibrated {r['calibrated']:,}")
        else:
            lines.append(
                f"  {r['claim']}: {r['calibrated_times_fresh']}x (5m: {r['calibrated_times_fresh_5m']}x)"
            )
    return "\n".join(lines)


def write_figure(report: dict, out: Path) -> Path:
    """Measured ratio against fresh start, next to both model variants."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 12, "text.parse_math": False})
    ink, muted, green, red = "#1f2328", "#59636e", "#1a7f37", "#cf222e"
    ratios = report["short_tasks"]["ratios"]
    labels = [
        r["claim"].replace(" vs fresh", "") + ("\n(prediction)" if r["role"] == "prediction" else "\n(explained)")
        for r in ratios
    ]
    fig, ax = plt.subplots(figsize=(10, 5))
    y = list(range(len(ratios)))[::-1]
    for yi, r in zip(y, ratios):
        ax.plot([r["published"], r["measured"]], [yi, yi], color="#d0d7de", lw=2, zorder=1)
        ax.scatter(r["measured"], yi, s=150, color=ink, zorder=3, label="measured" if yi == y[0] else None)
        ax.scatter(r["calibrated"], yi, s=70, color=green, marker="D", zorder=4, label="model, calibrated" if yi == y[0] else None)
        ax.scatter(r["published"], yi, s=80, facecolors="white", edgecolors=red, lw=2, zorder=2, label="model, published constants" if yi == y[0] else None)
    ax.set_yticks(y, labels)
    ax.set_xlabel("Usage of the same quick fix, mean fresh /clear start = 1")
    ax.axvline(1, color=muted, lw=1, ls=":")
    ax.set_xlim(0, max(max(r["measured"], r["calibrated"], r["published"]) for r in ratios) * 1.12)
    ax.set_title("The caching model against the live runs", loc="left", color=ink)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.legend(loc="upper right", frameon=False)
    fig.tight_layout()
    path = out / "figures" / "model_vs_measured.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, metadata={"Software": None})
    plt.close(fig)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Back-test the caching model against the live runs.")
    parser.add_argument("--live", type=Path, default=LIVE)
    parser.add_argument("--out", type=Path, help="write validation.json and the figure here")
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
