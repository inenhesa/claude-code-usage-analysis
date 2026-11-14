import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOLLAR = re.compile(r"\$[0-9]")


def _public_text_files(suffixes=(".md", ".py", ".toml")):
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {".venv", "node_modules", ".git", "__pycache__", ".pytest_cache"} for part in path.parts):
            continue
        if path.suffix.lower() in suffixes:
            yield path


def _user_facing():
    yield ROOT / "README.md"
    yield from sorted((ROOT / "docs").glob("*.md"))
    yield from sorted((ROOT / "benchmarks").glob("*.md"))
    yield ROOT / "outputs" / "summary.json"


def test_no_em_dashes_anywhere():
    offenders = []
    for path in _public_text_files((".md", ".py", ".toml", ".json")):
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


def test_front_pages_drop_the_old_framing():
    for relative in ("README.md", "docs/article-draft.md"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        lowered = text.lower()
        assert "4x" not in lowered, relative
        assert ("link" + "edin") not in lowered, relative
        assert ("200" + "%") not in lowered, relative
        assert "\u2014" not in text, relative
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "250k" not in readme
    assert "25%" not in readme
    assert "3.9x" not in readme


def test_user_facing_text_has_no_dollar_amounts():
    offenders = []
    for path in _user_facing():
        text = path.read_text(encoding="utf-8")
        for match in DOLLAR.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{path.relative_to(ROOT)}:{line}")
    assert offenders == []


def test_no_private_material_in_public_pages():
    needles = ("sk-" + "ant", ".claude_" + "study_key", "/home/", "/Users/", "/tmp/")
    offenders = []
    for path in _user_facing():
        text = path.read_text(encoding="utf-8")
        offenders.extend(f"{path.relative_to(ROOT)}: {needle}" for needle in needles if needle in text)
    assert offenders == []


def test_readme_leads_with_the_hero_and_rules_and_embeds_the_charts():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.startswith("# Your Claude Code coffee break costs 10x")
    hero = readme.index("outputs/figures/hero_coffee_break.png")
    rules = readme.index("## The rules")
    measured = readme.index("## How we measured")
    assert hero < rules < measured
    assert "(#how-we-measured)" in readme[:measured]
    # The top stays short: title, hero, rules and copy-paste fit in roughly one screen.
    assert readme[:measured].count("\n") < 60
    assert readme.index("### 1. The coffee-break tax") > measured
    for figure in (
        "hero_coffee_break.png",
        "ttl_price_of_a_pause.png",
        "resume_crossover.png",
        "cron_resume_vs_fresh.png",
        "live_compaction_arms.png",
        "live_clear_vs_warm.png",
        "live_client_comparison.png",
        "scenario_comparison.png",
        "eoq_curve.png",
        "context_sawtooth.png",
    ):
        assert f"outputs/figures/{figure}" in readme, figure
        assert (ROOT / "outputs" / "figures" / figure).is_file(), figure
    assert "ttl_cliff" not in readme
    # Caveats move below the fold but must still be there.
    details = readme[measured:]
    assert "cache-weighted" in details
    assert "1 / 1.25 / 2 / 0.05 / 5" in details
    assert "Honest limits" in details
    assert "n=1" in details
    assert "(modeled)" in readme[:measured]


def test_docs_reconcile_the_compound_trigger():
    for relative in ("docs/reference.md", "docs/examples.md", "benchmarks/protocol.md", "README.md"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "62,500" not in text, relative
    reference = (ROOT / "docs" / "reference.md").read_text(encoding="utf-8")
    assert "CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS" in reference
    assert "100,000" in reference
    client = (ROOT / "benchmarks" / "client_comparison.md").read_text(encoding="utf-8").lower()
    assert "cold start" not in client
