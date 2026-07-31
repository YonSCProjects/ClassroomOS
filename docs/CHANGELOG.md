# ClassroomOS — Changelog

> Complete development history of the ClassroomOS project.

---

## [0.4.0] — 2026-07-31: Service-Mode Correctness, Task Scheduler, Build Pipeline

The headline of this release is that the agent now works **when installed as a
Windows service**, not just when run by hand from a console window. Everything
that draws a window, captures the screen, injects input or touches the
student's profile was silently doing nothing (or the wrong thing) under the
service because of Windows session 0 isolation.

### Added

- **Session bridge** (`agent/session.py`, `agent/ui_bridge.py`, `agent/agent_ui.py`,
  `agent/ui_commands.py`): the service detects that it is in session 0 and
  launches a helper process inside the student's session via
  `CreateProcessAsUser`, then forwards lock / message / screenshot / broadcast /
  input commands to it over a loopback socket authenticated with a per-launch
  token. In dev mode nothing changes — the same `ui_commands.handle()` runs
  in-process, so the tested path is untouched.
- **Maintenance task queue** (`agent/scheduler.py`, Phase 4): persistent tasks in
  `%PROGRAMDATA%\ClassroomOS\task_queue.json`, checked every 60 s and **caught
  up at boot** when the machine was switched off at the scheduled time. New
  commands `SCHEDULE_TASK`, `LIST_TASKS`, `DELETE_TASK`, `RUN_TASK_NOW`, with a
  whitelist of commands safe to run unattended.
- **Scheduled Tasks panel** (`console/gui/task_panel.py`): create, run, delete
  and review tasks across the lab, including each machine's next run time and
  last result.
- **Auto-logoff warning** (Phase 4): a countdown popup appears before an idle
  logoff, with the delay configurable from the sidebar (`Auto-Logoff`).
- **`installer/build_agent.ps1`** (Phase 7): PyInstaller build with a
  `--hidden-import` generated for every handler module, since they are loaded
  dynamically and PyInstaller cannot see them. The same binary hosts the UI
  helper via `--ui-host`.
- **`CMD.DISMISS_MSG` / `CMD.STOP_BROADCAST`** plus sidebar buttons, and
  `agent/scheduling.py` for shared schedule maths.
- **Local scheduled backups**: `backup.run_local_backup()` writes archives to
  the client and prunes to the newest `keep` copies.

### Fixed

- **Scheduled backups produced nothing.** The auto-backup built the ZIP and
  discarded it — there is no console connected at 02:00 to receive it. It is now
  written to disk on the client.
- **`SET_INTERNET` dropped `console_ip`.** The dispatcher never passed it on, so
  the block-all-outbound firewall rule was created with no exception for the
  console. The console now also auto-detects its own LAN IP.
- **`panic_reset()` tried to kill almost every process on the machine.** The
  protected list held 12 names, so `dwm.exe`, `sihost.exe`, `spoolsv.exe`,
  every service and every other user's processes were all candidates. It now
  targets only the interactive student's applications, protects the shell and
  system infrastructure, and asks apps to close before forcing them.
- **Teacher messages could never be closed.** Popups are sent with `timeout: 0`
  and block Alt+F4, and `dismiss_all()` was never wired to a command.
- **The broadcast overlay could never be closed** — fullscreen, always-on-top,
  no dispatch for `stop_broadcast()`. It is also no longer driven from the
  network thread, which was not Tk-safe.
- **Auto-logoff would have fired continuously under the service.**
  `GetLastInputInfo` is per-session; from session 0 it reports the service's own
  permanent idleness. Idle time is now read in the student's session, and an
  unknown idle time never triggers a logoff.
- **File push and cleanup used the SYSTEM profile.** "Push to Desktop" landed in
  `C:\Windows\system32\config\systemprofile\Desktop` and cleanup reported
  "0.0 MB freed" while the student's disk stayed full.
- **Large transfers timed out.** Every command used the 10-second socket
  timeout; `COLLECT_FILES` and `BACKUP_NOW` now get `LONG_TIMEOUT` (300 s).
- **Remote control froze the console.** Every `<Motion>` event did a blocking
  TCP round-trip on the Tk main thread while holding the client lock. Input is
  now queued to a sender thread and cursor moves are rate-limited to 20/s.
- **`self.config` shadowed Tkinter's `config()` method** on `Dashboard` and
  `LoginWindow`; renamed to `app_config`.
- **`install_agent.ps1` could not run under Windows PowerShell 5.1.** The file is
  UTF-8 with box-drawing characters but had no BOM, so 5.1 decoded it as ANSI
  and the parse failed. Both installer scripts now carry a BOM.
- **`install_agent.ps1` hard-coded a Python 3.11 path** for `pywin32_postinstall`;
  it now asks the interpreter where its scripts live.
- **The shared secret was readable by students.** `config.json` is now ACL'd to
  SYSTEM + Administrators only.
- **Blocklist enforcement aborted** when `psutil` returned a `None` process name.
- **Weekly backup scheduling looped ~105,000 times** per check when no backup had
  ever run (`datetime.min` anchor).
- `RestrictionWatcher` now survives an exception in a single iteration instead
  of dying silently and stopping all enforcement.

### Changed

- Backup handlers return the standard `ok_response` / `error_response` envelope
  instead of a bespoke top-level dict.
- Handler loading moved to `agent/handler_loader.py` — one cache, shared by the
  agent and the UI helper, and failures are cached so imports are not retried
  on every command.
- Removed `CMD.START_STREAM` / `CMD.STOP_STREAM`: declared and documented but
  never dispatched or sent. Remote control is screenshot polling.
- `capture_screenshot()` raises a clear "no active desktop" error instead of a
  low-level mss failure.

---

## [0.3.0] — 2026-07-06: Backup System + Compression

### Added
- **Backup Handler** (`agent/handlers/backup.py`): Multi-folder backup with compress toggle (ZIP_DEFLATED vs ZIP_STORED), auto-scheduler daemon thread, persistent schedule in `C:\ProgramData\ClassroomOS\backup_schedule.json`
- **Backup Panel** (`console/gui/backup_panel.py`): Dedicated GUI window with machine picker, multi-folder list, compression toggle with contextual hint, schedule picker (manual/daily/weekly), save directory chooser, backup-now + save-schedule + check-status actions
- **Protocol commands**: `BACKUP_NOW`, `BACKUP_STATUS`, `BACKUP_CONFIG` added to `shared/protocol.py`
- **Compression toggle in Collect Assignments**: The "Collect Assignments" dialog now asks whether to compress (DEFLATED) or use fast mode (STORED) before transferring
- **Unified `_zip_paths()` function** in `files.py`: Shared by both `files.py` and `backup.py` to eliminate duplicate zip logic. Accepts a list of paths, supports both compression methods
- **Multi-folder `collect_folder()`**: Now accepts `str | list[str]` for collecting from multiple directories in one ZIP
- **Backup dispatch in agent**: `agent_main.py` now dispatches all three backup commands, calls `backup.init()` at startup, and passes `compress`/`compresslevel` through `COLLECT_FILES`

### Changed
- `agent/handlers/files.py`: Complete rewrite — unified zip logic, multi-path support, Desktop/Documents/Downloads shortcuts, removed unused `ctypes` import
- `console/gui/dashboard.py`: Added "📦 Backups" section to sidebar with "Backup Manager" button, added compression dialog to `_collect_all()`
- `agent/agent_main.py`: Added backup handler dispatch cases, backup.init() in startup

---

## [0.2.0] — 2026-07-04: Dashboard Stabilization + English UI

### Fixed
- **Critical crash**: Remote control window crashed when opening on an offline machine (no socket → unhandled exception). Now shows "Machine offline, retrying..." banner with exponential backoff
- **TclError crashes**: Background threads calling `self.after()` on destroyed windows caused `TclError: invalid command name`. Added `winfo_exists()` guards to every `after()` call across all GUI files
- **Socket liveness check**: Replaced `MSG_PEEK` probe in `client_manager.py` with `select(timeout=0)` — the old approach blocked on Windows when the remote end closed
- **Import conflicts**: Fixed mixed `from protocol import` vs `from shared.protocol import` causing module identity issues
- **Unused imports**: Cleaned up 11 instances across `power.py`, `lock.py`, `restrictions.py`, `screen.py`, `cleanup.py`, `agent_service.py`, `wol.py`
- **WOL handler**: `wake_all()` was calling `.get()` on `ClientInfo` dataclasses instead of attribute access
- **Stop safety**: `ClientManager.stop()` now wraps each `mark_offline()` in try/except and iterates over a copy of the client dict

### Changed
- **Dashboard** (`dashboard.py`): Complete rewrite — all Hebrew UI text changed to English, added `_safe_after()` and `_guarded()` wrappers for thread-safe error-resilient callbacks, audit logging with CSV export
- **Remote Control** (`remote_view.py`): Complete rewrite — connection retry with exponential backoff, FPS counter, window lifecycle management, all `after()` calls guarded
- **Login** (`login.py`): Complete rewrite — English UI, cleaner first-run vs login mode distinction
- **Machine Card** (`machine_card.py`): Complete rewrite — English UI, `_safe_cb()` wraps all button callbacks, thumbnail update guarded with `winfo_exists()`

### Design Decisions
- Admin console is 100% English
- Hebrew/RTL text only in student-facing payloads (lock screen message, popup messages)
- Lock screen default message remains Hebrew: `"המסך נעול — המתן להוראות המורה"`

---

## [0.1.0] — 2026-07-03: Initial Build (Core Skeleton)

### Added

#### Shared
- `shared/protocol.py`: TCP wire protocol with 4-byte length prefix, JSON payloads, HMAC-SHA256 authentication, all command constants (22 commands initially)

#### Agent (runs on each student PC)
- `agent/agent_main.py`: TCP server on port 9000, HMAC verification, lazy-loading command dispatcher
- `agent/agent_service.py`: Windows Service wrapper using pywin32
- `agent/config.json`: Default agent configuration template
- `agent/handlers/power.py`: Shutdown, restart, logoff, panic reset via subprocess
- `agent/handlers/screen.py`: Screenshot capture (mss + PIL), teacher screen broadcast display
- `agent/handlers/lock.py`: Fullscreen lock overlay (Hebrew RTL, always-on-top, input-capturing)
- `agent/handlers/messaging.py`: Message popup window (Hebrew RTL, auto-dismiss timer)
- `agent/handlers/restrictions.py`: App blocklist enforcement, USB storage toggle (registry), internet blocking (netsh firewall)
- `agent/handlers/cleanup.py`: Temp files, recycle bin, downloads, browser cache cleanup
- `agent/handlers/files.py`: Push file + collect folder (with zip)
- `agent/handlers/health.py`: CPU/RAM/disk metrics (psutil), software inventory, process list, WOL check (PowerShell)
- `agent/handlers/input_replay.py`: Mouse + keyboard replay via pyautogui

#### Console (admin GUI)
- `console/main.py`: Application entry point — config loading, login flow, dashboard launch
- `console/core/auth.py`: bcrypt password hashing, config file I/O, first-run detection
- `console/core/client_manager.py`: TCP connection pool with per-client locks, 5-second heartbeat ping loop, status change callbacks, parallel `send_to_all()`
- `console/core/wol.py`: Wake-on-LAN magic packet generator (UDP broadcast to port 9)
- `console/gui/login.py`: Login window with first-run password setup mode
- `console/gui/dashboard.py`: Main admin window — top bar (start/end of day, unlock, panic), sidebar (all actions), 4×3 machine grid, status bar, thumbnail auto-refresh
- `console/gui/machine_card.py`: Per-machine tile showing name, IP, status dot, thumbnail, action buttons
- `console/gui/remote_view.py`: Full remote control window — live screen + mouse/keyboard forwarding

#### Deployment
- `installer/install_agent.ps1`: PowerShell installer — copies files, installs deps, registers service, opens firewall, writes config
- `requirements.txt`: Annotated with which packages are needed on console vs agent vs both

#### Data (templates)
- `console/data/clients.json`: Pre-populated with 12 machines (PC-01 through PC-12, 192.168.1.101-112)

---

## File Inventory (Current)

| File | Lines | Description |
|------|-------|-------------|
| `shared/protocol.py` | 152 | Wire protocol, HMAC auth, 25 CMD constants |
| `agent/agent_main.py` | 388 | TCP server, command dispatcher |
| `agent/agent_service.py` | 82 | Windows Service wrapper |
| `agent/handlers/backup.py` | 188 | Multi-folder backup with scheduler |
| `agent/handlers/cleanup.py` | 181 | Temp/browser/recycle bin cleanup |
| `agent/handlers/files.py` | 164 | Push file, collect folder, _zip_paths() |
| `agent/handlers/health.py` | 209 | System metrics, WOL check, processes |
| `agent/handlers/input_replay.py` | 112 | Mouse + keyboard replay |
| `agent/handlers/lock.py` | 125 | Fullscreen lock overlay |
| `agent/handlers/messaging.py` | 112 | Message popup |
| `agent/handlers/power.py` | 83 | Shutdown, restart, logoff, panic |
| `agent/handlers/restrictions.py` | 169 | Blocklist, USB, internet control |
| `agent/handlers/screen.py` | 111 | Screenshot capture, broadcast |
| `console/main.py` | 70 | Entry point |
| `console/core/auth.py` | 57 | bcrypt auth, config I/O |
| `console/core/client_manager.py` | 275 | Connection pool, ping loop |
| `console/core/wol.py` | 78 | Wake-on-LAN |
| `console/gui/dashboard.py` | 757 | Main admin window |
| `console/gui/machine_card.py` | 189 | Machine tile widget |
| `console/gui/remote_view.py` | 312 | Remote control window |
| `console/gui/login.py` | 157 | Login screen |
| `console/gui/backup_panel.py` | 444 | Backup manager window |
| **TOTAL** | **4,415** | **27 Python files** |
