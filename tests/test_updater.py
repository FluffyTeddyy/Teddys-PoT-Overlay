"""The data updater, offline: the network is replaced by a fake Potservers.

    python -m tests.test_updater

Checks that a patch's removed quests disappear, new ones appear, unchanged map images aren't
downloaded again, and an interrupted update continues instead of starting over.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

from potoverlay import config as C
import tools.build_quests as B
import tools.fetch_potservers as F

failures = 0


def check(name, cond, detail=""):
    global failures
    print(("  ok   " if cond else "  FAIL ") + name + (f"  ({detail})" if detail else ""))
    failures += not cond


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="potoverlay_update_"))
    raw = tmp / "potservers"
    (raw / "quests").mkdir(parents=True)
    src = C.DATA_DIR / "potservers"
    shutil.copy(src / "map_riparia.json", raw / "map_riparia.json")
    listing = json.loads((src / "quests.json").read_text(encoding="utf-8"))
    rip = [q for q in listing["data"] if q["map"] == "riparia"][:40]
    for q in rip:
        shutil.copy(src / "quests" / f"{q['id']}.json", raw / "quests" / f"{q['id']}.json")
    (tmp / "maps").mkdir()
    (tmp / "maps" / "riparia.webp").write_bytes(b"old image")
    (tmp / "quests").mkdir()

    # The "patch": quest 0 is removed, quest 1 is new (a copy under a new id), the rest unchanged.
    removed, kept = rip[0], rip[1:]
    new = dict(rip[1], id="patch_new_quest", name="Brand New Quest",
               url="https://potservers.net/api/v1/quests/patch_new_quest")
    new_detail = json.loads((src / "quests" / f"{rip[1]['id']}.json").read_text(encoding="utf-8"))
    new_detail["data"].update(id="patch_new_quest", name="Brand New Quest")
    server_list = {"data": kept + [new], "meta": {"updated_at": "2026-11-12T00:00:00Z"}}
    calls: list[str] = []

    def fake_fetch(url: str) -> bytes:
        calls.append(url)
        if url.endswith("/quests"):
            return json.dumps(server_list).encode()
        if "/maps/riparia/image" in url:
            return b"new image"
        if url.endswith("/maps/riparia"):
            return (src / "map_riparia.json").read_bytes()  # same map version as before
        if url.endswith("patch_new_quest"):
            return json.dumps(new_detail).encode()
        qid = url.rsplit("/", 1)[1]
        return (src / "quests" / f"{qid}.json").read_bytes()

    F.fetch, F.RAW, B.RAW = fake_fetch, raw, raw
    C.MAPS_DIR, C.QUESTS_DIR = tmp / "maps", tmp / "quests"

    print("update after a game patch (fake Potservers)")
    F.fetch_map("riparia", refresh=True)
    check("unchanged map image not downloaded again", not any("/image" in u for u in calls)
          and (tmp / "maps" / "riparia.webp").read_bytes() == b"old image")

    # Interrupt the quest download half way, then run again.
    real_save, n = F.save, {"saved": 0}

    def flaky_save(path, data):
        if path.parent.name == "quests" and n["saved"] >= 15:
            raise KeyboardInterrupt
        real_save(path, data)
        if path.parent.name == "quests":
            n["saved"] += 1
    F.save = flaky_save
    try:
        F.fetch_quests(("riparia",), refresh=True)
    except KeyboardInterrupt:
        pass
    F.save = real_save
    time.sleep(0.05)
    calls.clear()
    F.fetch_quests(("riparia",), refresh=True)
    detail_calls = [u for u in calls if "/quests/" in u]
    check("interrupted update continues instead of starting over",
          len(detail_calls) == len(kept) + 1 - 15, f"{len(detail_calls)} re-downloaded of {len(kept) + 1}")
    check("finished update clears its progress marker", not (raw / ".refresh_in_progress").exists())
    check("removed quest's old file deleted", not (raw / "quests" / f"{removed['id']}.json").exists())

    B.build_map("riparia")
    names = {q["name"] for q in json.loads((tmp / "quests" / "riparia.json").read_text(encoding="utf-8"))["quests"]}
    check("new quest in the quest list", "Brand New Quest" in names)

    # And the builder alone ignores a stale file even if one is lying around.
    shutil.copy(src / "quests" / f"{removed['id']}.json", raw / "quests" / f"{removed['id']}.json")
    stale_name = json.loads((src / "quests" / f"{removed['id']}.json").read_text(encoding="utf-8"))["data"]["name"]
    still_listed = any(q["name"] == stale_name for q in kept)
    B.build_map("riparia")
    out = json.loads((tmp / "quests" / "riparia.json").read_text(encoding="utf-8"))["quests"]
    ids = {q.get("id") for q in out}
    check("builder skips quests no longer in Potservers' list", removed["id"] not in ids,
          f"{stale_name} (same name still listed elsewhere: {still_listed})")
    print("\nALL PASSED" if failures == 0 else f"\n{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
