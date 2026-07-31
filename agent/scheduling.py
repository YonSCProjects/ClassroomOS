"""
ClassroomOS — Schedule Time Maths
====================================
Works out when a recurring job is next due.

Shared by the maintenance task queue (`scheduler.py`) and the backup handler
(`handlers/backup.py`) so both agree on what "daily at 02:00" means — including
the case the lab actually hits every morning: the machine was switched off when
the job was due, and the run has to be caught up at boot.

The anchor for the next occurrence is the last successful run, or the time the
job was created if it has never run. That makes catch-up fall out naturally:
if the next occurrence after the anchor is already in the past, the job is due
right now.
"""

from __future__ import annotations

from datetime import datetime, timedelta

# Recurrence values accepted throughout the project.
MANUAL = "manual"
ONCE   = "once"
DAILY  = "daily"
WEEKLY = "weekly"

VALID_INTERVALS = (MANUAL, ONCE, DAILY, WEEKLY)


def parse_time(value: str | None) -> datetime | None:
    """Parse an ISO timestamp, tolerating None and malformed values."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def next_due(
    interval: str,
    hour: int = 2,
    minute: int = 0,
    weekday: int = 0,
    last_run: datetime | None = None,
    created: datetime | None = None,
    run_at: datetime | None = None,
) -> datetime | None:
    """
    Next datetime at which a job should run, or None if it never should.

    Args:
        interval: one of MANUAL / ONCE / DAILY / WEEKLY.
        hour:     hour of day (0–23) for daily and weekly jobs.
        minute:   minute of hour (0–59).
        weekday:  Monday=0 … Sunday=6, used by weekly jobs.
        last_run: when the job last completed successfully.
        created:  when the job was defined; the anchor before any run happens.
        run_at:   the single fire time for a ONCE job.

    Returns None for manual jobs and for one-shot jobs that already ran.
    """
    if interval == MANUAL:
        return None

    if interval == ONCE:
        return None if last_run else run_at

    anchor = last_run or created or datetime.now()
    hour = _clamp(hour, 0, 23)
    minute = _clamp(minute, 0, 59)

    candidate = anchor.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if interval == DAILY:
        if candidate <= anchor:
            candidate += timedelta(days=1)
        return candidate

    if interval == WEEKLY:
        weekday = _clamp(weekday, 0, 6)
        # Move forward to the requested weekday, then past the anchor.
        candidate += timedelta(days=(weekday - candidate.weekday()) % 7)
        if candidate <= anchor:
            candidate += timedelta(weeks=1)
        return candidate

    return None


def is_due(now: datetime, due: datetime | None) -> bool:
    """True when *due* is set and has arrived (or was missed while powered off)."""
    return due is not None and now >= due


def _clamp(value, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return low
