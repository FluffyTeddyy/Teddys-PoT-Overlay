"""Tray "Update speed" presets: picking one saves it and the menu shows it; custom values show too.

    python -m tests.test_speed_menu

Uses a temporary config.json, so yours isn't touched.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")

from PySide6.QtWidgets import QApplication  # noqa: E402

from potoverlay import config as C  # noqa: E402

failures = 0


def check(name, cond, detail=""):
    global failures
    print(("  ok   " if cond else "  FAIL ") + name + (f"  ({detail})" if detail else ""))
    failures += not cond


def speed_items(app) -> list[tuple[str, bool, bool]]:
    menu = next(a.menu() for a in app.menu.actions() if a.menu() and a.text() == "Update speed")
    return [(a.text(), a.isChecked(), a.isEnabled()) for a in menu.actions() if a.text()]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="potoverlay_speed_"))
    shutil.copy(C.CONFIG_PATH, tmp / "config.json")
    C.CONFIG_PATH = tmp / "config.json"
    cfg = json.loads(C.CONFIG_PATH.read_text(encoding="utf-8"))
    cfg.update(toggle_hotkey="", refresh_hotkey="", check_for_data_updates=False, tracking_fps=10,
               map_screen_fps=12)
    C.CONFIG_PATH.write_text(json.dumps(cfg), encoding="utf-8")

    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    from potoverlay.app import App
    app = App(qapp)
    try:
        items = speed_items(app)
        print("menu:", *[f"{'[x]' if c else '[ ]'} {t}" for t, c, _ in items], sep="\n   ")
        check("4 presets + 'set your own' item", len(items) == 5, str(len(items)))
        check("Balanced is ticked by default", any(c and t.startswith("Balanced") for t, c, _ in items))

        app.set_speed(20, 15)  # what clicking "Smooth" does
        disk = json.loads(C.CONFIG_PATH.read_text(encoding="utf-8"))
        check("Smooth saved to config.json", (disk["tracking_fps"], disk["map_screen_fps"]) == (20, 15),
              f"{disk['tracking_fps']} / {disk['map_screen_fps']}")
        check("tracker restarted with the new speed", app.tracker is not None and app.tracker.cfg["tracking_fps"] == 20)
        check("Smooth now ticked", any(c and t.startswith("Smooth") for t, c, _ in speed_items(app)))

        # Hand-edited values that match no preset, then "Reload config and quests".
        disk.update(tracking_fps=15, map_screen_fps=18)
        C.CONFIG_PATH.write_text(json.dumps(disk), encoding="utf-8")
        app.restart()
        items = speed_items(app)
        custom = [(t, c, e) for t, c, e in items if t.startswith("Custom")]
        check("custom values shown as ticked 'Custom' entry", bool(custom) and custom[0][1],
              custom[0][0] if custom else str(items))
        check("no preset ticked for custom values", not any(c for t, c, _ in items if not t.startswith("Custom")))
        check("tracker uses the custom speed", app.tracker.cfg["map_screen_fps"] == 18)

        disk.update(map_screen_fps=0)  # nonsense from a hand edit must not crash
        C.CONFIG_PATH.write_text(json.dumps(disk), encoding="utf-8")
        app.restart()
        check("0 fps in config doesn't crash", app.tracker is not None and app.tracker.isRunning())
    finally:
        app.shutdown()
    print("\nALL PASSED" if failures == 0 else f"\n{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
