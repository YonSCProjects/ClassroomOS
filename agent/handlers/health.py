"""
ClassroomOS — Agent Health Handler
=====================================
Collects system health metrics, installed software inventory,
running process list, and basic machine info.

Uses psutil for cross-platform system stats and the Windows
Registry for installed software enumeration.
"""

import os
import socket
import getpass
import platform
import subprocess
import winreg
import time
import logging

logger = logging.getLogger("handler.health")

try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False
    logger.warning("psutil not available — health data will be limited")


def get_basic_info() -> dict:
    """
    Return quick identification info.
    Used by the console heartbeat (PING response also includes this).
    Includes WOL capability check via PowerShell.
    """
    return {
        "hostname":    socket.gethostname(),
        "user":        _safe_getuser(),
        "ip":          _get_local_ip(),
        "os_version":  platform.version(),
        "platform":    platform.system(),
        "wol_enabled": _check_wol_enabled(),
    }


def _check_wol_enabled() -> bool | None:
    """
    Check if Wake-on-LAN is enabled on any NIC via PowerShell.
    Returns True if WOL is enabled, False if disabled, None if undetermined.
    """
    try:
        result = subprocess.run(
            [
                "powershell", "-NonInteractive", "-Command",
                "(Get-NetAdapter | Get-NetAdapterAdvancedProperty "
                "-RegistryKeyword 'WakeOnMagicPacket' 2>$null "
                "| Where-Object {$_.RegistryValue -eq 1} "
                "| Measure-Object).Count -gt 0",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip().lower() == "true"
    except Exception:
        return None


def get_health_report() -> dict:
    """
    Full health report per machine:
      - CPU usage
      - RAM usage (total, used, free)
      - Disk usage per drive
      - Uptime
      - Pending Windows updates (count)
      - Logged-in user
    """
    report = get_basic_info()

    if _PSUTIL_AVAILABLE:
        # CPU
        report["cpu_percent"] = psutil.cpu_percent(interval=1)
        report["cpu_cores"]   = psutil.cpu_count()

        # RAM
        ram = psutil.virtual_memory()
        report["ram"] = {
            "total_gb":  round(ram.total / 1e9, 2),
            "used_gb":   round(ram.used  / 1e9, 2),
            "free_gb":   round(ram.available / 1e9, 2),
            "percent":   ram.percent,
        }

        # Disks
        disks = []
        for part in psutil.disk_partitions():
            if "cdrom" in part.opts or part.fstype == "":
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
                disks.append({
                    "drive":    part.mountpoint,
                    "total_gb": round(usage.total / 1e9, 2),
                    "used_gb":  round(usage.used  / 1e9, 2),
                    "free_gb":  round(usage.free  / 1e9, 2),
                    "percent":  usage.percent,
                })
            except Exception:
                pass
        report["disks"] = disks

        # Uptime
        boot_time = psutil.boot_time()
        uptime_s  = time.time() - boot_time
        report["uptime_hours"] = round(uptime_s / 3600, 1)

    # Pending Windows updates (lightweight check)
    report["pending_updates"] = _count_pending_updates()
    report["timestamp"] = time.time()
    return report


def get_processes() -> list[dict]:
    """
    Return list of running processes.
    Each entry: {name, pid, cpu_percent, memory_mb, username}
    """
    if not _PSUTIL_AVAILABLE:
        return []
    result = []
    for proc in psutil.process_iter(["name", "pid", "cpu_percent", "memory_info", "username"]):
        try:
            info = proc.info
            result.append({
                "name":      info["name"],
                "pid":       info["pid"],
                "cpu":       info["cpu_percent"],
                "mem_mb":    round((info["memory_info"].rss if info["memory_info"] else 0) / 1e6, 1),
                "username":  info["username"] or "",
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    # Sort by memory descending (most hungry apps first)
    result.sort(key=lambda x: x["mem_mb"], reverse=True)
    return result


def get_installed_software() -> list[dict]:
    """
    Read the list of installed programs from the Windows Registry.
    Checks both 32-bit and 64-bit uninstall keys.
    Returns a list of {name, version, publisher, install_date}
    """
    software = []
    keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]

    seen = set()
    for hive, key_path in keys:
        try:
            key = winreg.OpenKey(hive, key_path)
            num_subkeys = winreg.QueryInfoKey(key)[0]
            for i in range(num_subkeys):
                try:
                    sub_name = winreg.EnumKey(key, i)
                    sub_key  = winreg.OpenKey(key, sub_name)
                    name = _reg_value(sub_key, "DisplayName")
                    if not name or name in seen:
                        winreg.CloseKey(sub_key)
                        continue
                    seen.add(name)
                    software.append({
                        "name":         name,
                        "version":      _reg_value(sub_key, "DisplayVersion"),
                        "publisher":    _reg_value(sub_key, "Publisher"),
                        "install_date": _reg_value(sub_key, "InstallDate"),
                    })
                    winreg.CloseKey(sub_key)
                except Exception:
                    pass
            winreg.CloseKey(key)
        except Exception:
            pass

    return sorted(software, key=lambda x: (x["name"] or "").lower())


# ── Helpers ───────────────────────────────────────────────────────────────────

def _reg_value(key, value_name: str) -> str:
    """Read a string value from a registry key, return empty string on error."""
    try:
        val, _ = winreg.QueryValueEx(key, value_name)
        return str(val)
    except Exception:
        return ""


def _safe_getuser() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return os.environ.get("USERNAME", "unknown")


def _get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _count_pending_updates() -> int:
    """
    Count pending Windows updates using PowerShell's WindowsUpdate COM API.
    Returns -1 if the check fails or is unavailable.
    """
    try:
        result = subprocess.run(
            [
                "powershell", "-NonInteractive", "-Command",
                "(New-Object -ComObject Microsoft.Update.Session)"
                ".CreateUpdateSearcher().Search('IsInstalled=0').Updates.Count",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return int(result.stdout.strip())
    except Exception:
        return -1
