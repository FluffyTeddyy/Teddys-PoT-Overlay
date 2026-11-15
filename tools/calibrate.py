"""Mark where the minimap and quest panel are on screen.

Run with the game open. After a countdown it takes a screenshot and shows it
full screen. Drag a box around the minimap, press Enter, then drag a box around
the quest list on the right, press Enter. Saved to config.json.

    python -m tools.calibrate              # 5 s countdown, then capture
    python -m tools.calibrate --image shot.png
"""
from __future__ import annotations

import argparse
import os
import sys
import time

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")

import cv2  # noqa: E402
from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget  # noqa: E402

from potoverlay import config as C  # noqa: E402
from potoverlay.capture import ScreenGrabber  # noqa: E402
from potoverlay.locator import imread_any  # noqa: E402

STEPS = [
    ("minimap", "Drag a box tightly around the MINIMAP.  C = toggle circle/square  |  Enter = next  |  Esc = cancel"),
    ("quest_panel", "Drag a box around the QUEST LIST on the right (include room for more quests).  "
                    "Enter = save  |  S = skip  |  Esc = cancel"),
]


class Picker(QWidget):
    def __init__(self, shot, monitor: dict, cfg: dict):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.cfg = cfg
        self.shot = shot
        h, w = shot.shape[:2]
        rgb = cv2.cvtColor(shot, cv2.COLOR_BGR2RGB)
        self.pix = QPixmap.fromImage(QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy())
        self.setGeometry(monitor["left"], monitor["top"], w, h)
        self.setCursor(Qt.CrossCursor)
        self.step = 0
        self.shape = cfg["minimap"].get("shape", "circle")
        self.rects: dict[str, QRect] = {}
        for key, _ in STEPS:
            r = cfg.get(key, {})
            if C.region_ok(r):
                self.rects[key] = QRect(int(r["x"]), int(r["y"]), int(r["w"]), int(r["h"]))
        self.drag_start: QPoint | None = None
        self.saved = False

    @property
    def key(self) -> str:
        return STEPS[self.step][0]

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.drag_start = e.position().toPoint()
            self.rects[self.key] = QRect(self.drag_start, self.drag_start)
            self.update()

    def mouseMoveEvent(self, e):
        if self.drag_start is not None:
            end = e.position().toPoint()
            if self.key == "minimap" and self.shape == "circle":
                # Keep it square for a round minimap.
                d = max(abs(end.x() - self.drag_start.x()), abs(end.y() - self.drag_start.y()))
                end = QPoint(self.drag_start.x() + (d if end.x() >= self.drag_start.x() else -d),
                             self.drag_start.y() + (d if end.y() >= self.drag_start.y() else -d))
            self.rects[self.key] = QRect(self.drag_start, end).normalized()
            self.update()

    def mouseReleaseEvent(self, e):
        self.drag_start = None

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key_Escape:
            self.close()
        elif k == Qt.Key_C and self.key == "minimap":
            self.shape = "rect" if self.shape == "circle" else "circle"
            self.update()
        elif k == Qt.Key_S and self.key == "quest_panel":
            self.rects.pop("quest_panel", None)
            self.finish()
        elif k in (Qt.Key_Return, Qt.Key_Enter):
            r = self.rects.get(self.key)
            if r is None or r.width() < 8 or r.height() < 8:
                return
            if self.step + 1 < len(STEPS):
                self.step += 1
                self.update()
            else:
                self.finish()

    def finish(self):
        disk = C.load_config()
        mm = self.rects["minimap"]
        disk["minimap"] = {"x": mm.x(), "y": mm.y(), "w": mm.width(), "h": mm.height(), "shape": self.shape}
        qp = self.rects.get("quest_panel")
        disk["quest_panel"] = ({"x": qp.x(), "y": qp.y(), "w": qp.width(), "h": qp.height()}
                               if qp else {"x": 0, "y": 0, "w": 0, "h": 0})
        disk["monitor"] = self.cfg["monitor"]
        sw, sh = int(self.shot.shape[1]), int(self.shot.shape[0])
        disk["screen_size"] = [sw, sh]
        # Remember it for this screen size, so switching resolutions later just works.
        disk.setdefault("layouts", {})[f"{sw}x{sh}"] = {
            "minimap": disk["minimap"], "quest_panel": disk["quest_panel"]}
        try:
            C.save_config(disk)
            C.DATA_DIR.mkdir(parents=True, exist_ok=True)
            ok, buf = cv2.imencode(".png", self.shot)
            if ok:
                buf.tofile(str(C.CALIBRATION_SHOT))
            self.saved = True
        except OSError as e:
            QMessageBox.warning(self, "Calibrate", f"Could not save config.json:\n{e}")
        self.close()

    def paintEvent(self, e):
        p = QPainter(self)
        p.drawPixmap(0, 0, self.pix)
        p.fillRect(self.rect(), QColor(0, 0, 0, 90))
        colors = {"minimap": QColor(80, 220, 255), "quest_panel": QColor(255, 200, 60)}
        for key, r in self.rects.items():
            p.drawPixmap(r, self.pix, r)  # un-dim the selection
            p.setPen(QPen(colors[key], 2, Qt.SolidLine if key == self.key else Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r)
            if key == "minimap" and self.shape == "circle":
                p.drawEllipse(r)
        p.setFont(QFont("Segoe UI", 13, QFont.DemiBold))
        text = f"Step {self.step + 1}/{len(STEPS)}:  {STEPS[self.step][1]}"
        br = p.boundingRect(self.rect().adjusted(0, 20, 0, 0), Qt.AlignHCenter | Qt.AlignTop, text)
        p.fillRect(br.adjusted(-12, -6, 12, 6), QColor(0, 0, 0, 200))
        p.setPen(QColor(255, 255, 255))
        p.drawText(br, Qt.AlignCenter, text)
        p.end()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", help="use this screenshot instead of capturing the screen")
    ap.add_argument("--delay", type=float, default=5.0, help="seconds before capturing (switch to the game)")
    ap.add_argument("--monitor", type=int, help="monitor number (1 = primary)")
    args = ap.parse_args()

    # Qt first: it sets the DPI awareness, which Windows allows only once (mss would set it too).
    app = QApplication(sys.argv)
    cfg = C.load_config()
    if args.monitor:
        cfg["monitor"] = args.monitor
    grab = ScreenGrabber(cfg["monitor"])
    monitor = grab.monitor
    if args.image:
        shot = imread_any(args.image)
        if shot is None:
            print(f"Could not read {args.image}")
            return 1
    else:
        for i in range(int(args.delay), 0, -1):
            print(f"Switch to Path of Titans... capturing in {i}", flush=True)
            time.sleep(1)
        shot = grab.grab()
    grab.close()

    C.apply_layout(cfg, shot.shape[1], shot.shape[0])  # start from the boxes known for this size
    w = Picker(shot, monitor, cfg)
    w.show()
    w.activateWindow()
    w.raise_()
    app.exec()
    print("Saved to config.json" if w.saved else "Cancelled, nothing saved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
