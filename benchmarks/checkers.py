"""Machine checks for generated benchmark tasks. No model output is read."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path


def _py_files(root: Path, relative: str) -> list[Path]:
    base = root / relative
    if not base.exists():
        return []
    return sorted(path for path in base.rglob("*.py") if path.is_file())


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def check_inv_01(root: Path) -> int:
    """Float literals or float() calls under ledgerkit/modules or entry.py."""
    hits = 0
    targets = _py_files(root, "ledgerkit/modules")
    entry = root / "ledgerkit" / "entry.py"
    if entry.is_file():
        targets.append(entry)
    for path in targets:
        tree = ast.parse(_source(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                hits += 1
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "float":
                hits += 1
    return hits


def check_inv_02(root: Path) -> int:
    """datetime.now, time.time, date.today outside ledgerkit/clock.py."""
    hits = 0
    for path in _py_files(root, "ledgerkit"):
        if path.name == "clock.py":
            continue
        tree = ast.parse(_source(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            name = node.func.attr
            if name in {"now", "time", "today"}:
                hits += 1
    return hits


def check_no_metric_edit(root: Path, manifest: dict[str, str]) -> int:
    path = root / "relayd" / "metrics.py"
    if not path.is_file():
        return 0
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    expected = manifest.get("relayd/metrics.py")
    if expected is None:
        return 0
    return 0 if digest == expected else 1


def check_decisions(root: Path) -> dict[str, int]:
    """Steered-feature probes. A violation is a count, not a boolean hide."""
    text = ""
    limits = root / "orbit_api" / "limits.py"
    if limits.is_file():
        text = _source(limits)
    plugins = root / "orbit_plugins" / "hook.py"
    plugin = _source(plugins) if plugins.is_file() else ""
    cli = root / "orbit_cli" / "limits_cmd.py"
    cli_text = _source(cli) if cli.is_file() else ""
    store = root / "orbit_store" / "counters.py"
    store_text = _source(store) if store.is_file() else ""
    return {
        "D1": 0 if "DEFAULT_RPM = 50" in text else 1,
        "D2": 0 if "per_key" in text and "per_ip" not in text else 1,
        "D4": 0 if "print(" not in cli_text else 1,
        "D5": 0 if "CREATE TABLE" not in store_text else 1,
        "D6": 0 if "import orbit_api" not in plugin else 1,
    }


def run_checkers(root: Path, names: list[str], manifest: dict[str, str] | None = None) -> dict[str, int]:
    manifest = manifest or {}
    results: dict[str, int] = {}
    for name in names:
        if name == "INV-01":
            results[name] = check_inv_01(root)
        elif name == "INV-02":
            results[name] = check_inv_02(root)
        elif name == "B-INV-2":
            results[name] = check_no_metric_edit(root, manifest)
        elif name == "D":
            results.update(check_decisions(root))
        else:
            results[name] = 0
    return results
