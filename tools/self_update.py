"""Update the overlay itself from GitHub releases. run.bat runs this before every start.

    python -m tools.self_update            # check, and update if a newer release is out
    python -m tools.self_update --check    # only say whether one is out

One small request to GitHub (about a second; skipped quietly when offline). If the latest release
is newer than the VERSION file here, its zip (the same as make-release.bat builds) is downloaded,
unpacked into a temporary folder and copied over this folder. Your own files are never touched:
config.json, the .venv, the map images and downloaded data, the quest log, your fish buff values,
and quests you made in the quest editor (they live in data/quests and are kept by build_quests).
Files that an older version shipped and the new one doesn't are removed. If requirements.txt
changed, the packages are updated too. Anything going wrong leaves the current version in place.

Turn it off with "check_for_app_updates": false in config.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"
MANIFEST = ROOT / "MANIFEST.txt"   # the files the installed version shipped (written by make_release)
HEADERS = {"User-Agent": "pot-quest-overlay-updater", "Accept": "application/vnd.github+json"}
TIMEOUT = 6
# Never overwritten or removed by an update, even if a release happened to contain them.
KEEP = ("config.json", ".venv/", ".cache/", "data/maps/", "data/potservers/", "data/vulnona/",
        "data/quest_log.txt", "data/fish_buffs.json", "dist/")


def repo() -> str:
    """"owner/name" of the GitHub repository releases come from (UPDATE_REPO in config.py)."""
    sys.path.insert(0, str(ROOT))
    from potoverlay import config as C
    return getattr(C, "UPDATE_REPO", "")


def local_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "0"


def parse(v: str) -> tuple:
    parts = []
    for p in v.strip().lstrip("vV").split("."):
        num = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts + [0] * (3 - len(parts)))


def enabled() -> bool:
    try:
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        return cfg.get("check_for_app_updates", True) is not False
    except (OSError, ValueError):
        return True


def latest(repo_name: str) -> dict | None:
    """The newest published release: {"version", "zip_url", "notes"}, or None."""
    url = f"https://api.github.com/repos/{repo_name}/releases/latest"
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=TIMEOUT) as r:
        rel = json.loads(r.read().decode("utf-8"))
    assets = [a for a in rel.get("assets", []) if a.get("name", "").lower().endswith(".zip")]
    if not assets:
        return None
    return {"version": rel.get("tag_name", ""), "zip_url": assets[0]["browser_download_url"],
            "notes": (rel.get("body") or "").strip()}


def _kept(rel: str) -> bool:
    return any(rel == k or rel.startswith(k) for k in KEEP)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else ""


def _own_quests(path: Path) -> list[dict]:
    """Quests made in the quest editor (not from Potservers) in a quest file."""
    try:
        return [q for q in json.loads(path.read_text(encoding="utf-8"))["quests"]
                if q.get("source") != "potservers"]
    except (OSError, ValueError, KeyError):
        return []


def _add_quests(path: Path, mine: list[dict]) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    have = {(q.get("name"), q.get("poi")) for q in data["quests"]}
    data["quests"] = [q for q in mine if (q.get("name"), q.get("poi")) not in have] + data["quests"]
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")


def install(zip_url: str, version: str) -> None:
    work = Path(tempfile.mkdtemp(prefix="potoverlay_update_"))
    try:
        zpath = work / "release.zip"
        with urllib.request.urlopen(urllib.request.Request(zip_url, headers=HEADERS), timeout=60) as r:
            zpath.write_bytes(r.read())
        unpacked = work / "unpacked"
        with zipfile.ZipFile(zpath) as z:
            for name in z.namelist():  # refuse anything that would land outside the folder
                if name.startswith(("/", "\\")) or ".." in Path(name).parts:
                    raise ValueError(f"unsafe path in update: {name}")
            z.extractall(unpacked)
        tops = [p for p in unpacked.iterdir() if p.is_dir()]
        src = tops[0] if len(tops) == 1 else unpacked
        if not (src / "potoverlay" / "app.py").exists() or not (src / "VERSION").exists():
            raise ValueError("the release zip doesn't look like this overlay")

        new_files = [p.relative_to(src).as_posix() for p in src.rglob("*") if p.is_file()]
        old_files = MANIFEST.read_text(encoding="utf-8").split() if MANIFEST.exists() else []
        req_before = _sha(ROOT / "requirements.txt")
        for rel in new_files:
            if _kept(rel):
                continue
            dst = ROOT / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            mine = _own_quests(dst) if rel.startswith("data/quests/") else []
            shutil.copy2(src / rel, dst)
            if mine:
                _add_quests(dst, mine)
        for rel in set(old_files) - set(new_files):  # shipped before, gone now
            if not _kept(rel) and (ROOT / rel).is_file():
                (ROOT / rel).unlink()
        if _sha(ROOT / "requirements.txt") != req_before:
            print("Updating Python packages...", flush=True)
            subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q",
                            "-r", str(ROOT / "requirements.txt")], check=False)
        print(f"Updated to {version}.", flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="only report, don't install")
    args = ap.parse_args()
    name = repo()
    if not name or not enabled():
        return 0
    mine = local_version()
    try:
        rel = latest(name)
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"(Couldn't check for overlay updates: {getattr(e, 'reason', e)}. Starting as is.)", flush=True)
        return 0
    if not rel or parse(rel["version"]) <= parse(mine):
        print(f"Overlay {mine} is up to date.", flush=True)
        return 0
    print(f"A new version of the overlay is out: {rel['version']} (you have {mine}).", flush=True)
    if rel["notes"]:
        print("  " + rel["notes"].replace("\n", "\n  ")[:800], flush=True)
    if args.check:
        return 0
    try:
        print("Downloading and installing it...", flush=True)
        install(rel["zip_url"], rel["version"])
    except Exception as e:  # never stop the overlay from starting
        print(f"Update failed ({e}). Starting the current version; it will try again next time.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
