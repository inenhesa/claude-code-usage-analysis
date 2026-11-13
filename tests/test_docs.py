from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_markdown_has_no_em_dashes():
    offenders = []
    for path in ROOT.rglob("*.md"):
        if any(part in {".venv", "node_modules"} for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8")
        if "\u2014" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
