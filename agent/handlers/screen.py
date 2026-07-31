"""
ClassroomOS — Agent Screen Handler
=====================================
Handles: screenshot capture, broadcast display.

Screenshots are captured with `mss` (fast, low-overhead),
JPEG-compressed, and base64-encoded for transport over JSON.

Broadcast display shows a fullscreen overlay of the teacher's
(or another student's) screen, with an exit mechanism only
the console can trigger.
"""

import base64
import io
import threading
import logging
import tkinter as tk
from PIL import Image, ImageTk
import mss

import mss.tools

logger = logging.getLogger("handler.screen")

# ── Broadcast state ────────────────────────────────────────────────────────────
# Exactly one broadcast overlay may exist. Only the broadcast thread ever
# touches the Tk widgets; other threads communicate through _pending_frame and
# _stop_event, which is what keeps this safe to drive from the network thread.
_broadcast_thread: threading.Thread | None = None
_broadcast_lock = threading.Lock()
_pending_frame: str | None = None
_stop_event = threading.Event()

_POLL_MS = 50   # How often the overlay checks for a newer frame.


def capture_screenshot(quality: int = 50, monitor_index: int = 1) -> str:
    """
    Capture the primary screen and return it as a base64-encoded JPEG string.

    Args:
        quality:       JPEG quality (1–95). Lower = smaller, faster transfer.
        monitor_index: Monitor to capture (1 = primary).

    Returns:
        Base64-encoded JPEG bytes as a string.

    Raises:
        RuntimeError: when there is no desktop to capture — this happens at the
            Windows login screen and whenever this code is reached from the
            non-interactive session 0.
    """
    try:
        with mss.mss() as sct:
            monitors = sct.monitors
            if len(monitors) <= monitor_index:
                monitor_index = 0 if monitors else -1
            if monitor_index < 0:
                raise RuntimeError("No monitors available")
            monitor = monitors[monitor_index]  # monitors[0] = all monitors combined
            screenshot = sct.grab(monitor)
    except RuntimeError:
        raise
    except Exception as e:
        # mss raises a variety of low-level errors when there is no visible
        # desktop; turn them into one message the teacher can understand.
        raise RuntimeError(f"Screen capture unavailable (no active desktop?): {e}") from e

    # Convert to PIL Image, then compress
    img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def show_broadcast(image_b64: str) -> None:
    """
    Show the teacher's screen fullscreen on this machine.

    Called from the network thread. It never touches Tk directly: the frame is
    handed to the broadcast thread, which owns the window and picks it up on
    its next poll. The first call starts that thread.
    """
    global _broadcast_thread, _pending_frame

    if not image_b64:
        return

    with _broadcast_lock:
        _pending_frame = image_b64
        if _broadcast_thread and _broadcast_thread.is_alive():
            return  # Running overlay will pick the frame up.
        _stop_event.clear()
        _broadcast_thread = threading.Thread(
            target=_run_broadcast_thread, daemon=True, name="Broadcast"
        )
        _broadcast_thread.start()

    logger.info("Broadcast overlay started")


def stop_broadcast() -> None:
    """
    Close the broadcast overlay.

    Without this the student is left staring at a fullscreen always-on-top
    window with no way out, so it is wired to CMD.STOP_BROADCAST and is also
    called when the UI helper shuts down.
    """
    _stop_event.set()
    with _broadcast_lock:
        thread = _broadcast_thread
    if thread and thread.is_alive():
        thread.join(timeout=3.0)
    logger.info("Broadcast stopped")


def _run_broadcast_thread() -> None:
    """Own the overlay window and refresh it as new frames arrive."""
    global _broadcast_thread, _pending_frame

    try:
        root = tk.Tk()
        root.title("ClassroomOS Broadcast")
        root.attributes("-fullscreen", True)
        root.attributes("-topmost", True)
        root.configure(background="black")
        root.resizable(False, False)
        # The student must not be able to dismiss the teacher's screen.
        root.protocol("WM_DELETE_WINDOW", lambda: None)
        root.bind("<Alt-F4>", lambda e: "break")

        label = tk.Label(root, bg="black")
        label.pack(expand=True, fill="both")

        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        shown: str | None = None

        def _poll():
            nonlocal shown
            if _stop_event.is_set():
                root.destroy()
                return

            with _broadcast_lock:
                frame = _pending_frame

            if frame and frame is not shown:
                try:
                    img = Image.open(io.BytesIO(base64.b64decode(frame)))
                    img = img.resize((screen_w, screen_h), Image.LANCZOS)
                    photo = ImageTk.PhotoImage(img)
                    label.config(image=photo)
                    label.image = photo   # keep a reference alive
                    shown = frame
                except Exception as e:
                    logger.debug(f"Broadcast frame decode error: {e}")

            root.attributes("-topmost", True)
            root.after(_POLL_MS, _poll)

        root.after(0, _poll)
        root.mainloop()

    except Exception as e:
        logger.error(f"Broadcast display error: {e}")
    finally:
        with _broadcast_lock:
            _broadcast_thread = None
            _pending_frame = None
        _stop_event.clear()
