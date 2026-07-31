"""
ClassroomOS — Maintenance Task Queue
=======================================
Runs unattended maintenance on the client: nightly cleanups, scheduled
restarts, automatic backups.

The point of this module — and the reason a bare `threading.Timer` will not do
— is that lab machines are switched off overnight. A job scheduled for 02:00
never fires if the PC is unplugged at 16:00. So the queue is persisted to disk
and every job records when it last completed; at startup anything whose next
occurrence has already passed is run immediately (see `scheduling.next_due`).

Tasks live in ``%PROGRAMDATA%\\ClassroomOS\\task_queue.json`` and survive
reboots, agent upgrades and service restarts.

Protocol
--------
  CMD.SCHEDULE_TASK  payload: {id, cmd, payload, interval, hour, minute,
                               weekday, run_at, enabled, catch_up}  → {task}
  CMD.LIST_TASKS     payload: {}                                    → {tasks}
  CMD.DELETE_TASK    payload: {id}                                  → {deleted}
  CMD.RUN_TASK_NOW   payload: {id}                                  → {result}
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from protocol import CMD, ok_response, error_response
from handler_loader import get_handler
import scheduling

logger = logging.getLogger("scheduler")

_TASK_FILE = (
    Path(os.environ.get("PROGRAMDATA", "C:\\ProgramData"))
    / "ClassroomOS" / "task_queue.json"
)

CHECK_INTERVAL = 60.0   # Seconds between due-time checks.

# Only commands that are safe to run with nobody watching. Anything that needs
# the interactive desktop (messages, lock screen) is deliberately excluded —
# there may be no session at 02:00.
ALLOWED_TASK_COMMANDS = frozenset({
    CMD.CLEANUP,
    CMD.BACKUP_NOW,
    CMD.RESTART,
    CMD.SHUTDOWN,
    CMD.LOGOFF,
    CMD.SET_USB,
    CMD.SET_INTERNET,
})

_tasks: list[dict] = []
_lock = threading.RLock()
_thread: threading.Thread | None = None
_config: dict = {}


# ── Persistence ────────────────────────────────────────────────────────────────

def _load() -> None:
    global _tasks
    try:
        if _TASK_FILE.exists():
            with open(_TASK_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                with _lock:
                    _tasks = [t for t in data if isinstance(t, dict) and t.get("id")]
                logger.info(f"Loaded {len(_tasks)} scheduled task(s)")
    except Exception as e:
        logger.warning(f"Could not load task queue: {e}")


def _save() -> None:
    try:
        _TASK_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            snapshot = list(_tasks)
        with open(_TASK_FILE, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Could not save task queue: {e}")


# ── Task helpers ───────────────────────────────────────────────────────────────

def _find(task_id: str) -> dict | None:
    with _lock:
        for t in _tasks:
            if t.get("id") == task_id:
                return t
    return None


def _due_time(task: dict) -> datetime | None:
    return scheduling.next_due(
        interval = task.get("interval", scheduling.MANUAL),
        hour     = task.get("hour", 2),
        minute   = task.get("minute", 0),
        weekday  = task.get("weekday", 0),
        last_run = scheduling.parse_time(task.get("last_run")),
        created  = scheduling.parse_time(task.get("created")),
        run_at   = scheduling.parse_time(task.get("run_at")),
    )


def _describe(task: dict) -> dict:
    """Task as reported to the console, with the computed next run time."""
    due = _due_time(task)
    out = dict(task)
    out["next_due"] = due.isoformat() if due else None
    return out


# ── Execution ──────────────────────────────────────────────────────────────────

def _execute(task: dict) -> dict:
    """
    Run one task's command locally.

    Returns a protocol response. Failures are recorded on the task rather than
    raised, so one broken task cannot stop the queue.
    """
    cmd = task.get("cmd", "")
    payload = task.get("payload") or {}

    if cmd not in ALLOWED_TASK_COMMANDS:
        return error_response(f"Command not allowed in scheduled tasks: {cmd!r}")

    try:
        if cmd == CMD.CLEANUP:
            h = get_handler("cleanup")
            if not h:
                return error_response("cleanup handler unavailable")
            return ok_response({"report": h.run_cleanup(payload.get("options", {}))})

        if cmd == CMD.BACKUP_NOW:
            h = get_handler("backup")
            if not h:
                return error_response("backup handler unavailable")
            # Scheduled backups are written to disk on this machine; the ZIP is
            # not shipped back because nobody is waiting to receive it.
            return h.run_local_backup(payload)

        if cmd in (CMD.RESTART, CMD.SHUTDOWN, CMD.LOGOFF):
            h = get_handler("power")
            if not h:
                return error_response("power handler unavailable")
            delay = int(payload.get("delay", 60))
            if cmd == CMD.RESTART:
                h.restart(delay)
            elif cmd == CMD.SHUTDOWN:
                h.shutdown(delay)
            else:
                h.logoff()
            return ok_response()

        if cmd in (CMD.SET_USB, CMD.SET_INTERNET):
            h = get_handler("restrictions")
            if not h:
                return error_response("restrictions handler unavailable")
            enabled = bool(payload.get("enabled", True))
            if cmd == CMD.SET_USB:
                ok = h.set_usb(enabled)
            else:
                ok = h.set_internet(
                    enabled,
                    console_ip=payload.get("console_ip") or _config.get("console_ip", ""),
                )
            return ok_response() if ok else error_response("Restriction change failed")

    except Exception as e:
        logger.exception(f"Task {task.get('id')!r} raised: {e}")
        return error_response(str(e))

    return error_response(f"Unhandled task command: {cmd!r}")


def _run_task(task: dict, *, missed: bool = False) -> dict:
    label = task.get("id", "?")
    logger.info(f"Running {'missed ' if missed else ''}task {label!r} ({task.get('cmd')})")

    result = _execute(task)
    ok = result.get("status") == "ok"

    with _lock:
        task["last_run"] = datetime.now().isoformat()
        task["last_result"] = "ok" if ok else (result.get("error") or "error")
        task["last_was_missed"] = missed
        if task.get("interval") == scheduling.ONCE and ok:
            task["enabled"] = False
    _save()

    if ok:
        logger.info(f"Task {label!r} completed")
    else:
        logger.warning(f"Task {label!r} failed: {task['last_result']}")
    return result


# ── Scheduler loop ─────────────────────────────────────────────────────────────

def _tick(now: datetime | None = None, *, startup: bool = False) -> None:
    """Run every task whose next occurrence has arrived or been missed."""
    now = now or datetime.now()
    with _lock:
        candidates = list(_tasks)

    for task in candidates:
        try:
            if not task.get("enabled", True):
                continue
            due = _due_time(task)
            if not scheduling.is_due(now, due):
                continue
            # A job whose slot passed while the machine was off is only replayed
            # when the teacher asked for catch-up (the default).
            missed = due is not None and (now - due).total_seconds() > CHECK_INTERVAL * 2
            if missed and not task.get("catch_up", True):
                with _lock:
                    task["last_run"] = now.isoformat()
                    task["last_result"] = "skipped (missed)"
                _save()
                logger.info(f"Task {task.get('id')!r} slot missed — skipped per config")
                continue
            _run_task(task, missed=missed and startup)
        except Exception as e:
            logger.error(f"Scheduler error on task {task.get('id')!r}: {e}")


def _loop() -> None:
    while True:
        time.sleep(CHECK_INTERVAL)
        try:
            _tick()
        except Exception as e:
            logger.error(f"Scheduler loop error: {e}")


def init(config: dict | None = None) -> None:
    """
    Load persisted tasks, replay anything missed while the machine was off, and
    start the background checker. Call once at agent startup.
    """
    global _thread, _config
    _config = config or {}
    _load()

    # Catch-up pass before the periodic loop starts.
    try:
        _tick(startup=True)
    except Exception as e:
        logger.error(f"Startup catch-up failed: {e}")

    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, daemon=True, name="TaskScheduler")
    _thread.start()
    logger.info("Task scheduler started")


# ── Protocol handlers ──────────────────────────────────────────────────────────

def handle_schedule_task(payload: dict) -> dict:
    """Create or update a scheduled task."""
    task_id = str(payload.get("id") or "").strip()
    if not task_id:
        return error_response("Task id is required")

    cmd = payload.get("cmd", "")
    if cmd not in ALLOWED_TASK_COMMANDS:
        return error_response(
            f"Command {cmd!r} cannot be scheduled. Allowed: "
            + ", ".join(sorted(ALLOWED_TASK_COMMANDS))
        )

    interval = payload.get("interval", scheduling.DAILY)
    if interval not in scheduling.VALID_INTERVALS:
        return error_response(
            f"Invalid interval {interval!r}. Use one of: "
            + ", ".join(scheduling.VALID_INTERVALS)
        )

    if interval == scheduling.ONCE and not scheduling.parse_time(payload.get("run_at")):
        return error_response("A 'once' task needs a valid ISO run_at timestamp")

    with _lock:
        existing = _find(task_id)
        task = existing if existing is not None else {
            "id": task_id,
            "created": datetime.now().isoformat(),
            "last_run": None,
            "last_result": None,
        }
        task.update({
            "cmd":      cmd,
            "payload":  payload.get("payload") or {},
            "interval": interval,
            "hour":     payload.get("hour", 2),
            "minute":   payload.get("minute", 0),
            "weekday":  payload.get("weekday", 0),
            "run_at":   payload.get("run_at"),
            "enabled":  bool(payload.get("enabled", True)),
            "catch_up": bool(payload.get("catch_up", True)),
        })
        if existing is None:
            _tasks.append(task)

    _save()
    logger.info(f"Task {task_id!r} scheduled: {cmd} / {interval}")
    return ok_response({"task": _describe(task)})


def handle_list_tasks(_payload: dict) -> dict:
    with _lock:
        tasks = [_describe(t) for t in _tasks]
    return ok_response({"tasks": tasks})


def handle_delete_task(payload: dict) -> dict:
    task_id = str(payload.get("id") or "").strip()
    with _lock:
        before = len(_tasks)
        _tasks[:] = [t for t in _tasks if t.get("id") != task_id]
        removed = before - len(_tasks)
    if removed:
        _save()
        logger.info(f"Task {task_id!r} deleted")
    return ok_response({"deleted": removed})


def handle_run_task_now(payload: dict) -> dict:
    task_id = str(payload.get("id") or "").strip()
    task = _find(task_id)
    if not task:
        return error_response(f"No such task: {task_id!r}")
    result = _run_task(task)
    return ok_response({"result": result, "task": _describe(task)})
