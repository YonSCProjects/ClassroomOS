"""
ClassroomOS — UI Bridge (session 0 side)
===========================================
Lets the agent reach the student's interactive desktop while it is itself stuck
in the isolated session 0 as a Windows service.

How it works
------------
1. The bridge listens on **127.0.0.1 only**, on `ui_port` (default 9001).
2. A watchdog thread notices when somebody is logged in at the console but no
   helper is running, and launches `agent_ui.py` inside that session using
   `CreateProcessAsUser` (see `session.launch_in_active_session`).
3. The helper connects back, presents the one-time token it was launched with,
   and then simply waits for commands.
4. `UIBridge.call()` forwards an interactive command to the helper and returns
   its response, so `agent_main._dispatch` can treat it like any local handler.

Security
--------
The listener is bound to loopback and every helper must present a token that is
regenerated on each launch. The console's `shared_secret` is deliberately *not*
reused here: the helper runs as the student, and the student must never need
read access to the secret that authenticates the teacher.

If anything in this chain is unavailable — no pywin32, nobody logged in, launch
denied — `call()` returns a normal error response and the rest of the agent
keeps working.
"""

from __future__ import annotations

import logging
import os
import secrets
import socket
import threading
import time

from protocol import (
    send_message, recv_message, ok_response, error_response, SOCKET_TIMEOUT,
)
import session

logger = logging.getLogger("ui_bridge")

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
UI_PORT    = 9001
CALL_TIMEOUT = 20.0      # Interactive commands are quick; screenshots dominate.
WATCH_INTERVAL = 5.0     # How often the watchdog re-checks the session.
RELAUNCH_BACKOFF = 15.0  # Minimum gap between helper launch attempts.


class UIBridge:
    """Owns the helper process and the loopback connection to it."""

    def __init__(self, config: dict):
        self.config = config
        self.port = int(config.get("ui_port", UI_PORT))

        self._server: socket.socket | None = None
        self._conn: socket.socket | None = None
        self._conn_lock = threading.Lock()   # serialises call() over one socket
        self._token = ""
        self._running = False
        self._last_launch = 0.0
        self._launched_session = -1

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Bind the loopback listener and start the accept + watchdog threads."""
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", self.port))
            srv.listen(2)
            srv.settimeout(2.0)
            self._server = srv
        except Exception as e:
            logger.error(f"UI bridge could not bind 127.0.0.1:{self.port}: {e}")
            return

        self._running = True
        threading.Thread(target=self._accept_loop, daemon=True, name="UIBridgeAccept").start()
        threading.Thread(target=self._watchdog_loop, daemon=True, name="UIBridgeWatchdog").start()
        logger.info(f"UI bridge listening on 127.0.0.1:{self.port}")

    def stop(self) -> None:
        self._running = False
        self._drop_connection()
        if self._server:
            try:
                self._server.close()
            except Exception:
                pass
            self._server = None

    # ── Connection handling ────────────────────────────────────────────────────

    def _accept_loop(self) -> None:
        while self._running and self._server:
            try:
                conn, addr = self._server.accept()
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    logger.debug(f"UI bridge accept error: {e}")
                continue

            if addr[0] != "127.0.0.1":
                logger.warning(f"Rejected non-loopback UI connection from {addr}")
                _close(conn)
                continue

            if not self._authenticate(conn):
                _close(conn)
                continue

            with self._conn_lock:
                old, self._conn = self._conn, conn
            if old:
                _close(old)
            logger.info("UI helper connected")

    def _authenticate(self, conn: socket.socket) -> bool:
        """Read the helper's HELLO frame and compare its one-time token."""
        try:
            conn.settimeout(SOCKET_TIMEOUT)
            hello = recv_message(conn)
            token = hello.get("token", "")
            expected = self._token
            if not expected or not secrets.compare_digest(str(token), expected):
                logger.warning("UI helper presented an invalid token")
                send_message(conn, error_response("Invalid token"))
                return False
            send_message(conn, ok_response({"ok": True}))
            return True
        except Exception as e:
            logger.debug(f"UI helper handshake failed: {e}")
            return False

    def _drop_connection(self) -> None:
        with self._conn_lock:
            conn, self._conn = self._conn, None
        if conn:
            _close(conn)

    @property
    def connected(self) -> bool:
        return self._conn is not None

    # ── Command forwarding ─────────────────────────────────────────────────────

    def call(self, cmd: str, payload: dict) -> dict:
        """
        Forward an interactive command to the helper and return its response.

        On any transport failure the connection is dropped so the watchdog can
        relaunch the helper, and an error response is returned to the console.
        """
        with self._conn_lock:
            conn = self._conn
            if conn is None:
                return error_response(_no_session_reason())
            try:
                conn.settimeout(CALL_TIMEOUT)
                send_message(conn, {"cmd": cmd, "payload": payload})
                return recv_message(conn)
            except Exception as e:
                logger.warning(f"UI helper call {cmd!r} failed: {e}")
                self._conn = None
                _close(conn)
                return error_response(f"Interactive session lost ({e})")

    # ── Watchdog ───────────────────────────────────────────────────────────────

    def _watchdog_loop(self) -> None:
        """Keep exactly one helper alive in whichever session is at the console."""
        while self._running:
            try:
                current = session.active_console_session()

                if current < 0:
                    # Nobody logged in — drop any stale helper and wait.
                    if self._conn is not None:
                        logger.info("No interactive session — dropping UI helper")
                        self._drop_connection()
                        self._launched_session = -1

                elif self._conn is None or current != self._launched_session:
                    # Either we have no helper, or the user switched sessions
                    # (fast user switching / logoff+login) and the old helper is
                    # now in the wrong desktop.
                    if current != self._launched_session:
                        self._drop_connection()
                    self._launch_helper(current)

            except Exception as e:
                logger.debug(f"UI watchdog error: {e}")

            time.sleep(WATCH_INTERVAL)

    def _launch_helper(self, target_session: int) -> None:
        now = time.time()
        if now - self._last_launch < RELAUNCH_BACKOFF:
            return
        self._last_launch = now

        # Fresh token per launch: a leaked command line stops being useful as
        # soon as the helper is restarted.
        self._token = secrets.token_hex(16)

        args = session.python_command(
            os.path.join(BASE_DIR, "agent_ui.py"),
            "--ui-host",
            "--port", str(self.port),
            "--token", self._token,
        )
        pid = session.launch_in_active_session(args)
        if pid:
            self._launched_session = target_session
            logger.info(f"UI helper launched into session {target_session} (pid {pid})")
        else:
            logger.warning("Could not launch UI helper into the interactive session")


def _close(sock) -> None:
    try:
        sock.close()
    except Exception:
        pass


def _no_session_reason() -> str:
    """A message the teacher can act on, rather than a bare failure."""
    if not session.has_interactive_user():
        return "No user is logged in on this machine"
    return "Interactive helper not connected yet"
