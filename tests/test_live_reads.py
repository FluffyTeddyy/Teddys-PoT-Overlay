"""Regression test on real, noisy OCR reads from a play session (tests/fixtures/ocr_reads_live.json,
extracted from data/quest_log.txt with `python -m tests.replay_log --extract`).

    python -m tests.test_live_reads
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from potoverlay import config as C
from potoverlay.quests import ActiveQuestTracker, QuestDB
from tests.replay_log import replay

# The list at the end (a session at Coastland Swamp; its quests are known since the place is matched by name)
EXPECTED = {"Gather Mushrooms", "Gifts from the Sea", "Scavenge from the Dead"}
failures = 0


def check(name, cond, detail=""):
    global failures
    print(("  ok   " if cond else "  FAIL ") + name + (f"  ({detail})" if detail else ""))
    failures += not cond


def main() -> int:
    path = Path(__file__).parent / "fixtures" / "ocr_reads_live.json"
    if not path.exists():
        print("no live reads fixture; skipping")
        return 0
    reads = json.loads(path.read_text(encoding="utf-8"))
    db = QuestDB.load(C.quest_file("riparia"), "riparia")
    print(f"replaying {len(reads)} real reads")
    churn, final = replay(reads, db)
    check("ends with exactly the quests on screen", set(final) == EXPECTED, str(final))
    check("little churn (was 175 add/drop events before the fix)", churn <= 30, f"{churn}")

    t = ActiveQuestTracker(db)
    for lines in reads[:-70]:
        t.update(lines)
    noise = 0
    for lines in reads[-70:]:
        t.update(lines)
        noise += len(t.added) + len(t.dropped)
    check("stable stretch: few add/drop events", noise <= 10, f"{noise}")

    # F9 on real reads: play through the session, have a quest from the earlier list wrongly
    # linger, then refresh from 3 captures of 3 consecutive reads.
    t = ActiveQuestTracker(db)
    for lines in reads[:-9]:
        t.update(lines)
    old = ActiveQuestTracker(db)
    old.update(["Quests", "Cicada Molt", "- Collect 20 Cicada Molt 0/20"])  # a quest no longer on screen
    t._items.update(old._items)
    stale = {lab for _, lab in t.labeled}
    check("(stale quest injected)", "Cicada Molt" in stale, str(sorted(stale)))
    caps = [reads[-9:-6], reads[-6:-3], reads[-3:]]
    t.refresh(caps)
    after = {lab for _, lab in t.labeled}
    check("F9 replaces a stale list with what's on screen", after == EXPECTED,
          f"before {sorted(stale)} -> after {sorted(after)}")
    objectives = sorted(k for k in t._items)
    check("all of Gifts from the Sea's objectives kept under its title",
          {"collect:conch", "collect:kelp", "collect:turtle shell"} <= set(objectives), str(objectives))
    print("\nALL PASSED" if failures == 0 else f"\n{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
