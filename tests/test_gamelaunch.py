"""Starting with the game ("Path of Titans + Overlay" shortcut): finding the launcher, waiting for
the game without screen capture, and quitting when it closes. The game process is faked.

    python -m tests.test_gamelaunch
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")

from potoverlay import config as C  # noqa: E402
from potoverlay import gamelaunch as G  # noqa: E402

failures = 0


def check(name, cond, detail=""):
    global failures
    print(("  ok   " if cond else "  FAIL ") + name + (f"  ({detail})" if detail else ""))
    failures += not cond


def test_finding_exe() -> None:
    print("reading the launcher's location from registry values")
    d = Path(tempfile.mkdtemp(prefix="potoverlay_launcher_")) / "Some Games" / "Alderon"
    d.mkdir(parents=True)
    exe = d / "Alderon Games Launcher.exe"
    exe.write_bytes(b"")
    check("uninstall entry icon value 'path,0'", G._exe_from(f"{exe},0") == exe)
    check("link handler '\"path\" \"%1\"'", G._exe_from(f'"{exe}" "%1"') == exe)
    check("missing file: not used", G._exe_from(r"Z:\nope\Alderon Games Launcher.exe,0") is None)
    found = G.find_launcher()
    print("     on this PC:", found)


def test_waiting_mode() -> None:
    print("with the game: wait (no screen capture), start with the game, quit when it closes")
    from PySide6.QtWidgets import QApplication

    tmp = Path(tempfile.mkdtemp(prefix="potoverlay_withgame_"))
    cfg = json.loads(C.CONFIG_PATH.read_text(encoding="utf-8")) if C.CONFIG_PATH.exists() else {}
    cfg.update(toggle_hotkey="", refresh_hotkey="", check_for_data_updates=False)
    (tmp / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    C.CONFIG_PATH = tmp / "config.json"
    qapp = QApplication.instance() or QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)

    import potoverlay.app as A
    running = {"v": False}
    launched = []
    A.gamelaunch.game_running = lambda: running["v"]
    A.gamelaunch.start_launcher = lambda: launched.append(1) or True
    quits = []
    app = A.App(qapp, with_game=True)
    app.qapp = type("Q", (), {"quit": lambda self: quits.append(1)})()
    try:
        check("launcher opened", launched == [1])
        check("waiting: no tracking, no screen capture", app.waiting_for_game and app.tracker is None
              and app.quests is None)
        app._check_game()
        check("still waiting while the game isn't running", app.tracker is None and not quits)
        app.restart()  # e.g. a setting changed in the tray menu while waiting
        check("changing a setting while waiting doesn't start capturing", app.tracker is None)
        running["v"] = True
        app._check_game()
        check("game started: tracking starts", not app.waiting_for_game and app.tracker is not None)
        running["v"] = False
        app._check_game()
        check("one missed check isn't enough to quit (game restarting, hiccup)", not quits)
        app._check_game()
        check("game closed: overlay quits", quits == [1])
    finally:
        app.shutdown()

    app2 = A.App(qapp, with_game=False)
    try:
        check("normal run.bat: starts tracking right away, no game checks",
              app2.tracker is not None and not hasattr(app2, "_game_timer"))
    finally:
        app2.shutdown()


if __name__ == "__main__":
    test_finding_exe()
    test_waiting_mode()
    print("\nALL PASSED" if failures == 0 else f"\n{failures} FAILED")
    sys.exit(1 if failures else 0)
