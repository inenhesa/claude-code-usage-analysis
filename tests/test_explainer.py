from pathlib import Path

from cc_usage_study import explainer

SVG = Path(__file__).resolve().parents[1] / "outputs" / "figures" / "explainer.svg"


def test_committed_explainer_matches_generator():
    assert SVG.read_text(encoding="utf-8") == explainer.build()


def test_explainer_tells_the_story_and_ends_with_a_star():
    svg = explainer.build()
    for beat in ("THE PROBLEM", "WHY", "WHAT TO DO", "Avoid", "Do", "Star this repo if you found it useful"):
        assert beat in svg, beat
    assert chr(0x2014) not in svg
    assert "<script" not in svg
    assert "prefers-color-scheme:dark" in svg and "prefers-reduced-motion" in svg
