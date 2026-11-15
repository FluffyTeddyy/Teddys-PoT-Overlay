"""Build a small zip to share: code + quest data only.

    python -m tools.make_release        # -> dist/PoT-Quest-Overlay-<date>.zip

Left out (rebuilt by setup.bat on the other PC): .venv, .cache, map images, raw Potservers data.
Also left out: tests (their screenshots show your game, chat names included), debug images, and
config.json (your personal settings).
"""
from __future__ import annotations

import datetime
import sys
import zipfile
from pathlib import Path

from potoverlay import config as C

ROOT = C.ROOT
TOP = "pot-quest-overlay"  # folder name inside the zip

INCLUDE = [
    "potoverlay/*.py",
    "tools/*.py",
    "setup.bat", "run.bat", "calibrate.bat", "quest-editor.bat", "test-match.bat", "update-data.bat",
    # No config.json: yours holds your own choices (and calibrations); friends start from the
    # defaults in potoverlay/config.py and the layout for their screen is worked out on their PC.
    "requirements.txt", "README.md", "VERSION",
    "data/quests/*.json",
    "data/freshwater/*.json",
    "data/maps/PUT_MAPS_HERE.txt",
]
EXCLUDE_NAMES = {"make_release.py"}


def files() -> list[Path]:
    out: list[Path] = []
    for pattern in INCLUDE:
        out += [p for p in ROOT.glob(pattern) if p.is_file() and p.name not in EXCLUDE_NAMES]
    return sorted(set(out))


def main() -> int:
    picked = files()
    missing = [p for p in ("potoverlay/app.py", "setup.bat", "run.bat", "data/quests/riparia.json")
               if not (ROOT / p).exists()]
    if missing:
        print("missing:", missing)
        return 1
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip() if (ROOT / "VERSION").exists() else \
        f"{datetime.date.today():%Y-%m-%d}"
    out = dist / f"PoT-Quest-Overlay-{version}.zip"
    rels = [p.relative_to(ROOT).as_posix() for p in picked]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p, rel in zip(picked, rels):
            z.write(p, f"{TOP}/{rel}")
        # What this version ships, so the updater can remove files a later version drops.
        z.writestr(f"{TOP}/MANIFEST.txt", "\n".join(sorted(rels + ["MANIFEST.txt"])) + "\n")
    raw = sum(p.stat().st_size for p in picked)
    print(f"{len(picked)} files, {raw / 1e6:.1f} MB -> {out.name}: {out.stat().st_size / 1e6:.2f} MB")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
