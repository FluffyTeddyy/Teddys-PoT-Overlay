"""The tray's "Update quest data": runs in the background while the overlay keeps working,
shows progress, then reloads by itself. Offline: the real updater is swapped for a fake one.

    python -m tests.test_update_flow
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")

from PySide6.QtWidgets import QApplication  # noqa: E402

from potoverlay import config as C  # noqa: E402

failures = 0


def check(name, cond, detail=""):
    global failures
    print(("  ok   " if cond else "  FAIL ") + name + (f"  ({detail})" if detail else ""))
    failures += not cond


FAKE_UPDATER = r"""
import json, sys, time
from pathlib import Path
q = Path(sys.argv[1])
print("[riparia] map image unchanged, kept", flush=True)
print("quests: 10 for riparia, 10 to download (~0 min)", flush=True)
for i in (5, 10):
    time.sleep(0.6)
    print(f"  {i}/10", flush=True)
if sys.argv[2] == "fail":
    print("giving up on https://potservers.net/api/v1/quests/x", flush=True)
    sys.exit(1)
d = json.loads(q.read_text(encoding="utf-8"))
d["quests"].append({"name": "Brand New Quest", "points": [[1, 1]], "aliases": [], "color": ""})
q.write_text(json.dumps(d), encoding="utf-8")
print("done", flush=True)
"""


def run(app_cls, qapp, outcome: str, trigger: str = "menu", auto: bool = True) -> tuple:
    tmp = Path(tempfile.mkdtemp(prefix="potoverlay_flow_"))
    (tmp / "quests").mkdir()
    shutil.copy(C.ROOT / "data" / "quests" / "riparia.json", tmp / "quests" / "riparia.json")
    fake = tmp / "fake_updater.py"
    fake.write_text(FAKE_UPDATER, encoding="utf-8")
    C.QUESTS_DIR = tmp / "quests"

    app = app_cls(qapp)
    notes: list[tuple[str, str]] = []
    app._notify = lambda title, text, is_update=False: notes.append((title, text))
    app.UPDATE_ARGS = [str(fake), str(tmp / "quests" / "riparia.json"), outcome]
    app.cfg["auto_update_data"] = auto
    tracker_before = app.tracker
    if trigger == "menu":
        app.run_data_update()
    else:  # what the launch check does when Potservers has newer data
        app.on_data_update("2026-11-12T23:08:25Z", "2026-10-20T14:11:46Z")
    menu_texts, tracker_alive = set(), True
    end = time.time() + 20
    while time.time() < end:
        qapp.processEvents()
        if app._update_proc is None:
            break  # finished (and possibly reloaded) during that processEvents
        menu_texts.add(app.act_update.text())
        tracker_alive &= tracker_before.isRunning()
        time.sleep(0.05)
    for _ in range(20):
        qapp.processEvents()
        time.sleep(0.02)
    result = (notes, menu_texts, tracker_alive, app.act_update.text(), app.tracker is not tracker_before)
    app.shutdown()
    return result


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="potoverlay_flowcfg_"))
    cfg = json.loads(C.CONFIG_PATH.read_text(encoding="utf-8"))
    cfg.update(toggle_hotkey="", refresh_hotkey="", check_for_data_updates=False)
    (tmp / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    C.CONFIG_PATH = tmp / "config.json"
    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    from potoverlay.app import App

    print("update succeeds")
    notes, menus, alive, final_menu, reloaded = run(App, qapp, "ok")
    titles = [t for t, _ in notes]
    check("says it runs in the background and you can keep playing",
          any("background" in t.lower() and "keep playing" in x.lower() for t, x in notes), str(titles))
    check("progress shown in the tray menu", any("5/10 quests" in m and "keep playing" in m for m in menus),
          str(sorted(menus)))
    check("overlay kept working during the update", alive)
    check("reloaded by itself when done", reloaded)
    done = [x for t, x in notes if t == "Quest data updated"]
    check("finish message says what changed", bool(done) and "1 new" in done[0], str(done))
    check("menu back to normal afterwards", final_menu.startswith("Update quest data"), final_menu)

    print("launch finds newer data -> updates by itself")
    notes, menus, alive, final_menu, reloaded = run(App, qapp, "ok", trigger="launch")
    first = notes[0] if notes else ("", "")
    check("starts on its own and says so", first[0] == "Updating quest data in the background"
          and "downloading now" in first[1] and "keep playing" in first[1].lower(), first[1][:120])
    check("overlay kept working, then switched by itself", alive and reloaded)

    print("launch finds newer data, auto-update turned off -> only a notification")
    notes, menus, alive, final_menu, reloaded = run(App, qapp, "ok", trigger="launch", auto=False)
    check("offers it instead of starting", [t for t, _ in notes] == ["New quest data available"]
          and not reloaded, str([t for t, _ in notes]))

    print("update fails")
    notes, menus, alive, final_menu, reloaded = run(App, qapp, "fail")
    stopped = [x for t, x in notes if t == "Quest data update stopped"]
    check("failure explained, nothing broken", bool(stopped) and "still uses the current" in stopped[0]
          and "giving up" in stopped[0], str(stopped)[:160])
    check("no reload after a failed update", not reloaded)
    check("can try again from the menu", final_menu.startswith("Update quest data"), final_menu)
    print("\nALL PASSED" if failures == 0 else f"\n{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
