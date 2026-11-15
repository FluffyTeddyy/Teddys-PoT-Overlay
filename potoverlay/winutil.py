"""Small Win32 helpers via ctypes: foreground window title, global hotkey, capture exclusion."""
from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)
user32 = ctypes.WinDLL("user32", use_last_error=True)

WDA_EXCLUDEFROMCAPTURE = 0x11
WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000

user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]


def foreground_title() -> str:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def game_is_focused(titles: list[str]) -> bool:
    t = foreground_title().lower()
    return any(s.lower() in t for s in titles)


def exclude_from_capture(hwnd: int) -> bool:
    """Hide a window from screen capture (Win10 2004+) so our markers don't confuse the matcher."""
    ok = bool(user32.SetWindowDisplayAffinity(wintypes.HWND(hwnd), WDA_EXCLUDEFROMCAPTURE))
    if not ok:
        log.warning("SetWindowDisplayAffinity failed (error %d); overlay may show up in captures",
                    ctypes.get_last_error())
    return ok


def parse_hotkey(spec: str) -> tuple[int, int]:
    mods, vk = 0, 0
    for part in [p.strip().lower() for p in spec.split("+") if p.strip()]:
        if part in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif part == "alt":
            mods |= MOD_ALT
        elif part == "shift":
            mods |= MOD_SHIFT
        elif part == "win":
            mods |= MOD_WIN
        elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            vk = 0x70 + int(part[1:]) - 1
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        else:
            raise ValueError(f"Unknown key '{part}' in hotkey '{spec}'")
    if not vk:
        raise ValueError(f"Hotkey '{spec}' has no main key")
    return mods, vk


class GlobalHotkey(QObject):
    triggered = Signal()

    def __init__(self, spec: str):
        super().__init__()
        self.spec = spec
        self.mods, self.vk = parse_hotkey(spec)
        self._tid = 0
        self.registered = threading.Event()  # set once Windows accepted the hotkey
        self.failed = threading.Event()      # set if it's taken by another app
        self._thread = threading.Thread(target=self._run, name="hotkey", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        self._tid = ctypes.windll.kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, 1, self.mods | MOD_NOREPEAT, self.vk):
            log.warning("Could not register hotkey %s (already used by another app?)", self.spec)
            self.failed.set()
            return
        self.registered.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                self.triggered.emit()

    def stop(self) -> None:
        if self._tid:
            user32.PostThreadMessageW(self._tid, 0x0012, 0, 0)  # WM_QUIT
