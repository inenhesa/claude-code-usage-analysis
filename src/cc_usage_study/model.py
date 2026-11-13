"""Call-level model of Claude Code token usage under auto-compaction.

Physical prefix caching: a warm call reads the entire previous request and
writes only the previous output plus the new tail. A cold call (TTL expiry,
or an explicit miss) rewrites the whole prompt. Compaction's uncached tail
is written at the 5-minute cache-write rate. The main conversation uses the
configured TTL (60 minutes inside a subscription plan, 5 minutes on usage
credits or an API key).

Nothing here is a measurement of an account. The about-967K default is the
documentation's approximation for native 1M models. The 33,000-token reserve
is a T3 Code source comment, not an Anthropic statement. Cache multipliers
are published API list-price shapes, not subscription-meter weights.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

# https://code.claude.com/docs/en/model-config
OFFICIAL_ABOUT_967K = 967_000
# T3 Code ClaudeAdapterV2.ts comment, commit 65e4272, read 13 Nov 2026.
# "Claude compacts 33,000 tokens short of the window." Not an Anthropic spec.
# 1_000_000 - 33_000 = 967_000, which matches the documented approximation.
# That equality is an inference.
T3_WINDOW_RESERVE_COMMENT = 33_000
WINDOW_MIN = 100_000
WINDOW_MAX = 1_000_000

# Third-party client-side /usage attribution, not the server meter.
# https://www.turboai.dev/blog/claude-code-limits-usage-breakdown-guide
ATTRIBUTION_V2_1_317 = {
    "read": 1.0,
    "input": 10.0,
    "write": 12.5,
    "output": 50.0,
    "model_factor": {"haiku": 1.0, "sonnet": 3.0, "opus": 5.0, "fable": 10.0},
    "disclaimer": (
        "Third-party reading of client-side /usage attribution in Claude Code "
        "around v2.1.317. It is not the server-side five-hour or weekly meter."
    ),
}


@dataclass(frozen=True)
class Assumptions:
    """One scenario. Token counts are integers. Minutes are floats.

    ``pct`` is CLAUDE_AUTOCOMPACT_PCT_OVERRIDE as a fraction (0.25 means 25).
    None means the override is unset. Values at or above 1 cannot raise the
    trigger, matching the docs.

    ``window`` is the absolute auto-compact window in tokens. None means unset.
    ``reserve`` defaults to 0, so the trigger is the window itself. Pass
    ``reserve=33000`` when modeling T3 Code's UI, which a source comment says
    fires 33,000 tokens short of the window. That comment is not an Anthropic
    spec, and it is not applied unless you set ``reserve``.

    ``user_turns`` is how many times a person sends a message. Each user turn
    is ``calls_per_turn`` model requests (the tool loop). Those are not the
    same thing.
    """

    label: str = ""
    context_window: int = 1_000_000
    window: int | None = None
    pct: float | None = None
    default_trigger: int | None = None
    reserve: int | None = None
    user_turns: int = 60
    calls_per_turn: int = 8
    tail: int = 4_000
    output_per_call: int = 600
    system_prompt_tokens: int = 20_000
    project_context_tokens: int = 8_000
    reinjected_tokens: int = 15_000
    summary_min: int = 3_000
    summary_frac: float = 0.0
    summary_max: int = 20_000
    compact_instr: int = 1_000
    rework_calls: int = 0
    rework_tail: int = 8_000
    gap_min: float = 2.0
    long_gaps: tuple[tuple[int, float], ...] = ()
    ttl_min: float = 60.0
    idle_compaction: bool = True
    idle_min_tokens: int = 200_000
    cache_read_multiplier: float = 0.1
    cache_write_1h: float = 2.0
    cache_write_5m: float = 1.25
    output_multiplier: float = 5.0
    # Call indices (0-based, counted before the call) that are full cache misses.
    full_miss_calls: tuple[int, ...] = ()
    attribution_family: str = "sonnet"
    # Optional inline research burst, counted inside one user turn.
    burst_turn: int = -1
    burst_calls: int = 0
    burst_tail: int = 0
    burst_return: int = 0

    def validate(self) -> None:
        if self.context_window < 1:
            raise ValueError("context_window must be positive")
        if self.user_turns < 1 or self.calls_per_turn < 1:
            raise ValueError("user_turns and calls_per_turn must be positive")
        if self.pct is not None and not 0 < self.pct <= 1:
            raise ValueError("pct must be in (0, 1]")
        if self.reinjected_tokens < 0 or self.reinjected_tokens > 50_000:
            raise ValueError("reinjected_tokens must be in [0, 50000]")
        if self.tail < 0 or self.output_per_call < 0:
            raise ValueError("tail and output_per_call must be >= 0")
        if self.ttl_min <= 0:
            raise ValueError("ttl_min must be positive")


def effective_trigger(assumptions: Assumptions) -> tuple[int, list[str]]:
    """Return (trigger tokens, warnings).

    No window and no percentage: about 967K on a 1M context, otherwise the
    context limit (or ``default_trigger`` when set).

    Window only: the window minus ``reserve``. Reserve defaults to 0.
    A T3 "Auto-compact after 250000" model sets ``reserve`` to 33,000 and
    fires at 217,000.

    Percentage set: that fraction of the auto-compact window, and it cannot
    raise the no-percentage trigger. A window plus a percentage compounds
    and produces a warning. Reserve is not subtracted again after the
    percentage. 250,000 with 25% is 62,500.
    """
    assumptions.validate()
    warnings: list[str] = []
    ctx = assumptions.context_window
    reserve = 0 if assumptions.reserve is None else assumptions.reserve
    pct = assumptions.pct
    pct_active = pct is not None and pct < 1.0

    if assumptions.window is None:
        if assumptions.default_trigger is not None:
            base = min(assumptions.default_trigger, ctx)
        elif ctx >= 1_000_000:
            base = min(OFFICIAL_ABOUT_967K, ctx)
        else:
            base = ctx
        if assumptions.reserve is not None and not pct_active:
            base = base - assumptions.reserve
        if pct_active:
            return max(1, min(base, int(ctx * pct))), warnings
        return max(1, base), warnings

    window = min(max(assumptions.window, WINDOW_MIN), WINDOW_MAX, ctx)
    if window != assumptions.window:
        warnings.append(
            f"window clamped from {assumptions.window} to {window} "
            "(documented range 100000 to 1000000, capped at the model window)"
        )
    if pct_active:
        warnings.append(
            "compounding: an absolute window and CLAUDE_AUTOCOMPACT_PCT_OVERRIDE "
            "are both set. The percentage applies to the window. Reserve is not "
            "subtracted again after the percentage. Whether production does "
            "the same is unverified; check compactMetadata.preTokens."
        )
        ceiling = max(1, window - reserve)
        default_pct = ceiling / window
        fraction = min(pct, default_pct)
        return max(1, int(window * fraction)), warnings
    return max(1, window - reserve), warnings


def summary_size(assumptions: Assumptions, context_tokens: int) -> int:
    raw = assumptions.summary_frac * context_tokens
    return int(min(assumptions.summary_max, max(assumptions.summary_min, raw)))


def eoq_trigger(
    *,
    reset_tokens: float,
    fixed_compaction_cost: float,
    growth_per_call: float,
    read_multiplier: float,
) -> float:
    """Harris EOQ form: T* ≈ R + sqrt(2 * K * (g + o) / r).

    ``fixed_compaction_cost`` is K, the per-cycle overhead that does not grow
    with T. ``growth_per_call`` is g + o, the context added by one call.
    ``read_multiplier`` is the holding cost per token per call.
    """
    if read_multiplier <= 0 or growth_per_call <= 0:
        raise ValueError("read_multiplier and growth_per_call must be positive")
    return reset_tokens + math.sqrt(
        2.0 * fixed_compaction_cost * growth_per_call / read_multiplier
    )


def fixed_compaction_cost(assumptions: Assumptions) -> tuple[float, int]:
    """Return (K, R) for the EOQ formula, with K excluding the r*T term.

    K = 1.25 * instruction + 5 * summary_min + w * (R - system) + r * system.
    w is the main-conversation write multiplier for the configured TTL.
    """
    summary = assumptions.summary_min
    reset = (
        assumptions.system_prompt_tokens
        + assumptions.project_context_tokens
        + summary
        + assumptions.reinjected_tokens
    )
    write = assumptions.cache_write_1h if assumptions.ttl_min >= 60 else assumptions.cache_write_5m
    k = (
        assumptions.cache_write_5m * assumptions.compact_instr
        + assumptions.output_multiplier * summary
        + write * (reset - assumptions.system_prompt_tokens)
        + assumptions.cache_read_multiplier * assumptions.system_prompt_tokens
    )
    return k, reset


@dataclass(frozen=True)
class Call:
    kind: str
    user_turn: int
    prompt_tokens: int
    input_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    output_tokens: int
    history_after: int
    write_multiplier: float
    cold: bool

    @property
    def cache_write_5m_tokens(self) -> int:
        if abs(self.write_multiplier - 1.25) < 1e-9:
            return self.cache_write_tokens
        return 0

    @property
    def cache_write_1h_tokens(self) -> int:
        if abs(self.write_multiplier - 2.0) < 1e-9:
            return self.cache_write_tokens
        return 0


@dataclass
class SimulationResult:
    assumptions: Assumptions
    trigger_tokens: int
    warnings: list[str] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    cold_rebuilds: int = 0
    idle_compactions: int = 0

    @property
    def compactions(self) -> int:
        return sum(1 for call in self.calls if call.kind == "compaction")

    @property
    def productive_calls(self) -> int:
        return sum(1 for call in self.calls if call.kind == "turn")

    @property
    def rework_call_count(self) -> int:
        return sum(1 for call in self.calls if call.kind == "rework")

    def totals(self) -> dict[str, float]:
        fresh = sum(c.input_tokens for c in self.calls)
        read = sum(c.cache_read_tokens for c in self.calls)
        write = sum(c.cache_write_tokens for c in self.calls)
        output = sum(c.output_tokens for c in self.calls)
        return {
            "calls": len(self.calls),
            "productive_calls": self.productive_calls,
            "rework_calls": self.rework_call_count,
            "compactions": self.compactions,
            "idle_compactions": self.idle_compactions,
            "cold_rebuilds": self.cold_rebuilds,
            "input_tokens": fresh,
            "cache_read_tokens": read,
            "cache_write_tokens": write,
            "output_tokens": output,
            "prompt_tokens": fresh + read + write,
            "peak_prompt_tokens": max((c.prompt_tokens for c in self.calls), default=0),
            "trigger_tokens": self.trigger_tokens,
        }

    def cost(self, scheme: str = "api") -> float:
        """Weight the trace.

        Schemes:
        - api: published price shape (read r, writes already at 1.25 or 2, output 5)
        - raw: prompt tokens plus output tokens
        - raw_prompt: prompt tokens only
        - read_full: cache reads at 1.0x, writes and output as in api
        - h_zero: cache reads free, writes and output as in api
        - attribution_v2_1_317: third-party client-side weights, labelled as such
        """
        a = self.assumptions
        fresh = 0
        read = 0
        write = 0
        write_cost = 0.0
        output = 0
        for call in self.calls:
            fresh += call.input_tokens
            read += call.cache_read_tokens
            write += call.cache_write_tokens
            write_cost += call.cache_write_tokens * call.write_multiplier
            output += call.output_tokens
        if scheme == "raw":
            return float(fresh + read + write + output)
        if scheme == "raw_prompt":
            return float(fresh + read + write)
        if scheme == "read_full":
            return fresh + read * 1.0 + write_cost + output * a.output_multiplier
        if scheme == "h_zero":
            return fresh + write_cost + output * a.output_multiplier
        if scheme == "attribution_v2_1_317":
            weights = ATTRIBUTION_V2_1_317
            family = a.attribution_family
            factor = float(weights["model_factor"][family])
            return factor * (
                fresh * weights["input"]
                + read * weights["read"]
                + write * weights["write"]
                + output * weights["output"]
            )
        if scheme != "api":
            raise ValueError(f"unknown scheme {scheme}")
        return fresh + read * a.cache_read_multiplier + write_cost + output * a.output_multiplier


def simulate(assumptions: Assumptions) -> SimulationResult:
    """Walk the session. Compaction is at most the calls the trigger requires.

    Rework calls are extra model requests after a compaction. They go through
    the same trigger check. A compaction spawned by rework does not itself
    schedule another rework burst, so a low trigger cannot recurse forever.
    """
    assumptions.validate()
    trigger, warnings = effective_trigger(assumptions)
    result = SimulationResult(
        assumptions=assumptions,
        trigger_tokens=trigger,
        warnings=list(warnings),
    )
    if assumptions.reinjected_tokens == 0 and assumptions.summary_min == 0:
        result.warnings.append("reset context is only the system prompt and project files")

    ctx = assumptions.system_prompt_tokens + assumptions.project_context_tokens
    cached = 0
    floor = (
        assumptions.system_prompt_tokens
        + assumptions.project_context_tokens
        + assumptions.summary_min
        + assumptions.reinjected_tokens
    )
    main_write = (
        assumptions.cache_write_1h if assumptions.ttl_min >= 60 else assumptions.cache_write_5m
    )
    gaps = dict(assumptions.long_gaps)
    misses = set(assumptions.full_miss_calls)
    call_index = 0

    def emit(kind: str, user_turn: int, tail: int, out: int, write_mult: float) -> None:
        nonlocal ctx, cached, call_index
        if call_index in misses:
            cached = 0
            result.cold_rebuilds += 1
        prompt = ctx + tail
        read = min(cached, ctx) if prompt else 0
        # Previous output sits in ctx and was not part of the cached prefix
        # unless it was inside `cached`. read = min(cached, ctx) does that:
        # on a warm call cached is the previous prompt, so the previous
        # output (ctx - cached) is written, plus the new tail.
        write = prompt - read
        fresh = 0
        result.calls.append(
            Call(
                kind=kind,
                user_turn=user_turn,
                prompt_tokens=prompt,
                input_tokens=fresh,
                cache_read_tokens=read,
                cache_write_tokens=write,
                output_tokens=out,
                history_after=prompt + out,
                write_multiplier=write_mult,
                cold=read == 0,
            )
        )
        cached = prompt
        ctx = prompt + out
        call_index += 1

    def compact(user_turn: int, *, idle: bool, with_rework: bool) -> None:
        nonlocal ctx, cached
        size = summary_size(assumptions, ctx)
        emit("compaction", user_turn, assumptions.compact_instr, size, assumptions.cache_write_5m)
        if idle:
            result.idle_compactions += 1
        ctx = (
            assumptions.system_prompt_tokens
            + assumptions.project_context_tokens
            + size
            + assumptions.reinjected_tokens
        )
        cached = assumptions.system_prompt_tokens
        if not with_rework:
            return
        for _ in range(assumptions.rework_calls):
            if ctx + assumptions.rework_tail > trigger and ctx > floor:
                compact(user_turn, idle=False, with_rework=False)
            emit("rework", user_turn, assumptions.rework_tail, assumptions.output_per_call, main_write)

    def maybe_compact(user_turn: int, tail: int) -> None:
        if ctx + tail > trigger and ctx > floor:
            compact(user_turn, idle=False, with_rework=True)

    for user_turn in range(assumptions.user_turns):
        gap = gaps.get(user_turn, assumptions.gap_min) if user_turn else 0.0
        if user_turn and gap > assumptions.ttl_min:
            if (
                assumptions.idle_compaction
                and assumptions.ttl_min >= 60
                and ctx >= assumptions.idle_min_tokens
            ):
                compact(user_turn, idle=True, with_rework=True)
                # The idle summary runs while the cache is still warm.
                # The gap still exceeds the TTL, so the short prefix expires.
                cached = 0
                result.cold_rebuilds += 1
            else:
                result.cold_rebuilds += 1
                cached = 0
        if user_turn == assumptions.burst_turn:
            for _ in range(assumptions.burst_calls):
                maybe_compact(user_turn, assumptions.burst_tail)
                emit("turn", user_turn, assumptions.burst_tail, assumptions.output_per_call, main_write)
            if assumptions.burst_return:
                maybe_compact(user_turn, assumptions.burst_return)
                emit("turn", user_turn, assumptions.burst_return, assumptions.output_per_call, main_write)
        for index in range(assumptions.calls_per_turn):
            maybe_compact(user_turn, assumptions.tail)
            emit("turn", user_turn, assumptions.tail, assumptions.output_per_call, main_write)
    if result.compactions == 0:
        result.warnings.append("no_compaction: the session never reached the trigger")
    return result


def saving(high: SimulationResult, low: SimulationResult, scheme: str = "api") -> float:
    """How many times larger the high-trigger cost is. 1.0 means no change."""
    low_cost = low.cost(scheme)
    if low_cost == 0:
        raise ValueError("low cost is zero")
    return high.cost(scheme) / low_cost


def closed_form_prompt_tokens(
    *,
    base: int,
    growth: int,
    output: int,
    turns: int,
) -> int:
    """No-compaction sum when each request adds ``growth`` input and ``output`` output.

    Prompt on request t (1-based) is base + t*growth + (t-1)*output.
    Sum = N*base + growth*N*(N+1)/2 + output*N*(N-1)/2.
    ``growth`` does not include the previous output. That term is separate.
    """
    n = turns
    return n * base + growth * n * (n + 1) // 2 + output * n * (n - 1) // 2
