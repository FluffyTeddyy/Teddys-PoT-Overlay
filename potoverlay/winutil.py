"""Small Win32 helpers via ctypes: foreground window title, is the game in view, global hotkey,
capture exclusion."""
from __future__ import annotations

import ctypes
import logging
import os
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


# ---------- is the game the window you see at these points? ----------
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT, WS_EX_LAYERED = 0x20, 0x80000
DWMWA_EXTENDED_FRAME_BOUNDS, DWMWA_CLOAKED = 9, 14
_ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [_ENUM_PROC, wintypes.LPARAM]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
# The taskbar shows over a borderless game once the game loses the focus; it's a thin strip at the
# screen's edge, away from the minimap and the quest list, so it doesn't count as hiding the game.
TASKBARS = ("Shell_TrayWnd", "Shell_SecondaryTrayWnd")
try:
    _dwm = ctypes.WinDLL("dwmapi")
    _dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
except OSError:
    _dwm = None


def _title(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _rect(hwnd) -> tuple[int, int, int, int]:
    """The window's visible rectangle (without the invisible resize border Windows 10/11 adds)."""
    r = wintypes.RECT()
    if _dwm is None or _dwm.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r),
                                                  ctypes.sizeof(r)) != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def _cloaked(hwnd) -> bool:
    """Windows that exist but aren't drawn (apps on another virtual desktop, suspended apps)."""
    v = wintypes.DWORD()
    return (_dwm is not None and _dwm.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(v),
                                                            ctypes.sizeof(v)) == 0 and v.value != 0)


def _seen_windows() -> list[tuple[int, int, int, int, str]]:
    """Windows that are drawn on screen, front to back: (left, top, right, bottom, title). Leaves
    out our own overlay and other click-through overlays (Discord, Steam, GeForce), which sit on top
    of everything without hiding the game, and the taskbar."""
    me, out = os.getpid(), []

    def visit(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd) or _cloaked(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if pid.value == me or (ex & WS_EX_TRANSPARENT and ex & WS_EX_LAYERED):
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value in TASKBARS:
            return True
        l, t, r, b = _rect(hwnd)
        if r > l and b > t:
            out.append((l, t, r, b, _title(hwnd)))
        return True

    user32.EnumWindows(_ENUM_PROC(visit), 0)
    return out


def front_title_at(windows, x: int, y: int) -> str | None:
    """Title of the front-most window at screen point (x, y)."""
    for l, t, r, b, title in windows:
        if l <= x < r and t <= y < b:
            return title
    return None


def game_in_view(titles: list[str], points: list[tuple[int, int]]) -> bool:
    """True when the game is the window you see at every point (screen pixels): it's in front, or
    you clicked a window on another screen (or beside the game) and it's still in plain sight."""
    if game_is_focused(titles):
        return True
    if not points:
        return False
    try:
        windows = _seen_windows()
    except Exception:  # never let this check take the overlay down
        log.debug("Couldn't list windows", exc_info=True)
        return False
    names = [s.lower() for s in titles]
    for x, y in points:
        t = front_title_at(windows, x, y)
        if t is None or not any(n in t.lower() for n in names):
            return False
    return True


def region_points(monitor: dict, region: dict) -> list[tuple[int, int]]:
    """Centre and four inset corners of a region given relative to a monitor, in screen pixels."""
    x0, y0 = monitor["left"] + int(region["x"]), monitor["top"] + int(region["y"])
    w, h = int(region["w"]), int(region["h"])
    if w <= 0 or h <= 0:
        return []
    ix, iy = max(1, w // 8), max(1, h // 8)
    return [(x0 + w // 2, y0 + h // 2), (x0 + ix, y0 + iy), (x0 + w - ix, y0 + iy),
            (x0 + ix, y0 + h - iy), (x0 + w - ix, y0 + h - iy)]


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
