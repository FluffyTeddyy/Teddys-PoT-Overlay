"""Download map images, item spawn points and quests from the Potservers API.

    python -m tools.fetch_potservers                # all maps, skip what's already downloaded
    python -m tools.fetch_potservers --maps riparia
    python -m tools.fetch_potservers --refresh      # re-download everything

Data from Potservers (https://potservers.net). Unofficial; Path of Titans belongs to Alderon Games.
The API allows 120 requests a minute; this stays under that. After downloading it runs
tools.build_quests to turn the raw data into data/quests/<map>.json.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from potoverlay import config as C

BASE = "https://potservers.net/api/v1"
MAPS = ("riparia", "gondwa", "panjura")
RAW = C.DATA_DIR / "potservers"
HEADERS = {"User-Agent": "pot-quest-overlay/0.1 (personal minimap overlay)"}
MIN_INTERVAL = 0.6  # seconds between requests (100/min)

_last = 0.0


def fetch(url: str, retries: int = 4) -> bytes:
    global _last
    for attempt in range(retries):
        wait = MIN_INTERVAL - (time.monotonic() - _last)
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                time.sleep(10 * (attempt + 1))
                continue
            raise
        except urllib.error.URLError:
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"giving up on {url}")


def save(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)


def _image_url(map_json: Path) -> str | None:
    try:
        data = json.loads(map_json.read_text(encoding="utf-8"))["data"]
        return next(i["url"] for i in data["images"] if i["size"] == 4096 and i["format"] == "webp")
    except (OSError, ValueError, KeyError, StopIteration):
        return None


def fetch_map(name: str, refresh: bool) -> None:
    path = RAW / f"map_{name}.json"
    old_url = _image_url(path)
    if refresh or not path.exists():
        print(f"[{name}] map data...", flush=True)
        save(path, fetch(f"{BASE}/maps/{name}"))
    url = _image_url(path)
    out = C.MAPS_DIR / f"{name}.webp"
    # The image URL carries a version (?v=...): only download when it changed (or is missing).
    if not out.exists() or (refresh and url != old_url):
        print(f"[{name}] map image 4096px...", flush=True)
        save(out, fetch(url))
        print(f"[{name}] saved {out.name} ({out.stat().st_size / 1e6:.1f} MB)", flush=True)
    elif refresh:
        print(f"[{name}] map image unchanged, kept", flush=True)


def fetch_quests(maps: tuple[str, ...], refresh: bool) -> None:
    lst_path = RAW / "quests.json"
    if refresh or not lst_path.exists():
        save(lst_path, fetch(f"{BASE}/quests"))
    listing = json.loads(lst_path.read_text(encoding="utf-8"))["data"]
    quests = [q for q in listing if q.get("map") in maps]
    qdir = RAW / "quests"
    # Quests removed from the game (gone from the list) must not linger in the quest files.
    current = {q["id"] for q in listing}
    stale = [p for p in qdir.glob("*.json") if p.stem not in current] if qdir.exists() else []
    for p in stale:
        p.unlink()
    if stale:
        print(f"removed {len(stale)} quests no longer in the game", flush=True)
    marker = RAW / ".refresh_in_progress"
    if refresh:
        # A refresh re-downloads everything. If an earlier one was interrupted, carry on from it:
        # files written since it started are already fresh.
        if not marker.exists():
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("started", encoding="utf-8")
        else:
            print("continuing an interrupted update", flush=True)
        since = marker.stat().st_mtime

        def fresh(p: Path) -> bool:
            return p.exists() and p.stat().st_mtime >= since
        todo = [q for q in quests if not fresh(qdir / f"{q['id']}.json")]
    else:
        todo = [q for q in quests if not (qdir / f"{q['id']}.json").exists()]
    print(f"quests: {len(quests)} for {', '.join(maps)}, {len(todo)} to download "
          f"(~{len(todo) * MIN_INTERVAL / 60:.0f} min)", flush=True)
    for i, q in enumerate(todo, 1):
        save(qdir / f"{q['id']}.json", fetch(q["url"]))
        if i % 50 == 0 or i == len(todo):
            print(f"  {i}/{len(todo)}", flush=True)
    marker.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--maps", nargs="+", default=list(MAPS), choices=MAPS)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--images-only", action="store_true",
                    help="only the map images (the quest files in data/quests are already there)")
    args = ap.parse_args()
    maps = tuple(args.maps)
    for m in maps:
        fetch_map(m, args.refresh)
    if args.images_only:
        print("done", flush=True)
        return 0
    fetch_quests(maps, args.refresh)
    if not args.no_build:
        from tools.build_quests import build_all

        build_all(maps)
    print("done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
