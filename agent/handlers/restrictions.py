"""
ClassroomOS — Agent Restrictions Handler
============================================
Handles: app blocklist enforcement, USB storage disable, internet disable.

App blocking: monitors running processes and kills blocked ones immediately.
USB: modifies the USBSTOR registry key (requires SYSTEM privileges).
Internet: adds/removes a firewall rule that blocks all outbound traffic
          except for the console IP (so the agent stays connected).
"""

import subprocess
import winreg
import threading
import time
import logging
import ctypes

logger = logging.getLogger("handler.restrictions")

# ── App Blocklist ─────────────────────────────────────────────────────────────
_blocklist: set[str] = set()
_blocklist_lock = threading.Lock()

FIREWALL_RULE_NAME = "ClassroomOS_BlockInternet"


def update_blocklist(names: list[str]) -> None:
    """Update the in-memory blocklist. Names are case-insensitive exe names."""
    global _blocklist
    with _blocklist_lock:
        _blocklist = {n.lower() for n in names}
    logger.info(f"Blocklist updated: {_blocklist}")


def _kill_blocked_processes() -> None:
    """Kill any running processes that are on the blocklist."""
    try:
        import psutil
    except ImportError:
        logger.warning("psutil not available — cannot enforce blocklist")
        return

    with _blocklist_lock:
        blocked = set(_blocklist)
    if not blocked:
        return

    for proc in psutil.process_iter(["name", "pid"]):
        try:
            # process_iter can hand back a None name for processes that exit
            # mid-scan; .lower() on that used to abort the whole sweep.
            name = (proc.info.get("name") or "").lower()
            if name and name in blocked:
                proc.kill()
                logger.info(f"Killed blocked process: {proc.info['name']} (PID {proc.info['pid']})")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        except Exception as e:
            logger.debug(f"Blocklist check failed for a process: {e}")
            continue


# ── USB Storage ───────────────────────────────────────────────────────────────
USBSTOR_KEY = r"SYSTEM\CurrentControlSet\Services\USBSTOR"
USBSTOR_VALUE = "Start"
USB_ENABLED_VAL  = 3   # Manual start (USB works normally)
USB_DISABLED_VAL = 4   # Disabled


def set_usb(enabled: bool) -> bool:
    """
    Enable or disable USB Mass Storage devices via registry.
    Requires the agent to be running as SYSTEM or Administrator.

    Returns True on success.
    """
    value = USB_ENABLED_VAL if enabled else USB_DISABLED_VAL
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            USBSTOR_KEY,
            0,
            winreg.KEY_SET_VALUE,
        )
        winreg.SetValueEx(key, USBSTOR_VALUE, 0, winreg.REG_DWORD, value)
        winreg.CloseKey(key)
        status = "enabled" if enabled else "disabled"
        logger.info(f"USB storage {status} (registry value = {value})")

        # Unload the driver immediately if disabling (already-inserted drives)
        if not enabled:
            subprocess.run(
                ["sc", "stop", "USBSTOR"],
                capture_output=True,
            )
        return True
    except PermissionError:
        logger.error("Cannot modify USBSTOR — agent needs SYSTEM/admin privileges")
        return False
    except Exception as e:
        logger.error(f"USB registry error: {e}")
        return False


# ── Internet Access ────────────────────────────────────────────────────────────
def set_internet(enabled: bool, console_ip: str = "") -> bool:
    """
    Enable or disable internet access using Windows Firewall rules.

    When disabled:
      - Adds a firewall rule blocking ALL outbound connections
      - Adds an exception for the console IP so the agent stays reachable

    When enabled:
      - Removes the blocking rule
    
    Returns True on success.
    """
    try:
        if enabled:
            # Remove the block rule if it exists
            subprocess.run(
                ["netsh", "advfirewall", "firewall", "delete", "rule",
                 f"name={FIREWALL_RULE_NAME}"],
                capture_output=True,
            )
            logger.info("Internet access enabled — firewall block rule removed")
        else:
            # First, delete any existing rule with the same name (idempotent)
            subprocess.run(
                ["netsh", "advfirewall", "firewall", "delete", "rule",
                 f"name={FIREWALL_RULE_NAME}"],
                capture_output=True,
            )
            # Add block-all-outbound rule
            subprocess.run(
                ["netsh", "advfirewall", "firewall", "add", "rule",
                 f"name={FIREWALL_RULE_NAME}",
                 "dir=out", "action=block", "protocol=any",
                 "enable=yes", "profile=any"],
                check=True,
            )
            # Add allow exception for console IP (agent communication)
            if console_ip:
                subprocess.run(
                    ["netsh", "advfirewall", "firewall", "add", "rule",
                     f"name={FIREWALL_RULE_NAME}_ConsoleAllow",
                     "dir=out", "action=allow", "protocol=TCP",
                     f"remoteip={console_ip}", "enable=yes", "profile=any"],
                    check=True,
                )
            logger.info("Internet access disabled — firewall block rule added")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Firewall command failed: {e}")
        return False
    except Exception as e:
        logger.error(f"Internet control error: {e}")
        return False


# ── Restriction Watcher (background thread) ───────────────────────────────────
class RestrictionWatcher:
    """
    Background thread that:
      1. Continuously enforces the process blocklist (kills forbidden apps)
      2. Monitors idle time and auto-logs-off if configured, after warning
         the student

    Idle detection and the warning popup both need the student's session, which
    the agent does not have when it runs as a service. Rather than guess, the
    agent injects *idle_provider* and *notify* callbacks that route to whichever
    session is interactive. When idle time cannot be determined the auto-logoff
    is skipped entirely — logging a class out because we could not read a timer
    would be far worse than leaving them logged in.
    """

    POLL_INTERVAL = 2.0  # Seconds between checks

    def __init__(self, config: dict, idle_provider=None, notify=None):
        self.config = config
        self.idle_provider = idle_provider
        self.notify = notify
        self._warning_shown_at: float | None = None
        # Initialize blocklist from config
        update_blocklist(config.get("blocklist", []))

    def _get_idle_seconds(self) -> float | None:
        """
        Seconds since the student last touched the keyboard or mouse,
        or None when that cannot be determined.
        """
        if self.idle_provider is not None:
            try:
                return self.idle_provider()
            except Exception as e:
                logger.debug(f"Idle provider failed: {e}")
                return None

        # In-session fallback (dev mode).
        try:
            class LASTINPUTINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

            lii = LASTINPUTINFO()
            lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
            if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
                return None
            millis = (ctypes.windll.kernel32.GetTickCount() - lii.dwTime) & 0xFFFFFFFF
            return millis / 1000.0
        except Exception as e:
            logger.debug(f"GetLastInputInfo failed: {e}")
            return None

    def _warn(self, seconds_left: int) -> None:
        """Tell the student what is about to happen, if we can reach them."""
        if self.notify is None:
            return
        try:
            self.notify(
                "התנתקות אוטומטית",
                f"לא זוהתה פעילות. המחשב יתנתק בעוד {seconds_left} שניות.",
                seconds_left,
            )
        except Exception as e:
            logger.debug(f"Auto-logoff warning failed: {e}")

    def _check_autologoff(self) -> None:
        idle_limit_min = self.config.get("autologoff_idle_minutes", 0) or 0
        if idle_limit_min <= 0:
            self._warning_shown_at = None
            return

        idle_secs = self._get_idle_seconds()
        if idle_secs is None:
            return  # Unknown idle time — never log anybody off on a guess.

        warn_secs = int(self.config.get("autologoff_warning_seconds", 30) or 0)
        limit_secs = idle_limit_min * 60

        if idle_secs < limit_secs - warn_secs:
            self._warning_shown_at = None
            return

        if idle_secs < limit_secs:
            # Inside the warning window — show the countdown once.
            if self._warning_shown_at is None:
                remaining = max(1, int(limit_secs - idle_secs))
                logger.info(f"Auto-logoff warning shown ({remaining}s remaining)")
                self._warn(remaining)
                self._warning_shown_at = time.time()
            return

        logger.info(f"Auto-logoff triggered (idle {idle_secs:.0f}s)")
        import handlers.power as power_handler
        power_handler.logoff()
        self._warning_shown_at = None
        time.sleep(30)  # Don't log off again immediately

    def run(self) -> None:
        """Main watcher loop. Runs forever as a daemon thread."""
        logger.info("RestrictionWatcher started")
        while True:
            try:
                if _blocklist:
                    _kill_blocked_processes()
                self._check_autologoff()
            except Exception as e:
                # A watcher that dies stops enforcing everything, so it must
                # survive any single failed iteration.
                logger.error(f"RestrictionWatcher iteration failed: {e}")

            time.sleep(self.POLL_INTERVAL)
