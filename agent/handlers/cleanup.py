"""
ClassroomOS — Agent Cleanup Handler
======================================
Performs end-of-day or on-demand cleanup of:
  - Windows temp files (%TEMP%, C:\\Windows\\Temp)
  - Recycle Bin
  - Downloads folder
  - Browser data (Chrome, Edge, Firefox)
  
Each category is individually toggleable via the *options* dict.
Returns a report of what was cleaned and how much space was freed.
"""

import os
import shutil
import ctypes
import logging
from pathlib import Path

import session

logger = logging.getLogger("handler.cleanup")


# ── Student-profile resolution ────────────────────────────────────────────────
# As a Windows service the agent runs as SYSTEM, so %TEMP%, %LOCALAPPDATA% and
# Path.home() all point at the SYSTEM profile. Cleaning those would report
# "0.0 MB freed" every time while the student's disk stays full, so every path
# below is resolved against whoever is actually logged in.

def _student_home() -> Path:
    profile = session.user_profile_dir()
    return Path(profile) if profile else Path.home()


def _student_temp() -> str:
    return session.user_temp_dir() or os.environ.get("TEMP", "")


def _student_appdata(local: bool = True) -> str:
    return session.user_appdata(local=local) or os.environ.get(
        "LOCALAPPDATA" if local else "APPDATA", ""
    )


def _bytes_to_mb(b: int) -> float:
    return round(b / (1024 * 1024), 2)


def _rmtree_safe(path: str) -> int:
    """
    Delete all contents of *path* (not the directory itself).
    Returns the number of bytes freed (approximate).
    """
    freed = 0
    if not os.path.exists(path):
        return 0
    for item in os.listdir(path):
        item_path = os.path.join(path, item)
        try:
            size = _dir_size(item_path) if os.path.isdir(item_path) else os.path.getsize(item_path)
            if os.path.isdir(item_path):
                shutil.rmtree(item_path, ignore_errors=True)
            else:
                os.remove(item_path)
            freed += size
        except Exception as e:
            logger.debug(f"Cannot remove {item_path}: {e}")
    return freed


def _dir_size(path: str) -> int:
    """Get total size of a directory tree in bytes."""
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for fname in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, fname))
            except Exception:
                pass
    return total


def _empty_recycle_bin() -> bool:
    """Empty the Recycle Bin using the Windows Shell API."""
    try:
        # SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND = 0x00000007
        result = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x00000007)
        return result == 0
    except Exception as e:
        logger.error(f"Recycle bin error: {e}")
        return False


def _clear_browser_chrome() -> int:
    """Clear Chrome cache, cookies, and history. Returns bytes freed."""
    freed = 0
    appdata = _student_appdata(local=True)
    if not appdata:
        return 0
    chrome_base = os.path.join(appdata, "Google", "Chrome", "User Data")
    if not os.path.exists(chrome_base):
        return 0

    targets = [
        "Default\\Cache\\Cache_Data",
        "Default\\Code Cache",
        "Default\\GPUCache",
        "Default\\History",
        "Default\\Cookies",
        "Default\\Sessions",
        "Default\\Session Storage",
        "Default\\IndexedDB",
        "Default\\databases",
    ]
    for rel in targets:
        p = os.path.join(chrome_base, rel)
        freed += _rmtree_safe(p) if os.path.isdir(p) else 0
        if os.path.isfile(p):
            try:
                freed += os.path.getsize(p)
                os.remove(p)
            except Exception:
                pass
    return freed


def _clear_browser_edge() -> int:
    """Clear Edge cache and data."""
    freed = 0
    appdata = _student_appdata(local=True)
    if not appdata:
        return 0
    edge_base = os.path.join(appdata, "Microsoft", "Edge", "User Data")
    if not os.path.exists(edge_base):
        return 0

    targets = [
        "Default\\Cache\\Cache_Data",
        "Default\\Code Cache",
        "Default\\GPUCache",
        "Default\\History",
        "Default\\Cookies",
        "Default\\Sessions",
    ]
    for rel in targets:
        p = os.path.join(edge_base, rel)
        if os.path.isdir(p):
            freed += _rmtree_safe(p)
        elif os.path.isfile(p):
            try:
                freed += os.path.getsize(p)
                os.remove(p)
            except Exception:
                pass
    return freed


def _clear_browser_firefox() -> int:
    """Clear Firefox profiles cache."""
    freed = 0
    appdata = _student_appdata(local=False)
    if not appdata:
        return 0
    ff_profiles = os.path.join(appdata, "Mozilla", "Firefox", "Profiles")
    if not os.path.exists(ff_profiles):
        return 0

    for profile_dir in os.listdir(ff_profiles):
        profile_path = os.path.join(ff_profiles, profile_dir)
        if os.path.isdir(profile_path):
            for cache_dir in ["cache2", "startupCache", "thumbnails"]:
                freed += _rmtree_safe(os.path.join(profile_path, cache_dir))
    return freed


def run_cleanup(options: dict) -> dict:
    """
    Run cleanup based on *options* dict.
    
    Options keys (all default True if not specified):
        temp_files   : bool  Clear %TEMP% and Windows\\Temp
        recycle_bin  : bool  Empty the Recycle Bin
        downloads    : bool  Clear the user's Downloads folder
        browser_chrome : bool  Clear Chrome data
        browser_edge   : bool  Clear Edge data
        browser_firefox: bool  Clear Firefox data
    
    Returns a dict summarising what was cleaned.
    """
    report = {}
    total_freed = 0

    def opt(key: str) -> bool:
        return options.get(key, True)

    # ── Temp files ─────────────────────────────────────────────────────────────
    if opt("temp_files"):
        freed = 0
        seen: set[str] = set()
        for temp_dir in (_student_temp(), os.environ.get("TEMP"), "C:\\Windows\\Temp"):
            if not temp_dir:
                continue
            key = os.path.normcase(os.path.abspath(temp_dir))
            if key in seen:
                continue      # SYSTEM's %TEMP% and the student's can coincide
            seen.add(key)
            freed += _rmtree_safe(temp_dir)
        report["temp_files"] = {"freed_mb": _bytes_to_mb(freed)}
        total_freed += freed
        logger.info(f"Temp files cleaned: {_bytes_to_mb(freed)} MB")

    # ── Recycle bin ────────────────────────────────────────────────────────────
    if opt("recycle_bin"):
        ok = _empty_recycle_bin()
        report["recycle_bin"] = {"success": ok}
        logger.info(f"Recycle bin {'emptied' if ok else 'failed'}")

    # ── Downloads folder ───────────────────────────────────────────────────────
    if opt("downloads"):
        downloads = str(_student_home() / "Downloads")
        freed = _rmtree_safe(downloads)
        report["downloads"] = {"freed_mb": _bytes_to_mb(freed)}
        total_freed += freed
        logger.info(f"Downloads cleaned: {_bytes_to_mb(freed)} MB")

    # ── Browser data ───────────────────────────────────────────────────────────
    if opt("browser_chrome"):
        freed = _clear_browser_chrome()
        report["browser_chrome"] = {"freed_mb": _bytes_to_mb(freed)}
        total_freed += freed

    if opt("browser_edge"):
        freed = _clear_browser_edge()
        report["browser_edge"] = {"freed_mb": _bytes_to_mb(freed)}
        total_freed += freed

    if opt("browser_firefox"):
        freed = _clear_browser_firefox()
        report["browser_firefox"] = {"freed_mb": _bytes_to_mb(freed)}
        total_freed += freed

    report["total_freed_mb"] = _bytes_to_mb(total_freed)
    logger.info(f"Cleanup complete. Total freed: {_bytes_to_mb(total_freed)} MB")
    return report
