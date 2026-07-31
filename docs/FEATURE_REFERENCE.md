# ClassroomOS — Feature Reference

> Complete reference for every feature in the system.  
> Each entry describes what it does, how to use it from the admin console, and the underlying protocol.

---

## 1. Power Control

### 1.1 Wake-on-LAN (WOL)

**What**: Powers on machines that are fully shut down (not sleep/hibernate).

**How it works**: Sends a UDP "magic packet" (broadcast to port 9) containing the target machine's MAC address repeated 16 times. The network card sees this and boots the machine.

**Requirements**: WOL must be enabled in each machine's BIOS/UEFI settings. Use the "Test WOL" button in the sidebar to verify which machines have it configured.

**Console UI**:
- Sidebar → "🔆 Wake All (WOL)" — sends magic packets to all machines
- Top bar → "🌅 Start of Day" — same as Wake All, with a confirmation dialog

**Protocol**: This uses UDP broadcast, NOT the TCP agent protocol (the agent isn't running when the machine is off).

**Code**: `console/core/wol.py` → `wake_all(clients)`

---

### 1.2 Shutdown

**What**: Gracefully shuts down one or all machines with a configurable delay.

**Console UI**:
- Per-machine: click the ⏻ button on a machine card → immediate shutdown (30s delay)
- Sidebar → "⏻ Shut Down All" → all machines (30s delay)
- Top bar → "🌙 End of Day" → all machines (60s delay)

**Protocol**: `CMD.SHUTDOWN` → `{delay: int}` (seconds)

**Agent**: Calls `shutdown /s /t {delay}` via subprocess.

---

### 1.3 Restart

**What**: Restarts one or all machines.

**Console UI**:
- Per-machine: click 🔄 on a card
- Sidebar → "🔄 Restart All"

**Protocol**: `CMD.RESTART` → `{delay: int}`

**Agent**: Calls `shutdown /r /t {delay}`

---

### 1.4 Log Off

**What**: Logs off the currently logged-in user on all machines.

**Console UI**: Sidebar → "🚪 Log Off All"

**Protocol**: `CMD.LOGOFF` → `{}`

**Agent**: Calls `shutdown /l`

---

### 1.5 Panic Reset

**What**: Emergency action — closes every application the students have open, so
the class is back to a clean desktop immediately. Used when students are running
unauthorized software or when a lesson needs resetting.

**Console UI**: Top bar → "🚨 Panic Reset" (confirmation required)

**Protocol**: `CMD.PANIC` → `{}`

**Agent** (`power.py`): Considers only processes owned by the **interactive
student**. Applications are asked to close (`terminate()`), given a 3-second
grace period so anything with unsaved work can prompt, and only then forced.

Never touched: kernel and session infrastructure (`csrss`, `lsass`, `svchost`,
`dwm`, …), the interactive shell (`explorer`, the Start menu, the input host —
killing these leaves a black screen with no taskbar), and ClassroomOS itself
including its in-session UI helper.

> The point of "panic" is to reset the class, not to break the Windows install.
> An earlier implementation protected only 12 process names and would take down
> the print spooler, the desktop compositor and every service on the machine.

---

## 2. Screen Monitoring & Remote Control

### 2.1 Grid Thumbnails

**What**: The 4×3 dashboard grid shows live, low-resolution screenshots of every online machine.

**How it works**: A background thread (`ThumbnailLoop`) iterates through all online machines in parallel, requesting JPEG screenshots at quality=30 (tiny, ~15-30 KB each). Each screenshot is displayed on the corresponding `MachineCard` widget.

**Refresh rate**: Configurable via `thumbnail_interval_ms` in console config (default: 3000ms = 3 seconds).

**Protocol**: `CMD.SCREENSHOT` → `{quality: 30}` → `{image_b64: "..."}`

**Agent** (`screen.py`): Uses `mss` library for capture, PIL for JPEG compression.

---

### 2.2 Full Remote Control

**What**: Opens a live, full-resolution view of a single machine with mouse and keyboard forwarding.

**Console UI**: Click 🖥️ "Remote" on a machine card → opens `RemoteControlWindow`.

**How it works**:
1. A daemon thread requests `SCREENSHOT` at ~8 FPS (quality=55)
2. Each frame is decoded from base64 → PIL Image → displayed in a CTkLabel
3. Mouse events on the canvas are converted to relative coordinates (0.0–1.0) and forwarded as `MOUSE_EVENT`
4. Keyboard events are forwarded as `KEY_EVENT`

**Offline handling**: If the machine is unreachable, the window shows a clear "⚠ Machine offline" banner and retries every 5 seconds. It never crashes the main dashboard.

**Input events forwarded**:
| Event | Protocol |
|-------|----------|
| Mouse move | `MOUSE_EVENT {action:"move", x, y}` |
| Left click | `MOUSE_EVENT {action:"click", button:"left", x, y}` |
| Right click | `MOUSE_EVENT {action:"right_click", x, y}` |
| Double click | `MOUSE_EVENT {action:"double_click", x, y}` |
| Mouse down/up | `MOUSE_EVENT {action:"mousedown"/"mouseup", button:"left", x, y}` |
| Scroll | `MOUSE_EVENT {action:"scroll", x, y, scroll: ±3}` |
| Key press | `KEY_EVENT {action:"press", key:"enter"}` |
| Character type | `KEY_EVENT {action:"type", text:"a"}` |
| Hotkey | `KEY_EVENT {action:"hotkey", keys:["ctrl","alt","delete"]}` |

**Agent** (`input_replay.py`): Uses `pyautogui` to replay events. Coordinates are scaled from relative (0.0–1.0) to actual screen resolution.

---

### 2.3 Screen Broadcast

**What**: Captures the teacher's screen and sends it to all student machines, where it's displayed fullscreen.

**Console UI**: Sidebar → "📡 Broadcast Screen"

**Protocol**: `CMD.BROADCAST_FRAME` → `{image_b64: "..."}`

**Agent** (`screen.py`): Opens a fullscreen Tkinter window showing the received image.

---

## 3. Communication & Attention

### 3.1 Send Message

**What**: Shows a popup window on student machines with a title and message body.

**Console UI**:
- Per-machine: 💬 button on card → prompts for title and body
- Sidebar → "📢 Message All" → prompts for title and body, sends to everyone

**Protocol**: `CMD.SEND_MSG` → `{title, message, timeout}`

**Agent** (`messaging.py`): Creates a Tkinter popup with Hebrew RTL support, always-on-top, auto-dismiss after `timeout` seconds (if > 0).

**Important**: students **cannot** close these popups — the close button and
Alt+F4 are both disabled deliberately. A message sent with `timeout: 0` (which is
what both console buttons send) stays on screen until the teacher dismisses it
with "Dismiss Messages" below.

---

### 3.2 Dismiss Messages

**What**: Closes every teacher popup still open on the students' screens.

**Console UI**: Sidebar → "🧹 Dismiss Messages"

**Protocol**: `CMD.DISMISS_MSG` → `{}`

**Agent** (`messaging.py`): Bumps a dismiss counter that every open popup polls,
so each window closes itself on its own Tk thread rather than being destroyed
from the network thread.

---

### 3.3 Lock Screen

**What**: Covers the entire student screen with a fullscreen overlay showing a custom message. Mouse and keyboard are effectively blocked (the overlay captures all input).

**Console UI**:
- Per-machine: 🔒 button on card → locks with default Hebrew message
- Sidebar → "🔒 Lock All" → prompts for custom message (default: Hebrew "screen is locked")
- Top bar → "🔓 Unlock All" → removes all lock overlays

**Protocol**:
- Lock: `CMD.LOCK_SCREEN` → `{message: "..."}`
- Unlock: `CMD.UNLOCK_SCREEN` → `{}`

**Agent** (`lock.py`): Creates a fullscreen, topmost, borderless Tkinter window. The message is centered in a large font. The window captures mouse clicks and keyboard to prevent interaction with the desktop beneath it.

**Default message** (Hebrew, student-facing): `"המסך נעול — המתן להוראות המורה"` ("Screen is locked — wait for teacher instructions")

**Limitation**: locking requires a logged-in student. At the Windows login screen
there is no session to draw into, and the agent reports
"No user is logged in on this machine".

---

### 3.4 Stop Broadcast

**What**: Removes the fullscreen teacher-screen overlay from student machines.

**Console UI**: Sidebar → "⏹ Stop Broadcast"

**Protocol**: `CMD.STOP_BROADCAST` → `{}`

**Agent** (`screen.py`): Signals the broadcast thread, which destroys its window
from its own Tk loop. Without this the overlay is fullscreen, always-on-top and
undismissable by the student.

---

## 4. Restrictions

### 4.1 Block/Allow Internet

**What**: Blocks or unblocks internet access on student machines using Windows Firewall rules.

**Console UI**: Sidebar → "🌐 Block Internet" / "🌐 Allow Internet"

**Protocol**: `CMD.SET_INTERNET` → `{enabled: bool, console_ip: "..."}`

**Agent** (`restrictions.py`): Uses `netsh advfirewall` to add/remove outbound block rules. The `console_ip` is whitelisted so the agent can still communicate with the console.

---

### 4.2 Block/Allow USB Storage

**What**: Enables or disables USB mass storage devices via the Windows registry.

**Console UI**: Sidebar → "🔌 Block USB" / "🔌 Allow USB"

**Protocol**: `CMD.SET_USB` → `{enabled: bool}`

**Agent** (`restrictions.py`): Sets `HKLM\SYSTEM\CurrentControlSet\Services\USBSTOR\Start` to 4 (disabled) or 3 (enabled). Requires SYSTEM privileges.

---

### 4.3 Application Blocklist

**What**: Automatically kills listed applications if students launch them.

**Protocol**: `CMD.SET_BLOCKLIST` → `{blocklist: ["chrome.exe", "discord.exe"]}`

**Agent** (`restrictions.py`): A background `RestrictionWatcher` thread polls the process list every few seconds and kills any matching processes.

---

## 5. Files

### 5.1 Push File

**What**: Sends a file from the teacher's machine to all student machines.

**Console UI**: Sidebar → "⬆️ Push File to All" → file picker + destination folder prompt

**Protocol**: `CMD.PUSH_FILE` → `{filename, dest_path, data_b64}`

**Destination shortcuts**: The agent resolves `"Desktop"`, `"Documents"`, `"Downloads"`, `"HandIn"` to the correct user profile paths.

---

### 5.2 Collect Assignments

**What**: Zips a folder on each student machine and transfers it back to the teacher.

**Console UI**: Sidebar → "⬇️ Collect Assignments" → source folder prompt + destination picker + compression toggle

**Protocol**: `CMD.COLLECT_FILES` → `{folder, compress: bool, compresslevel: int}` → `{zip_b64}`

**Compression**:
- `compress=true` (default): ZIP_DEFLATED — smaller but slower
- `compress=false`: ZIP_STORED — fast transfer for already-compressed data

**Output**: Each machine's files are saved as `{machine_name}_handin.zip` in the chosen directory.

---

## 6. Maintenance

### 6.1 Cleanup

**What**: Cleans temporary files, recycle bin, downloads folder, and browser data on student machines.

**Console UI**: Sidebar → "🧹 Clean All" → prompts about downloads and browser data

**Protocol**: `CMD.CLEANUP` → `{options: {temp_files, recycle_bin, downloads, browser_chrome, browser_edge, browser_firefox}}`

**Agent** (`cleanup.py`): Deletes files from Windows temp directories, empties the recycle bin via `ctypes.windll`, clears browser cache directories.

**Whose files get cleaned**: the **student's**. Under the service the agent runs
as SYSTEM, so `%TEMP%` and `%LOCALAPPDATA%` would point at the SYSTEM profile and
the cleanup would free nothing useful. Every path is resolved against the
interactive user via `session.user_profile_dir()`. A report of `0.0 MB` on a
machine that clearly has junk is the symptom of that resolution failing.

---

### 6.2 Scheduled Maintenance Tasks

**What**: Jobs stored *on each client* that run on a schedule, with the console
closed and — with `catch_up` enabled — even if the machine was switched off when
the job was due.

**Console UI**: Sidebar → "⏰ Scheduled Tasks" → opens the task panel. Give the
task a name, pick what it runs, choose daily/weekly/manual and a time, choose
whether to apply it to all machines or only online ones, then Save. The table at
the bottom lists every task across the lab with its next run time and last result.

**Protocol**:
- `CMD.SCHEDULE_TASK` → `{id, cmd, payload, interval, hour, minute, weekday, enabled, catch_up}`
- `CMD.LIST_TASKS` → `{}` → `{tasks: [...]}`
- `CMD.DELETE_TASK` → `{id}`
- `CMD.RUN_TASK_NOW` → `{id}`

**What can be scheduled**: only commands that make sense with nobody watching —
`CLEANUP`, `BACKUP_NOW`, `RESTART`, `SHUTDOWN`, `LOGOFF`, `SET_USB`,
`SET_INTERNET`. The agent rejects anything else (`ALLOWED_TASK_COMMANDS` in
`scheduler.py`); notably the lock screen and message popups cannot be scheduled,
because at 02:00 there is no session to show them in.

**Catch-up**: tasks are anchored to their last successful run. If the next
occurrence after that is already in the past — which is what happens every time
the lab is powered off overnight — the task runs at startup. Turn off "Run the
task at next boot" to skip a missed slot instead.

**Storage**: `C:\ProgramData\ClassroomOS\task_queue.json`

---

### 6.3 Auto-Logoff on Idle

**What**: Logs a student off after a configurable period of inactivity, showing a
countdown popup first.

**Console UI**: Sidebar → "💤 Auto-Logoff" → enter minutes (0 disables).

**Protocol**: `CMD.SET_AUTOLOGOFF` → `{minutes: int}`

**Agent** (`restrictions.py` → `RestrictionWatcher`): Idle time is read **inside
the student's session** — `GetLastInputInfo` is per-session, and asking from
session 0 would report the service's own permanent idleness and log the class out
immediately. If idle time cannot be determined the logoff is skipped entirely;
leaving students logged in is the safer failure.

The warning appears `autologoff_warning_seconds` (default 30) before the logoff
and is shown as a normal student popup with an auto-dismiss timer.

---

### 6.4 Health Report

**What**: Fetches system health data from all machines and displays it in a table.

**Console UI**: Sidebar → "📊 Health Report" → opens a popup window with a table

**Data collected**: CPU %, RAM %, free disk space (GB), uptime (hours), pending Windows updates, logged-in user.

**Protocol**: `CMD.HEALTH_REPORT` → `{cpu_percent, ram, disks, uptime_hours, pending_updates, user, ...}`

---

### 6.5 WOL Verification

**What**: Checks whether each online machine has Wake-on-LAN properly configured in its network adapter.

**Console UI**: Sidebar → "🔍 Test WOL"

**How**: Sends `CMD.GET_INFO` to each machine. The agent runs a PowerShell command to check if any network adapter has WOL enabled.

**Output**: Shows ✅ (WOL enabled), ⚠️ (WOL disabled), ❓ (unknown), or 🔴 (offline) for each machine.

---

### 6.6 Audit Log

**What**: Every admin action is logged to `console/data/audit_log.csv` with timestamp, target, action name, and details.

**Console UI**: Sidebar → "📋 Audit Log" → scrollable viewer showing last 200 entries

---

## 7. Backup System

### 7.1 Backup Manager

**What**: A dedicated window for configuring and triggering backups of specific folders on student machines (e.g., game server worlds, project files).

**Console UI**: Sidebar → "📦 Backup Manager" → opens `BackupPanel`

**Features**:
- Machine selector with online/offline status
- Multi-folder list (add/remove individual source paths)
- Compression toggle: DEFLATED (smaller) vs STORED (faster for pre-compressed data)
- Schedule: Manual only / Daily at HH:00 / Weekly
- "Backup Now" — immediate one-shot backup
- "Save Schedule" — configures auto-backup on the agent
- "Check Status" — shows last backup time and size

**Protocol**:
- Backup now: `CMD.BACKUP_NOW` → `{folders, label, compress, compresslevel}` → `{zip_b64, file_count, size_bytes, timestamp}`
- Get status: `CMD.BACKUP_STATUS` → `{enabled, folders, last_backup, last_size, ...}`
- Set schedule: `CMD.BACKUP_CONFIG` → `{folders, label, interval, hour, enabled, compress, compresslevel}`

**Agent** (`backup.py`): Stores the schedule in `C:\ProgramData\ClassroomOS\backup_schedule.json`. A daemon thread checks every 60 seconds whether a scheduled backup is due.

**Console**: Downloads the ZIP and saves it as `{machine}_backup_{timestamp}.zip` in the chosen directory.

### 7.2 On-demand vs scheduled backups — where the archive goes

These behave differently, and the difference matters:

| | Trigger | Destination |
|---|---|---|
| **On demand** | "Backup Now" in the panel | Streamed to the console as base64, saved to the teacher's chosen folder |
| **Scheduled** | Auto-backup or a `BACKUP_NOW` maintenance task | Written on the **client**, to `backup_dir` (default `C:\ProgramData\ClassroomOS\Backups`) |

A scheduled backup cannot stream anywhere — it runs at 02:00 with no console
connected. It keeps the newest `keep` archives (default 7) per label and deletes
older ones so the client disk cannot fill up.

Like maintenance tasks, a scheduled backup that was missed because the machine
was switched off runs at next boot.

---

## 8. Admin Authentication

### 8.1 Password System

**First run**: The console prompts for a new password (minimum 6 characters, confirmed). The password is stored as a bcrypt hash.

**Subsequent runs**: The console shows a login screen. The entered password is verified against the stored hash.

**Code**: `console/core/auth.py` → `hash_password()`, `verify_password()`, `set_password()`, `is_first_run()`

### 8.2 Configuration

The admin password hash and shared secret are stored in `console/data/config.json`. This file is created on first run and should not be shared with students.
