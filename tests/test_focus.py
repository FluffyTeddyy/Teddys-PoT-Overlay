"""The overlay stays on while the game is in plain sight, even when another window has the focus
(a click on another screen, then back to the game with a controller). Windows are faked, plus one
look at the real window list.

    python -m tests.test_focus
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")

from potoverlay import app, winutil  # noqa: E402

failures = 0
MON = {"left": 1920, "top": 0, "width": 1920, "height": 1080}  # game on the right-hand screen
MM = {"x": 1600, "y": 40, "w": 280, "h": 280}
GAME = (1920, 0, 3840, 1080, "Path of Titans")


def check(name, cond, detail=""):
    global failures
    print(("  ok   " if cond else "  FAIL ") + name + (f"  ({detail})" if detail else ""))
    failures += not cond


class patched:
    """Swap module attributes for the duration of a with-block."""

    def __init__(self, mod, **attrs):
        self.mod, self.attrs, self.old = mod, attrs, {}

    def __enter__(self):
        for k, v in self.attrs.items():
            self.old[k] = getattr(self.mod, k)
            setattr(self.mod, k, v)

    def __exit__(self, *exc):
        for k, v in self.old.items():
            setattr(self.mod, k, v)


def test_points() -> None:
    print("points checked over a region")
    pts = winutil.region_points(MON, MM)
    check("centre first, in screen pixels", pts[0] == (1920 + 1600 + 140, 40 + 140), str(pts[0]))
    check("all inside the region", all(3520 <= x < 3800 and 40 <= y < 320 for x, y in pts))
    check("empty region: no points", winutil.region_points(MON, {"x": 0, "y": 0, "w": 0, "h": 0}) == [])
    wins = [(0, 0, 800, 600, "Claude"), GAME]
    check("front-most window wins", winutil.front_title_at(wins, 100, 100) == "Claude")
    check("game behind nothing", winutil.front_title_at(wins, 3000, 100) == "Path of Titans")
    check("off every window", winutil.front_title_at(wins, 5000, 100) is None)


def test_in_view() -> None:
    print("game in view while another window has the focus")
    t, pts = ["path of titans"], winutil.region_points(MON, MM)
    cases = [
        ("Claude on the other screen: stays on", [(0, 0, 1920, 1080, "Claude"), GAME], True),
        ("Claude dragged over the minimap: hides", [(3400, 0, 3840, 600, "Claude"), GAME], False),
        ("Claude covering a bit of the game away from the minimap: stays on",
         [(1920, 600, 2600, 1080, "Claude"), GAME], True),
        ("game minimised (exclusive fullscreen alt-tab): hides", [(0, 0, 1920, 1080, "Claude")], False),
    ]
    for name, wins, want in cases:
        with patched(winutil, game_is_focused=lambda _t: False, _seen_windows=lambda w=wins: w):
            check(name, winutil.game_in_view(t, pts) is want)
    with patched(winutil, game_is_focused=lambda _t: True, _seen_windows=lambda: []):
        check("game in front: on", winutil.game_in_view(t, pts))

    def broken():
        raise OSError("no windows")
    with patched(winutil, game_is_focused=lambda _t: False, _seen_windows=broken):
        check("window list fails: hides, no crash", not winutil.game_in_view(t, pts))


def test_setting() -> None:
    print("the setting")
    cfg = {"only_when_game_focused": True, "game_window_titles": ["path of titans"],
           "stay_when_game_visible": True}
    with patched(app, game_in_view=lambda t, p: True, game_is_focused=lambda t: False):
        check("on: in view is enough", app.game_shown(cfg, MON, MM))
        cfg["stay_when_game_visible"] = False
        check("off: needs the focus, as before", not app.game_shown(cfg, MON, MM))
        cfg["only_when_game_focused"] = False
        check("only_when_game_focused off: always on", app.game_shown(cfg, MON, MM))
    from potoverlay import config as C
    check("on by default", C.DEFAULTS.get("stay_when_game_visible") is True)


def test_real_windows() -> None:
    print("the real window list on this PC")
    wins = winutil._seen_windows()
    check("lists windows", len(wins) > 0, f"{len(wins)} windows")
    check("all have a size", all(r > l and b > t for l, t, r, b, _ in wins))
    check("our own process left out", not any("pot quest overlay" in w[4].lower() for w in wins))
    print("     front windows:", [w[4] for w in wins[:5]])


if __name__ == "__main__":
    test_points()
    test_in_view()
    test_setting()
    test_real_windows()
    print("\nALL PASSED" if failures == 0 else f"\n{failures} FAILED")
    sys.exit(1 if failures else 0)
