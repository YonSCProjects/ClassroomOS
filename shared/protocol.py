"""
ClassroomOS — Shared Protocol Module
=====================================
Used by both the admin console and every client agent.

Protocol format:
  [4 bytes big-endian length][JSON payload bytes]

Every message is a JSON object:
  {
    "cmd":     str,   # Command identifier (see COMMANDS below)
    "token":   str,   # HMAC-SHA256 of the payload with the shared secret
    "payload": dict   # Command-specific data
  }

Every response is a JSON object:
  {
    "status": "ok" | "error",
    "data":   dict | None,
    "error":  str  | None
  }
"""

import json
import struct
import socket
import hashlib
import hmac
import logging

logger = logging.getLogger("protocol")

# ── Constants ──────────────────────────────────────────────────────────────────
AGENT_PORT      = 9000          # TCP port the agent listens on
HEADER_SIZE     = 4             # Bytes for the length prefix
MAX_MESSAGE_MB  = 50            # Maximum payload size in MB (for file transfers)
ENCODING        = "utf-8"
SOCKET_TIMEOUT  = 10.0          # Default recv timeout in seconds
LONG_TIMEOUT    = 300.0         # Timeout for commands that zip/transfer large data

# ── Command identifiers ────────────────────────────────────────────────────────
class CMD:
    # Lifecycle
    PING            = "PING"
    SHUTDOWN        = "SHUTDOWN"
    RESTART         = "RESTART"
    LOGOFF          = "LOGOFF"

    # Screen
    SCREENSHOT      = "SCREENSHOT"
    BROADCAST_FRAME = "BROADCAST_FRAME"
    STOP_BROADCAST  = "STOP_BROADCAST"

    # Remote input
    MOUSE_EVENT     = "MOUSE_EVENT"
    KEY_EVENT       = "KEY_EVENT"

    # Messaging & attention
    SEND_MSG        = "SEND_MSG"
    DISMISS_MSG     = "DISMISS_MSG"
    LOCK_SCREEN     = "LOCK_SCREEN"
    UNLOCK_SCREEN   = "UNLOCK_SCREEN"

    # Restrictions
    SET_BLOCKLIST   = "SET_BLOCKLIST"
    SET_USB         = "SET_USB"
    SET_INTERNET    = "SET_INTERNET"

    # Cleanup
    CLEANUP         = "CLEANUP"

    # Files
    PUSH_FILE       = "PUSH_FILE"
    COLLECT_FILES   = "COLLECT_FILES"

    # Health & info
    HEALTH_REPORT   = "HEALTH_REPORT"
    GET_INFO        = "GET_INFO"
    INVENTORY       = "INVENTORY"
    GET_PROCESSES   = "GET_PROCESSES"

    # Session
    PANIC           = "PANIC"
    SET_AUTOLOGOFF  = "SET_AUTOLOGOFF"

    # Config
    UPDATE_CONFIG   = "UPDATE_CONFIG"

    # Backup
    BACKUP_NOW      = "BACKUP_NOW"
    BACKUP_STATUS   = "BACKUP_STATUS"
    BACKUP_CONFIG   = "BACKUP_CONFIG"

    # Scheduled maintenance tasks
    SCHEDULE_TASK   = "SCHEDULE_TASK"
    LIST_TASKS      = "LIST_TASKS"
    DELETE_TASK     = "DELETE_TASK"
    RUN_TASK_NOW    = "RUN_TASK_NOW"


# Commands that zip, transfer or otherwise block for a long time.
# The console raises the socket timeout to LONG_TIMEOUT for these so that
# collecting or backing up a large folder does not abort mid-transfer.
LONG_COMMANDS = frozenset({
    CMD.COLLECT_FILES,
    CMD.PUSH_FILE,
    CMD.BACKUP_NOW,
    CMD.CLEANUP,
    CMD.INVENTORY,
    CMD.HEALTH_REPORT,
    CMD.RUN_TASK_NOW,
})


# ── Auth helpers ───────────────────────────────────────────────────────────────
def compute_token(secret: str, payload_json: str) -> str:
    """
    Compute an HMAC-SHA256 token from the shared secret and the payload string.
    Both console and agent use this to authenticate commands.
    """
    key = secret.encode(ENCODING)
    msg = payload_json.encode(ENCODING)
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def verify_token(secret: str, payload_json: str, token: str) -> bool:
    """Constant-time comparison to prevent timing attacks."""
    expected = compute_token(secret, payload_json)
    return hmac.compare_digest(expected, token)


# ── Low-level send / receive ───────────────────────────────────────────────────
def send_message(sock: socket.socket, message: dict) -> None:
    """
    Serialize *message* to JSON and send it with a 4-byte length prefix.
    Raises: OSError on network failure, ValueError on oversized payload.
    """
    data = json.dumps(message, ensure_ascii=False).encode(ENCODING)
    size = len(data)
    if size > MAX_MESSAGE_MB * 1024 * 1024:
        raise ValueError(f"Payload too large: {size} bytes")
    header = struct.pack(">I", size)        # big-endian uint32
    sock.sendall(header + data)


def recv_message(sock: socket.socket) -> dict:
    """
    Read exactly one message from *sock*.
    Blocks until the full message arrives or the socket errors.
    Raises: ConnectionError if the peer closed the connection.
            ValueError if the payload is oversized or invalid JSON.
    """
    # Read the 4-byte length header
    raw_header = _recv_exact(sock, HEADER_SIZE)
    if raw_header is None:
        raise ConnectionError("Connection closed by peer")
    (size,) = struct.unpack(">I", raw_header)
    if size > MAX_MESSAGE_MB * 1024 * 1024:
        raise ValueError(f"Incoming payload too large: {size} bytes")
    # Read the payload
    raw_body = _recv_exact(sock, size)
    if raw_body is None:
        raise ConnectionError("Connection closed mid-message")
    return json.loads(raw_body.decode(ENCODING))


def _recv_exact(sock: socket.socket, n: int) -> bytes | None:
    """Read exactly *n* bytes from *sock*. Returns None if connection closed."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


# ── High-level helpers (console side) ─────────────────────────────────────────
def build_command(cmd: str, payload: dict, secret: str) -> dict:
    """
    Build a signed command dict ready to send to an agent.
    """
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    token = compute_token(secret, payload_json)
    return {
        "cmd":     cmd,
        "token":   token,
        "payload": payload,
    }


def send_command(sock: socket.socket, cmd: str, payload: dict, secret: str) -> dict:
    """
    Build, sign and send a command, then wait for and return the response.
    Convenience wrapper combining build_command + send_message + recv_message.
    """
    msg = build_command(cmd, payload, secret)
    send_message(sock, msg)
    return recv_message(sock)


# ── Response builders (agent side) ────────────────────────────────────────────
def ok_response(data: dict | None = None) -> dict:
    return {"status": "ok", "data": data or {}, "error": None}


def error_response(message: str) -> dict:
    return {"status": "error", "data": None, "error": message}
