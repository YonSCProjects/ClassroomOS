"""
ClassroomOS — Agent Power Handler
====================================
Handles: shutdown, restart, logoff, panic reset.
All operations are deferred slightly to allow the response to be sent first.
"""

import subprocess
import threading
import logging
import time

logger = logging.getLogger("handler.power")


def _run_delayed(fn, delay: float = 1.0):
    """Run *fn* after *delay* seconds on a daemon thread."""
    def _wrapper():
        time.sleep(delay)
        fn()
    threading.Thread(target=_wrapper, daemon=True).start()


def shutdown(delay_seconds: int = 30) -> None:
    """
    Schedule a system shutdown.
    *delay_seconds* gives the user time to save work.
    A delay of 0 shuts down immediately (after response is sent).
    """
    logger.info(f"Shutdown scheduled in {delay_seconds}s")
    # Send the Windows shutdown command
    # /f = force close apps, /t = delay in seconds, /s = shutdown
    _run_delayed(
        lambda: subprocess.run(
            ["shutdown", "/s", "/f", "/t", str(delay_seconds)],
            shell=False,
        )
    )


def restart(delay_seconds: int = 30) -> None:
    """Schedule a system restart."""
    logger.info(f"Restart scheduled in {delay_seconds}s")
    _run_delayed(
        lambda: subprocess.run(
            ["shutdown", "/r", "/f", "/t", str(delay_seconds)],
            shell=False,
        )
    )


def logoff() -> None:
    """Log off the currently logged-in user."""
    logger.info("Logging off current user")
    _run_delayed(
        lambda: subprocess.run(
            ["shutdown", "/l", "/f"],
            shell=False,
        )
    )


def abort_shutdown() -> None:
    """Abort a pending shutdown/restart."""
    subprocess.run(["shutdown", "/a"], shell=False)
    logger.info("Shutdown aborted")


# Windows falls over if these die, so they are never candidates — even when
# they are running as the student. The shell entries (explorer, the Start menu,
# the input host) are excluded because killing them leaves a black screen with
# no taskbar, which is not "reset the class", it is "break the machine".
_NEVER_KILL = {
    # Kernel / session infrastructure
    "system", "registry", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe",
    "services.exe", "lsass.exe", "svchost.exe", "fontdrvhost.exe", "dwm.exe",
    "conhost.exe", "dllhost.exe", "audiodg.exe", "userinit.exe", "logonui.exe",
    "spoolsv.exe", "sppsvc.exe", "wudfhost.exe", "memory compression",
    # Interactive shell — the student needs a desktop after the reset
    "explorer.exe", "sihost.exe", "ctfmon.exe", "taskhostw.exe",
    "runtimebroker.exe", "shellexperiencehost.exe", "startmenuexperiencehost.exe",
    "searchapp.exe", "searchhost.exe", "textinputhost.exe",
    "applicationframehost.exe", "lockapp.exe", "systemsettings.exe",
    # ClassroomOS itself
    "classroomosagent.exe", "agent_main.exe",
}

# Marks a process as part of ClassroomOS even when it is a generic python.exe.
_OWN_MARKERS = ("agent_main", "agent_ui", "agent_service", "--ui-host", "classroomos")

_TERMINATE_GRACE = 3.0   # Seconds to let apps close before forcing them.


def _is_own_process(proc, own_pids: set[int]) -> bool:
    """True for the agent, the in-session UI helper, and their children."""
    try:
        if proc.pid in own_pids:
            return True
        cmdline = " ".join(proc.cmdline() or []).lower()
    except Exception:
        return False
    return any(marker in cmdline for marker in _OWN_MARKERS)


def panic_reset(config: dict) -> None:
    """
    Close every application the student has open, and nothing else.

    "Panic" means the teacher wants the class back to a clean desktop *now* —
    it does not mean destroying the Windows install. Only processes owned by
    the interactive user are candidates, the shell and system infrastructure
    are never touched, and ClassroomOS is careful not to kill itself or its own
    in-session helper.

    Applications are asked to close first and only forced after a short grace
    period, so anything with unsaved work gets its chance to prompt.
    """
    logger.warning("PANIC RESET initiated")

    try:
        import psutil
    except ImportError:
        logger.error("psutil unavailable — cannot perform a targeted panic reset")
        return

    # Whose applications are we closing?
    try:
        import session
        target_user = (session.active_username() or "").lower()
    except Exception:
        target_user = ""

    own_pids = _collect_own_pids(psutil)
    victims = []

    for proc in psutil.process_iter(["name", "pid", "username"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if not name or name in _NEVER_KILL:
                continue

            owner = (proc.info.get("username") or "").split("\\")[-1].lower()
            # Only the student's own applications. Without a known user we stay
            # conservative and skip anything running as a service account.
            if target_user:
                if owner != target_user:
                    continue
            elif owner in ("system", "local service", "network service", ""):
                continue

            if _is_own_process(proc, own_pids):
                continue

            victims.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        except Exception:
            continue

    if not victims:
        logger.info("Panic reset: nothing to close")
        return

    for proc in victims:
        try:
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        except Exception as e:
            logger.debug(f"terminate({proc.pid}) failed: {e}")

    gone, alive = psutil.wait_procs(victims, timeout=_TERMINATE_GRACE)

    for proc in alive:
        try:
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        except Exception as e:
            logger.debug(f"kill({proc.pid}) failed: {e}")

    logger.info(
        f"Panic reset complete: {len(gone)} closed gracefully, {len(alive)} forced"
    )


def _collect_own_pids(psutil) -> set[int]:
    """PIDs of this process, its parents and its children."""
    pids = set()
    try:
        me = psutil.Process()
        pids.add(me.pid)
        for child in me.children(recursive=True):
            pids.add(child.pid)
        parent = me.parent()
        while parent:
            pids.add(parent.pid)
            parent = parent.parent()
    except Exception:
        pass
    return pids
