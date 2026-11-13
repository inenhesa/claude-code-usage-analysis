"""Deterministic bug hunt. Three chained bugs, fixed only by --solve."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def generate(out: Path, *, seed: int = 1337, scale: str = "S", variant: str = "P-doc", solve: bool = False) -> dict:
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    _write(out / "relayd" / "__init__.py", "")
    if solve:
        codec = (
            "def encode_varint(n: int) -> bytes:\n"
            "    if n < 0:\n"
            "        raise ValueError('negative')\n"
            "    out = bytearray()\n"
            "    while True:\n"
            "        byte = n & 0x7F\n"
            "        n >>= 7\n"
            "        if n:\n"
            "            out.append(byte | 0x80)\n"
            "        else:\n"
            "            out.append(byte)\n"
            "            return bytes(out)\n"
        )
        cache = (
            "def cache_key(payload: bytes) -> bytes:\n"
            "    return payload\n"
        )
        retry = (
            "MAX_BACKOFF_S = 30\n\n"
            "def backoff(attempt: int) -> int:\n"
            "    return min(MAX_BACKOFF_S, 2 ** attempt)\n"
        )
    else:
        codec = (
            "def encode_varint(n: int) -> bytes:\n"
            "    if n >= 16384:\n"
            "        return bytes([n & 0x7F])\n"
            "    return bytes([n & 0x7F])\n"
        )
        cache = (
            "def cache_key(payload: bytes) -> bytes:\n"
            "    return payload[:16]\n"
        )
        retry = (
            "MAX_BACKOFF_S = 30\n\n"
            "def backoff(attempt: int) -> int:\n"
            "    return 2 ** attempt\n"
        )
    _write(out / "relayd" / "codec.py", codec)
    _write(out / "relayd" / "cache.py", cache)
    _write(out / "relayd" / "retry.py", retry)
    _write(
        out / "relayd" / "metrics.py",
        f"# frozen metrics module seed={seed} noise={rng.random()}\nVALUE = 1\n",
    )
    _write(out / "logs" / ".gitkeep", "")
    _write(
        out / "tests" / "visible" / "test_smoke.py",
        "def test_metrics_constant():\n    from relayd.metrics import VALUE\n    assert VALUE == 1\n",
    )
    manifest = {}
    for path in sorted(p for p in out.rglob("*") if p.is_file() and p.name != "MANIFEST.json"):
        manifest[path.relative_to(out).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    payload = {
        "generator": "gen_noisy_bughunt",
        "generator_version": 1,
        "seed": seed,
        "scale": scale,
        "variant": variant,
        "solved": solve,
        "files": manifest,
    }
    _write(out / "MANIFEST.json", json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--scale", default="S")
    parser.add_argument("--variant", default="P-doc")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--solve", action="store_true")
    args = parser.parse_args(argv)
    generate(args.out, seed=args.seed, scale=args.scale, variant=args.variant, solve=args.solve)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
