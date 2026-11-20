"""Publish a new version: friends' overlays install it the next time they start run.bat.

    python -m tools.publish_release "What changed"            # 1.2.3 -> 1.2.4
    python -m tools.publish_release "What changed" --minor    # 1.2.3 -> 1.3.0
    python -m tools.publish_release "What changed" --version 2.0.0

Bumps VERSION, builds the release zip (make_release), commits and pushes everything to GitHub,
and creates a GitHub release with the zip attached. Needs Git and the GitHub CLI, signed in.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"


def exe(name: str, *fallbacks: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    for f in fallbacks:
        if Path(f).exists():
            return f
    sys.exit(f"{name} not found. Install it (winget install {'Git.Git' if name == 'git' else 'GitHub.cli'}).")


GIT = exe("git", r"C:\Program Files\Git\cmd\git.exe")
GH = exe("gh", r"C:\Program Files\GitHub CLI\gh.exe")
# The GitHub CLI calls git itself: make sure it can find it (e.g. a window opened before Git was installed).
os.environ["PATH"] = str(Path(GIT).parent) + os.pathsep + os.environ.get("PATH", "")


def run(*args: str, capture: bool = False) -> str:
    print(">", " ".join(Path(args[0]).stem if i == 0 else a for i, a in enumerate(args)), flush=True)
    r = subprocess.run(list(args), cwd=ROOT, text=True, capture_output=capture)
    if r.returncode != 0:
        sys.exit(f"failed: {' '.join(args[1:])}\n{(r.stderr or '') if capture else ''}")
    return (r.stdout or "").strip()


def bumped(current: str, minor: bool) -> str:
    major, mi, patch = (list(map(int, current.split("."))) + [0, 0, 0])[:3]
    return f"{major}.{mi + 1}.0" if minor else f"{major}.{mi}.{patch + 1}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("notes", help="what changed (shown to friends when they update)")
    ap.add_argument("--minor", action="store_true", help="bigger update: 1.2.3 -> 1.3.0")
    ap.add_argument("--version", help="set the version yourself, e.g. 2.0.0")
    args = ap.parse_args()

    current = VERSION_FILE.read_text(encoding="utf-8").strip()
    new = args.version or bumped(current, args.minor)
    VERSION_FILE.write_text(new + "\n", encoding="utf-8")
    print(f"Version {current} -> {new}", flush=True)

    sys.path.insert(0, str(ROOT))
    from tools import make_release
    if make_release.main() != 0:
        VERSION_FILE.write_text(current + "\n", encoding="utf-8")
        return 1
    zip_path = ROOT / "dist" / f"PoT-Quest-Overlay-{new}.zip"

    run(GIT, "add", "-A")
    run(GIT, "commit", "-m", f"v{new}: {args.notes}", "-m", "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>")
    run(GIT, "push")
    run(GH, "release", "create", f"v{new}", str(zip_path), "--title", f"v{new}", "--notes", args.notes)
    print(f"\nPublished v{new}. Friends get it the next time they start run.bat.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
