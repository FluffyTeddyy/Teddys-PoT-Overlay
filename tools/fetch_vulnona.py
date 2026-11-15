"""Download VulnonaMAP's map data (drinking water outlines and names) for the PoT maps.

    python -m tools.fetch_vulnona        # -> data/vulnona/<map>.txt, then builds data/freshwater/

VulnonaMAP (https://vulnona.com/game/map/) is a fan map by Coco.N. Its non-image data is
"collective intelligence"; the author neither grants nor denies permission and asks for a link to
the map beside work that uses it (see the tray menu and README). Three small text files, fetched
politely, only when you run this.
"""
from __future__ import annotations

import sys
import time
import urllib.request

from potoverlay import config as C

BASE = "https://vulnona.com/game/map/map/{name}/data_1.txt"
MAPS = {"riparia": "Riparia", "gondwa": "Gondwa", "panjura": "Panjura"}
HEADERS = {"User-Agent": "pot-quest-overlay/0.1 (personal minimap overlay)"}
RAW = C.DATA_DIR / "vulnona"


def main() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    for i, (ours, theirs) in enumerate(MAPS.items()):
        if i:
            time.sleep(2)
        url = BASE.format(name=theirs)
        with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=60) as r:
            data = r.read()
        (RAW / f"{ours}.txt").write_bytes(data)
        print(f"[{ours}] {len(data) / 1024:.0f} KB")
    from tools import build_freshwater
    return build_freshwater.main()


if __name__ == "__main__":
    sys.exit(main())
