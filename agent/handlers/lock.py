"""
ClassroomOS — Agent Lock Handler
===================================
Creates a fullscreen, always-on-top overlay that blocks student interaction.

Features:
  - Displays a custom message (Hebrew/RTL supported natively by Windows)
  - Intercepts Escape, Alt+F4, Ctrl+Alt+Delete attempts where possible
  - Cannot be minimized or resized
  - Only the console can unlock it (sends UNLOCK_SCREEN command)
"""

import threading
import tkinter as tk
from tkinter import font as tkfont
import logging

logger = logging.getLogger("handler.lock")

# Global lock window state
_lock_window: tk.Tk | None = None
_lock_thread: threading.Thread | None = None
_lock_active = threading.Event()


def lock(message: str = "המסך נעול") -> None:
    """
    Show the lock screen overlay with *message*.
    Safe to call even if already locked (updates message).
    """
    global _lock_window, _lock_thread

    if _lock_active.is_set():
        # Already locked — just update the message
        if _lock_window:
            try:
                _lock_window.after(0, lambda: _update_message(message))
            except Exception:
                pass
        return

    _lock_active.set()
    _lock_thread = threading.Thread(
        target=_run_lock_window,
        args=(message,),
        daemon=True,
        name="LockScreen",
    )
    _lock_thread.start()
    logger.info(f"Screen locked with message: {message!r}")


def unlock() -> None:
    """Remove the lock screen overlay."""
    global _lock_window
    _lock_active.clear()
    if _lock_window:
        try:
            _lock_window.after(0, _lock_window.destroy)
        except Exception:
            pass
        _lock_window = None
    logger.info("Screen unlocked")


def _update_message(message: str) -> None:
    """Update the label text on the existing lock window (call from Tk thread)."""
    global _lock_window
    if _lock_window:
        for widget in _lock_window.winfo_children():
            if isinstance(widget, tk.Label) and hasattr(widget, "_is_msg"):
                widget.config(text=message)
                break


def _run_lock_window(message: str) -> None:
    """Create and run the lock screen window on its own thread."""
    global _lock_window

    root = tk.Tk()
    root.title("ClassroomOS — Locked")

    # Fullscreen, always on top, no decorations
    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)
    root.overrideredirect(True)         # Remove title bar entirely
    root.resizable(False, False)
    root.configure(background="#0a0a1a")

    # Intercept all close attempts
    root.protocol("WM_DELETE_WINDOW", lambda: None)
    root.bind("<Alt-F4>",    lambda e: "break")
    root.bind("<Escape>",    lambda e: "break")
    root.bind("<Control-w>", lambda e: "break")

    # ── Layout ────────────────────────────────────────────────────────────────
    # Lock icon (Unicode padlock)
    icon_label = tk.Label(
        root,
        text="🔒",
        font=("Segoe UI Emoji", 80),
        bg="#0a0a1a",
        fg="#4a9eff",
    )
    icon_label.pack(expand=True, pady=(0, 20))

    # Main message — RTL via justify=RIGHT, correct font for Hebrew
    msg_font = tkfont.Font(family="Segoe UI", size=32, weight="bold")
    msg_label = tk.Label(
        root,
        text=message,
        font=msg_font,
        bg="#0a0a1a",
        fg="#ffffff",
        wraplength=900,
        justify="right",     # RTL text alignment
    )
    msg_label._is_msg = True
    msg_label.pack(padx=40, pady=10)

    # Subtitle
    sub_label = tk.Label(
        root,
        text="המתן להוראות המורה",   # "Wait for the teacher's instructions"
        font=("Segoe UI", 16),
        bg="#0a0a1a",
        fg="#6688aa",
        justify="right",
    )
    sub_label.pack(pady=(0, 80))

    # Decorative bottom gradient strip
    canvas = tk.Canvas(root, height=4, bg="#4a9eff", highlightthickness=0)
    canvas.pack(fill="x", side="bottom")

    _lock_window = root

    # Periodically force window to stay on top (workaround for some games/apps)
    def _enforce_topmost():
        if _lock_active.is_set() and root.winfo_exists():
            root.attributes("-topmost", True)
            root.lift()
            root.focus_force()
            root.after(500, _enforce_topmost)

    root.after(100, _enforce_topmost)
    root.mainloop()

    _lock_window = None
    logger.debug("Lock window closed")
