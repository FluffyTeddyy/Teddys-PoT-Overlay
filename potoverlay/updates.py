"""Is newer quest data out? One small request to Potservers, compared with the date stamped into
data/quests/*.json when they were built. Downloading is what's slow (update-data.bat); checking isn't.
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.request

from PySide6.QtCore import QObject, Signal

from . import config as C

log = logging.getLogger(__name__)
QUESTS_URL = "https://potservers.net/api/v1/quests"
HEADERS = {"User-Agent": "pot-quest-overlay/0.1 (personal minimap overlay)"}


def local_data_date() -> str:
    """Date of the quest data in use ("" if unknown, e.g. built before dates were stamped)."""
    dates = []
    for p in C.QUESTS_DIR.glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8")).get("data_updated_at", "")
        except (OSError, ValueError):
            continue
        if d:
            dates.append(d)
    # No stamp (built by an older version): treat as old, so an update gets offered. (Not the
    # downloaded list's date: an update replaces that first, before the quest files are rebuilt.)
    return min(dates) if dates else ""


def remote_data_date(timeout: float = 15.0) -> str:
    with urllib.request.urlopen(urllib.request.Request(QUESTS_URL, headers=HEADERS), timeout=timeout) as r:
        return json.loads(r.read())["meta"].get("updated_at", "")


def is_newer(remote: str, local: str) -> bool:
    # ISO-8601 UTC strings ("2026-11-12T23:08:25Z") sort correctly as text.
    return bool(remote) and (not local or remote > local)


class UpdateChecker(QObject):
    """Checks in a background thread; emits `available(remote_date, local_date)` if newer data is out."""

    available = Signal(str, str)

    def check(self) -> None:
        threading.Thread(target=self._run, name="update-check", daemon=True).start()

    def _run(self) -> None:
        try:
            local = local_data_date()
            remote = remote_data_date()
        except Exception as e:  # offline, site down...: just try again next start
            log.info("Quest data update check skipped: %s", e)
            return
        log.info("Quest data: ours %s, Potservers %s", local or "?", remote or "?")
        if is_newer(remote, local):
            self.available.emit(remote, local)
