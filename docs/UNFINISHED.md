# ClassroomOS — What's Left (Unfinished Work)

> This document is written for **an AI agent or developer who will continue this project**.
> It records what is done, what is genuinely left, and — importantly — which
> parts have never been run on real hardware.

---

## Overview of Completion Status

| Phase | Status | Description |
|-------|--------|-------------|
| Phase 1 — Core Skeleton | ✅ COMPLETE | TCP protocol, agent handlers, console GUI |
| Phase 2 — WOL Check | ✅ COMPLETE | WOL verification via PowerShell in `health.py`, sidebar button |
| Phase 2 — Veyon Integration | ⏸ DEFERRED | Not needed — our own screenshot system works well enough |
| Phase 3 — Backup Panel | ✅ COMPLETE | Multi-folder backup, compress toggle, scheduled, full GUI |
| Phase 4 — Scheduled Tasks | ✅ COMPLETE | Persistent queue, boot catch-up, auto-logoff warning, console panel |
| Phase 5 — Lab Testing | ❌ NOT STARTED | **Nothing here has run on a real lab machine** |
| Phase 6 — Documentation | ✅ COMPLETE | README, architecture, feature ref, setup guide, this file |
| Phase 7 — Polish + Build | 🟡 MOSTLY DONE | `build_agent.ps1` written; never executed on a clean VM |

---

## Phase 5: Lab Testing — THE REMAINING PRIORITY

Everything below has been written, linted, import-tested and unit-tested where
possible, but **the project has never been deployed to a client PC**. That is now
the only thing standing between this and a working system.

### 5.1 What must be tested first: service mode

The v0.4.0 work made the agent function as a Windows service rather than only in
dev mode (see `docs/ARCHITECTURE.md` §3.5). The session bridge is the largest
piece of untested code in the project, because it needs a real service, a real
logged-in user and real `CreateProcessAsUser` privileges — none of which can be
exercised from a development checkout.

Test in this order on **one** machine before touching the other eleven:

1. Install with `install_agent.ps1`; confirm the service reaches *Running*.
2. Check `C:\ClassroomAgent\agent.log` for
   `Running as a service in session 0 — starting UI bridge` followed by
   `UI helper launched into session N`.
3. Check `%TEMP%\classroomos_ui.log` **in the student's profile** for
   `Connected to agent on port 9001`.
4. From the console: thumbnails should show the student's desktop, not black.
5. Lock / unlock, send a message, dismiss it, broadcast, stop broadcast.
6. Remote control: move the mouse, type, verify input lands.
7. Log the student off and back on — the watchdog should relaunch the helper
   into the new session within ~15 s.

If the helper never connects, the likely causes in order are: pywin32 missing on
the client, `WTSQueryUserToken` denied (the service must run as LocalSystem),
or an antivirus blocking `CreateProcessAsUser`.

### 5.2 Then test everything else end-to-end

- Power: shutdown, restart, logoff, and **panic reset** — verify the desktop and
  taskbar survive it, which is the specific thing the old implementation broke
- Restrictions: internet block/unblock (confirm the console stays reachable —
  that is what the `console_ip` exception is for), USB block/unblock, blocklist
- Files: push a file to "Desktop" and confirm it lands in the *student's*
  Desktop, then collect it back and verify integrity
- Cleanup: confirm it reports a non-zero freed size (a zero means it is still
  cleaning the SYSTEM profile)
- Backup: on-demand to the console, then a scheduled one — power the machine off
  over the scheduled time and confirm it catches up at boot
- Scheduled tasks: same catch-up test via the Scheduled Tasks panel
- Auto-logoff: set 2 minutes, confirm the warning popup appears before logoff
- WOL: physically power off, wake from the console
- Concurrency: all 12 machines online, thumbnail loop + `send_to_all` under load
- Failure cases: unplug the network mid-transfer, stop the agent mid-backup

### Known issues that might still surface

- **Lock screen at the login screen**: with nobody logged in there is no session
  to draw into. The agent now reports "No user is logged in on this machine"
  instead of failing silently, but the screen still cannot be locked.
- **Antivirus and the one-file build**: PyInstaller one-file executables are
  frequently quarantined. `build_agent.ps1 -OneDir` exists for this.
- **Ctrl+Alt+Del** cannot be injected — Windows reserves the secure attention
  sequence. The button only helps with apps that bind the combination.

---

## Phase 7: Build — written, not yet proven

`installer/build_agent.ps1` exists and generates a `--hidden-import` for every
module in `agent/handlers/` (they are loaded via `importlib` and PyInstaller
cannot see them otherwise). It has **not been run**.

Remaining:

1. Run `.\build_agent.ps1 -Clean` and confirm it produces
   `installer\dist\ClassroomOSAgent.exe`.
2. Copy just that .exe + `config.json` to a clean Windows 10/11 VM with **no
   Python installed** and run it in the foreground.
3. Verify every handler loads — a missing `--hidden-import` shows up as
   `"<name> handler unavailable"` in the log rather than a crash.
4. Verify `--ui-host` self-re-execution works in the frozen build: the bridge
   launches `sys.executable --ui-host ...`, which only works because
   `agent_main.py` checks for that flag at `__main__`.
5. Install as a service with `install_agent.ps1 -UseExe`.

---

## Optional polish (nice-to-have, not blocking)

- Keyboard shortcuts on the dashboard (Ctrl+L lock all, Ctrl+W wake all)
- Save and restore the dashboard window size/position
- Screenshots and a short demo video for the docs
- Per-machine group selection (the protocol supports `send_to_group`, the GUI
  does not expose it)

---

## Key Technical Context for Continuation

### Codebase conventions

1. **All GUI updates from threads must use `_safe_after()`** — never call
   `widget.configure()` from a background thread directly.
2. **Never name a window attribute `self.config`** — every Tkinter widget already
   has a `config()` method. Use `self.app_config`.
3. **Every action callback is wrapped in try/except** — one broken machine must
   never crash the dashboard.
4. **Hebrew text only in student-facing payloads** — the admin console is
   English-only.
5. **Handlers are lazy-loaded** through `agent/handler_loader.py` so a missing
   optional dependency disables one feature rather than the whole agent.
6. **`shared/protocol.py` is used by BOTH sides** — always update both when
   adding a command, and remember the agent is deployed separately, so a new
   command means redeploying the agent too.
7. **Anything touching the desktop belongs in `ui_commands.py`**, not in
   `_dispatch()` — that is what makes it work under the service.

### Import paths

- **Agent side**: `sys.path` includes `agent/` and `shared/`. Import protocol as
  `from protocol import CMD`, handlers as `from handlers.X import Y`, and
  agent-level modules directly (`import session`, `import scheduling`).
- **Console side**: `sys.path` includes the project root and `shared/`. Import as
  `from shared.protocol import CMD`, `from console.core.X import Y`.

### Adding a new command

1. Add the constant to `shared/protocol.py` → `class CMD`.
2. If it is slow, add it to `LONG_COMMANDS` in the same file.
3. Implement it in the appropriate `agent/handlers/X.py`.
4. Dispatch it:
   - needs the interactive desktop → add it to `UI_COMMANDS` and handle it in
     `agent/ui_commands.py`;
   - otherwise → add an `elif` branch in `AgentHandler._dispatch()`.
5. Call it console-side from a background thread, updating the GUI via
   `_safe_after`.
6. Document it in `docs/FEATURE_REFERENCE.md` and `docs/CHANGELOG.md`.

### Test strategy

There is no automated test suite. Validation is:

```powershell
cd ClassroomOS
Get-ChildItem -Recurse -Filter "*.py" | ForEach-Object { python -m py_compile $_.FullName }
python -m ruff check . --select=F,E9,W,B --ignore=E501,E402,B905,W293
```

plus import smoke tests of both sides. Note that the agent smoke test will
report `input_replay` as unavailable on a machine without `pyautogui` — that is
the lazy-loading policy working, not a failure.

### File sizes and performance

- Thumbnail quality: 30 (JPEG) — ~15-30 KB per frame
- Remote control quality: 55 — ~50-80 KB per frame; cursor moves capped at 20/s
- Broadcast quality: 40 — ~30-50 KB per frame
- Backup compression: ZIP_DEFLATED level 6 (or ZIP_STORED for fast mode)
- Max payload: 50 MB (`protocol.MAX_MESSAGE_MB`)
- Long-command timeout: 300 s (`protocol.LONG_TIMEOUT`)
