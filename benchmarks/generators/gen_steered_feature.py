"""Iterative feature task. Decisions D1-D6 hold only after --solve."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


CHECKPOINTS = [
    "Add token-bucket rate limiting. Default 100 requests per minute. Key rate_limit.rpm.",
    "Change of plan: default is 50 rpm, per API key, not per IP. Keep the key name rate_limit.rpm.",
    "Add orbit limits show. Use orbit_cli.render.table, never print() directly.",
    "Persist counters through the existing KV interface. No new tables.",
    "Add plugin hook on_limit_exceeded. Plugins must not import orbit_api.",
    "Finalize: run the suite and add a CHANGELOG entry under Unreleased.",
]


def generate(out: Path, *, seed: int = 1337, scale: str = "S", variant: str = "P-doc", solve: bool = False) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    if solve:
        limits = "DEFAULT_RPM = 50\nSCOPE = 'per_key'\n"
        cli = "def show():\n    return 'table'\n"
        store = "def save(key, value):\n    return {key: value}\n"
        plugin = "def on_limit_exceeded():\n    return None\n"
    else:
        limits = "DEFAULT_RPM = 100\nSCOPE = 'per_ip'\n"
        cli = "def show():\n    print('limits')\n"
        store = "def save(key, value):\n    sql = 'CREATE TABLE counters (k, v)'\n    return sql\n"
        plugin = "import orbit_api\n\ndef on_limit_exceeded():\n    return orbit_api\n"
    _write(out / "orbit_api" / "__init__.py", "")
    _write(out / "orbit_api" / "limits.py", limits)
    _write(out / "orbit_cli" / "__init__.py", "")
    _write(out / "orbit_cli" / "limits_cmd.py", cli)
    _write(out / "orbit_store" / "__init__.py", "")
    _write(out / "orbit_store" / "counters.py", store)
    _write(out / "orbit_plugins" / "__init__.py", "")
    _write(out / "orbit_plugins" / "hook.py", plugin)
    _write(out / "docs" / "ARCHITECTURE.md", "Plugins never import orbit_api.\n")
    _write(out / "CHANGELOG.md", "## Unreleased\n")
    lines = [{"type": "user", "message": {"role": "user", "content": text}} for text in CHECKPOINTS]
    _write(out / "checkpoints.jsonl", "".join(json.dumps(row) + "\n" for row in lines))
    _write(
        out / "tests" / "visible" / "test_changelog.py",
        "from pathlib import Path\n\ndef test_unreleased_section_exists():\n"
        "    assert 'Unreleased' in Path('CHANGELOG.md').read_text(encoding='utf-8')\n",
    )
    manifest = {}
    for path in sorted(p for p in out.rglob("*") if p.is_file() and p.name != "MANIFEST.json"):
        manifest[path.relative_to(out).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    payload = {
        "generator": "gen_steered_feature",
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
