# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Layout

This file sits at the repo root; all paths below are relative to it. Note the directory nesting on the original dev machine — the repo lives at `c:\ClassroomOS\ClassroomOS`, one level *below* the workspace root `c:\ClassroomOS`. Open the inner directory to get this file loaded automatically.

Remote: `https://github.com/YonSCProjects/ClassroomOS` (default branch `main`).

## What This Is

A fully-offline classroom management system for Windows computer labs: one admin console + up to 12 client PCs on a LAN, no domain controller, no cloud. Two Python components communicate over raw TCP:

| Component | Runs on | Entry point |
|-----------|---------|-------------|
| **Console** | Teacher's machine | [console/main.py](console/main.py) — CustomTkinter GUI |
| **Agent** | Each student PC | [agent/agent_main.py](agent/agent_main.py) — TCP server, or as a Windows service via [agent/agent_service.py](agent/agent_service.py) |

The console always initiates; the agent never connects outbound. Windows-only (the agent uses `winreg`, `pywin32`, `netsh`).

Project status is **feature-complete but never deployed**. Every phase except Phase 5 (lab testing) is built. Nothing here has run on a real client PC, and no PyInstaller build has been produced — treat the service-mode path as unverified. [docs/UNFINISHED.md](docs/UNFINISHED.md) has the ordered deployment test plan.

## Commands

```powershell
cd ClassroomOS
pip install -r requirements.txt

python console/main.py        # Admin console (first run prompts for admin password)
cd agent; python agent_main.py  # Agent in dev mode (instead of as a service)
```

Deploy the agent to a client PC (Administrator PowerShell — copies files, installs deps, registers the `ClassroomOSAgent` service, opens TCP 9000):

```powershell
.\installer\install_agent.ps1 -ConsoleIP "192.168.1.10" -SharedSecret "<shared_secret from console/data/config.json>"
```

### Validation

There is **no test suite**. Validation is syntax + lint + import smoke test — run all three after any change:

```powershell
cd ClassroomOS
Get-ChildItem -Recurse -Filter "*.py" | ForEach-Object { python -m py_compile $_.FullName }
python -m ruff check . --select=F,E9,W,B --ignore=E501,E402,B905,W293
python -c "import sys; sys.path.insert(0,'.'); sys.path.insert(0,'shared'); from shared.protocol import CMD; from console.core.client_manager import ClientManager; from console.gui.dashboard import Dashboard; from console.gui.remote_view import RemoteControlWindow; from console.gui.backup_panel import BackupPanel; print('ALL IMPORTS OK')"
```

Note the smoke test only covers console-side imports — agent handlers can't be imported from the console's `sys.path` context (see below).

## Architecture

### Wire protocol ([shared/protocol.py](shared/protocol.py))

`[4-byte big-endian length][UTF-8 JSON]` over TCP port 9000. Requests are `{cmd, token, payload}`; responses are `{status, data, error}` built by `ok_response()` / `error_response()`.

`token` is HMAC-SHA256 over `json.dumps(payload, ensure_ascii=False, sort_keys=True)` keyed by the shared secret. **Both sides must serialize the payload identically** — if you change the serialization on one side, auth silently fails everywhere. `MAX_MESSAGE_MB = 50` caps every frame, including base64 file/backup transfers.

### Agent

`AgentServer` accepts connections and spawns one `AgentHandler` thread each; `AgentHandler.handle()` loops recv → `verify_token()` → `_dispatch()` → send. `_dispatch()` is one long `elif` chain mapping `CMD.*` to a handler module.

Handlers in `agent/handlers/` are **lazy-loaded** via `handler_loader.get_handler()` (`importlib` + a process-wide cache, failures cached too). This is deliberate: a missing optional dependency (e.g. `pyautogui`) must not prevent the agent from starting — the affected command returns `"<name> handler unavailable"` instead. Consequences:
- A new handler module needs no registration; `get_handler("name")` finds it.
- PyInstaller cannot see them — `installer/build_agent.ps1` generates a `--hidden-import handlers.<name>` for each.

Background daemon threads started in `main()`: `RestrictionWatcher` (blocklist + idle enforcement), the backup scheduler (`handlers.backup.init`), the maintenance task queue (`scheduler.init`), and — in service mode only — the UI bridge accept + watchdog threads.

### Session 0 — the agent has two modes

This is the thing to understand before changing anything on the agent side. As a Windows service the agent runs in session 0, which has no visible desktop. Windows created there are never shown, `mss` captures a blank desktop, `pyautogui` injects into nothing, and `Path.home()`/`%TEMP%` resolve to the SYSTEM profile.

So: `agent/ui_commands.py` holds the **single implementation** of every command that needs the interactive desktop (`UI_COMMANDS` — screenshot, broadcast, input, messages, lock). `AgentHandler._dispatch_ui()` either runs it in-process (dev mode, already on the desktop) or forwards it through `ui_bridge.UIBridge` to `agent_ui.py`, a helper the watchdog launches into the student's session with `CreateProcessAsUser`. The helper connects back over loopback and authenticates with a token regenerated per launch — deliberately *not* the console's `shared_secret`, since the helper runs as the student.

Practical rules:
- **Anything that draws, captures, or injects belongs in `ui_commands.py`**, not in `_dispatch()`. Adding such a branch to `_dispatch()` produces code that works when you test it by hand and silently does nothing in the lab.
- **Never use `Path.home()`, `%TEMP%`, `%LOCALAPPDATA%` or `%APPDATA%` in a handler.** Use `session.user_profile_dir()` / `user_temp_dir()` / `user_appdata()`, which resolve against whoever is logged in at the console.
- Per-session Windows APIs have the same trap: `GetLastInputInfo` from session 0 reports the service's own permanent idleness, which is why idle detection goes through the bridge (`ui_commands.IDLE_SECONDS`).

### Console

`main.py` → first-run password setup → login → `ClientManager` → `Dashboard.mainloop()`.

`ClientManager` keeps one persistent socket per client with a per-client `threading.Lock`, probes liveness with `select(timeout=0)` before each use, auto-reconnects on failure, and runs a `PingLoop` daemon thread every 5s that fires status-change callbacks into the GUI. `send_to_all()` fans out one thread per client.

`Dashboard` owns the only Tkinter mainloop; every other window (`RemoteControlWindow`, `BackupPanel`, `HealthReportWindow`, `AuditLogWindow`) is a `CTkToplevel`.

## Invariants — do not violate

**Import paths differ between the two sides.** The agent puts `agent/` and `shared/` on `sys.path`, so it imports `from protocol import CMD`, `from handlers.files import _zip_paths`, and agent-level modules directly (`import session`, `import scheduling`). The console puts the project root and `shared/` on `sys.path`, so it imports `from shared.protocol import CMD` and `from console.core.X import Y`. Mixing these (`protocol` vs `shared.protocol` in the same process) creates two distinct module objects and was already the source of one fixed bug.

**All Tkinter widget mutation happens on the main thread.** Background threads schedule via `self._safe_after(0, lambda: ...)`, defined on `Dashboard`, `RemoteControlWindow`, `BackupPanel` and `TaskPanel`, which guards with `winfo_exists()`. A bare `self.after()` on a window the user has since closed raises `TclError: invalid command name`.

Corollary on the agent side: a Tk window owned by a background thread must close *itself* from its own loop. `messaging.dismiss_all()` bumps a counter each popup polls, and `screen.stop_broadcast()` sets an event the overlay checks — neither destroys a widget from the network thread.

**Never name a window attribute `self.config`.** Every Tkinter widget already has a `config()` method (the alias for `configure()`); assigning a dict over it turns any internal `self.config(...)` call into "dict is not callable". The app config is `self.app_config` on every window class.

**Every user-facing callback is wrapped.** `Dashboard._guarded()` (and `machine_card._safe_cb()`) catch, log, and show the exception. One failing machine or command must never take down the dashboard.

**Never block the Tk main thread on a network call.** `ClientManager.send()` is synchronous and holds a per-client lock; calling it from a widget callback freezes the console. Dashboard actions spawn a thread; remote-control input goes through a queue drained by a sender thread.

**Language split.** Admin console UI is English-only. Hebrew/RTL appears *only* in student-facing payloads — the lock-screen message, message popups, and their defaults in `lock.py` / `messaging.py`. Never add Hebrew to console UI strings.

## Adding a command

1. Add the constant to `class CMD` in [shared/protocol.py](shared/protocol.py). If it zips or transfers bulk data, add it to `LONG_COMMANDS` too or it will abort on the 10-second default timeout.
2. Implement it in the appropriate `agent/handlers/*.py`.
3. Dispatch it — **and this is the decision that matters**:
   - Needs the interactive desktop? Add it to `UI_COMMANDS` and handle it in [agent/ui_commands.py](agent/ui_commands.py).
   - Otherwise add an `elif cmd == CMD.X:` branch in `AgentHandler._dispatch()`, following the `h = self._get_handler("mod"); if h: ...; return error_response("... unavailable")` pattern.
4. Call it console-side from a background thread (`self.manager.send(client, CMD.X, {...})`), updating the GUI only via `_safe_after`.
5. Update [docs/FEATURE_REFERENCE.md](docs/FEATURE_REFERENCE.md) and [docs/CHANGELOG.md](docs/CHANGELOG.md).

Because agent and console are separate processes with separate copies of `shared/protocol.py` at deploy time, adding a command means redeploying the agent too.

## Non-obvious gotchas

- **The shared secret is derived from the admin password**: `auth.set_password()` sets `shared_secret = sha256(password)`. Changing the console password therefore invalidates every deployed agent's secret and requires re-running the installer on all clients.
- **The backup schedule and task queue live on the agent**, under `C:\ProgramData\ClassroomOS\` — not in console config. The console reads them back via `BACKUP_STATUS` / `LIST_TASKS`.
- **Scheduled backups write to the client, on-demand backups stream to the console.** At 02:00 nothing is connected to receive a ZIP. Confusing the two is how the old auto-backup ended up building archives and discarding them.
- **Schedule maths lives in `agent/scheduling.py`** and is shared by the task queue and the backup handler. `next_due()` anchors to the last successful run, which is what makes a job missed while the lab was powered off run at next boot.
- **`_zip_paths()` in `files.py` is the single zip implementation**, shared by `files.py` and `backup.py`. `compress=True` → `ZIP_DEFLATED` (text/code); `compress=False` → `ZIP_STORED` (already-compressed data such as game worlds and video). Multiple source paths are nested under their basename to avoid collisions.
- **`mss` returns BGRA**; PIL conversion order matters — swapped color channels point here.
- **The agent service runs as `LocalSystem`**, required for USB registry keys, `netsh` firewall rules, and `WTSQueryUserToken` (without which the UI helper cannot be launched).
- **Only commands in `scheduler.ALLOWED_TASK_COMMANDS` can be scheduled** — nothing that needs a logged-in student, because unattended tasks run when there is no session. `console/gui/task_panel.py::TASK_TYPES` must stay a subset of it.
- **Ctrl+Alt+Del cannot be injected.** Windows reserves the secure attention sequence; the remote-control button only reaches apps that bind the combination themselves.
- **The audit log parser skips malformed CSV rows silently** by design; a corrupt `console/data/audit_log.csv` must never crash the dashboard.
- **PowerShell scripts in `installer/` must keep their UTF-8 BOM.** They contain box-drawing characters, and Windows PowerShell 5.1 decodes a BOM-less file as ANSI — which made `install_agent.ps1` fail to parse entirely until this was fixed.
- **`.gitignore` excludes `console/data/config.json`, `audit_log.csv` and `console.log`** but deliberately not `clients.json` — the machine registry (name/IP/MAC) is meant to ship with the project.
