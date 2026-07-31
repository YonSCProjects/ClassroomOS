"""
ClassroomOS — Interactive-Desktop Commands
=============================================
The subset of commands that can only work from inside the student's own
Windows session: drawing windows, capturing the screen, injecting input.

This module is the *single* implementation of those commands. It is executed
either directly by the agent (when the agent already runs on the interactive
desktop, i.e. dev mode) or by the in-session helper `agent_ui.py` (when the
agent runs as a service in session 0 and forwards them over the UI bridge).

Keeping one implementation means the dev-mode path everybody tests and the
service path deployed in the lab cannot drift apart.
"""

import ctypes
import logging

from protocol import CMD, ok_response, error_response
from handler_loader import get_handler

logger = logging.getLogger("ui_commands")

# Commands that must run on the interactive desktop.
UI_COMMANDS = frozenset({
    CMD.SCREENSHOT,
    CMD.BROADCAST_FRAME,
    CMD.STOP_BROADCAST,
    CMD.MOUSE_EVENT,
    CMD.KEY_EVENT,
    CMD.SEND_MSG,
    CMD.DISMISS_MSG,
    CMD.LOCK_SCREEN,
    CMD.UNLOCK_SCREEN,
})

# Internal command, never sent by the console. The agent uses it to ask the
# helper how long the student has been idle: GetLastInputInfo is per-session, so
# asking from session 0 would report the service's own (permanent) idleness and
# log everyone off immediately.
IDLE_SECONDS = "__UI_IDLE__"


def local_idle_seconds() -> float | None:
    """
    Seconds since the last keyboard or mouse input **in this session**.
    Returns None when the value cannot be read, so callers can tell
    "idle for 0 seconds" apart from "no idea".
    """
    try:
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            return None
        # Both values are 32-bit tick counts, so mask the subtraction to stay
        # correct across the ~49-day GetTickCount wrap.
        elapsed = (ctypes.windll.kernel32.GetTickCount() - lii.dwTime) & 0xFFFFFFFF
        return elapsed / 1000.0
    except Exception as e:
        logger.debug(f"Idle query failed: {e}")
        return None


def handle(cmd: str, payload: dict) -> dict:
    """
    Execute an interactive-desktop command in the *current* session.

    Returns a normal protocol response dict. Never raises: a failure here is
    reported to the console rather than killing the connection or the helper.
    """
    try:
        # ── Internal: idle time in this session ────────────────────────────
        if cmd == IDLE_SECONDS:
            return ok_response({"idle_seconds": local_idle_seconds()})

        # ── Screen capture & broadcast ─────────────────────────────────────
        if cmd == CMD.SCREENSHOT:
            h = get_handler("screen")
            if not h:
                return error_response("screen handler unavailable")
            data = h.capture_screenshot(quality=payload.get("quality", 50))
            return ok_response({"image_b64": data})

        elif cmd == CMD.BROADCAST_FRAME:
            h = get_handler("screen")
            if not h:
                return error_response("screen handler unavailable")
            h.show_broadcast(payload.get("image_b64", ""))
            return ok_response()

        elif cmd == CMD.STOP_BROADCAST:
            h = get_handler("screen")
            if not h:
                return error_response("screen handler unavailable")
            h.stop_broadcast()
            return ok_response()

        # ── Remote input ───────────────────────────────────────────────────
        elif cmd == CMD.MOUSE_EVENT:
            h = get_handler("input_replay")
            if not h:
                return error_response("input handler unavailable")
            h.replay_mouse(payload)
            return ok_response()

        elif cmd == CMD.KEY_EVENT:
            h = get_handler("input_replay")
            if not h:
                return error_response("input handler unavailable")
            h.replay_key(payload)
            return ok_response()

        # ── Messaging ──────────────────────────────────────────────────────
        elif cmd == CMD.SEND_MSG:
            h = get_handler("messaging")
            if not h:
                return error_response("messaging handler unavailable")
            h.show_message(
                title   = payload.get("title", "הודעה"),
                message = payload.get("message", ""),
                timeout = payload.get("timeout", 0),
            )
            return ok_response()

        elif cmd == CMD.DISMISS_MSG:
            h = get_handler("messaging")
            if not h:
                return error_response("messaging handler unavailable")
            h.dismiss_all()
            return ok_response()

        # ── Lock screen ────────────────────────────────────────────────────
        elif cmd == CMD.LOCK_SCREEN:
            h = get_handler("lock")
            if not h:
                return error_response("lock handler unavailable")
            h.lock(message=payload.get("message", "המסך נעול"))
            return ok_response()

        elif cmd == CMD.UNLOCK_SCREEN:
            h = get_handler("lock")
            if not h:
                return error_response("lock handler unavailable")
            h.unlock()
            return ok_response()

        return error_response(f"Not an interactive command: {cmd!r}")

    except Exception as e:
        logger.exception(f"UI command {cmd!r} failed: {e}")
        return error_response(f"{cmd} failed: {e}")
