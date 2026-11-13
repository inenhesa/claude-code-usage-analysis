"""Analytical model of cumulative tokens versus compaction threshold.

Each model call re-sends the whole context. Without compaction, cumulative
input therefore grows roughly with the square of the number of turns. Compaction
replaces the history with a summary plus a re-injected prefix, which caps the
per-turn context and adds a summary call.

Nothing in this module is a measurement. Token sizes other than the documented
"about 967K" default trigger are assumptions. Cache multipliers default to the
published API rates. Those rates are not the subscription usage-limit weights.
Anthropic has not published those weights.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace


# Model-config docs: native 1M models compact at about 967K tokens by default.
# https://code.claude.com/docs/en/model-config
# The figure is the doc's approximation, not an exact constant.
OFFICIAL_ABOUT_967K = 967_000


@dataclass(frozen=True)
class Assumptions:
    """One scenario. All token counts are integers.

    ``threshold_tokens``, when set, is the context size at which the next
    turn triggers compaction. Otherwise the threshold is
    ``threshold_fraction * context_window``.

    ``turns`` counts productive turns. Rework turns caused by compaction are
    extra, so two scenarios with the same ``turns`` do the same amount of
    forward work and may do different amounts of repair.
    """

    context_window: int = 1_000_000
    threshold_fraction: float = 0.95
    threshold_tokens: int | None = None
    turns: int = 120
    growth_per_turn: int = 20_000
    output_per_turn: int = 2_000
    system_prompt_tokens: int = 12_000
    project_context_tokens: int = 8_000
    summary_tokens: int = 4_000
    reinjected_tokens: int = 6_000
    compaction_instruction_tokens: int = 1_000
    compaction_output_tokens: int = 2_000
    caching: bool = True
    cache_hit_rate: float = 0.95
    system_prompt_survives_compaction: bool = True
    cache_read_multiplier: float = 0.1
    cache_write_multiplier: float = 2.0
    output_multiplier: float = 5.0
    rework_turns_per_compaction: int = 0
    rework_growth_per_turn: int = 12_000
    label: str = ""

    def validate(self) -> None:
        if self.context_window < 1:
            raise ValueError("context_window must be positive")
        if self.turns < 1:
            raise ValueError("turns must be positive")
        if not 0.0 <= self.cache_hit_rate <= 1.0:
            raise ValueError("cache_hit_rate must be between 0 and 1")
        if self.threshold_tokens is None:
            if not 0.0 < self.threshold_fraction <= 1.0:
                raise ValueError("threshold_fraction must be in (0, 1]")
        elif self.threshold_tokens < 1:
            raise ValueError("threshold_tokens must be positive")
        for name in (
            "growth_per_turn",
            "output_per_turn",
            "system_prompt_tokens",
            "project_context_tokens",
            "summary_tokens",
            "reinjected_tokens",
            "compaction_instruction_tokens",
            "compaction_output_tokens",
            "rework_turns_per_compaction",
            "rework_growth_per_turn",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0")
        if self.cache_read_multiplier < 0 or self.cache_write_multiplier < 0:
            raise ValueError("cache multipliers must be >= 0")
        if self.output_multiplier < 0:
            raise ValueError("output_multiplier must be >= 0")

    @property
    def base_prefix_tokens(self) -> int:
        return self.system_prompt_tokens + self.project_context_tokens

    @property
    def reset_tokens(self) -> int:
        """Context size immediately after a compaction.

        System prompt and project context are re-injected. The summary
        replaces the dropped history. A few recently touched files are
        re-read. See "What survives compaction" in the context-window docs.
        """
        return (
            self.system_prompt_tokens
            + self.project_context_tokens
            + self.summary_tokens
            + self.reinjected_tokens
        )

    def resolved_threshold(self) -> int:
        if self.threshold_tokens is not None:
            return self.threshold_tokens
        return int(round(self.threshold_fraction * self.context_window))


# Long session: both a 25% and a 95% threshold of a 1M window are crossed.
DEFAULT_ASSUMPTIONS = Assumptions(label="long_session")

# Short session: 95% of 1M is never reached. 25% is.
SHORT_ASSUMPTIONS = Assumptions(
    label="short_session",
    turns=30,
    growth_per_turn=8_000,
    output_per_turn=2_000,
)


@dataclass(frozen=True)
class Call:
    kind: str
    productive_turn: int
    prompt_tokens: int
    input_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    output_tokens: int
    history_after: int
    cache_warm_after: bool

    @property
    def api_parts(self) -> tuple[int, int, int, int]:
        return (
            self.input_tokens,
            self.cache_read_tokens,
            self.cache_write_tokens,
            self.output_tokens,
        )


@dataclass
class SimulationResult:
    assumptions: Assumptions
    calls: list[Call]
    warnings: list[str] = field(default_factory=list)

    @property
    def threshold_tokens(self) -> int:
        return self.assumptions.resolved_threshold()

    @property
    def compaction_count(self) -> int:
        return sum(1 for call in self.calls if call.kind == "compaction")

    @property
    def productive_turns(self) -> int:
        return sum(1 for call in self.calls if call.kind == "turn")

    @property
    def rework_turns(self) -> int:
        return sum(1 for call in self.calls if call.kind == "rework")

    @property
    def total_prompt_tokens(self) -> int:
        return sum(call.prompt_tokens for call in self.calls)

    @property
    def total_input_tokens(self) -> int:
        return sum(call.input_tokens for call in self.calls)

    @property
    def total_cache_read_tokens(self) -> int:
        return sum(call.cache_read_tokens for call in self.calls)

    @property
    def total_cache_write_tokens(self) -> int:
        return sum(call.cache_write_tokens for call in self.calls)

    @property
    def total_output_tokens(self) -> int:
        return sum(call.output_tokens for call in self.calls)

    @property
    def peak_prompt_tokens(self) -> int:
        return max((call.prompt_tokens for call in self.calls), default=0)

    def api_equivalent_cost(self) -> float:
        """Cost in units of one base input token, using the scenario multipliers.

        This is the published API price shape (cache read, cache write, output).
        It is not a subscription-meter prediction.
        """
        a = self.assumptions
        return (
            self.total_input_tokens
            + self.total_cache_read_tokens * a.cache_read_multiplier
            + self.total_cache_write_tokens * a.cache_write_multiplier
            + self.total_output_tokens * a.output_multiplier
        )

    def totals(self) -> dict[str, float | int | str | list[str]]:
        a = self.assumptions
        return {
            "label": a.label,
            "threshold_tokens": self.threshold_tokens,
            "threshold_fraction_of_window": self.threshold_tokens / a.context_window,
            "productive_turns": self.productive_turns,
            "rework_turns": self.rework_turns,
            "compactions": self.compaction_count,
            "prompt_tokens": self.total_prompt_tokens,
            "input_tokens": self.total_input_tokens,
            "cache_read_tokens": self.total_cache_read_tokens,
            "cache_write_tokens": self.total_cache_write_tokens,
            "output_tokens": self.total_output_tokens,
            "api_equivalent_cost": self.api_equivalent_cost(),
            "peak_prompt_tokens": self.peak_prompt_tokens,
            "warnings": list(self.warnings),
        }

    def as_dict(self) -> dict:
        payload = self.totals()
        payload["assumptions"] = asdict(self.assumptions)
        return payload


def closed_form_prompt_tokens(assumptions: Assumptions) -> int:
    """Cumulative prompt tokens if the session never compacts.

    Turn ``t`` (1-based) sends ``base + t * growth + (t - 1) * output``.
    The sum is ``N * base + growth * N * (N + 1) / 2 + output * N * (N - 1) / 2``.

    Caching does not change this total. Cache reads and cache writes partition
    the same prompt. The formula does not include compaction calls. Compare it
    with a simulation only when that simulation's compaction count is zero.
    """
    assumptions.validate()
    n = assumptions.turns
    base = assumptions.base_prefix_tokens
    growth = assumptions.growth_per_turn
    output = assumptions.output_per_turn
    return (
        n * base
        + growth * n * (n + 1) // 2
        + output * n * (n - 1) // 2
    )


def _split_prompt(
    assumptions: Assumptions,
    prefix: int,
    new_tail: int,
    *,
    warm: bool,
    first_call: bool,
) -> tuple[int, int, int]:
    """Return fresh input, cache read, cache write. The three sum to the prompt."""
    prompt = prefix + new_tail
    if not assumptions.caching:
        return prompt, 0, 0
    if first_call or not warm:
        cache_read = 0
        if (
            not first_call
            and assumptions.system_prompt_survives_compaction
            and assumptions.system_prompt_tokens > 0
        ):
            # After compaction the system prompt can still hit. Project context
            # is re-read from disk and cached together with the new summary, so
            # it is a write, not a read. See the prompt-caching docs.
            cache_read = min(assumptions.system_prompt_tokens, prefix)
        return 0, cache_read, prompt - cache_read
    cache_read = int(round(prefix * assumptions.cache_hit_rate))
    cache_read = min(max(cache_read, 0), prefix)
    cache_write = (prefix - cache_read) + new_tail
    return 0, cache_read, cache_write


def simulate(assumptions: Assumptions) -> SimulationResult:
    """Walk a session turn by turn.

    Compaction runs before a productive turn when the next request would pass
    the threshold, and at most once per productive turn. If the post-compaction
    context is not smaller than the current history, compaction is disabled for
    the rest of the session and a warning is recorded. That is the regime where
    the threshold sits inside the summary itself.
    """
    assumptions.validate()
    threshold = assumptions.resolved_threshold()
    history = assumptions.base_prefix_tokens
    warm = False
    first_call = True
    calls: list[Call] = []
    warnings: list[str] = []
    warned_still_over = False
    warned_reset = False

    def account(kind: str, productive_turn: int, prefix: int, new_tail: int, output: int) -> int:
        nonlocal history, warm, first_call
        fresh, cache_read, cache_write = _split_prompt(
            assumptions,
            prefix,
            new_tail,
            warm=warm,
            first_call=first_call,
        )
        prompt = fresh + cache_read + cache_write
        history = prefix + new_tail + output
        warm = True
        first_call = False
        calls.append(
            Call(
                kind=kind,
                productive_turn=productive_turn,
                prompt_tokens=prompt,
                input_tokens=fresh,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
                output_tokens=output,
                history_after=history,
                cache_warm_after=warm,
            )
        )
        return prompt

    def compact(productive_turn: int) -> bool:
        nonlocal history, warm, warned_reset
        reset = assumptions.reset_tokens
        # The session starts at the re-injected prefix, which is smaller than
        # the post-compaction context (that context also holds the summary).
        # Compaction only helps once the history has grown past the reset size.
        if reset >= history:
            return False
        account(
            "compaction",
            productive_turn,
            history,
            assumptions.compaction_instruction_tokens,
            assumptions.compaction_output_tokens,
        )
        history = reset
        # The summary call warms nothing the next turn can reuse except the
        # system prompt, which ``_split_prompt`` handles on a cold turn.
        warm = False
        if reset >= threshold and not warned_reset:
            warnings.append(
                "threshold_at_or_below_reset: the summary context is already "
                "at or above the threshold, so compaction cannot open a full turn"
            )
            warned_reset = True
        return True

    for index in range(1, assumptions.turns + 1):
        if history + assumptions.growth_per_turn > threshold:
            did = compact(index)
            if did:
                for _ in range(assumptions.rework_turns_per_compaction):
                    account(
                        "rework",
                        index,
                        history,
                        assumptions.rework_growth_per_turn,
                        assumptions.output_per_turn,
                    )
                if history + assumptions.growth_per_turn > threshold and not warned_still_over:
                    warnings.append(
                        "request_still_over_threshold: one compaction did not "
                        "bring the next turn under the threshold"
                    )
                    warned_still_over = True
        account(
            "turn",
            index,
            history,
            assumptions.growth_per_turn,
            assumptions.output_per_turn,
        )

    if not any(call.kind == "compaction" for call in calls):
        warnings.append("no_compaction: the session never reached the threshold")
    return SimulationResult(assumptions=assumptions, calls=calls, warnings=warnings)


def ratio(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def compare(low: SimulationResult, high: SimulationResult) -> dict[str, float | int | None]:
    """Ratios of ``low`` over ``high``. A value of 0.25 means low uses a quarter of high."""
    return {
        "low_threshold_tokens": low.threshold_tokens,
        "high_threshold_tokens": high.threshold_tokens,
        "prompt_tokens_ratio": ratio(low.total_prompt_tokens, high.total_prompt_tokens),
        "api_equivalent_ratio": ratio(low.api_equivalent_cost(), high.api_equivalent_cost()),
        "output_tokens_ratio": ratio(low.total_output_tokens, high.total_output_tokens),
        "compaction_count_low": low.compaction_count,
        "compaction_count_high": high.compaction_count,
        "prompt_tokens_low": low.total_prompt_tokens,
        "prompt_tokens_high": high.total_prompt_tokens,
        "api_equivalent_low": low.api_equivalent_cost(),
        "api_equivalent_high": high.api_equivalent_cost(),
        "cache_read_low": low.total_cache_read_tokens,
        "cache_read_high": high.total_cache_read_tokens,
        "cache_write_low": low.total_cache_write_tokens,
        "cache_write_high": high.total_cache_write_tokens,
    }


def with_threshold(assumptions: Assumptions, fraction: float | None = None, tokens: int | None = None) -> Assumptions:
    if tokens is not None:
        return replace(assumptions, threshold_tokens=tokens, threshold_fraction=tokens / assumptions.context_window)
    if fraction is None:
        raise ValueError("pass fraction or tokens")
    return replace(assumptions, threshold_fraction=fraction, threshold_tokens=None)
