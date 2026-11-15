"""The launch-time updater against a fake GitHub release (no network).

    python -m tests.test_self_update
"""
from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import urllib.error
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

from tools import self_update as U

failures = 0


def check(name, cond, detail=""):
    global failures
    print(("  ok   " if cond else "  FAIL ") + name + (f"  ({detail})" if detail else ""))
    failures += not cond


def make_install(root: Path) -> None:
    """A tiny installed copy of version 1.0.0."""
    (root / "potoverlay").mkdir(parents=True)
    (root / "potoverlay" / "app.py").write_text("old app\n", encoding="utf-8")
    (root / "tools").mkdir()
    (root / "tools" / "gone.py").write_text("dropped in the new version\n", encoding="utf-8")
    (root / "data" / "quests").mkdir(parents=True)
    (root / "data" / "maps").mkdir(parents=True)
    (root / "data" / "maps" / "riparia.webp").write_bytes(b"map image")
    (root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    (root / "requirements.txt").write_bytes(b"numpy\n")  # same as the release: no package update
    (root / "config.json").write_text(json.dumps({"monitor": 2}), encoding="utf-8")
    quests = {"map": "riparia", "quests": [
        {"name": "My Own Spot", "poi": "Home", "source": "", "points": [[1, 2]]},
        {"name": "Old Potservers Quest", "poi": "X", "source": "potservers", "points": []}]}
    (root / "data" / "quests" / "riparia.json").write_text(json.dumps(quests), encoding="utf-8")
    (root / "MANIFEST.txt").write_text("\n".join(["VERSION", "potoverlay/app.py", "tools/gone.py",
                                                  "data/quests/riparia.json", "requirements.txt",
                                                  "MANIFEST.txt"]) + "\n", encoding="utf-8")


def make_release_zip(path: Path, version: str) -> None:
    files = {
        "VERSION": version + "\n",
        "potoverlay/app.py": "new app\n",
        "requirements.txt": "numpy\n",
        "data/quests/riparia.json": json.dumps({"map": "riparia", "quests": [
            {"name": "New Potservers Quest", "poi": "Y", "source": "potservers", "points": []}]}),
        "config.json": json.dumps({"monitor": 1}),  # must never overwrite the friend's own
    }
    files["MANIFEST.txt"] = "\n".join(sorted(files)) + "\n"
    with zipfile.ZipFile(path, "w") as z:
        for rel, text in files.items():
            z.writestr(f"pot-quest-overlay/{rel}", text)


def point_at(root: Path) -> None:
    U.ROOT, U.VERSION_FILE, U.MANIFEST = root, root / "VERSION", root / "MANIFEST.txt"


def main_out(*argv) -> str:
    sys.argv = ["self_update", *argv]
    buf = io.StringIO()
    with redirect_stdout(buf):
        U.main()
    return buf.getvalue()


def test_versions() -> None:
    print("version compare")
    check("1.0.10 > 1.0.9", U.parse("v1.0.10") > U.parse("1.0.9"))
    check("v1.2 == 1.2.0", U.parse("v1.2") == U.parse("1.2.0"))


def test_update() -> None:
    print("update from a fake release")
    tmp = Path(tempfile.mkdtemp(prefix="potoverlay_selfupdate_"))
    try:
        root = tmp / "install"
        make_install(root)
        zpath = tmp / "PoT-Quest-Overlay-1.1.0.zip"
        make_release_zip(zpath, "1.1.0")
        point_at(root)
        U.repo = lambda: "someone/Teddys-PoT-Overlay"
        U.latest = lambda name: {"version": "v1.1.0", "zip_url": zpath.as_uri(), "notes": "Fresh water!"}
        out = main_out()
        print("     " + out.strip().replace("\n", "\n     "))
        check("new version installed", (root / "VERSION").read_text().strip() == "1.1.0"
              and (root / "potoverlay" / "app.py").read_text() == "new app\n")
        check("your config.json untouched", json.loads((root / "config.json").read_text()) == {"monitor": 2})
        check("map images untouched", (root / "data" / "maps" / "riparia.webp").read_bytes() == b"map image")
        q = [x["name"] for x in json.loads((root / "data" / "quests" / "riparia.json").read_text())["quests"]]
        check("quest data replaced, your own quests kept", q == ["My Own Spot", "New Potservers Quest"], str(q))
        check("file dropped by the new version removed", not (root / "tools" / "gone.py").exists())
        check("notes shown", "Fresh water!" in out)
        check("packages left alone when requirements.txt didn't change", "Updating Python packages" not in out)
        out2 = main_out()
        check("next start: up to date, nothing downloaded", "up to date" in out2, out2.strip())

        def offline(name):
            raise urllib.error.URLError("no internet")
        U.latest = offline
        check("offline: starts anyway", "Starting as is" in main_out())

        bad = tmp / "bad.zip"
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("../../evil.txt", "x")
        U.latest = lambda name: {"version": "v9.0.0", "zip_url": bad.as_uri(), "notes": ""}
        out3 = main_out()
        check("unsafe zip refused, current version kept", "Update failed" in out3
              and (root / "VERSION").read_text().strip() == "1.1.0" and not (tmp / "evil.txt").exists())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_versions()
    test_update()
    print("\nALL PASSED" if failures == 0 else f"\n{failures} FAILED")
    sys.exit(1 if failures else 0)
