"""
ClassroomOS — Agent Messaging Handler
==========================================
Displays non-dismissible popup messages from the teacher.
Hebrew/RTL text is rendered correctly by Windows' Segoe UI font stack.
"""

import threading
import tkinter as tk
from tkinter import font as tkfont
import logging

logger = logging.getLogger("handler.messaging")

_msg_lock = threading.Lock()
_open_count = 0
# Bumped by dismiss_all(); each popup polls it and closes itself when it sees a
# newer value than the one it was created with. Popping the window from another
# thread is not safe, so the window closes itself instead.
_dismiss_generation = 0

_POLL_MS = 250


def show_message(title: str, message: str, timeout: int = 0) -> None:
    """
    Show a popup message to the student.

    Args:
        title:   Window title / header text
        message: Body text (Hebrew/RTL supported)
        timeout: Auto-close after this many seconds (0 = stays until the
                 teacher sends CMD.DISMISS_MSG)
    """
    with _msg_lock:
        generation = _dismiss_generation

    t = threading.Thread(
        target=_create_message_window,
        args=(title, message, timeout, generation),
        daemon=True,
        name="MessagePopup",
    )
    t.start()
    logger.info(f"Message displayed: {title!r}")


def dismiss_all() -> None:
    """
    Close every open message popup.

    Students cannot close these windows themselves — Alt+F4 and the close
    button are both disabled — so this is the only way a message sent with
    timeout=0 ever goes away. It is wired to CMD.DISMISS_MSG.
    """
    global _dismiss_generation
    with _msg_lock:
        _dismiss_generation += 1
        count = _open_count
    logger.info(f"Dismissing {count} message popup(s)")


def open_message_count() -> int:
    """How many popups are currently on screen."""
    with _msg_lock:
        return _open_count


def _create_message_window(title: str, message: str, timeout: int,
                           generation: int) -> None:
    global _open_count

    root = tk.Tk()
    root.withdraw()                   # Hide briefly while we configure

    root.title("ClassroomOS — הודעה")
    root.configure(background="#12131f")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    # Students cannot close this window
    root.protocol("WM_DELETE_WINDOW", lambda: None)
    root.bind("<Alt-F4>", lambda e: "break")

    # ── Content ────────────────────────────────────────────────────────────────
    # Header bar
    header = tk.Frame(root, bg="#4a9eff", height=4)
    header.pack(fill="x")

    # Icon + title row
    top_frame = tk.Frame(root, bg="#12131f", pady=20)
    top_frame.pack(fill="x", padx=30)

    tk.Label(
        top_frame,
        text="📢",
        font=("Segoe UI Emoji", 28),
        bg="#12131f",
        fg="#4a9eff",
    ).pack(side="right")

    tk.Label(
        top_frame,
        text=title,
        font=tkfont.Font(family="Segoe UI", size=18, weight="bold"),
        bg="#12131f",
        fg="#ffffff",
        justify="right",
        anchor="e",
    ).pack(side="right", padx=(0, 10))

    # Message body
    tk.Label(
        root,
        text=message,
        font=tkfont.Font(family="Segoe UI", size=14),
        bg="#12131f",
        fg="#ccddff",
        wraplength=500,
        justify="right",
        anchor="e",
        padx=30,
        pady=15,
    ).pack(fill="x")

    # Footer hint
    footer_text = (
        f"ההודעה תסגר אוטומטית בעוד {timeout} שניות" if timeout
        else "ההודעה תיסגר על ידי המורה"
    )
    tk.Label(
        root,
        text=footer_text,
        font=tkfont.Font(family="Segoe UI", size=10),
        bg="#12131f",
        fg="#445566",
        pady=15,
    ).pack(fill="x")

    # Bottom accent
    tk.Frame(root, bg="#4a9eff", height=2).pack(fill="x")

    # Center window on screen
    root.update_idletasks()
    w = root.winfo_reqwidth()
    h = root.winfo_reqheight()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")
    root.deiconify()

    with _msg_lock:
        _open_count += 1

    def _poll_dismiss():
        """Close this popup once the teacher has dismissed messages."""
        with _msg_lock:
            dismissed = _dismiss_generation != generation
        if dismissed:
            try:
                root.destroy()
            except Exception:
                pass
            return
        root.after(_POLL_MS, _poll_dismiss)

    root.after(_POLL_MS, _poll_dismiss)

    if timeout > 0:
        root.after(timeout * 1000, root.destroy)

    try:
        root.mainloop()
    finally:
        with _msg_lock:
            _open_count = max(0, _open_count - 1)
