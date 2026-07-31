"""
ClassroomOS — Agent Backup Handler
=====================================
Backs up one or more folders on demand or on a schedule.

Key design decisions
--------------------
* **On-demand backup** (teacher clicks "Backup Now") returns the archive to the
  console as base64, which saves it wherever the teacher chose.
* **Scheduled backup** runs at 02:00 with nobody connected, so there is nowhere
  to send the archive — it is written to a folder on the client instead
  (`backup_dir`, default ``%PROGRAMDATA%\\ClassroomOS\\Backups``) and pruned to
  the most recent `keep` archives so the disk cannot fill up.
* compress=True (default) → ZIP_DEFLATED.  Good for text, source code, logs.
* compress=False          → ZIP_STORED.    Fast; best for already-compressed
                            data (Minecraft worlds, Unity projects, videos).
* Multiple source folders are all packed into the *same* ZIP, each under a
  top-level subdirectory matching the folder's basename, so the archive is
  self-documenting even when multiple roots share file names.

Protocol
--------
  CMD.BACKUP_NOW     payload: {folders, label, compress, compresslevel}
                     → {zip_b64, file_count, size_bytes, label, timestamp}

  CMD.BACKUP_STATUS  payload: {}
                     → {enabled, folders, label, interval, hour,
                         last_backup, last_size, compress, next_due}

  CMD.BACKUP_CONFIG  payload: {folders, label, interval, hour,
                                enabled, compress, compresslevel, backup_dir}
                     → {}
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from handlers.files import _zip_paths   # shared zip logic
from protocol import ok_response, error_response
import scheduling

logger = logging.getLogger("handler.backup")

# ── Schedule store ─────────────────────────────────────────────────────────────
_PROGRAMDATA   = Path(os.environ.get("PROGRAMDATA", "C:\\ProgramData")) / "ClassroomOS"
_SCHED_FILE    = _PROGRAMDATA / "backup_schedule.json"
_DEFAULT_DIR   = _PROGRAMDATA / "Backups"
_schedule_lock = threading.RLock()

CHECK_INTERVAL = 60.0   # Seconds between due-time checks.

_schedule: dict = {
    "enabled":       False,
    "folders":       [],          # List[str] — source folders to back up
    "label":         "backup",
    "interval":      "daily",     # "manual" | "daily" | "weekly"
    "hour":          2,
    "minute":        0,
    "weekday":       0,           # Monday=0, used by weekly
    "compress":      True,        # True = DEFLATED, False = STORED
    "compresslevel": 6,
    "backup_dir":    "",          # Empty → _DEFAULT_DIR
    "keep":          7,           # Archives to retain locally
    "created":       None,
    "last_backup":   None,
    "last_size":     0,
    "last_result":   None,
}

_scheduler_thread: threading.Thread | None = None


# ── Core zip ───────────────────────────────────────────────────────────────────

def _build_archive(folders: list[str], compress: bool, level: int) -> tuple[bytes, int]:
    """Zip *folders* into memory. Raises on unrecoverable failure."""
    return _zip_paths(folders, compress=compress, compresslevel=level)


def _normalise_folders(raw) -> list[str]:
    if isinstance(raw, str):
        raw = [raw] if raw.strip() else []
    return [f for f in (raw or []) if isinstance(f, str) and f.strip()]


def _archive_name(label: str, when: datetime) -> str:
    safe = "".join(c for c in (label or "backup") if c.isalnum() or c in "-_") or "backup"
    return f"{safe}_{when.strftime('%Y%m%d_%H%M%S')}.zip"


# ── On-demand backup (result travels back to the console) ─────────────────────

def handle_backup_now(payload: dict) -> dict:
    """
    CMD.BACKUP_NOW handler — returns the archive to the console.

    payload keys (all optional with defaults):
        folders       – list of folder paths (or single string)
        label         – human name for this backup
        compress      – bool, default True (DEFLATED)
        compresslevel – int 1-9, default 6
    """
    folders = _normalise_folders(payload.get("folders") or payload.get("folder"))
    if not folders:
        return error_response("No folders specified")

    label         = payload.get("label", "backup")
    compress      = bool(payload.get("compress", True))
    compresslevel = int(payload.get("compresslevel", 6) or 6)

    algo = "DEFLATED" if compress else "STORED (fast)"
    logger.info(f"Backup requested: {folders}  label={label!r}  algo={algo}")

    try:
        zip_bytes, file_count = _build_archive(folders, compress, compresslevel)
    except Exception as e:
        logger.error(f"Backup failed: {e}")
        return error_response(str(e))

    size = len(zip_bytes)
    ts   = datetime.now().isoformat()

    with _schedule_lock:
        _schedule["last_backup"] = ts
        _schedule["last_size"]   = size
        _schedule["last_result"] = "ok"
    _save_schedule()

    logger.info(f"Backup complete: {file_count} files, {size/1024:.1f} KB ({algo})")
    return ok_response({
        "zip_b64":    base64.b64encode(zip_bytes).decode(),
        "file_count": file_count,
        "size_bytes": size,
        "label":      label,
        "timestamp":  ts,
        "compressed": compress,
    })


# ── Scheduled backup (result stays on this machine) ───────────────────────────

def run_local_backup(payload: dict | None = None) -> dict:
    """
    Back up to a folder on this machine instead of streaming to the console.

    This is what the auto-scheduler and the maintenance task queue call: at
    02:00 there is no console connected to receive a 200 MB archive, so writing
    it locally is the only thing that actually preserves the data.
    """
    payload = payload or {}
    with _schedule_lock:
        folders  = _normalise_folders(payload.get("folders") or _schedule["folders"])
        label    = payload.get("label") or _schedule["label"]
        compress = bool(payload.get("compress", _schedule["compress"]))
        level    = int(payload.get("compresslevel", _schedule["compresslevel"]) or 6)
        dest_dir = payload.get("backup_dir") or _schedule["backup_dir"] or str(_DEFAULT_DIR)
        keep     = int(payload.get("keep", _schedule["keep"]) or 7)

    if not folders:
        return error_response("No folders configured for backup")

    try:
        zip_bytes, file_count = _build_archive(folders, compress, level)
    except Exception as e:
        logger.error(f"Local backup failed: {e}")
        _record_result(f"error: {e}")
        return error_response(str(e))

    now = datetime.now()
    try:
        out_dir = Path(dest_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / _archive_name(label, now)
        with open(out_path, "wb") as f:
            f.write(zip_bytes)
    except Exception as e:
        logger.error(f"Could not write backup archive: {e}")
        _record_result(f"error: {e}")
        return error_response(f"Could not write archive: {e}")

    _prune(out_dir, label, keep)

    size = len(zip_bytes)
    with _schedule_lock:
        _schedule["last_backup"] = now.isoformat()
        _schedule["last_size"]   = size
        _schedule["last_result"] = "ok"
    _save_schedule()

    logger.info(
        f"Local backup written: {out_path} ({file_count} files, {size/1024:.1f} KB)"
    )
    return ok_response({
        "path":       str(out_path),
        "file_count": file_count,
        "size_bytes": size,
        "label":      label,
        "timestamp":  now.isoformat(),
    })


def _prune(out_dir: Path, label: str, keep: int) -> None:
    """Delete all but the newest *keep* archives for this label."""
    if keep <= 0:
        return
    try:
        safe = "".join(c for c in (label or "backup") if c.isalnum() or c in "-_") or "backup"
        archives = sorted(
            out_dir.glob(f"{safe}_*.zip"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in archives[keep:]:
            try:
                old.unlink()
                logger.info(f"Pruned old backup: {old.name}")
            except Exception as e:
                logger.debug(f"Could not prune {old}: {e}")
    except Exception as e:
        logger.debug(f"Prune failed: {e}")


def _record_result(text: str) -> None:
    with _schedule_lock:
        _schedule["last_result"] = text
    _save_schedule()


# ── Status & configuration ─────────────────────────────────────────────────────

def handle_backup_status(_payload: dict) -> dict:
    with _schedule_lock:
        data = dict(_schedule)
    due = _next_due()
    data["next_due"] = due.isoformat() if due else None
    data["backup_dir"] = data["backup_dir"] or str(_DEFAULT_DIR)
    return ok_response(data)


def handle_backup_config(payload: dict) -> dict:
    """Update the persistent backup schedule."""
    interval = payload.get("interval")
    if interval is not None and interval not in scheduling.VALID_INTERVALS:
        return error_response(
            f"Invalid interval {interval!r}. Use one of: "
            + ", ".join(scheduling.VALID_INTERVALS)
        )

    with _schedule_lock:
        for key in ("folders", "label", "interval", "hour", "minute", "weekday",
                    "enabled", "compress", "compresslevel", "backup_dir", "keep"):
            if key in payload:
                val = payload[key]
                if key == "folders":
                    val = _normalise_folders(val)
                _schedule[key] = val
        if not _schedule.get("created"):
            _schedule["created"] = datetime.now().isoformat()
        snapshot = dict(_schedule)

    _save_schedule()
    _maybe_start_scheduler()
    logger.info(
        f"Backup config updated: interval={snapshot['interval']} "
        f"enabled={snapshot['enabled']} folders={snapshot['folders']}"
    )
    return ok_response({"schedule": snapshot})


# ── Schedule persistence ───────────────────────────────────────────────────────

def _save_schedule() -> None:
    try:
        _SCHED_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _schedule_lock:
            snapshot = dict(_schedule)
        with open(_SCHED_FILE, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Could not save backup schedule: {e}")


def _load_schedule() -> None:
    try:
        if _SCHED_FILE.exists():
            with open(_SCHED_FILE, encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                with _schedule_lock:
                    _schedule.update(saved)
                    _schedule["folders"] = _normalise_folders(_schedule.get("folders"))
                logger.info(
                    f"Backup schedule loaded: {_schedule['interval']}, "
                    f"folders={_schedule['folders']}"
                )
    except Exception as e:
        logger.warning(f"Could not load backup schedule: {e}")


# ── Auto-scheduler ─────────────────────────────────────────────────────────────

def _next_due():
    """When the next automatic backup is due, or None if auto-backup is off."""
    with _schedule_lock:
        if not _schedule.get("enabled") or not _schedule.get("folders"):
            return None
        return scheduling.next_due(
            interval = _schedule.get("interval", scheduling.DAILY),
            hour     = _schedule.get("hour", 2),
            minute   = _schedule.get("minute", 0),
            weekday  = _schedule.get("weekday", 0),
            last_run = scheduling.parse_time(_schedule.get("last_backup")),
            created  = scheduling.parse_time(_schedule.get("created")),
        )


def _scheduler_loop() -> None:
    while True:
        time.sleep(CHECK_INTERVAL)
        try:
            due = _next_due()
            if scheduling.is_due(datetime.now(), due):
                logger.info("Auto-backup due — starting")
                result = run_local_backup({})
                if result.get("status") != "ok":
                    logger.warning(f"Auto-backup failed: {result.get('error')}")
        except Exception as e:
            logger.error(f"Scheduler loop error: {e}")


def _maybe_start_scheduler() -> None:
    global _scheduler_thread
    if _scheduler_thread and _scheduler_thread.is_alive():
        return
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop, daemon=True, name="BackupScheduler"
    )
    _scheduler_thread.start()


def init(config: dict | None = None) -> None:
    """
    Call once at agent startup — loads the saved schedule, catches up on a
    backup that was missed while the machine was off, and starts the checker.
    """
    _load_schedule()

    if config:
        with _schedule_lock:
            if not _schedule.get("backup_dir") and config.get("backup_dir"):
                _schedule["backup_dir"] = config["backup_dir"]

    # Catch-up: the 02:00 slot is missed every time the lab is powered down
    # overnight, which is most nights.
    try:
        due = _next_due()
        if scheduling.is_due(datetime.now(), due):
            logger.info("Backup slot was missed while powered off — running now")
            run_local_backup({})
    except Exception as e:
        logger.warning(f"Backup catch-up failed: {e}")

    _maybe_start_scheduler()
    logger.info("Backup handler initialized")
