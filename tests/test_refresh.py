"""Manual quest refresh (F9) and the quest log.

    python -m tests.test_refresh

Uses the 1920x1200 HUD screenshot through a fake screen grabber, and a temporary data folder.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")

import numpy as np  # noqa: E402
from PySide6.QtCore import QCoreApplication, QTimer  # noqa: E402

import potoverlay.app as A  # noqa: E402
from potoverlay import config as C  # noqa: E402
from potoverlay.locator import imread_any  # noqa: E402
from potoverlay.quests import ActiveQuestTracker, Quest, QuestDB  # noqa: E402

failures = 0


def check(name, cond, detail=""):
    global failures
    print(("  ok   " if cond else "  FAIL ") + name + (f"  ({detail})" if detail else ""))
    failures += not cond


def test_tracker_refresh() -> None:
    print("tracker refresh logic")
    db = QuestDB("t", [Quest("Gather Ferns", [(1, 1)], objectives=["Collect 5 Ferns"]),
                       Quest("Gather Moss", [(2, 2)], objectives=["Collect 3 Moss"]),
                       Quest("Hunt", [(3, 3)], objectives=["Deliver 1 Bones"])])
    t = ActiveQuestTracker(db, miss_tolerance=2)
    t.update(["Quests", "Gather Ferns", "- Collect 5 Ferns 0/5"])
    moss = ["Quests", "Gather Moss", "- Collect 3 Moss 0/3"]
    junk = ["Quests", "- Somethin unreadable"]
    bones = ["Quests", "- Deliver 1 Bones 0/1"]  # a one-off misread in one capture
    captures = [[moss, junk, junk], [junk, moss, junk], [junk, junk, bones]]  # 3 captures x 3 reads
    names = sorted(n for g in t.refresh(captures) for n in g)
    check("quest in 2 of 3 captures is kept, even if only one reading of each saw it",
          "Gather Moss" in names, str(names))
    check("something in only 1 capture is not added", "Hunt" not in names, str(names))
    check("refresh reports what it removed", t.dropped == ["Gather Ferns"], str(t.dropped))
    check("titles shown", [lab for _, lab in t.labeled] == ["Gather Moss"], str(t.labeled))
    before = t.active
    t.refresh([["Settings", "Graphics"], []])
    check("refresh with no quest list on screen changes nothing", t.active == before)
    t.refresh([["Quests"]])
    check("refresh of an empty quest list clears it", t.active == [])


class FakeGrab:
    img = None

    def __init__(self, monitor_index: int = 1):
        self.monitor = {"left": 0, "top": 0, "width": 1920, "height": 1200}

    def grab(self, r=None):
        im = FakeGrab.img
        return im.copy() if r is None else np.ascontiguousarray(im[r["y"]:r["y"] + r["h"], r["x"]:r["x"] + r["w"]])

    def close(self):
        pass


def test_worker_refresh() -> None:
    print("F9 refresh through the quest-reading thread (real OCR on the HUD screenshot)")
    hud = imread_any(Path(__file__).parent / "fixtures" / "hud_1920x1200.png")
    FakeGrab.img = np.ascontiguousarray(np.pad(hud, ((0, 1200 - hud.shape[0]), (0, 1920 - hud.shape[1]), (0, 0))))
    A.ScreenGrabber = FakeGrab
    C.DATA_DIR = Path(tempfile.mkdtemp(prefix="potoverlay_refresh_"))
    cfg = C.load_config()
    C.apply_layout(cfg, 1920, 1200)
    cfg.update(only_when_game_focused=False, ocr_interval_s=60)  # only the first read + F9 reads
    db = QuestDB.load(C.quest_file("riparia"), "riparia")
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)
    w = A.QuestWorker(cfg, db)
    events = []
    w.active_changed.connect(lambda labeled, lines: events.append(("active", len({it[1] for it in labeled}))))
    w.refreshed.connect(lambda n: events.append(("refreshed", n)))
    w.start()
    t0 = {}

    def after_first_read():
        # Simulate the bug: a quest got lost although it's still on screen.
        w.tracker._items.popitem()
        t0["t"] = time.perf_counter()
        w.request_refresh()  # what F9 does (normal reads are 60 s apart here)

    def paused_refresh():
        w.paused = True
        w.request_refresh()

    QTimer.singleShot(1500, after_first_read)
    QTimer.singleShot(6000, paused_refresh)
    QTimer.singleShot(8000, app.quit)
    app.exec()
    w.stop()
    w.wait(5000)
    first = next((n for k, n in events if k == "active"), None)
    refreshes = [n for k, n in events if k == "refreshed"]
    print("     events:", events)
    check("first normal read found the quests", first is not None and first >= 4, str(first))
    check("F9 refresh found them all again", bool(refreshes) and refreshes[0] == first, str(refreshes))
    check("refresh while the map is open says so", -1 in refreshes, str(refreshes))
    logf = C.DATA_DIR / "quest_log.txt"
    text = logf.read_text(encoding="utf-8") if logf.exists() else ""
    check("quest log written", "added:" in text and "refresh (F9) found:" in text, f"{len(text)} chars")
    print("     log excerpt:", " / ".join(text.splitlines()[:2])[:200])


def test_hotkey() -> None:
    print("F9 hotkey")
    from potoverlay.winutil import GlobalHotkey
    hk = GlobalHotkey("F9")
    ok = hk.registered.wait(2)
    if not ok and hk.failed.is_set():
        # Windows says F9 is taken: almost always the overlay itself, running while you test.
        print("  skip F9 registered with Windows  (F9 is in use, probably by your running overlay)")
    else:
        check("F9 registered with Windows", ok)
    hk.stop()


if __name__ == "__main__":
    test_tracker_refresh()
    test_worker_refresh()
    test_hotkey()
    print("\nALL PASSED" if failures == 0 else f"\n{failures} FAILED")
    sys.exit(1 if failures else 0)
