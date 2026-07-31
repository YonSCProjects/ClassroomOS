"""
ClassroomOS — Client Manager
==============================
Manages TCP connections to all registered client agents.

Each client is represented by a ClientConnection object that:
  - Stores metadata (name, IP, MAC)
  - Maintains a persistent TCP socket
  - Tracks online/offline status
  - Provides thread-safe send/receive

The ClientManager:
  - Loads client list from clients.json
  - Periodically pings all clients to update online status
  - Provides send_to_one() and send_to_all() convenience methods
  - Fires callbacks on status changes (for GUI updates)
"""

import json
import os
import select
import socket
import threading
import time
import logging
from typing import Callable, Optional
from dataclasses import dataclass, field


from shared.protocol import (
    CMD, send_command, AGENT_PORT, SOCKET_TIMEOUT, LONG_TIMEOUT, LONG_COMMANDS,
)


logger = logging.getLogger("client_manager")

CLIENTS_JSON = os.path.join(os.path.dirname(__file__), "..", "data", "clients.json")
PING_INTERVAL = 5.0   # Seconds between heartbeat pings


@dataclass
class ClientInfo:
    """Metadata and connection state for a single client machine."""
    name:        str
    ip:          str
    mac:         str
    description: str = ""

    # Runtime state (not persisted)
    online:      bool          = False
    last_seen:   float         = 0.0       # Unix timestamp
    logged_user: str           = ""
    hostname:    str           = ""
    _sock:       Optional[socket.socket] = field(default=None, repr=False)
    _lock:       threading.Lock           = field(default_factory=threading.Lock, repr=False)

    def is_stale(self, timeout: float = 15.0) -> bool:
        """True if we haven't heard from this client in *timeout* seconds."""
        return (time.time() - self.last_seen) > timeout

    def mark_online(self) -> None:
        self.online    = True
        self.last_seen = time.time()

    def mark_offline(self) -> None:
        self.online = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None


class ClientManager:
    """
    Central manager for all client agent connections.

    Thread-safety: All public methods acquire per-client locks before
    touching sockets. The ping loop runs on a daemon thread.
    """

    def __init__(self, config: dict):
        self.config  = config
        self.secret  = config.get("shared_secret", "")
        self.port    = config.get("agent_port", AGENT_PORT)
        self.clients: dict[str, ClientInfo] = {}   # keyed by client name
        self._callbacks: list[Callable] = []
        self._ping_thread: Optional[threading.Thread] = None
        self._running = False

    # ── Load / Save ──────────────────────────────────────────────────────────

    def load_clients(self) -> None:
        """Load the client list from clients.json."""
        if not os.path.exists(CLIENTS_JSON):
            logger.warning("clients.json not found — starting with empty client list")
            return
        with open(CLIENTS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        for entry in data.get("clients", []):
            c = ClientInfo(
                name        = entry["name"],
                ip          = entry["ip"],
                mac         = entry.get("mac", ""),
                description = entry.get("description", ""),
            )
            self.clients[c.name] = c
        logger.info(f"Loaded {len(self.clients)} clients from {CLIENTS_JSON}")

    def save_clients(self) -> None:
        """Persist the current client list to clients.json."""
        data = {
            "clients": [
                {
                    "name":        c.name,
                    "ip":          c.ip,
                    "mac":         c.mac,
                    "description": c.description,
                }
                for c in self.clients.values()
            ]
        }
        os.makedirs(os.path.dirname(CLIENTS_JSON), exist_ok=True)
        with open(CLIENTS_JSON, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def add_client(self, name: str, ip: str, mac: str = "", description: str = "") -> ClientInfo:
        """Add a new client machine to the managed list."""
        c = ClientInfo(name=name, ip=ip, mac=mac, description=description)
        self.clients[name] = c
        self.save_clients()
        return c

    def remove_client(self, name: str) -> None:
        """Remove a client and close its connection."""
        if name in self.clients:
            self.clients[name].mark_offline()
            del self.clients[name]
            self.save_clients()

    # ── Connection helpers ────────────────────────────────────────────────────

    def _connect(self, client: ClientInfo) -> bool:
        """
        Attempt to open a TCP connection to the client agent.
        Returns True on success.
        """
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(SOCKET_TIMEOUT)
            s.connect((client.ip, self.port))
            client._sock = s
            return True
        except Exception as e:
            logger.debug(f"Cannot connect to {client.name} ({client.ip}): {e}")
            return False

    def _is_socket_alive(self, sock: socket.socket) -> bool:
        """
        Fast, non-blocking check that the socket is still connected.
        Uses select() with a 0-second timeout so it never blocks.
        If select reports the socket as readable but recv returns b"",
        the peer has closed the connection.
        """
        try:
            # select with timeout=0 → immediate, never blocks
            readable, _, exceptional = select.select([sock], [], [sock], 0)
            if exceptional:
                return False
            if readable:
                # Peek without consuming — b"" means closed
                data = sock.recv(1, socket.MSG_PEEK)
                return data != b""
            # Not readable → no incoming data, but connection is fine
            return True
        except Exception:
            return False

    def _get_or_connect(self, client: ClientInfo) -> Optional[socket.socket]:
        """
        Return an existing open socket or reconnect if the socket is dead.
        Returns None if the machine is unreachable.
        """
        with client._lock:
            if client._sock and not self._is_socket_alive(client._sock):
                try:
                    client._sock.close()
                except Exception:
                    pass
                client._sock = None

            if not client._sock:
                if not self._connect(client):
                    client.mark_offline()
                    return None

            return client._sock

    # ── Send / Receive ────────────────────────────────────────────────────────

    @staticmethod
    def _timeout_for(cmd: str) -> float:
        """
        How long to wait for a reply.

        Most commands answer in milliseconds, but zipping a term's worth of
        assignments or a Minecraft world takes minutes. Using the default
        10-second timeout for those aborted the transfer and made the machine
        look offline, so long-running commands get their own budget.
        """
        return LONG_TIMEOUT if cmd in LONG_COMMANDS else SOCKET_TIMEOUT

    def send(self, client: ClientInfo, cmd: str, payload: dict = None) -> Optional[dict]:
        """
        Send *cmd* to *client* and return the response dict.
        Returns None if the machine is offline or an error occurs.
        """
        payload = payload or {}
        sock = self._get_or_connect(client)
        if not sock:
            return None
        with client._lock:
            try:
                sock.settimeout(self._timeout_for(cmd))
                response = send_command(sock, cmd, payload, self.secret)
                client.mark_online()
                return response
            except Exception as e:
                logger.warning(f"Send failed to {client.name}: {e}")
                client.mark_offline()
                return None
            finally:
                # Restore the default so a slow command does not leave this
                # socket lingering for five minutes on the next ping.
                try:
                    sock.settimeout(SOCKET_TIMEOUT)
                except Exception:
                    pass

    def send_to_all(self, cmd: str, payload: dict = None, parallel: bool = True) -> dict[str, Optional[dict]]:
        """
        Broadcast *cmd* to every client.
        If *parallel* is True, uses threads for speed.
        Returns a dict mapping client name → response (or None on failure).
        """
        payload  = payload or {}
        results  = {}
        lock     = threading.Lock()

        def _worker(client: ClientInfo):
            resp = self.send(client, cmd, payload)
            with lock:
                results[client.name] = resp

        if parallel:
            threads = [
                threading.Thread(target=_worker, args=(c,), daemon=True)
                for c in self.clients.values()
            ]
            for t in threads:
                t.start()
            join_budget = self._timeout_for(cmd) + 5
            for t in threads:
                t.join(timeout=join_budget)
        else:
            for c in self.clients.values():
                results[c.name] = self.send(c, cmd, payload)

        return results

    def send_to_group(self, names: list[str], cmd: str, payload: dict = None) -> dict[str, Optional[dict]]:
        """Send to a specific subset of clients by name."""
        payload = payload or {}
        targets = [c for n, c in self.clients.items() if n in names]
        results = {}
        for c in targets:
            results[c.name] = self.send(c, cmd, payload)
        return results

    # ── Heartbeat ─────────────────────────────────────────────────────────────

    def _ping_loop(self) -> None:
        """Background thread: ping all clients periodically."""
        while self._running:
            for client in list(self.clients.values()):
                resp = self.send(client, CMD.PING, {})
                was_online = client.online

                if resp and resp.get("status") == "ok":
                    client.mark_online()
                    # Update cached info if provided
                    data = resp.get("data", {})
                    if data.get("user"):
                        client.logged_user = data["user"]
                    if data.get("hostname"):
                        client.hostname = data["hostname"]
                else:
                    client.mark_offline()

                # Notify GUI if status changed
                if was_online != client.online:
                    self._fire_callbacks(client)

            time.sleep(PING_INTERVAL)

    def start(self) -> None:
        """Start the background ping loop."""
        self._running = True
        self._ping_thread = threading.Thread(target=self._ping_loop, daemon=True, name="PingLoop")
        self._ping_thread.start()
        logger.info("ClientManager started")

    def stop(self) -> None:
        """Stop the ping loop and close all connections gracefully."""
        self._running = False
        for client in list(self.clients.values()):
            try:
                client.mark_offline()
            except Exception:
                pass
        logger.info("ClientManager stopped")


    # ── Callback registration ─────────────────────────────────────────────────

    def on_status_change(self, callback: Callable[[ClientInfo], None]) -> None:
        """Register a callback to be called when a client goes online/offline."""
        self._callbacks.append(callback)

    def _fire_callbacks(self, client: ClientInfo) -> None:
        for cb in self._callbacks:
            try:
                cb(client)
            except Exception as e:
                logger.error(f"Callback error: {e}")

    # ── Convenience getters ───────────────────────────────────────────────────

    @property
    def online_clients(self) -> list[ClientInfo]:
        return [c for c in self.clients.values() if c.online]

    @property
    def offline_clients(self) -> list[ClientInfo]:
        return [c for c in self.clients.values() if not c.online]

    def get_client(self, name: str) -> Optional[ClientInfo]:
        return self.clients.get(name)
