from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _public_text_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {".venv", "node_modules", ".git", "__pycache__"} for part in path.parts):
            continue
        if path.suffix.lower() in {".md", ".py", ".toml"}:
            yield path


def test_markdown_has_no_em_dashes():
    offenders = []
    for path in _public_text_files():
        text = path.read_text(encoding="utf-8")
        if "\u2014" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_public_pages_do_not_mention_removed_claims():
    offenders = []
    for path in _public_text_files():
        text = path.read_text(encoding="utf-8").lower()
        banned = ("link" + "edin", "200" + "%")
        if any(needle in text for needle in banned):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
