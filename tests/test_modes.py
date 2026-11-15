"""Minimap <-> in-game map screen switching, with the real tracker thread fed scripted frames.

    python -m tests.test_modes

Nothing is shown on screen: the screen grabber is replaced by one that returns
the HUD screenshot, then a simulated map screen, then the HUD again.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")

import numpy as np  # noqa: E402
from PySide6.QtCore import QCoreApplication, QTimer  # noqa: E402

import potoverlay.app as A  # noqa: E402
from potoverlay import config as C  # noqa: E402
from potoverlay.locator import imread_any  # noqa: E402
from potoverlay.quests import ActiveQuestTracker, QuestDB  # noqa: E402
from tests.test_mapview import fake_map_screen  # noqa: E402

HUD = imread_any(Path(__file__).parent / "fixtures" / "hud_1920x1200.png")
HUD = np.ascontiguousarray(np.pad(HUD, ((0, 1200 - HUD.shape[0]), (0, 1920 - HUD.shape[1]), (0, 0))))
# (seconds from start, what's on screen). The map opens twice: the first time it's found the slow
# way and its title bar is learned; the second time the title bar should make it near-instant.
SCRIPT = [(0.0, "hud"), (3.0, "map"), (6.0, "hud"), (8.0, "map"), (10.0, "hud")]
T0 = time.perf_counter()
frames: dict[str, np.ndarray] = {"hud": HUD}


class FakeGrabber:
    def __init__(self, monitor_index: int = 1):
        self.monitor = {"left": 0, "top": 0, "width": 1920, "height": 1200}

    def grab(self, region=None):
        t = time.perf_counter() - T0
        kind = [k for start, k in SCRIPT if t >= start][-1]
        img = frames[kind]
        if region is None:
            return img.copy()
        x, y, w, h = int(region["x"]), int(region["y"]), int(region["w"]), int(region["h"])
        return np.ascontiguousarray(img[y:y + h, x:x + w])

    def close(self):
        pass


def main() -> int:
    import cv2
    cv2.setNumThreads(2)  # same CPU limit as the real app
    full = imread_any(C.map_image_path("riparia"))
    zoom, center = 0.55, (1700.0, 1500.0)
    frames["map"], z, off = fake_map_screen(full, zoom, center)
    real = Path(__file__).parent / "fixtures" / "mapscreen_riparia.png"
    if "--real" in sys.argv:
        # The real map window: the minimap still half shows beside it and keeps matching weakly.
        frames["map"] = imread_any(real)

    A.ScreenGrabber = FakeGrabber
    import tempfile
    C.DATA_DIR = Path(tempfile.mkdtemp(prefix="potoverlay_modes_"))  # keep the learned title bar out of data/
    cfg = C.load_config()
    C.apply_layout(cfg, 1920, 1200)
    # Independent of your own tray choices in config.json:
    cfg.update(only_when_game_focused=False, active_map="riparia", auto_detect_map=False,
               map_screen_mode=True, map_screen_fps=12, tracking_fps=10, show_all_quests=False)
    db = QuestDB.load(C.quest_file("riparia"), "riparia")
    lines = ['Gather Mushrooms', '- Collect 15 Milk Cap Mushrooms 0/15', 'Cicada Molt',
             '- Collect 20 Cicada Molt 0/20']
    groups = ActiveQuestTracker(db).update(lines)

    app = QCoreApplication(sys.argv)
    tr = A.TrackerWorker(cfg, db)
    tr.set_active(groups)
    log = []  # (t, kind, state)
    tr.state_ready.connect(lambda s: log.append((time.perf_counter() - T0, "minimap", s)))
    tr.view_ready.connect(lambda s: log.append((time.perf_counter() - T0, "view", s)))
    switches = []
    tr.map_screen.connect(lambda on: switches.append((round(time.perf_counter() - T0, 1), on)))
    tr.start()
    QTimer.singleShot(12500, app.quit)
    app.exec()
    tr.stop()
    tr.wait(5000)

    failures = 0

    def check(name, cond, detail=""):
        nonlocal failures
        print(("  ok   " if cond else "  FAIL ") + name + (f"  ({detail})" if detail else ""))
        failures += not cond

    print("switches (time, map open):", switches)
    check("map screen opened and closed twice", [on for _, on in switches] == [True, False, True, False],
          str(switches))
    if len(switches) == 4:
        check("1st open within 1.5 s", switches[0][0] - 3.0 < 1.5, f"{switches[0][0] - 3.0:.2f} s")
        check("closes within 0.5 s", max(switches[1][0] - 6.0, switches[3][0] - 10.0) < 0.5,
              f"{switches[1][0] - 6.0:.2f} s / {switches[3][0] - 10.0:.2f} s")
        if "--real" in sys.argv:  # the simulated screen has no title bar to learn
            check("2nd open fast via the title bar", switches[2][0] - 8.0 < 0.4, f"{switches[2][0] - 8.0:.2f} s")
    hidden_late = [t for t, k, s in log if k == "view" and s.visible and (6.25 < t < 8 or t > 10.25)]
    check("no stale map markers after closing", not hidden_late, f"{len(hidden_late)} late updates")
    tracking_before = [s for t, k, s in log if k == "minimap" and t < 3 and s.tracking]
    tracking_after = [s for t, k, s in log if k == "minimap" and t > 11 and s.tracking]
    check("minimap tracked before and after", bool(tracking_before) and bool(tracking_after))
    views = [s for t, k, s in log if k == "view" and s.visible]
    check("map screen markers sent", bool(views), f"{len(views)} updates")
    in_map = [t for t, k, s in log if k == "view" and s.visible and 4.5 < t < 6.0]
    rate = len(in_map) / 1.5
    check("map updates at 10+ per second", rate >= 10, f"{rate:.1f}/s")
    if views:
        v = views[-1]
        names = {m.label for m in v.markers}
        check("only active quests on the map", names <= {"Gather Mushrooms", "Cicada Molt"} and names, str(names))
        if "--real" not in sys.argv:
            # Cicada Molt (Big Tree Overlook version, nearest to the player) points, projected by hand.
            q = next(q for q in A.pick_nearest(groups, db.by_key(), (1709, 1524)) if q.name == "Cicada Molt")
            truth = np.asarray(q.points) * z + off
            got = np.array([(m.x, m.y) for m in v.markers if m.label == "Cicada Molt"])
            if len(got):
                d = np.min(np.hypot(got[:, None, 0] - truth[None, :, 0], got[:, None, 1] - truth[None, :, 1]),
                           axis=1)
                check("Cicada Molt markers on the right spots", d.max() < 3,
                      f"max {d.max():.2f}px, {len(got)} markers")
            else:
                check("Cicada Molt markers present", False)
        else:
            check("markers inside the map window",
                  all(165 <= m.x <= 1755 and 185 <= m.y <= 1010 for m in v.markers), f"{len(v.markers)} markers")
        corner = (165, 185) if "--real" in sys.argv else (0, 0)  # the real map is a window
        check("legend included, inside the map's area", bool(v.legend) and v.legend_pos is not None
              and v.legend_pos[0] > corner[0] and v.legend_pos[1] > corner[1], f"{v.legend} at {v.legend_pos}")
    hidden_during = [s for t, k, s in log if k == "minimap" and 4.5 < t < 6 and s.visible]
    check("minimap overlay hidden while the map is open", not hidden_during, f"{len(hidden_during)} visible")
    print("\nALL PASSED" if failures == 0 else f"\n{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
