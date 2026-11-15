"""End-to-end check without the game: a fake minimap and fake quest panel are drawn on screen
for a few seconds and the real app (capture -> match -> OCR -> markers) runs against them.

    python -m tests.test_e2e

Uses a temporary folder, so your config.json and data/ are not touched.
"""
from __future__ import annotations

import math
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QImage, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from potoverlay import config as C  # noqa: E402
from potoverlay.quests import Quest, QuestDB  # noqa: E402
from tools.test_locator import fake_minimap, synthetic_map  # noqa: E402


class ImageWindow(QWidget):
    def __init__(self, bgr, x, y):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        h, w = bgr.shape[:2]
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        self.pix = QPixmap.fromImage(QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy())
        self.setGeometry(x, y, w, h)

    def paintEvent(self, e):
        p = QPainter(self)
        p.drawPixmap(0, 0, self.pix)
        p.end()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="potoverlay_e2e_"))
    C.CONFIG_PATH, C.MAPS_DIR, C.QUESTS_DIR, C.CACHE_DIR = (
        tmp / "config.json", tmp / "maps", tmp / "quests", tmp / "cache")
    C.MAPS_DIR.mkdir(parents=True)

    full = synthetic_map(3000)
    cv2.imwrite(str(C.MAPS_DIR / "synth.png"), full)
    cx, cy, zoom, angle, size = 1400.0, 1700.0, 0.9, 25.0, 200
    fern = (cx + 50, cy - 30)  # visible on the minimap
    QuestDB("synth", [
        Quest("Gather Ferns", [fern], color="#ff00ff"),
        Quest("Gather Moss", [(cx - 40, cy + 20)]),  # visible too, but not active
    ]).save(C.quest_file("synth"))

    app = QApplication(sys.argv)  # before mss, so Qt sets the DPI awareness (Windows allows it once)
    from potoverlay.capture import ScreenGrabber
    mon = ScreenGrabber(1).monitor
    mm = {"x": 300, "y": 300, "w": size, "h": size, "shape": "circle"}
    qp = {"x": 650, "y": 300, "w": 320, "h": 120}
    cfg = C.load_config()
    cfg.update(active_map="synth", minimap=mm, quest_panel=qp, only_when_game_focused=False,
               show_all_quests=False, ocr_interval_s=0.5, toggle_hotkey="", map_screen_mode=False,
               # never touch the network (a temporary, unstamped quest folder would look outdated
               # and start a real data update)
               refresh_hotkey="", check_for_data_updates=False,
               # like a calibration for this screen size, so it wins over the built-in layout
               layouts={f"{mon['width']}x{mon['height']}": {"minimap": mm, "quest_panel": qp}})
    C.save_config(cfg)

    panel = np.full((qp["h"], qp["w"], 3), (40, 55, 45), np.uint8)
    for i, line in enumerate(["QUESTS", "Gather Ferns 1/3"]):
        cv2.putText(panel, line, (12, 40 + 45 * i), cv2.FONT_HERSHEY_DUPLEX, 0.8, (235, 235, 235), 1, cv2.LINE_AA)

    app.setQuitOnLastWindowClosed(False)
    w1 = ImageWindow(fake_minimap(full, cx, cy, zoom, angle, size), mon["left"] + mm["x"], mon["top"] + mm["y"])
    w2 = ImageWindow(panel, mon["left"] + qp["x"], mon["top"] + qp["y"])
    w1.show()
    w2.show()

    from potoverlay.app import App
    states = []
    holder = {}

    def start():
        holder["app"] = App(app)
        holder["app"].tracker.state_ready.connect(states.append)

    QTimer.singleShot(300, start)
    QTimer.singleShot(8000, app.quit)
    app.exec()
    w1.close()
    w2.close()

    a = math.radians(angle)
    dx, dy = fern[0] - cx, fern[1] - cy
    expect = (size / 2 + zoom * (math.cos(a) * dx + math.sin(a) * dy),
              size / 2 + zoom * (-math.sin(a) * dx + math.cos(a) * dy))
    last = states[-1] if states else None
    ok = True
    print(f"states received: {len(states)}")
    if last is None or not last.tracking:
        print("FAIL: not tracking at the end", last.status if last else "")
        return 1
    labels = [m.label for m in last.markers]
    print("status:", last.status, "| markers:", labels)
    ferns = [m for m in last.markers if m.label == "Gather Ferns"]
    if not ferns:
        print("FAIL: active quest 'Gather Ferns' has no marker (OCR or matching problem)")
        ok = False
    else:
        err = math.hypot(ferns[0].x - expect[0], ferns[0].y - expect[1])
        print(f"fern marker at ({ferns[0].x:.1f}, {ferns[0].y:.1f}), expected ({expect[0]:.1f}, {expect[1]:.1f}),"
              f" error {err:.2f}px")
        ok &= err < 4
    if "Gather Moss" in labels:
        print("FAIL: inactive quest 'Gather Moss' was drawn")
        ok = False
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
