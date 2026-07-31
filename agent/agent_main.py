"""
ClassroomOS — Agent Main
==========================
The TCP server that runs on each client PC as a Windows service.
Listens on port 9000, authenticates every command with HMAC, and
dispatches to the appropriate handler module.

Architecture:
  - One persistent TCP server socket
  - Each incoming console connection gets its own handler thread
  - Only one console connection is expected at a time, but multiple
    are handled gracefully (e.g., reconnects during a session)
"""

import sys
import os
import json
import socket
import threading
import logging
import time

# ── Path setup ────────────────────────────────────────────────────────────────
# When compiled with PyInstaller, __file__ may not exist; use sys.executable dir
BASE_DIR = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
SHARED_DIR = os.path.join(BASE_DIR, "..", "shared")
sys.path.insert(0, SHARED_DIR)
sys.path.insert(0, BASE_DIR)

from protocol import (
    CMD, recv_message, send_message, verify_token,
    ok_response, error_response, AGENT_PORT
)
from handler_loader import get_handler
import ui_commands
import session

# ── Logging setup ─────────────────────────────────────────────────────────────
LOG_PATH = os.path.join(BASE_DIR, "agent.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("agent")

# ── Config ─────────────────────────────────────────────────────────────────────
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

DEFAULT_CONFIG = {
    "shared_secret":    "",        # Must match the console's shared_secret
    "console_ip":       "",        # Optional: restrict to specific console IP
    "listen_port":      AGENT_PORT,
    "ui_port":          9001,      # Loopback port for the in-session UI helper
    "lock_hotkey_disabled": True,  # Disable Ctrl+Alt+Del on lock screen
    "blocklist":        [],        # App names to block (e.g. ["chrome.exe"])
    "usb_enabled":      True,
    "internet_enabled": True,
    "autologoff_idle_minutes": 0,  # 0 = disabled
    "autologoff_warning_seconds": 30,  # Countdown shown before an idle logoff
    "handin_folder":    "C:\\HandIn",
    "backup_dir":       "",        # Where scheduled backups are written locally
}


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        cfg = DEFAULT_CONFIG.copy()
        cfg.update(loaded)
        return cfg
    else:
        logger.warning("config.json not found, using defaults")
        return DEFAULT_CONFIG.copy()


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# ── Connection handler ────────────────────────────────────────────────────────
class AgentHandler:
    """
    Handles one console connection session.
    Reads commands in a loop, verifies auth, dispatches to handlers.
    """

    def __init__(self, sock: socket.socket, addr: tuple, config: dict,
                 ui_bridge=None):
        self.sock   = sock
        self.addr   = addr
        self.config = config
        self.secret = config.get("shared_secret", "")
        self.running = True
        # Set only in service mode; None means we are already on the desktop.
        self.ui_bridge = ui_bridge

    def _get_handler(self, name: str):
        """Handlers are lazily imported and cached process-wide."""
        return get_handler(name)

    def _dispatch_ui(self, cmd: str, payload: dict) -> dict:
        """
        Run an interactive-desktop command.

        In dev mode the agent already owns the interactive desktop, so the
        command runs in-process. As a service we are in session 0 and must hand
        it to the helper running in the student's session.
        """
        if self.ui_bridge is not None:
            return self.ui_bridge.call(cmd, payload)
        return ui_commands.handle(cmd, payload)

    def _dispatch(self, cmd: str, payload: dict) -> dict:
        """Route a command to the correct handler function."""
        try:
            # ── Lifecycle ──────────────────────────────────────────────────
            if cmd == CMD.PING:
                import socket as _socket
                import getpass
                return ok_response({
                    "hostname": _socket.gethostname(),
                    "user":     getpass.getuser(),
                    "time":     time.time(),
                })

            elif cmd == CMD.SHUTDOWN:
                h = self._get_handler("power")
                if h: h.shutdown(payload.get("delay", 30))
                return ok_response()

            elif cmd == CMD.RESTART:
                h = self._get_handler("power")
                if h: h.restart(payload.get("delay", 30))
                return ok_response()

            elif cmd == CMD.LOGOFF:
                h = self._get_handler("power")
                if h: h.logoff()
                return ok_response()

            # ── Interactive desktop: screen, input, messages, lock ────────
            # These only work from inside the student's own session, so they
            # are routed through the UI bridge when running as a service.
            elif cmd in ui_commands.UI_COMMANDS:
                return self._dispatch_ui(cmd, payload)

            # ── Restrictions ───────────────────────────────────────────────
            elif cmd == CMD.SET_BLOCKLIST:
                h = self._get_handler("restrictions")
                if h:
                    blocklist = payload.get("blocklist", [])
                    self.config["blocklist"] = blocklist
                    save_config(self.config)
                    h.update_blocklist(blocklist)
                    return ok_response()
                return error_response("restrictions handler unavailable")

            elif cmd == CMD.SET_USB:
                h = self._get_handler("restrictions")
                if h:
                    enabled = payload.get("enabled", True)
                    h.set_usb(enabled)
                    self.config["usb_enabled"] = enabled
                    save_config(self.config)
                    return ok_response()
                return error_response("restrictions handler unavailable")

            elif cmd == CMD.SET_INTERNET:
                h = self._get_handler("restrictions")
                if h:
                    enabled = payload.get("enabled", True)
                    # Without the console's IP the block-all-outbound rule would
                    # have no exception and the agent could lose the console.
                    console_ip = payload.get("console_ip") or self.config.get("console_ip", "")
                    ok = h.set_internet(enabled, console_ip=console_ip)
                    self.config["internet_enabled"] = enabled
                    if console_ip:
                        self.config["console_ip"] = console_ip
                    save_config(self.config)
                    if not ok:
                        return error_response(
                            "Firewall update failed — agent needs SYSTEM/admin privileges"
                        )
                    return ok_response()
                return error_response("restrictions handler unavailable")

            # ── Cleanup ────────────────────────────────────────────────────
            elif cmd == CMD.CLEANUP:
                h = self._get_handler("cleanup")
                if h:
                    options = payload.get("options", {})
                    report  = h.run_cleanup(options)
                    return ok_response({"report": report})
                return error_response("cleanup handler unavailable")

            # ── Files ──────────────────────────────────────────────────────
            elif cmd == CMD.PUSH_FILE:
                h = self._get_handler("files")
                if h:
                    result = h.receive_file(
                        filename  = payload.get("filename"),
                        dest_path = payload.get("dest_path"),
                        data_b64  = payload.get("data_b64"),
                    )
                    return ok_response(result)
                return error_response("files handler unavailable")

            elif cmd == CMD.COLLECT_FILES:
                h = self._get_handler("files")
                if h:
                    folder   = payload.get("folder") or payload.get("folders") or self.config.get("handin_folder", "C:\\HandIn")
                    compress = bool(payload.get("compress", True))
                    level    = int(payload.get("compresslevel", 6))
                    zip_b64  = h.collect_folder(folder, compress=compress, compresslevel=level)
                    return ok_response({"zip_b64": zip_b64, "folder": folder})
                return error_response("files handler unavailable")

            # ── Health & info ──────────────────────────────────────────────
            elif cmd == CMD.HEALTH_REPORT:
                h = self._get_handler("health")
                if h:
                    report = h.get_health_report()
                    return ok_response(report)
                return error_response("health handler unavailable")

            elif cmd == CMD.GET_INFO:
                h = self._get_handler("health")
                if h:
                    info = h.get_basic_info()
                    return ok_response(info)
                return error_response("health handler unavailable")

            elif cmd == CMD.INVENTORY:
                h = self._get_handler("health")
                if h:
                    inv = h.get_installed_software()
                    return ok_response({"software": inv})
                return error_response("health handler unavailable")

            elif cmd == CMD.GET_PROCESSES:
                h = self._get_handler("health")
                if h:
                    procs = h.get_processes()
                    return ok_response({"processes": procs})
                return error_response("health handler unavailable")

            # ── Session ────────────────────────────────────────────────────
            elif cmd == CMD.PANIC:
                h = self._get_handler("power")
                if h:
                    h.panic_reset(self.config)
                    return ok_response()
                return error_response("power handler unavailable")

            elif cmd == CMD.SET_AUTOLOGOFF:
                minutes = payload.get("minutes", 0)
                self.config["autologoff_idle_minutes"] = minutes
                save_config(self.config)
                return ok_response()

            # ── Config ─────────────────────────────────────────────────────
            elif cmd == CMD.UPDATE_CONFIG:
                updates = payload.get("updates", {})
                self.config.update(updates)
                save_config(self.config)
                return ok_response()

            # ── Backup ─────────────────────────────────────────────────────
            elif cmd == CMD.BACKUP_NOW:
                h = self._get_handler("backup")
                if h:
                    return h.handle_backup_now(payload)
                return error_response("backup handler unavailable")

            elif cmd == CMD.BACKUP_STATUS:
                h = self._get_handler("backup")
                if h:
                    return h.handle_backup_status(payload)
                return error_response("backup handler unavailable")

            elif cmd == CMD.BACKUP_CONFIG:
                h = self._get_handler("backup")
                if h:
                    return h.handle_backup_config(payload)
                return error_response("backup handler unavailable")

            # ── Scheduled maintenance tasks ────────────────────────────────
            elif cmd == CMD.SCHEDULE_TASK:
                import scheduler
                return scheduler.handle_schedule_task(payload)

            elif cmd == CMD.LIST_TASKS:
                import scheduler
                return scheduler.handle_list_tasks(payload)

            elif cmd == CMD.DELETE_TASK:
                import scheduler
                return scheduler.handle_delete_task(payload)

            elif cmd == CMD.RUN_TASK_NOW:
                import scheduler
                return scheduler.handle_run_task_now(payload)

            else:
                return error_response(f"Unknown command: {cmd!r}")

        except Exception as e:
            logger.exception(f"Handler error for cmd={cmd!r}: {e}")
            return error_response(f"Internal error: {e}")

    def handle(self) -> None:
        """Main loop: receive → authenticate → dispatch → respond."""
        logger.info(f"Console connected from {self.addr}")
        self.sock.settimeout(60.0)  # Generous timeout per command

        try:
            while self.running:
                try:
                    msg = recv_message(self.sock)
                except ConnectionError:
                    logger.info(f"Console {self.addr} disconnected")
                    break
                except Exception as e:
                    logger.warning(f"Recv error from {self.addr}: {e}")
                    break

                cmd     = msg.get("cmd", "")
                token   = msg.get("token", "")
                payload = msg.get("payload", {})

                # Authenticate
                import json as _json
                payload_json = _json.dumps(payload, ensure_ascii=False, sort_keys=True)
                if not verify_token(self.secret, payload_json, token):
                    logger.warning(f"Auth failed for cmd={cmd!r} from {self.addr}")
                    send_message(self.sock, error_response("Authentication failed"))
                    continue

                # Dispatch
                response = self._dispatch(cmd, payload)
                send_message(self.sock, response)

        finally:
            try:
                self.sock.close()
            except Exception:
                pass
            logger.info(f"Connection closed: {self.addr}")


# ── TCP Server ────────────────────────────────────────────────────────────────
class AgentServer:
    """Listens for console connections and spawns a handler thread per connection."""

    def __init__(self, config: dict, ui_bridge=None):
        self.config  = config
        self.port    = config.get("listen_port", AGENT_PORT)
        self.ui_bridge = ui_bridge
        self._server = None
        self._running = False

    def start(self) -> None:
        self._running = True
        self._server  = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("", self.port))
        self._server.listen(5)
        logger.info(f"Agent listening on port {self.port}")

        while self._running:
            try:
                self._server.settimeout(2.0)
                try:
                    conn, addr = self._server.accept()
                except socket.timeout:
                    continue
                handler = AgentHandler(conn, addr, self.config, self.ui_bridge)
                t = threading.Thread(
                    target=handler.handle,
                    daemon=True,
                    name=f"ConsoleConn-{addr}",
                )
                t.start()
            except Exception as e:
                if self._running:
                    logger.error(f"Server accept error: {e}")

    def stop(self) -> None:
        self._running = False
        if self._server:
            self._server.close()


# ── Background watchers ───────────────────────────────────────────────────────
def _make_ui_callers(ui_bridge):
    """
    Build the two callbacks the restriction watcher needs to reach the student.

    Both go through the UI bridge in service mode and run in-process in dev
    mode, so the watcher itself never has to know which mode it is in.
    """
    def _call(cmd: str, payload: dict) -> dict:
        if ui_bridge is not None:
            return ui_bridge.call(cmd, payload)
        return ui_commands.handle(cmd, payload)

    def idle_provider():
        resp = _call(ui_commands.IDLE_SECONDS, {})
        if resp.get("status") == "ok":
            return (resp.get("data") or {}).get("idle_seconds")
        return None

    def notify(title: str, message: str, timeout: int) -> None:
        _call(CMD.SEND_MSG, {"title": title, "message": message, "timeout": timeout})

    return idle_provider, notify


def _start_restriction_watcher(config: dict, ui_bridge=None) -> None:
    """Continuously enforce app blocklist and auto-logoff."""
    try:
        from handlers.restrictions import RestrictionWatcher
        idle_provider, notify = _make_ui_callers(ui_bridge)
        watcher = RestrictionWatcher(config, idle_provider=idle_provider, notify=notify)
        t = threading.Thread(target=watcher.run, daemon=True, name="RestrictionWatcher")
        t.start()
    except ImportError:
        logger.warning("restrictions handler not available — watcher not started")


def _start_ui_bridge(config: dict):
    """
    Start the session-0 → interactive-desktop bridge, if we need one.

    Returns the bridge, or None when the agent already runs on the interactive
    desktop (dev mode) and can do the work itself.
    """
    if not session.is_session0():
        logger.info("Running in an interactive session — UI commands handled in-process")
        return None

    logger.info("Running as a service in session 0 — starting UI bridge")
    try:
        from ui_bridge import UIBridge
        bridge = UIBridge(config)
        bridge.start()
        return bridge
    except Exception as e:
        logger.error(f"UI bridge failed to start: {e}")
        return None


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    config = load_config()
    logger.info(f"ClassroomOS Agent starting (port {config['listen_port']})")

    # Bridge to the student's desktop (service mode only). Started first so the
    # restriction watcher can use it for idle detection and warnings.
    ui_bridge = _start_ui_bridge(config)

    # Start background watchers
    _start_restriction_watcher(config, ui_bridge)

    # Init backup scheduler (loads saved schedule, starts daemon thread)
    try:
        from handlers.backup import init as backup_init
        backup_init(config)
    except Exception as e:
        logger.warning(f"Backup handler init failed: {e}")

    # Init the maintenance task queue (runs anything missed while powered off)
    try:
        import scheduler
        scheduler.init(config)
    except Exception as e:
        logger.warning(f"Task scheduler init failed: {e}")

    # Start TCP server (blocking)
    server = AgentServer(config, ui_bridge)
    try:
        server.start()
    except KeyboardInterrupt:
        logger.info("Agent shutting down (KeyboardInterrupt)")
    finally:
        server.stop()
        if ui_bridge:
            ui_bridge.stop()


if __name__ == "__main__":
    # A frozen build is a single .exe, so the UI helper re-executes this same
    # binary with --ui-host instead of launching a separate script.
    if "--ui-host" in sys.argv:
        import agent_ui
        sys.exit(agent_ui.main(sys.argv[1:]))
    main()
