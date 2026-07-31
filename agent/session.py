"""
ClassroomOS — Windows Session Utilities
==========================================
Everything in this module exists to answer one question:
**which Windows session is the student actually sitting in, and how do we
reach it?**

Why this matters
----------------
When the agent runs as a Windows service it lives in *session 0*, which since
Windows Vista is isolated from every interactive desktop. A window created
from session 0 is never drawn on the student's screen, `mss` captures a blank
session-0 desktop instead of the student's, `pyautogui` injects input nowhere,
and `%TEMP%` / `Path.home()` resolve to the SYSTEM profile rather than the
student's.

So the agent has two very different modes:

* **In-session (dev mode / `python agent_main.py`)** — the agent already runs
  on the interactive desktop. Everything works directly; this module reports
  "not session 0" and callers take the simple path.
* **Service mode (session 0)** — UI, capture and input work must be handed to a
  helper process launched *inside* the student's session
  (see `agent/agent_ui.py`), and filesystem paths must be resolved against the
  student's profile rather than SYSTEM's.

Every function here degrades to a safe default when pywin32 is unavailable or
when a call is denied, so importing this module can never stop the agent from
starting.
"""

from __future__ import annotations

import os
import logging
import subprocess
import sys

logger = logging.getLogger("session")

# pywin32 is optional — without it we simply behave as if we are in-session.
try:
    import win32api
    import win32con
    import win32process
    import win32profile
    import win32security
    import win32ts
    _WIN32 = True
except ImportError:  # pragma: no cover - depends on deployment machine
    _WIN32 = False
    logger.warning("pywin32 unavailable — session detection disabled")

# Accounts that own an explorer.exe but are not the student.
_NON_INTERACTIVE_USERS = {
    "system", "local service", "network service",
    "משתמש מערכת",           # localized SYSTEM on Hebrew Windows
}


# ── Session identity ───────────────────────────────────────────────────────────

def current_session_id() -> int:
    """
    Session this agent process is running in.
    Returns 0 when running as a service, or -1 if it cannot be determined.
    """
    if not _WIN32:
        return -1
    try:
        return win32ts.ProcessIdToSessionId(win32api.GetCurrentProcessId())
    except Exception as e:
        logger.debug(f"ProcessIdToSessionId failed: {e}")
        return -1


def active_console_session() -> int:
    """
    Session currently attached to the physical console (keyboard + screen).
    Returns -1 when nobody is logged in, or 0xFFFFFFFF is reported.
    """
    if not _WIN32:
        return -1
    try:
        sid = win32ts.WTSGetActiveConsoleSessionId()
        # 0xFFFFFFFF means "no session attached" (e.g. sitting at the lock screen
        # during a fast-user-switch transition).
        return -1 if sid in (0xFFFFFFFF, -1) else int(sid)
    except Exception as e:
        logger.debug(f"WTSGetActiveConsoleSessionId failed: {e}")
        return -1


def is_session0() -> bool:
    """
    True when the agent is running as a service in the isolated session 0 and
    therefore cannot touch the interactive desktop directly.
    """
    return current_session_id() == 0


def has_interactive_user() -> bool:
    """True when somebody is logged in at the console."""
    return active_console_session() >= 0


# ── Active user identity ───────────────────────────────────────────────────────

def active_username() -> str:
    """
    Username of the student logged in at the console, without the domain part.
    Falls back to the agent's own username when detection is unavailable, and
    returns "" when nobody is logged in.
    """
    if not _WIN32:
        return _own_username()

    sid = active_console_session()
    if sid < 0:
        return ""

    try:
        name = win32ts.WTSQuerySessionInformation(
            win32ts.WTS_CURRENT_SERVER_HANDLE, sid, win32ts.WTSUserName
        )
        if name:
            return str(name)
    except Exception as e:
        logger.debug(f"WTSQuerySessionInformation failed: {e}")

    return _explorer_owner() or _own_username()


def _own_username() -> str:
    try:
        import getpass
        return getpass.getuser()
    except Exception:
        return os.environ.get("USERNAME", "")


def _explorer_owner() -> str:
    """
    Fallback identification: whoever owns explorer.exe is the interactive user.
    Works without pywin32 and without any special privilege.
    """
    try:
        import psutil
    except ImportError:
        return ""

    for proc in psutil.process_iter(["name", "username"]):
        try:
            if (proc.info.get("name") or "").lower() != "explorer.exe":
                continue
            owner = proc.info.get("username") or ""
            short = owner.split("\\")[-1]
            if short and short.lower() not in _NON_INTERACTIVE_USERS:
                return short
        except Exception:
            continue
    return ""


def user_profile_dir() -> str:
    """
    Profile directory of the interactive student (e.g. ``C:\\Users\\student``).

    This is the single most important function for handlers that write to
    "Desktop" or clean "%TEMP%": under the service those environment variables
    point at the SYSTEM profile, which is never what the teacher meant.

    Falls back to the agent's own profile when no interactive user is found, so
    dev-mode behaviour is unchanged.
    """
    own = os.environ.get("USERPROFILE", "")

    # In-session: our own profile IS the student's profile.
    if not is_session0():
        return own

    token = _user_token()
    if token:
        try:
            path = win32profile.GetUserProfileDirectory(token)
            if path and os.path.isdir(path):
                return str(path)
        except Exception as e:
            logger.debug(f"GetUserProfileDirectory failed: {e}")
        finally:
            _close(token)

    # Last resort: derive from the username.
    user = active_username()
    if user:
        guess = os.path.join(os.environ.get("SYSTEMDRIVE", "C:") + "\\", "Users", user)
        if os.path.isdir(guess):
            return guess

    return own


def user_temp_dir() -> str:
    """Temp directory belonging to the interactive student."""
    if not is_session0():
        return os.environ.get("TEMP") or os.environ.get("TMP") or ""
    profile = user_profile_dir()
    if not profile:
        return ""
    return os.path.join(profile, "AppData", "Local", "Temp")


def user_appdata(local: bool = True) -> str:
    """
    AppData directory of the interactive student.
    *local* selects ``AppData\\Local`` (browsers' caches) vs ``AppData\\Roaming``.
    """
    if not is_session0():
        env = "LOCALAPPDATA" if local else "APPDATA"
        return os.environ.get(env, "")
    profile = user_profile_dir()
    if not profile:
        return ""
    return os.path.join(profile, "AppData", "Local" if local else "Roaming")


# ── Launching a process into the student's session ─────────────────────────────

def _user_token():
    """
    Primary token for the console session, or None.
    Requires SYSTEM privileges (SE_TCB_NAME) — i.e. only works from the service.
    """
    if not _WIN32:
        return None
    sid = active_console_session()
    if sid < 0:
        return None
    try:
        return win32ts.WTSQueryUserToken(sid)
    except Exception as e:
        logger.debug(f"WTSQueryUserToken({sid}) failed: {e}")
        return None


def _close(handle) -> None:
    try:
        handle.Close()
    except Exception:
        pass


def launch_in_active_session(args: list[str]) -> int | None:
    """
    Start a process inside the student's interactive session.

    *args* is an argv-style list; it is run with the same Python interpreter the
    agent uses (or directly, when the agent is a frozen .exe).

    Returns the new process id, or None when the launch was not possible
    (nobody logged in, no privileges, pywin32 missing).
    """
    if not _WIN32:
        return None

    token = _user_token()
    if not token:
        return None

    dup = None
    env_block = None
    try:
        # CreateProcessAsUser needs a *primary* token.
        dup = win32security.DuplicateTokenEx(
            token,
            win32security.SecurityImpersonation,
            win32con.MAXIMUM_ALLOWED,
            win32security.TokenPrimary,
            None,
        )
        env_block = win32profile.CreateEnvironmentBlock(dup, False)

        startup = win32process.STARTUPINFO()
        # This is what actually puts the window on the student's desktop.
        startup.lpDesktop = "winsta0\\default"
        startup.dwFlags = win32con.STARTF_USESHOWWINDOW
        startup.wShowWindow = win32con.SW_SHOW

        cmdline = subprocess.list2cmdline(args)
        flags = (
            win32con.CREATE_UNICODE_ENVIRONMENT
            | win32con.CREATE_NEW_CONSOLE
            | getattr(win32con, "CREATE_NO_WINDOW", 0x08000000)
        )

        h_process, h_thread, pid, _tid = win32process.CreateProcessAsUser(
            dup,
            None,          # application name — taken from the command line
            cmdline,
            None, None,    # process / thread security
            False,         # do not inherit handles
            flags,
            env_block,
            None,          # inherit current directory
            startup,
        )
        _close(h_process)
        _close(h_thread)
        logger.info(f"Launched in session {active_console_session()}: {cmdline} (pid {pid})")
        return int(pid)

    except Exception as e:
        logger.error(f"CreateProcessAsUser failed: {e}")
        return None
    finally:
        if env_block is not None:
            try:
                win32profile.DestroyEnvironmentBlock(env_block)
            except Exception:
                pass
        if dup is not None:
            _close(dup)
        _close(token)


def python_command(script_path: str, *script_args: str) -> list[str]:
    """
    Build the argv needed to run *script_path*, accounting for PyInstaller.

    When frozen, the bundled executable re-executes itself with the script name
    as the first argument (see the `--ui-host` switch in `agent_main.py`);
    otherwise we invoke the current interpreter.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, *script_args]
    exe = sys.executable
    # Prefer the windowed interpreter so the helper never flashes a console.
    pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.isfile(pythonw):
        exe = pythonw
    return [exe, script_path, *script_args]
