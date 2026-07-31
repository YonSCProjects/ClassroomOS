# ClassroomOS — Architecture

> Technical deep-dive into how ClassroomOS works internally.  
> This document is intended for developers who need to maintain, extend, or debug the system.

---

## 1. System Overview

ClassroomOS is a client-server system with two components:

| Component | Runs On | Language | Entry Point |
|-----------|---------|----------|-------------|
| **Agent** | Each client PC (student machine) | Python 3.10+ | `agent/agent_main.py` or Windows Service via `agent/agent_service.py` |
| **Console** | Admin/teacher machine | Python 3.10+ | `console/main.py` |

The console connects **outbound** to each agent over TCP. The agent listens on **port 9000**. There is no reverse connection — the console always initiates.

---

## 2. Network Protocol

### 2.1 Wire Format

All communication uses **length-prefixed JSON frames** over raw TCP:

```
┌──────────────┬──────────────────────────────────────┐
│ 4 bytes (BE) │ JSON payload (UTF-8)                 │
│ = length N   │ = N bytes                            │
└──────────────┴──────────────────────────────────────┘
```

- `HEADER_SIZE = 4` — the first 4 bytes are a big-endian unsigned int specifying the payload length
- `MAX_MESSAGE_MB = 50` — payloads over 50 MB are rejected (protects against memory bombs)
- `SOCKET_TIMEOUT = 10.0s` — default receive timeout

### 2.2 Command Envelope

Every command sent from the console is a JSON object:

```json
{
  "cmd":     "SCREENSHOT",
  "payload": {"quality": 55},
  "token":   "hmac-sha256-hex-string"
}
```

Every response from the agent is:

```json
{
  "status": "ok",
  "data":   {"image_b64": "...base64..."}
}
```

Or on error:

```json
{
  "status": "error",
  "message": "Description of what went wrong"
}
```

### 2.3 Authentication (HMAC-SHA256)

Every command includes an `token` field — an HMAC-SHA256 computed from:
- **Key**: the `shared_secret` (a random hex string generated on first console run)
- **Message**: the JSON-serialized `payload` (deterministic: `sort_keys=True`, `ensure_ascii=False`)

The agent verifies this token before processing any command. If it doesn't match, the command is rejected with `"Authentication failed"`.

**Important**: The shared secret is stored in:
- Console: `console/data/config.json` → `"shared_secret"`
- Agent: `agent/config.json` → `"shared_secret"`

These must match. The installer script copies the secret during deployment.

### 2.4 Full Command Reference

| Command | Direction | Payload | Response Data | Handler |
|---------|-----------|---------|---------------|---------|
Commands marked **[UI]** need the interactive desktop and are routed through the
session bridge when the agent runs as a service — see §3.5.

| Command | Direction | Payload | Response Data | Handler |
|---------|-----------|---------|---------------|---------|
| `PING` | Console→Agent | `{}` | `{hostname, user, time}` | inline in `agent_main.py` |
| `SHUTDOWN` | Console→Agent | `{delay: int}` | `{}` | `power.py` |
| `RESTART` | Console→Agent | `{delay: int}` | `{}` | `power.py` |
| `LOGOFF` | Console→Agent | `{}` | `{}` | `power.py` |
| `PANIC` | Console→Agent | `{}` | `{}` | `power.py` |
| `SCREENSHOT` **[UI]** | Console→Agent | `{quality: int}` | `{image_b64: str}` | `screen.py` |
| `BROADCAST_FRAME` **[UI]** | Console→Agent | `{image_b64: str}` | `{}` | `screen.py` |
| `STOP_BROADCAST` **[UI]** | Console→Agent | `{}` | `{}` | `screen.py` |
| `MOUSE_EVENT` **[UI]** | Console→Agent | `{action, x, y, ...}` | `{}` | `input_replay.py` |
| `KEY_EVENT` **[UI]** | Console→Agent | `{action, key/text}` | `{}` | `input_replay.py` |
| `SEND_MSG` **[UI]** | Console→Agent | `{title, message, timeout}` | `{}` | `messaging.py` |
| `DISMISS_MSG` **[UI]** | Console→Agent | `{}` | `{}` | `messaging.py` |
| `LOCK_SCREEN` **[UI]** | Console→Agent | `{message: str}` | `{}` | `lock.py` |
| `UNLOCK_SCREEN` **[UI]** | Console→Agent | `{}` | `{}` | `lock.py` |
| `SET_BLOCKLIST` | Console→Agent | `{blocklist: list}` | `{}` | `restrictions.py` |
| `SET_USB` | Console→Agent | `{enabled: bool}` | `{}` | `restrictions.py` |
| `SET_INTERNET` | Console→Agent | `{enabled, console_ip}` | `{}` | `restrictions.py` |
| `CLEANUP` | Console→Agent | `{options: dict}` | `{report: dict}` | `cleanup.py` |
| `PUSH_FILE` | Console→Agent | `{filename, dest_path, data_b64}` | `{path, size_bytes}` | `files.py` |
| `COLLECT_FILES` | Console→Agent | `{folder, compress, compresslevel}` | `{zip_b64, folder}` | `files.py` |
| `HEALTH_REPORT` | Console→Agent | `{}` | `{cpu_percent, ram, disks, ...}` | `health.py` |
| `GET_INFO` | Console→Agent | `{}` | `{hostname, user, wol_enabled, ...}` | `health.py` |
| `INVENTORY` | Console→Agent | `{}` | `{software: list}` | `health.py` |
| `GET_PROCESSES` | Console→Agent | `{}` | `{processes: list}` | `health.py` |
| `SET_AUTOLOGOFF` | Console→Agent | `{minutes: int}` | `{}` | inline |
| `UPDATE_CONFIG` | Console→Agent | `{updates: dict}` | `{}` | inline |
| `BACKUP_NOW` | Console→Agent | `{folders, label, compress, compresslevel}` | `{zip_b64, file_count, size_bytes, ...}` | `backup.py` |
| `BACKUP_STATUS` | Console→Agent | `{}` | `{enabled, folders, last_backup, next_due, ...}` | `backup.py` |
| `BACKUP_CONFIG` | Console→Agent | `{folders, label, interval, hour, ...}` | `{schedule}` | `backup.py` |
| `SCHEDULE_TASK` | Console→Agent | `{id, cmd, payload, interval, hour, ...}` | `{task}` | `scheduler.py` |
| `LIST_TASKS` | Console→Agent | `{}` | `{tasks: list}` | `scheduler.py` |
| `DELETE_TASK` | Console→Agent | `{id}` | `{deleted: int}` | `scheduler.py` |
| `RUN_TASK_NOW` | Console→Agent | `{id}` | `{result, task}` | `scheduler.py` |

### 2.5 Timeouts

`SOCKET_TIMEOUT` (10 s) is the default, but commands that zip or transfer bulk
data would abort mid-flight on it. `protocol.LONG_COMMANDS` lists those —
`COLLECT_FILES`, `PUSH_FILE`, `BACKUP_NOW`, `CLEANUP`, `INVENTORY`,
`HEALTH_REPORT`, `RUN_TASK_NOW` — and `ClientManager.send()` raises the socket
timeout to `LONG_TIMEOUT` (300 s) for them, restoring the default afterwards so
the next heartbeat is not left waiting five minutes on a dead machine.

---

## 3. Agent Architecture

### 3.1 Startup Flow

```
agent_main.py :: main()
  ├── load_config()           → read config.json
  ├── _start_restriction_watcher()  → daemon thread: enforce blocklist + auto-logoff
  ├── backup.init()           → daemon thread: load schedule, start auto-backup timer
  └── AgentServer.start()     → blocking: bind port 9000, accept loop
        └── per connection:
            AgentHandler(sock, addr, config)
              └── handle() loop:
                  recv_message() → verify_token() → _dispatch(cmd, payload) → send_message()
```

### 3.2 Handler Lazy-Loading

Handlers are loaded on first use via `importlib.import_module()`. This means:
- A missing handler (e.g., no `pyautogui` installed) doesn't prevent the agent from starting
- The first command to a handler has ~50ms import overhead; subsequent calls are instant
- The handler module is cached in `self._handlers` dict

### 3.3 Windows Service

`agent_service.py` wraps `agent_main.main()` as a Windows Service using `pywin32`:
- Service Name: `ClassroomOSAgent`
- Display Name: `ClassroomOS Agent`
- Start Type: Automatic
- Recovery: Restart on failure (1st, 2nd, 3rd failure all restart after 10 seconds)

The service runs as `SYSTEM` — this is required for:
- Lock screen overlays (needs desktop access)
- USB registry modification
- Firewall rule changes (internet blocking)
- Reading all user profiles

### 3.4 Threading Model

| Thread | Purpose | Daemon? |
|--------|---------|---------|
| Main | TCP accept loop | No |
| ConsoleConn-{addr} | One per console connection; handles command loop | Yes |
| RestrictionWatcher | Periodic blocklist enforcement + idle detection | Yes |
| BackupScheduler | Checks every 60s if a scheduled backup is due | Yes |
| TaskScheduler | Checks every 60s for due maintenance tasks | Yes |
| UIBridgeAccept | Accepts the in-session UI helper (service mode only) | Yes |
| UIBridgeWatchdog | Keeps one helper alive in the console session | Yes |

### 3.5 Session 0 Isolation — the two agent modes

This is the single most important thing to understand about the agent.

Since Windows Vista, services run in **session 0**, which has its own window
station and desktop that no user ever sees. That has four consequences, and the
agent gets all four wrong if it ignores them:

| Operation | What happens from session 0 if unhandled |
|-----------|------------------------------------------|
| Lock screen / message popup | Window is created on an invisible desktop |
| `mss` screen capture | Captures the blank session-0 desktop |
| `pyautogui` input replay | Injects into a session nobody is using |
| `Path.home()`, `%TEMP%`, `%LOCALAPPDATA%` | Resolve to the SYSTEM profile |

So the agent runs in one of two modes, decided at startup by
`session.is_session0()`:

```
Dev mode  (python agent_main.py)          Service mode (ClassroomOSAgent)
──────────────────────────────────        ─────────────────────────────────────
Agent is already on the interactive       Agent is in session 0.
desktop.                                  UIBridge listens on 127.0.0.1:9001.
                                                   │
_dispatch() → ui_commands.handle()                 │ watchdog launches, via
             (in-process)                          │ CreateProcessAsUser:
                                                   ▼
                                          agent_ui.py, in the student's session
                                                   │
                                          connects back, proves a per-launch
                                          token, then serves the same
                                          ui_commands.handle() calls
```

`ui_commands.py` is the **only** implementation of those commands, so the path
exercised in dev mode and the path deployed in the lab cannot drift apart.

Filesystem paths are handled separately and in both modes: `session.user_profile_dir()`,
`user_temp_dir()` and `user_appdata()` resolve against whoever is logged in at
the console, which is what `files.py` and `cleanup.py` use instead of
`Path.home()` and the `%TEMP%` family.

**Security note**: the bridge listens on loopback only, and the helper
authenticates with a token regenerated on every launch. The console's
`shared_secret` is deliberately *not* reused, because the helper runs as the
student and the student must never need read access to it.

If nobody is logged in, `UIBridge.call()` returns
`"No user is logged in on this machine"` rather than failing opaquely — which is
also the honest answer for a screenshot request at the Windows login screen.

---

## 4. Console Architecture

### 4.1 Startup Flow

```
console/main.py :: main()
  ├── load_config()
  ├── is_first_run() → LoginWindow(first_run=True) → set password
  ├── LoginWindow(first_run=False) → verify password
  ├── ClientManager(config)
  │     ├── load_clients() → read clients.json
  │     └── start() → PingLoop daemon thread (ping every 5s)
  └── Dashboard(manager, config)
        └── mainloop() (Tkinter event loop)
              ├── ThumbnailLoop → fetch screenshots from online clients
              └── GUI callbacks (sidebar, cards, remote control)
```

### 4.2 Threading Model

| Thread | Purpose | Daemon? |
|--------|---------|---------|
| Main | Tkinter event loop (GUI) | No |
| PingLoop | Pings all clients every 5 seconds | Yes |
| ThumbnailLoop | Fetches screenshots for the grid cards | Yes |
| Stream-{name} | One per open remote control window | Yes |
| CollectAll / HealthReport / etc. | One-shot background tasks | Yes |

### 4.3 GUI → Thread Safety

**Critical rule**: Tkinter widgets can only be modified from the main thread.

All background threads use one of these patterns to update the GUI:
- `self._safe_after(0, lambda: widget.configure(...))` — schedules on main thread
- `self.after(0, callback)` — only if wrapped in `try/except` with `winfo_exists()` check

The `_safe_after()` helper is defined on `Dashboard`, `RemoteControlWindow`, and `BackupPanel`:
```python
def _safe_after(self, ms: int, func) -> None:
    try:
        if self.winfo_exists():
            self.after(ms, func)
    except Exception:
        pass
```

### 4.4 ClientManager Connection Pool

- One TCP socket per client, reused across commands
- `select(timeout=0)` probe before each use to detect dead connections
- Auto-reconnect on send failure
- Thread-safe: per-client `threading.Lock` protects socket access
- `send_to_all()` uses parallel threads (one per client) for speed

### 4.5 Error Handling Strategy

Every user-facing callback is wrapped in `_guarded()`:
```python
def _guarded(self, fn):
    def _wrapper(*args, **kwargs):
        try:
            fn(*args, **kwargs)
        except Exception as e:
            logger.exception(f"Action error: {e}")
            messagebox.showerror("Error", f"Action failed:\n{e}")
    return _wrapper
```

The remote control window handles offline machines gracefully:
- Shows "⚠ Machine offline, retrying..." banner
- Exponential backoff on consecutive failures (1s → 2s → 3s → 5s max)
- Never crashes the main dashboard

---

## 5. Security Model

| Aspect | Implementation |
|--------|---------------|
| Admin password | bcrypt hash stored in `config.json` |
| Command auth | HMAC-SHA256 per command (shared secret) |
| Transport | Raw TCP (no TLS) — acceptable for isolated LAN |
| Agent access | Only processes commands from connections with valid HMAC |
| Lock screen | Always-on-top Tkinter window, but not truly bulletproof (Ctrl+Alt+Del still works on Windows) |
| USB/Internet | Uses Windows registry + netsh firewall rules — SYSTEM access required |

**Security limitations** (acceptable for a school lab):
- No TLS — traffic is visible on the LAN (but it's an isolated classroom network)
- The shared secret is stored in plaintext in both config.json files
- Lock screen can be bypassed via Ctrl+Alt+Del → Task Manager (by design — students shouldn't be completely locked out)

---

## 6. RTL / Hebrew Support

The admin console is **English-only**. Hebrew/RTL text appears only in:

| Where | Text | Why |
|-------|------|-----|
| Lock screen overlay (`lock.py`) | `"המסך נעול — המתן להוראות המורה"` | Student-facing |
| Message popup (`messaging.py`) | Title/body from admin | Student-facing |
| Default lock message in `dashboard.py` | `"המסך נעול"` | Sent as payload to client |

RTL rendering uses Tkinter's `justify="center"` and large fonts. The lock screen overlay is a fullscreen borderless window.

---

## 7. File Transfer & Compression

All file transfers use the unified `_zip_paths()` function in `files.py`:

```python
def _zip_paths(paths: list[str], compress: bool = True, compresslevel: int = 6) -> tuple[bytes, int]:
```

| Mode | ZIP Method | Best For |
|------|-----------|----------|
| `compress=True` (default) | `ZIP_DEFLATED` level 6 | Text files, source code, configs |
| `compress=False` | `ZIP_STORED` | Game worlds, videos, images (already compressed) |

Multi-folder support: multiple source paths are packed into one ZIP, each under a top-level subdirectory matching the folder's basename to prevent name collisions.

---

## 8. Configuration Files

### `agent/config.json`
```json
{
  "shared_secret": "hex-string",
  "console_ip": "192.168.1.10",
  "listen_port": 9000,
  "ui_port": 9001,
  "lock_hotkey_disabled": true,
  "blocklist": [],
  "usb_enabled": true,
  "internet_enabled": true,
  "autologoff_idle_minutes": 0,
  "autologoff_warning_seconds": 30,
  "handin_folder": "C:\\HandIn",
  "backup_dir": ""
}
```

On an installed client this file is ACL'd to SYSTEM + Administrators, because it
holds the secret that authenticates every console command and `C:\ClassroomAgent`
is otherwise readable by students.

### `console/data/config.json`
```json
{
  "password_hash": "$2b$...",
  "shared_secret": "hex-string",
  "console_ip": "192.168.1.10",
  "agent_port": 9000,
  "thumbnail_interval_ms": 3000
}
```

### `console/data/clients.json`
```json
{
  "clients": [
    {"name": "PC-01", "ip": "192.168.1.101", "mac": "AA:BB:CC:DD:EE:01", "description": ""}
  ]
}
```

### Backup Schedule (stored on agent at runtime)
Path: `C:\ProgramData\ClassroomOS\backup_schedule.json`
```json
{
  "enabled": true,
  "folders": ["C:\\MinecraftServer\\world", "C:\\Projects"],
  "label": "PC-01",
  "interval": "daily",
  "hour": 2,
  "minute": 0,
  "weekday": 0,
  "compress": true,
  "compresslevel": 6,
  "backup_dir": "",
  "keep": 7,
  "created": "2026-07-06T09:14:22.000000",
  "last_backup": "2026-07-06T02:00:00.123456",
  "last_size": 524288,
  "last_result": "ok"
}
```

A **scheduled** backup writes its archive to `backup_dir` (default
`C:\ProgramData\ClassroomOS\Backups`) and prunes to the newest `keep` copies.
Only an **on-demand** backup streams the ZIP back to the console — at 02:00
there is no console connected to receive one.

### Maintenance Task Queue (stored on agent at runtime)
Path: `C:\ProgramData\ClassroomOS\task_queue.json`
```json
[
  {
    "id": "nightly-cleanup",
    "cmd": "CLEANUP",
    "payload": {"options": {"temp_files": true, "recycle_bin": true}},
    "interval": "daily",
    "hour": 2,
    "minute": 0,
    "weekday": 0,
    "enabled": true,
    "catch_up": true,
    "created": "2026-07-30T11:02:00",
    "last_run": "2026-07-31T08:03:11",
    "last_result": "ok"
  }
]
```

### Schedule semantics (`agent/scheduling.py`)

Both the backup schedule and the task queue compute their next run the same way:
the next occurrence **strictly after** the last successful run (or after
`created`, if it has never run). Catch-up then falls out for free — if that
occurrence is already in the past, the job is due right now. This is what makes
"clean up at 02:00" survive a lab that is powered off every night; set
`catch_up: false` on a task to skip a missed slot instead of replaying it.

---

## 9. Audit Log

All admin actions are logged to `console/data/audit_log.csv`:

```csv
2026-07-04T11:45:36.123,PC-01,REMOTE_CONTROL,
2026-07-04T11:46:02.456,all,LOCK_ALL,המסך נעול
2026-07-04T11:50:00.789,all,SHUTDOWN_ALL,
```

Format: `timestamp,target,action,details`

The audit log viewer in the dashboard shows the last 200 entries in reverse chronological order.
