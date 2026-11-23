"""Start Path of Titans together with the overlay (desktop shortcut "Path of Titans + Overlay").

The game itself can't be started directly: the Alderon Games Launcher starts it with a fresh
sign-in (-AuthToken). So the shortcut opens the launcher (you click Play as usual) and starts the
overlay in "with game" mode: it waits without looking at the screen, only checking every few
seconds whether the game's process is running, starts tracking when it is, and quits when the game
closes.

The launcher is found wherever it was installed: its Windows uninstall entry, then its
alderon-games:// link handler, then the default folder.
"""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import winreg
from ctypes import wintypes
from pathlib import Path

log = logging.getLogger(__name__)

GAME_PROCESSES = ("pathoftitans-win64-shipping.exe", "pathoftitans-win64-shipping_be.exe")
SHORTCUT_NAME = "Path of Titans + Overlay.lnk"


# ---------- finding the launcher ----------
def _reg_values(root, path: str):
    try:
        with winreg.OpenKey(root, path) as k:
            i = 0
            while True:
                try:
                    yield winreg.EnumValue(k, i)
                except OSError:
                    return
                i += 1
    except OSError:
        return


def _exe_from(text: str) -> Path | None:
    """'"C:\\x\\Alderon Games Launcher.exe" "%1"' or 'C:\\x\\Launcher.exe,0' -> the exe path."""
    text = (text or "").strip()
    if text.startswith('"'):
        text = text[1:].split('"', 1)[0]
    else:
        text = text.split(",")[0].strip()
    p = Path(text)
    return p if p.suffix.lower() == ".exe" and p.exists() else None


def find_launcher() -> Path | None:
    """The Alderon Games Launcher's exe, wherever it was installed."""
    # 1) Its uninstall entry (per-user or per-machine install).
    for root, base in ((winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
                       (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                       (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall")):
        try:
            with winreg.OpenKey(root, base) as k:
                n = winreg.QueryInfoKey(k)[0]
                subs = [winreg.EnumKey(k, i) for i in range(n)]
        except OSError:
            continue
        for sub in subs:
            vals = dict((name, val) for name, val, _ in _reg_values(root, f"{base}\\{sub}"))
            if str(vals.get("DisplayName", "")).lower().startswith("alderon games launcher"):
                for key in ("DisplayIcon", "InstallLocation"):
                    v = str(vals.get(key) or "")
                    exe = _exe_from(v) if key == "DisplayIcon" else (
                        Path(v) / "Alderon Games Launcher.exe" if v and (Path(v) / "Alderon Games Launcher.exe").exists()
                        else None)
                    if exe:
                        return exe
    # 2) The alderon-games:// link handler it registers.
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            cmd = winreg.QueryValue(root, r"Software\Classes\alderon-games\shell\open\command")
        except OSError:
            continue
        exe = _exe_from(cmd)
        if exe:
            return exe
    # 3) The default folder.
    default = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "alderon-games-launcher" / \
        "Alderon Games Launcher.exe"
    return default if default.exists() else None


def start_launcher() -> bool:
    """Open the Alderon Games Launcher (if it's already open, it just comes to the front)."""
    exe = find_launcher()
    if exe is None:
        log.info("Alderon Games Launcher not found; start Path of Titans yourself")
        return False
    # Its own log (Electron, libtorrent, update checks...) goes nowhere instead of into our window.
    subprocess.Popen([str(exe)], cwd=str(exe.parent), close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    return True


# ---------- is the game running? (process list only, no screen capture) ----------
class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_wchar * 260)]


def game_running() -> bool:
    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if snap in (None, wintypes.HANDLE(-1).value):
        return False
    try:
        e = _PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            if e.szExeFile.lower() in GAME_PROCESSES:
                return True
            ok = k32.Process32NextW(snap, ctypes.byref(e))
        return False
    finally:
        k32.CloseHandle(snap)


# ---------- the desktop shortcut ----------
def desktop_dir() -> Path:
    buf = ctypes.create_unicode_buffer(260)
    if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0:  # CSIDL_DESKTOPDIRECTORY
        return Path(buf.value)
    return Path.home() / "Desktop"


def create_shortcut(overlay_root: Path) -> Path:
    """Desktop shortcut that opens the launcher and the overlay together (overlay's console window
    minimised). Uses the launcher's icon when it's found."""
    lnk = desktop_dir() / SHORTCUT_NAME
    run_bat = overlay_root / "run.bat"
    icon = find_launcher()
    ps = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK); "
        "$s.TargetPath = $env:ComSpec; "
        "$s.Arguments = '/c \"\"' + $env:RUNBAT + '\" --with-game\"'; "
        "$s.WorkingDirectory = $env:ROOT; "
        "$s.WindowStyle = 7; "
        "$s.Description = 'Opens the Alderon Games Launcher and the PoT overlay together'; "
        "if ($env:ICON) { $s.IconLocation = $env:ICON + ',0' }; "
        "$s.Save()"
    )
    env = dict(os.environ, LNK=str(lnk), RUNBAT=str(run_bat), ROOT=str(overlay_root), ICON=str(icon or ""))
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], env=env, check=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=30)
    return lnk
