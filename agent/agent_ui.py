"""
ClassroomOS — In-Session UI Helper
=====================================
Runs inside the student's own Windows session and performs the work the
service cannot do from session 0: showing the lock screen and message popups,
capturing the screen, and replaying mouse/keyboard input.

It is launched automatically by `ui_bridge.UIBridge` — you never start it by
hand. It connects back to the agent on 127.0.0.1, proves itself with the
one-time token it was given on the command line, and then serves interactive
commands until the agent goes away or the user logs off.

Because it executes `ui_commands.handle()`, it runs exactly the same code the
agent runs directly in dev mode.

Usage (internal):
    pythonw agent_ui.py --ui-host --port 9001 --token <hex>
"""

import argparse
import logging
import os
import socket
import sys
import time

# ── Path setup (same convention as agent_main.py) ─────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(
    sys.executable if getattr(sys, "frozen", False) else __file__
))
sys.path.insert(0, os.path.join(BASE_DIR, "..", "shared"))
sys.path.insert(0, BASE_DIR)

from protocol import send_message, recv_message, error_response  # noqa: E402
import ui_commands  # noqa: E402

LOG_PATH = os.path.join(
    os.environ.get("TEMP", BASE_DIR), "classroomos_ui.log"
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8")],
)
logger = logging.getLogger("agent_ui")

RETRY_DELAY = 3.0
MAX_RETRIES = 10


def _connect(port: int, token: str) -> socket.socket | None:
    """Connect to the agent's loopback bridge and complete the handshake."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10.0)
        sock.connect(("127.0.0.1", port))
        send_message(sock, {"token": token})
        resp = recv_message(sock)
        if resp.get("status") != "ok":
            logger.error(f"Handshake rejected: {resp.get('error')}")
            sock.close()
            return None
        logger.info(f"Connected to agent on port {port}")
        return sock
    except Exception as e:
        logger.debug(f"Connect failed: {e}")
        return None


def _serve(sock: socket.socket) -> None:
    """Handle interactive commands until the agent disconnects."""
    # No timeout: the helper spends most of its life idle, waiting for the
    # teacher to do something.
    sock.settimeout(None)
    while True:
        try:
            msg = recv_message(sock)
        except ConnectionError:
            logger.info("Agent closed the UI connection")
            return
        except Exception as e:
            logger.warning(f"UI receive error: {e}")
            return

        cmd = msg.get("cmd", "")
        payload = msg.get("payload", {})

        try:
            response = ui_commands.handle(cmd, payload)
        except Exception as e:
            logger.exception(f"Command {cmd!r} raised: {e}")
            response = error_response(str(e))

        try:
            send_message(sock, response)
        except Exception as e:
            logger.warning(f"UI send error: {e}")
            return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ClassroomOS in-session UI helper")
    parser.add_argument("--ui-host", action="store_true",
                        help="Marker flag used when re-executing a frozen build")
    parser.add_argument("--port", type=int, default=9001)
    parser.add_argument("--token", required=True)
    args = parser.parse_args(argv)

    logger.info(f"UI helper starting (pid {os.getpid()}, user {os.environ.get('USERNAME')})")

    # The service may still be binding its listener when we start.
    sock = None
    for _attempt in range(MAX_RETRIES):
        sock = _connect(args.port, args.token)
        if sock:
            break
        time.sleep(RETRY_DELAY)

    if not sock:
        logger.error("Could not reach the agent — exiting")
        return 1

    try:
        _serve(sock)
    finally:
        try:
            sock.close()
        except Exception:
            pass
        # Leave the desktop in a usable state if we are torn down mid-lock.
        try:
            from handler_loader import get_handler
            lock = get_handler("lock")
            if lock:
                lock.unlock()
        except Exception:
            pass

    logger.info("UI helper exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
