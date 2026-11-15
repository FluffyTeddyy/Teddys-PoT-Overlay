"""Replay real OCR reads (from data/quest_log.txt) through the quest tracker and count churn.

    python -m tests.replay_log --extract     # save the log's reads to tests/fixtures/ocr_reads_live.json
    python -m tests.replay_log              # replay them, print churn and the final quest list

Churn = quests added or dropped although the real quest list barely changed.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from potoverlay import config as C
from potoverlay.quests import ActiveQuestTracker, QuestDB

FIXTURE = Path(__file__).parent / "fixtures" / "ocr_reads_live.json"


def extract() -> list[list[str]]:
    text = (C.DATA_DIR / "quest_log.txt").read_text(encoding="utf-8")
    reads: list[list[str]] = []
    for line in text.splitlines():
        m = re.match(r"\s+read \d+: (.*)$", line)
        if m and m.group(1) != "(nothing)":
            r = m.group(1).split(" | ")
            if r not in reads[-4:]:  # the log repeats recent reads under each event
                reads.append(r)
    return reads


def replay(reads: list[list[str]], db: QuestDB, verbose: bool = False) -> tuple[int, list[str]]:
    t = ActiveQuestTracker(db)
    churn = 0
    for lines in reads:
        t.update(lines)
        churn += len(t.added) + len(t.dropped)
        if verbose and (t.added or t.dropped):
            print("   +", t.added, " -", t.dropped)
    final = sorted({label for _, label in t.labeled})
    return churn, final


def main() -> int:
    if "--extract" in sys.argv:
        reads = extract()
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(reads, indent=0), encoding="utf-8")
        print(f"saved {len(reads)} reads to {FIXTURE}")
        return 0
    reads = json.loads(FIXTURE.read_text(encoding="utf-8"))
    db = QuestDB.load(C.quest_file("riparia"), "riparia")
    churn, final = replay(reads, db, "-v" in sys.argv)
    print(f"{len(reads)} reads, {churn} add/drop events, final: {final}")
    # The last ~70 reads are one unchanged quest list (Gather Mushrooms x2, Scavenge from the Dead,
    # Gifts from the Sea): any add/drop there is pure noise.
    t = ActiveQuestTracker(db)
    for lines in reads[:-70]:
        t.update(lines)
    noise = 0
    for lines in reads[-70:]:
        t.update(lines)
        noise += len(t.added) + len(t.dropped)
    print(f"last 70 reads (quest list didn't change): {noise} add/drop events; "
          f"shown: {sorted({lab for _, lab in t.labeled})}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
