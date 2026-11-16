"""Write the README's animated explainer: problem, why, what to do, star.

    python -m cc_usage_study.explainer          # writes outputs/figures/explainer.svg

Four frames, a few large words each. Every number is a published README
finding. Plain CSS animation, no script, so it plays inside a GitHub <img>.
Light and dark colors follow prefers-color-scheme; with reduced motion the
"what to do" frame and the star line show without animation.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs" / "figures" / "explainer.svg"

W, H = 1200, 675
LOOP = 22.0  # seconds
FADE = 0.45
FRAMES = [(0.0, 5.0), (5.0, 10.5), (10.5, 18.0), (18.0, 22.0)]

STYLE_COLORS = """
.bg{fill:#FAF9F5}.card{fill:#FFFFFF;stroke:#E8E6DC}.ink{fill:#141413}.muted{fill:#5E5D59}
.acc{fill:#D97757}.good{fill:#3D3929}.bar{fill:#D1CFC5}.dot{fill:#D1CFC5}
@media (prefers-color-scheme:dark){
.bg{fill:#1F1E1D}.card{fill:#262624;stroke:#3D3929}.ink{fill:#F0EEE6}.muted{fill:#A6A39A}
.good{fill:#C2C0B6}.bar{fill:#5E5D59}.dot{fill:#5E5D59}}
"""

ALT = (
    "Animated explainer. The problem: after a 10-minute break, the next small Claude Code fix uses about 10x "
    "a fresh start (measured on a 97k-token session). Why: with an API key the session is cached for 5 minutes "
    "(1 hour on a subscription); after that the whole history is sent again. Fresh start 1x, back within "
    "5 minutes 1.5x, back after 10 minutes about 10x. What to do: avoid resuming after a break (about 10x), "
    "/compact after a break (about 10x), and turning off a tool mid-task (22x a normal turn, 2 runs). Do run "
    "/compact before a break (2.5x), /clear plus a 3-line note after one (about 1x, a fresh start), and stay "
    "in the session for quick fixes in a row (28% less than /clear between fixes, 1 run). "
    "Star this repo if you found it useful."
)


def _pct(t: float) -> str:
    return f"{100.0 * t / LOOP:.3f}%"


class _Anim:
    def __init__(self) -> None:
        self.rules: list[str] = []

    def window(self, start: float, end: float) -> str:
        """A class that fades in at ``start`` and out by ``end`` (seconds into the loop)."""
        name = f"a{len(self.rules)}"
        steps = [(0.0, 0)]
        if start > 0:
            steps.append((start, 0))
        steps += [(start + FADE, 1), (end - FADE, 1), (end, 0)]
        if end < LOOP:
            steps.append((LOOP, 0))
        frames = "".join(f"{_pct(t)}{{opacity:{o}}}" for t, o in steps)
        self.rules.append(
            f"@keyframes {name}{{{frames}}}.{name}{{opacity:0;animation:{name} {LOOP}s linear infinite both}}"
        )
        return name


def _text(x, y, s, size, cls="ink", weight=400, anchor="start", extra=""):
    return (
        f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}" '
        f'class="{cls}"{extra}>{escape(s)}</text>'
    )


def _label(s: str) -> str:
    return _text(80, 92, s, 24, "acc", 700, extra=' letter-spacing="3"')


def _dots(active: int) -> str:
    out = []
    for i in range(4):
        cls = "acc" if i == active else "dot"
        out.append(f'<circle cx="{W / 2 - 45 + 30 * i}" cy="625" r="7" class="{cls}"/>')
    return "".join(out)


def build() -> str:
    anim = _Anim()
    parts: list[str] = []

    # 1. The problem
    f = anim.window(*FRAMES[0])
    parts.append(
        f'<g class="{f}">'
        + _label("THE PROBLEM")
        + _text(W / 2, 250, "Resume a session after a 10-minute break:", 50, "ink", 600, "middle")
        + _text(W / 2, 450, "10x", 200, "acc", 800, "middle")
        + _text(W / 2, 525, "the usage of the same fix in a fresh session", 36, "muted", 400, "middle")
        + _text(W / 2, 572, "measured on a 97k-token session", 24, "muted", 400, "middle")
        + _dots(0)
        + "</g>"
    )

    # 2. Why, in one chart
    f = anim.window(*FRAMES[1])
    rows = [("Fresh start", 1.0, "1x", "bar"), ("Back within 5 min", 1.5, "1.5x", "bar"), ("Back after 10 min", 10.0, "~10x", "acc")]
    chart = []
    for i, (name, value, shown, cls) in enumerate(rows):
        y = 300 + i * 90
        chart.append(_text(80, y + 40, name, 32, "ink", 500))
        width = 62 * value
        chart.append(f'<rect x="420" y="{y}" width="{width:.0f}" height="56" rx="8" class="{cls}"/>')
        chart.append(_text(420 + width + 18, y + 41, shown, 38, "acc" if cls == "acc" else "ink", 800))
    parts.append(
        f'<g class="{f}">'
        + _label("WHY")
        + _text(80, 180, "Your session is cached for 5 minutes (API key).", 42, "ink", 600)
        + _text(80, 240, "After that, the whole history is sent again.", 42, "ink", 600)
        + "".join(chart)
        + _text(80, 590, "On a subscription the cache lasts 1 hour.", 24, "muted", 400)
        + _dots(1)
        + "</g>"
    )

    # 3. What to do: anti-patterns and best practices
    start, end = FRAMES[2]
    f = anim.window(start, end)
    avoid = [
        ("~10x", "Resume after a break", "vs a fresh start"),
        ("~10x", "/compact after a break", "vs a fresh start"),
        ("22x", "Turn off a tool mid-task", "vs a normal turn, 2 runs"),
    ]
    do = [
        ("2.5x", "/compact before a break", "vs a fresh start"),
        ("~1x", "/clear + 3-line note", "it is a fresh start"),
        ("-28%", "Same session, quick fixes", "vs /clear between fixes, 1 run"),
    ]
    cols = []
    for col, (x, head, mark, cls, items) in enumerate(
        [(80, "Avoid", "✕", "acc", avoid), (640, "Do", "✓", "good", do)]
    ):
        cols.append(f'<rect x="{x - 24}" y="140" width="528" height="430" rx="18" class="card" stroke-width="2"/>')
        cols.append(_text(x, 210, f"{mark}  {head}", 46, cls, 800))
        for i, (num, what, vs) in enumerate(items):
            item = anim.window(start + 0.6 + 0.35 * (3 * col + i), end)
            y = 300 + i * 95
            cols.append(
                f'<g class="{item}">'
                + _text(x, y, num, 36, cls, 800)
                + _text(x + 118, y, what, 30, "ink", 500)
                + _text(x + 118, y + 32, vs, 22, "muted", 400)
                + "</g>"
            )
    parts.append(f'<g class="{f} static">' + _label("WHAT TO DO") + "".join(cols) + _dots(2) + "</g>")

    # 4. Star
    f = anim.window(*FRAMES[3])
    parts.append(
        f'<g class="{f}">'
        + _text(W / 2, 300, "★", 150, "acc", 400, "middle")
        + _text(W / 2, 410, "Star this repo if you found it useful", 54, "ink", 700, "middle")
        + _text(W / 2, 470, "github.com/inevolin/claude-code-usage-analysis", 28, "muted", 400, "middle")
        + _dots(3)
        + "</g>"
    )
    # Reduced motion: frame 3 stays up; show the star line under it.
    parts.append(
        '<g class="rm" opacity="0">'
        + _text(W / 2, 630, "★ Star this repo if you found it useful", 26, "muted", 600, "middle")
        + "</g>"
    )

    style = (
        "text{font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif}"
        + STYLE_COLORS.replace("\n", "")
        + "".join(anim.rules)
        + "@media (prefers-reduced-motion:reduce){*{animation:none!important}"
        ".static,.static g{opacity:1!important}.static circle{opacity:0}.rm{opacity:1}}"
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        f'role="img" aria-label="{escape(ALT, {chr(34): "&quot;"})}">'
        "<title>A 10-minute break makes a small Claude Code fix use 10 times as much</title>"
        f"<style>{style}</style>"
        f'<rect width="{W}" height="{H}" rx="24" class="bg"/>'
        + "".join(parts)
        + "</svg>\n"
    )


def main() -> int:
    OUT.write_text(build(), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
