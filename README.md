# ClassroomOS

**Computer Lab Management System — Final Project (Unfinished)**

> A custom-built, fully-offline classroom management solution for Windows computer labs.  
> 1 admin console + up to 12 Windows client PCs on the same LAN.  
> No domain controller. No cloud. No internet dependency.

---

## ⚠ Current Status: FEATURE-COMPLETE, NOT YET LAB-TESTED

Every planned feature is built (Phases 1–4, 6, 7). The one remaining phase is
**Phase 5 — deploying to real lab machines**. See [UNFINISHED.md](./docs/UNFINISHED.md)
for the deployment test plan.

**Features:**
- Full admin dashboard with 4×3 machine grid
- Remote control (view + mouse/keyboard) with graceful offline handling
- Power management (shutdown, restart, logoff, WOL wake)
- Panic reset — closes the students' applications, leaving the desktop intact
- Screen lock with custom messages (Hebrew RTL for students)
- Message popups to students, dismissible from the console
- Screen broadcast, also stoppable from the console
- Internet/USB blocking and a process blocklist
- File push and assignment collection (with compression toggle)
- System health reports, software inventory and WOL verification
- Backup manager (multi-folder, scheduled, compressed/fast toggle)
- Scheduled maintenance tasks that survive reboots and catch up after downtime
- Auto-logoff on idle, with a warning countdown for the student
- Audit logging of all admin actions
- bcrypt admin authentication with first-run setup

**Known caveats:**
- **Nothing has been run on an actual lab machine yet.** The session bridge that
  makes the agent work as a Windows service is the largest untested piece —
  test it first, following the ordered plan in [UNFINISHED.md](./docs/UNFINISHED.md).
- `installer/build_agent.ps1` is written but has never been executed, so no
  compiled `.exe` has been produced or verified on a clean VM.
- The lock screen cannot appear when nobody is logged in (no session to draw
  into); the agent reports this rather than failing silently.

---

## Quick Start

### Prerequisites

- **Python 3.10+** on both the admin machine and all client PCs
- All machines on the **same LAN** (e.g., 192.168.1.0/24)
- **Administrator access** on client PCs (for service installation)
- Each client PC must have **port 9000** open in Windows Firewall

### 1. Install Dependencies

```bash
cd ClassroomOS
pip install -r requirements.txt
```

### 2. Configure Client List

Edit `console/data/clients.json` with your actual machine IPs and MAC addresses:

```json
{
  "clients": [
    { "name": "PC-01", "ip": "192.168.1.101", "mac": "AA:BB:CC:DD:EE:01" },
    { "name": "PC-02", "ip": "192.168.1.102", "mac": "AA:BB:CC:DD:EE:02" }
  ]
}
```

### 3. Start the Admin Console

```bash
cd ClassroomOS
python console/main.py
```

On first run, you'll be prompted to create an admin password. This is stored as a bcrypt hash in `console/data/config.json`.

### 4. Install the Agent on Each Client PC

From an **Administrator PowerShell** on each client machine:

```powershell
.\installer\install_agent.ps1 -ConsoleIP "192.168.1.10" -SharedSecret "<secret_from_console_config>"
```

The shared secret is in `console/data/config.json` → `shared_secret` field.

### 5. For Development (Running Agent Manually)

If you want to test without installing the Windows service:

```bash
cd ClassroomOS/agent
python agent_main.py
```

---

## Project Structure

```
ClassroomOS/
├── shared/                     # Shared between console and agent
│   └── protocol.py             # TCP wire protocol, HMAC auth, all CMD constants
│
├── agent/                      # Runs on each client PC (as Windows service)
│   ├── agent_main.py           # TCP server, command dispatcher (the core)
│   ├── agent_service.py        # pywin32 Windows Service wrapper
│   ├── agent_ui.py             # Helper process run inside the student's session
│   ├── ui_bridge.py            # Session-0 side of the bridge to that helper
│   ├── ui_commands.py          # The commands that need the interactive desktop
│   ├── session.py              # Which session is the student in, and how to reach it
│   ├── scheduler.py            # Persistent maintenance task queue
│   ├── scheduling.py           # Next-due maths shared with the backup handler
│   ├── handler_loader.py       # Lazy, cached, failure-tolerant handler imports
│   ├── config.json             # Agent config (shared_secret, port, etc.)
│   └── handlers/               # One module per feature domain
│       ├── backup.py           # Folder backup (multi-folder, compress toggle)
│       ├── cleanup.py          # Temp files, browser data, recycle bin cleanup
│       ├── files.py            # Push file + collect folder (multi-path, zip)
│       ├── health.py           # CPU/RAM/disk stats, WOL check, process list
│       ├── input_replay.py     # Remote mouse + keyboard replay via pyautogui
│       ├── lock.py             # Full-screen lock overlay (Hebrew RTL, always-on-top)
│       ├── messaging.py        # Popup message window (Hebrew RTL, auto-dismiss)
│       ├── power.py            # Shutdown, restart, logoff, panic reset
│       ├── restrictions.py     # Block apps, USB storage, internet access
│       └── screen.py           # Screenshot capture + teacher screen broadcast
│
├── console/                    # Admin GUI (runs on teacher's machine)
│   ├── main.py                 # Entry point — login flow → dashboard
│   ├── core/                   # Business logic
│   │   ├── auth.py             # bcrypt password hashing + config I/O
│   │   ├── client_manager.py   # TCP connection pool, ping loop, broadcast
│   │   └── wol.py              # Wake-on-LAN magic packet sender
│   ├── gui/                    # GUI components (CustomTkinter)
│   │   ├── dashboard.py        # Main window (topbar, sidebar, 4×3 grid)
│   │   ├── machine_card.py     # Single machine tile widget
│   │   ├── remote_view.py      # Live remote control window
│   │   ├── login.py            # Login / first-run password setup
│   │   ├── backup_panel.py     # Backup manager window
│   │   └── task_panel.py       # Scheduled maintenance tasks window
│   └── data/                   # Runtime data (gitignore these in production)
│       ├── clients.json        # Machine registry (name, IP, MAC)
│       ├── config.json         # Console config (password hash, secret)
│       └── audit_log.csv       # Admin action log
│
├── installer/
│   ├── install_agent.ps1       # One-command agent deployment script
│   └── build_agent.ps1         # PyInstaller build → standalone ClassroomOSAgent.exe
│
├── docs/                       # Documentation (see below)
│   ├── ARCHITECTURE.md         # Technical deep-dive
│   ├── SETUP_GUIDE.md          # Step-by-step setup for the teacher
│   ├── FEATURE_REFERENCE.md    # Every feature explained
│   ├── UNFINISHED.md           # What's left to do (for continuation)
│   └── CHANGELOG.md            # Development history
│
└── requirements.txt            # Python dependencies
```

---

## Architecture Overview

See [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) for the full technical deep-dive.

**In brief:**

```
┌──────────────────────┐         TCP/9000          ┌──────────────────────┐
│   Admin Console      │ ◄──── JSON + HMAC ─────► │   Agent (per PC)     │
│   (CustomTkinter)    │    Length-prefixed frames  │   (Windows Service)  │
│                      │                           │                      │
│  ┌─ Dashboard ──────┐│                           │  ┌─ Dispatcher ────┐ │
│  │  4×3 Grid Cards  ││   send_command()          │  │  PING → pong    │ │
│  │  Sidebar Actions ││ ─────────────────────────►│  │  SCREENSHOT →   │ │
│  │  Status Bar      ││                           │  │    capture+b64  │ │
│  └──────────────────┘│                           │  │  LOCK → overlay │ │
│                      │   ok_response() / error   │  │  BACKUP → zip   │ │
│  ┌─ ClientManager ──┐│ ◄─────────────────────────│  │  ... 25 cmds    │ │
│  │  Connection Pool ││                           │  └─────────────────┘ │
│  │  Ping Loop (5s)  ││                           │                      │
│  │  Status Callbacks││                           │  ┌─ Handlers ──────┐ │
│  └──────────────────┘│                           │  │  power.py       │ │
│                      │         UDP/9 (WOL)       │  │  screen.py      │ │
│  ┌─ WOL ────────────┐│ ─────────────────────────►│  │  lock.py        │ │
│  │  Magic Packet    ││    (broadcast to MAC)     │  │  backup.py      │ │
│  └──────────────────┘│                           │  │  ...            │ │
└──────────────────────┘                           └──────────────────────┘
```

---

## Dependencies

| Package | Version | Used By | Purpose |
|---------|---------|---------|---------|
| `bcrypt` | ≥4.0 | Console | Admin password hashing |
| `Pillow` | ≥10.0 | Both | Image handling (screenshots, thumbnails) |
| `psutil` | ≥5.9 | Agent | CPU/RAM/disk metrics, process listing |
| `pywin32` | ≥306 | Agent | Windows Service API, registry access |
| `pyautogui` | ≥0.9.54 | Agent | Remote mouse + keyboard input replay |
| `mss` | ≥9.0 | Both | Fast screen capture (multi-monitor) |
| `customtkinter` | ≥5.2 | Console | Modern dark-themed GUI toolkit |

---

## License

This is a school final project. No license is specified — all rights reserved by the author.

---

## Documentation

| Document | Description |
|----------|-------------|
| [ARCHITECTURE.md](./docs/ARCHITECTURE.md) | Technical deep-dive: protocol, threading, security model |
| [SETUP_GUIDE.md](./docs/SETUP_GUIDE.md) | Step-by-step deployment for the teacher |
| [FEATURE_REFERENCE.md](./docs/FEATURE_REFERENCE.md) | Every feature explained with its protocol commands |
| [UNFINISHED.md](./docs/UNFINISHED.md) | Everything that's left to do, in detail |
| [CHANGELOG.md](./docs/CHANGELOG.md) | Development history and changes |
