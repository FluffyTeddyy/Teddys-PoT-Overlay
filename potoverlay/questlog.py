"""data/quest_log.txt: when quests appear or drop, with what OCR read just before.

Meant for figuring out why a quest went missing: the lines show whether its text was misread,
covered by something, or really gone from the list. Kept small (old entries are trimmed).
"""
from __future__ import annotations

import datetime
import logging
from collections import deque
from pathlib import Path

log = logging.getLogger(__name__)

MAX_BYTES = 256_000


class QuestLog:
    def __init__(self, path: Path):
        self.path = path
        self.recent: deque[list[str]] = deque(maxlen=3)  # the last few OCR reads

    def saw(self, lines: list[str]) -> None:
        self.recent.append(list(lines))

    def write(self, event: str, names: list[str], reads: list[list[str]] | None = None) -> None:
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        out = [f"{stamp}  {event}: {', '.join(names) if names else '-'}"]
        for i, lines in enumerate(reads if reads is not None else list(self.recent)):
            out.append(f"    read {i + 1}: {' | '.join(lines) if lines else '(nothing)'}")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write("\n".join(out) + "\n")
            if self.path.stat().st_size > MAX_BYTES:  # keep the newer half
                text = self.path.read_text(encoding="utf-8")
                self.path.write_text(text[len(text) // 2:].split("\n", 1)[-1], encoding="utf-8")
        except OSError as e:
            log.debug("quest log not written: %s", e)
