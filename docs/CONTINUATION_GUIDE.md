# ClassroomOS — AI Agent Continuation Guide

> **This document is written specifically for an AI coding agent** that will continue developing this project.  
> Read this FIRST before touching any code.

---

## Context

This is a **final high school project** — a classroom management system for Windows computer labs. It manages up to 12 student PCs from a single admin console over LAN.

The project is **functional but incomplete**. Phases 1-3 are done and validated. Phases 4-7 need to be completed.

The student (user) wants:
- "make sure errors are handled properly EVERYWHERE"
- "the admin dashboard itself can be in English, it just needs to have RTL support for client-facing elements"
- "I would prefer in-depth ACCURATE documentation created, tweaked, and updated as we go"

---

## Critical Architecture Rules — Do NOT Violate

1. **GUI thread safety**: ALL Tkinter widget modifications MUST happen on the main thread. Background threads MUST use `self._safe_after(0, lambda: ...)` to schedule GUI updates. Calling `widget.configure()` directly from a thread WILL crash the app on Windows.

2. **winfo_exists() guard**: Before ANY `self.after()` call, check `self.winfo_exists()`. Windows that are closed while background threads are running will cause `TclError: invalid command name`.

3. **Lazy handler loading**: Agent handlers are loaded via `importlib.import_module()` in `agent_main.py`. This is intentional — it allows the agent to start even if some optional dependencies (like `pyautogui`) are missing.

4. **Per-client locks**: The `ClientManager` uses `threading.Lock` per client (`ClientInfo._lock`). Always acquire the lock before touching a client's socket.

5. **Socket probe**: Use `select(timeout=0)` to check socket liveness, NEVER `MSG_PEEK` with `setblocking(False)` — that blocks on Windows.

6. **Language split**: Admin console = English. Student-facing payloads (lock message, popup messages) = Hebrew RTL. NEVER add Hebrew to the admin UI.

7. **Error isolation**: Every user-facing callback must be wrapped in try/except. One broken machine card or failed command must NEVER crash the entire dashboard.

---

## How to Add a New Feature

### Step 1: Protocol Command
Add the command ID to `shared/protocol.py` → `class CMD`:
```python
MY_NEW_COMMAND = "MY_NEW_COMMAND"
```

### Step 2: Agent Handler
Create or modify a handler in `agent/handlers/`. Follow the existing pattern:
```python
def handle_my_command(payload: dict) -> dict:
    try:
        # do work
        return {"status": "ok", "data": {...}}
    except Exception as e:
        return {"status": "error", "message": str(e)}
```

### Step 3: Agent Dispatcher
Add the dispatch case in `agent/agent_main.py :: AgentHandler._dispatch()`:
```python
elif cmd == CMD.MY_NEW_COMMAND:
    h = self._get_handler("my_handler_module")
    if h:
        return h.handle_my_command(payload)
    return error_response("handler unavailable")
```

### Step 4: Console UI
Call from the dashboard or a panel:
```python
def _my_action(self):
    def _do():
        resp = self.manager.send(client, CMD.MY_NEW_COMMAND, {"key": "value"})
        if resp and resp.get("status") == "ok":
            self._set_status("Success!")
    threading.Thread(target=_do, daemon=True).start()
```

### Step 5: Documentation
Update `docs/FEATURE_REFERENCE.md` and `docs/CHANGELOG.md`.

---

## Import Paths — Pay Attention

The agent and console have different import contexts:

### Agent side
```python
# sys.path includes: agent/, shared/
from protocol import CMD, send_message, recv_message  # NOT shared.protocol
from handlers.files import _zip_paths                  # relative to agent/
```

### Console side
```python
# sys.path includes: project_root/, shared/
from shared.protocol import CMD                        # full path from root
from console.core.client_manager import ClientManager  # full path from root
from console.gui.backup_panel import BackupPanel       # full path from root
```

---

## Current File Map (27 Python files, 4,415 lines)

### Core (must understand before changing anything)
| File | Lines | Purpose |
|------|-------|---------|
| `shared/protocol.py` | 152 | The wire protocol — every command goes through this |
| `agent/agent_main.py` | 388 | Agent TCP server + command dispatcher |
| `console/core/client_manager.py` | 275 | Console → Agent connection pool |
| `console/gui/dashboard.py` | 757 | Main admin UI — sidebar, grid, toolbar |

### Agent Handlers (one feature per file)
| File | Lines | Commands Handled |
|------|-------|-----------------|
| `handlers/power.py` | 83 | SHUTDOWN, RESTART, LOGOFF, PANIC |
| `handlers/screen.py` | 111 | SCREENSHOT, START_STREAM, STOP_STREAM, BROADCAST_FRAME |
| `handlers/lock.py` | 125 | LOCK_SCREEN, UNLOCK_SCREEN |
| `handlers/messaging.py` | 112 | SEND_MSG |
| `handlers/restrictions.py` | 169 | SET_BLOCKLIST, SET_USB, SET_INTERNET |
| `handlers/cleanup.py` | 181 | CLEANUP |
| `handlers/files.py` | 164 | PUSH_FILE, COLLECT_FILES |
| `handlers/health.py` | 209 | HEALTH_REPORT, GET_INFO, INVENTORY, GET_PROCESSES |
| `handlers/input_replay.py` | 112 | MOUSE_EVENT, KEY_EVENT |
| `handlers/backup.py` | 188 | BACKUP_NOW, BACKUP_STATUS, BACKUP_CONFIG |

### Console GUI
| File | Lines | Purpose |
|------|-------|---------|
| `gui/dashboard.py` | 757 | Main window with everything |
| `gui/machine_card.py` | 189 | Single machine tile in the grid |
| `gui/remote_view.py` | 312 | Live remote control window |
| `gui/login.py` | 157 | Login / first-run screen |
| `gui/backup_panel.py` | 444 | Backup management window |

---

## What's Left to Build (Priority Order)

### Priority 1: Phase 5 — Lab Testing
Deploy to actual machines. This WILL surface bugs. Be prepared to fix:
- Firewall issues
- Screenshot failures at login screen
- Timeout issues with large file transfers
- WOL reliability

### Priority 2: Phase 4 — Scheduled Tasks
Create `agent/scheduler.py` with a persistent task queue. See `docs/UNFINISHED.md` for the detailed implementation plan.

### Priority 3: Phase 7 — PyInstaller Build
Build agent to standalone .exe. Remember `--hidden-import` for every handler module.

### Priority 4: Phase 6 — Final Documentation
- Inline docstring review pass
- Screenshots of the running application
- Video demo

---

## Validation Commands

Run these after ANY change to verify nothing is broken:

```powershell
cd ClassroomOS

# 1. Syntax check all files
Get-ChildItem -Recurse -Filter "*.py" | ForEach-Object {
    python -m py_compile $_.FullName
}

# 2. Lint check (real errors only)
python -m ruff check . --select=F,E9,W,B --ignore=E501,E402,B905,W293

# 3. Smoke tests
python -c "
import sys; sys.path.insert(0,'.'); sys.path.insert(0,'shared')
from shared.protocol import CMD
from console.core.client_manager import ClientManager
from console.gui.dashboard import Dashboard
from console.gui.remote_view import RemoteControlWindow
from console.gui.backup_panel import BackupPanel
print('ALL IMPORTS OK')
"
```

---

## Known Gotchas

1. **`customtkinter` version**: The GUI uses CustomTkinter 5.2+. Older versions have different APIs for `CTkSwitch`, `CTkRadioButton`, etc.

2. **`pywin32` on non-Windows**: The agent only runs on Windows. Don't try to run it on Linux/Mac.

3. **`mss` screen capture**: Returns screenshots in BGRA format. PIL converts via `Image.frombytes("RGB", ...)`. If you see color channels swapped, that's why.

4. **Tkinter mainloop**: Only ONE Tkinter mainloop can run per process. The dashboard is the mainloop. All other windows (`RemoteControlWindow`, `BackupPanel`, etc.) are `CTkToplevel` — they don't have their own mainloop.

5. **Audit log CSV**: The audit log parser in `dashboard.py` silently skips malformed rows. This is intentional — never crash on a corrupted log file.

6. **Backup schedule stored on agent**: The auto-backup schedule lives on the agent machine at `C:\ProgramData\ClassroomOS\backup_schedule.json`, NOT in the console. The console queries it via `BACKUP_STATUS`.

7. **Thread cleanup**: All threads are daemon threads. When the main process exits, they die automatically. No explicit cleanup needed.
