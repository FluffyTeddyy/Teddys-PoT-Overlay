"""Startup check for newer quest data (offline: Potservers' answer is faked).

    python -m tests.test_update_check
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from potoverlay import config as C
import potoverlay.updates as U
from potoverlay.quests import QuestDB

failures = 0


def check(name, cond, detail=""):
    global failures
    print(("  ok   " if cond else "  FAIL ") + name + (f"  ({detail})" if detail else ""))
    failures += not cond


def wait_for(app, checker, seconds=3.0):
    got = []
    checker.available.connect(lambda r, l: got.append((r, l)))
    checker.check()
    end = time.time() + seconds
    while time.time() < end and not got:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    return got


def main() -> int:
    app = QCoreApplication(sys.argv)
    tmp = Path(tempfile.mkdtemp(prefix="potoverlay_check_"))
    C.QUESTS_DIR, C.DATA_DIR = tmp / "quests", tmp
    db = QuestDB("riparia", data_updated_at="2026-10-20T14:11:46Z")
    db.save(C.quest_file("riparia"))

    print("data date stamping")
    check("quest file keeps its data date", QuestDB.load(C.quest_file("riparia"), "riparia").data_updated_at
          == "2026-10-20T14:11:46Z")
    check("app reads it", U.local_data_date() == "2026-10-20T14:11:46Z", U.local_data_date())

    print("update check")
    U.remote_data_date = lambda timeout=15.0: "2026-11-12T23:08:25Z"
    got = wait_for(app, U.UpdateChecker())
    check("newer data on Potservers -> notification", got == [("2026-11-12T23:08:25Z", "2026-10-20T14:11:46Z")],
          str(got))

    U.remote_data_date = lambda timeout=15.0: "2026-10-20T14:11:46Z"
    check("same data -> no notification", wait_for(app, U.UpdateChecker(), 1.0) == [])

    def offline(timeout=15.0):
        raise OSError("no internet")
    U.remote_data_date = offline
    check("offline -> quietly nothing", wait_for(app, U.UpdateChecker(), 1.0) == [])

    t = time.perf_counter()
    U.remote_data_date = lambda timeout=15.0: (time.sleep(2), "2026-11-12T00:00:00Z")[1]
    U.UpdateChecker().check()
    check("check doesn't hold up the app (runs in the background)", time.perf_counter() - t < 0.1,
          f"{1000 * (time.perf_counter() - t):.0f} ms")

    print("\nALL PASSED" if failures == 0 else f"\n{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
