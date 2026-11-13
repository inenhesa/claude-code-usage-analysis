"""Published Anthropic prompt-cache multipliers.

Snapshot of the public pricing table on 13 November 2026. Prices change.
This module does not describe Claude subscription usage limits. Anthropic
has not published how five-hour or weekly limits weight these buckets.

Source: https://platform.claude.com/docs/en/about-claude/pricing
Also stated in: https://platform.claude.com/docs/en/build-with-claude/prompt-caching
"""

from __future__ import annotations

SNAPSHOT_DATE = "2026-11-13"
PRICING_URL = "https://platform.claude.com/docs/en/about-claude/pricing"

# Multipliers relative to the base input token price.
CACHE_WRITE_5M_MULTIPLIER = 1.25
CACHE_WRITE_1H_MULTIPLIER = 2.0
STANDARD_CACHE_READ_MULTIPLIER = 0.1
# Footnotes on the pricing page, same snapshot:
# Opus 5.5 and Sonnet 5.5 cache hits are 0.05x base input.
# Fable 5.2, Mythos 5.2, Fable 5.1, and Mythos 5.1 cache hits are 0.025x.
OPUS_55_SONNET_55_CACHE_READ_MULTIPLIER = 0.05
FABLE_51_52_CACHE_READ_MULTIPLIER = 0.025

# USD per million tokens: base input, 5m write, 1h write, cache hit, output.
# Haiku 5.5 has two rows. The second applies when the prompt is over 100,000
# tokens. See HAIKU_55_LONG_PROMPT_MIN.
_Price = tuple[float, float, float, float, float]

PRICES_USD_PER_MILLION: dict[str, _Price] = {
    "claude-fable-5-2": (8.0, 10.0, 16.0, 0.20, 40.0),
    "claude-mythos-5-2": (8.0, 10.0, 16.0, 0.20, 40.0),
    "claude-fable-5-1": (10.0, 12.50, 20.0, 0.25, 50.0),
    "claude-mythos-5-1": (10.0, 12.50, 20.0, 0.25, 50.0),
    "claude-fable-5": (10.0, 12.50, 20.0, 1.0, 50.0),
    "claude-mythos-5": (10.0, 12.50, 20.0, 1.0, 50.0),
    "claude-opus-5-5": (4.0, 5.0, 8.0, 0.20, 20.0),
    "claude-opus-5": (5.0, 6.25, 10.0, 0.50, 25.0),
    "claude-opus-4-8": (5.0, 6.25, 10.0, 0.50, 25.0),
    "claude-opus-4-7": (5.0, 6.25, 10.0, 0.50, 25.0),
    "claude-opus-4-6": (5.0, 6.25, 10.0, 0.50, 25.0),
    "claude-opus-4-5": (5.0, 6.25, 10.0, 0.50, 25.0),
    "claude-opus-4-1": (15.0, 18.75, 30.0, 1.50, 75.0),
    "claude-opus-4": (15.0, 18.75, 30.0, 1.50, 75.0),
    "claude-sonnet-5-5": (2.0, 2.50, 4.0, 0.10, 10.0),
    "claude-sonnet-5": (2.0, 2.50, 4.0, 0.20, 10.0),
    "claude-sonnet-4-6": (3.0, 3.75, 6.0, 0.30, 15.0),
    "claude-sonnet-4-5": (3.0, 3.75, 6.0, 0.30, 15.0),
    "claude-sonnet-4": (3.0, 3.75, 6.0, 0.30, 15.0),
    "claude-haiku-4-5": (1.0, 1.25, 2.0, 0.10, 5.0),
}

HAIKU_55_SHORT: _Price = (0.10, 0.125, 0.20, 0.01, 0.50)
HAIKU_55_LONG: _Price = (0.50, 0.625, 1.0, 0.05, 2.50)
HAIKU_55_LONG_PROMPT_MIN = 100_000


def canonical_model_id(model: str) -> str | None:
    """Map a Claude Code model id onto a row in the snapshot table.

    Dated suffixes and provider prefixes are stripped only when the remaining
    id matches a known row. Unknown ids return None. Callers must not invent
    a price for those.
    """
    text = model.strip().lower()
    if not text:
        return None
    text = text.split("[", 1)[0]
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    if text.startswith("anthropic."):
        text = text[len("anthropic.") :]
    # Bedrock-style ids sometimes look like "us.anthropic.claude-...".
    if "anthropic." in text:
        text = text.split("anthropic.", 1)[1]
    text = text.split("@", 1)[0]
    text = text.split(":", 1)[0]
    if text.startswith("claude-haiku-5-5") or text == "claude-haiku-5-5":
        return "claude-haiku-5-5"
    # Prefer the longest matching known id so "claude-fable-5" does not
    # swallow "claude-fable-5-2".
    candidates = sorted(PRICES_USD_PER_MILLION, key=len, reverse=True)
    for known in candidates:
        if text == known or text.startswith(known + "-"):
            return known
    if text.startswith("claude-haiku-5-5"):
        return "claude-haiku-5-5"
    return None


def cache_read_multiplier(model: str | None) -> tuple[float, str]:
    """Return (multiplier, note). Unknown models use the standard 0.1x rate.

    The note says when the rate is a fallback rather than a matched row.
    """
    if model is None:
        return STANDARD_CACHE_READ_MULTIPLIER, "standard 0.1x; model not specified"
    known = canonical_model_id(model)
    if known is None:
        return (
            STANDARD_CACHE_READ_MULTIPLIER,
            "standard 0.1x fallback; model is not in the 2026-11-13 snapshot",
        )
    if known in {"claude-opus-5-5", "claude-sonnet-5-5"}:
        return OPUS_55_SONNET_55_CACHE_READ_MULTIPLIER, known
    if known in {
        "claude-fable-5-2",
        "claude-mythos-5-2",
        "claude-fable-5-1",
        "claude-mythos-5-1",
    }:
        return FABLE_51_52_CACHE_READ_MULTIPLIER, known
    return STANDARD_CACHE_READ_MULTIPLIER, known


def price_row(model: str, prompt_tokens: int) -> _Price | None:
    known = canonical_model_id(model)
    if known is None:
        return None
    if known == "claude-haiku-5-5":
        if prompt_tokens > HAIKU_55_LONG_PROMPT_MIN:
            return HAIKU_55_LONG
        return HAIKU_55_SHORT
    return PRICES_USD_PER_MILLION[known]


def estimate_call_usd(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_5m_tokens: int | None,
    cache_write_1h_tokens: int | None,
    prompt_tokens: int,
) -> tuple[float | None, str | None]:
    """List-price estimate in USD for one API call.

    Returns (None, reason) when the model is unknown or the cache-write TTL
    split is missing while cache writes are nonzero. A missing split must
    not be guessed: 5-minute writes are 1.25x and 1-hour writes are 2x.
    """
    row = price_row(model, prompt_tokens)
    if row is None:
        return None, "model not in the published snapshot; no price estimated"
    if cache_write_5m_tokens is None or cache_write_1h_tokens is None:
        return None, "cache write TTL split missing; 5m and 1h writes have different prices"
    base, write_5m, write_1h, hit, output = row
    usd = (
        input_tokens * base
        + cache_write_5m_tokens * write_5m
        + cache_write_1h_tokens * write_1h
        + cache_read_tokens * hit
        + output_tokens * output
    ) / 1_000_000
    return usd, None
