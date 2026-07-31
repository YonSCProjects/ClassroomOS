"""
ClassroomOS — Remote Control Window
======================================
Full remote control of a single client machine.

Error handling:
  - Offline machine: shows a clear "machine offline" state instead of crashing
  - Stream failures: silently update status bar, never crash the main window
  - Window destruction: all after() calls are guarded with winfo_exists()
  - All thread callbacks catch exceptions
"""

import customtkinter as ctk
from PIL import Image
import io
import base64
import queue
import threading
import time
import logging

from console.core.client_manager import ClientManager, ClientInfo
from shared.protocol import CMD

logger = logging.getLogger("gui.remote_view")

BG_DARK    = "#0d0e1a"
BG_TOP     = "#0f1120"
BG_CANVAS  = "#080a14"
ACCENT     = "#4a9eff"
ACCENT_G   = "#22cc88"
ACCENT_R   = "#ff4a6a"
TEXT_PRI   = "#ffffff"
TEXT_SEC   = "#8899bb"
BORDER     = "#1e2240"


class RemoteControlWindow(ctk.CTkToplevel):
    """
    Live remote control window for a single client.

    Gracefully handles offline machines — shows a status banner
    and retries connection in the background rather than crashing.
    """

    STREAM_FPS  = 8        # Frames per second (lower = less CPU pressure)
    DISPLAY_W   = 1024
    DISPLAY_H   = 576      # 16:9

    MOVE_INTERVAL = 0.05   # Cursor updates are rate-limited to 20/s.
    INPUT_QUEUE_MAX = 64   # Beyond this the operator is ahead of the network.

    def __init__(self, parent, client: ClientInfo, manager: ClientManager):
        super().__init__(parent)
        self.client   = client
        self.manager  = manager
        self._running = True
        self._connected = False

        # Input is queued and sent by a dedicated thread. Sending inline from
        # the Tk callbacks meant every <Motion> event blocked the GUI thread on
        # a TCP round-trip while holding the client lock, which froze the whole
        # console and starved the screen stream.
        self._input_q: queue.Queue = queue.Queue(maxsize=self.INPUT_QUEUE_MAX)
        self._last_move_sent = 0.0

        self.title(f"Remote Control — {client.name} ({client.ip})")
        self.configure(fg_color=BG_DARK)
        self.resizable(True, True)
        self.minsize(self.DISPLAY_W + 20, self.DISPLAY_H + 80)

        self._build_ui()

        # Start stream loop regardless of online status —
        # it will show the offline banner and retry automatically
        self._start_stream()
        self._start_input_worker()

    # ── UI Construction ────────────────────────────────────────────────────────

    def _build_ui(self):
        # Top bar
        topbar = ctk.CTkFrame(self, fg_color=BG_TOP, height=44)
        topbar.pack(fill="x")
        topbar.pack_propagate(False)

        ctk.CTkLabel(
            topbar,
            text=f"🖥️  {self.client.name} — Remote Control",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color=TEXT_PRI,
        ).pack(side="left", padx=16, pady=10)

        self._fps_label = ctk.CTkLabel(
            topbar,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=TEXT_SEC,
        )
        self._fps_label.pack(side="left", padx=8)

        for text, cmd_fn in [
            ("Ctrl+Alt+Del", self._send_cad),
            ("Refresh",      self._force_refresh),
        ]:
            ctk.CTkButton(
                topbar, text=text,
                width=110, height=28,
                font=ctk.CTkFont(size=11),
                fg_color="#223355", hover_color="#334466",
                corner_radius=6,
                command=cmd_fn,
            ).pack(side="right", padx=4, pady=8)

        # Screen canvas
        self._canvas = ctk.CTkLabel(
            self,
            text="📡  Connecting...",
            font=ctk.CTkFont(size=16),
            text_color=TEXT_SEC,
            width=self.DISPLAY_W,
            height=self.DISPLAY_H,
            fg_color=BG_CANVAS,
            corner_radius=0,
        )
        self._canvas.pack(fill="both", expand=True)

        # Bind mouse + keyboard events
        self._canvas.bind("<Motion>",          self._on_mouse_move)
        self._canvas.bind("<Button-1>",        self._on_left_click)
        self._canvas.bind("<Button-3>",        self._on_right_click)
        self._canvas.bind("<Double-Button-1>", self._on_double_click)
        self._canvas.bind("<MouseWheel>",      self._on_scroll)
        self._canvas.bind("<ButtonPress-1>",   self._on_mouse_down)
        self._canvas.bind("<ButtonRelease-1>", self._on_mouse_up)
        self.bind("<KeyPress>",                self._on_key)
        self._canvas.focus_set()

        # Status bar
        self._status = ctk.CTkLabel(
            self,
            text="Connecting...",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_SEC,
            height=24,
        )
        self._status.pack(fill="x", padx=8, pady=2)

    # ── Safe GUI update helpers ────────────────────────────────────────────────

    def _safe_after(self, ms: int, func) -> None:
        """Schedule a tkinter callback only if the window still exists."""
        try:
            if self.winfo_exists():
                self.after(ms, func)
        except Exception:
            pass

    def _set_status(self, text: str, color: str = TEXT_SEC) -> None:
        """Update status bar safely from any thread."""
        def _update():
            try:
                if self.winfo_exists():
                    self._status.configure(text=text, text_color=color)
            except Exception:
                pass
        self._safe_after(0, _update)

    def _show_offline_banner(self) -> None:
        """Show the offline/unreachable state on the canvas."""
        def _update():
            try:
                if self.winfo_exists():
                    self._canvas.configure(
                        image=None,
                        text=f"⚠️  {self.client.name} is offline or unreachable\n\n"
                             f"IP: {self.client.ip}\n\n"
                             "Retrying every 5 seconds...",
                        text_color=ACCENT_R,
                    )
                    try:
                        self._canvas._image = None  # type: ignore[attr-defined]
                    except Exception:
                        pass
            except Exception:
                pass
        self._safe_after(0, _update)

    def _show_connected(self) -> None:
        """Clear the offline banner when connection is established."""
        def _update():
            try:
                if self.winfo_exists():
                    self._canvas.configure(text="", text_color=TEXT_PRI)
            except Exception:
                pass
        self._safe_after(0, _update)

    # ── Screen streaming ───────────────────────────────────────────────────────

    def _start_stream(self) -> None:
        threading.Thread(
            target=self._stream_loop,
            daemon=True,
            name=f"Stream-{self.client.name}",
        ).start()

    def _stream_loop(self) -> None:
        interval = 1.0 / self.STREAM_FPS
        consecutive_failures = 0
        frame_count = 0
        t0 = time.time()

        while self._running:
            start = time.time()

            try:
                resp = self.manager.send(self.client, CMD.SCREENSHOT, {"quality": 55})

                if resp and resp.get("status") == "ok":
                    img_b64 = resp["data"].get("image_b64", "")
                    if img_b64:
                        consecutive_failures = 0
                        if not self._connected:
                            self._connected = True
                            self._show_connected()

                        # Capture b64 in closure to avoid late-binding issues
                        b64 = img_b64
                        self._safe_after(0, lambda b=b64: self._display_frame(b))

                        frame_count += 1
                        elapsed_total = time.time() - t0
                        if elapsed_total > 0:
                            fps = frame_count / elapsed_total
                            self._safe_after(0, lambda f=fps: self._update_fps(f))
                else:
                    consecutive_failures += 1
                    self._connected = False
                    self._show_offline_banner()
                    self._set_status(
                        f"⚠  {self.client.name} not responding  "
                        f"(attempt {consecutive_failures})",
                        ACCENT_R,
                    )
                    # Back off on repeated failures
                    time.sleep(min(5.0, consecutive_failures * 1.0))
                    continue

            except Exception as e:
                consecutive_failures += 1
                self._connected = False
                logger.debug(f"Stream error for {self.client.name}: {e}")
                self._show_offline_banner()
                time.sleep(min(5.0, consecutive_failures * 1.0))
                continue

            elapsed = time.time() - start
            time.sleep(max(0.0, interval - elapsed))

        logger.debug(f"Stream loop ended for {self.client.name}")

    def _update_fps(self, fps: float) -> None:
        try:
            if self.winfo_exists():
                self._fps_label.configure(text=f"~{fps:.1f} FPS")
        except Exception:
            pass

    def _display_frame(self, img_b64: str) -> None:
        if not self._running:
            return
        try:
            if not self.winfo_exists():
                return
            data  = base64.b64decode(img_b64)
            img   = Image.open(io.BytesIO(data)).resize(
                (self.DISPLAY_W, self.DISPLAY_H), Image.LANCZOS
            )
            photo = ctk.CTkImage(img, size=(self.DISPLAY_W, self.DISPLAY_H))
            self._canvas.configure(image=photo, text="")
            # Keep reference to prevent GC
            self._canvas._ctk_image = photo  # type: ignore[attr-defined]
        except Exception as e:
            logger.debug(f"Frame render error for {self.client.name}: {e}")

    # ── Input forwarding ───────────────────────────────────────────────────────

    def _rel(self, event) -> tuple[float, float]:
        """Convert canvas pixel coords to relative (0.0–1.0) for the agent."""
        w = self._canvas.winfo_width()  or self.DISPLAY_W
        h = self._canvas.winfo_height() or self.DISPLAY_H
        return (
            max(0.0, min(1.0, event.x / w)),
            max(0.0, min(1.0, event.y / h)),
        )

    def _start_input_worker(self) -> None:
        threading.Thread(
            target=self._input_loop,
            daemon=True,
            name=f"Input-{self.client.name}",
        ).start()

    def _input_loop(self) -> None:
        """Drain queued input events and forward them one at a time."""
        while self._running:
            try:
                item = self._input_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            cmd, payload = item
            try:
                self.manager.send(self.client, cmd, payload)
            except Exception as e:
                logger.debug(f"Input forward error for {self.client.name}: {e}")

    def _queue_input(self, cmd: str, payload: dict) -> None:
        """
        Hand an input event to the sender thread.

        Dropping events when the queue is full is the right behaviour here:
        the operator is producing input faster than the link can carry it, and
        a stale mouse position is worth less than a responsive window.
        """
        if not self._connected:
            return
        try:
            self._input_q.put_nowait((cmd, payload))
        except queue.Full:
            logger.debug("Input queue full — dropping event")

    def _send_mouse(self, action: str, event, **extra) -> None:
        x, y = self._rel(event)
        self._queue_input(CMD.MOUSE_EVENT, {"action": action, "x": x, "y": y, **extra})

    def _on_mouse_move(self, e) -> None:
        # Tk fires <Motion> dozens of times a second; forwarding every one of
        # them saturates the link without making the cursor any smoother.
        now = time.time()
        if now - self._last_move_sent < self.MOVE_INTERVAL:
            return
        self._last_move_sent = now
        self._send_mouse("move", e)

    def _on_left_click(self, e):   self._send_mouse("click", e, button="left")
    def _on_right_click(self, e):  self._send_mouse("right_click", e)
    def _on_double_click(self, e): self._send_mouse("double_click", e)
    def _on_mouse_down(self, e):   self._send_mouse("mousedown", e, button="left")
    def _on_mouse_up(self, e):     self._send_mouse("mouseup", e, button="left")

    def _on_scroll(self, e) -> None:
        x, y = self._rel(e)
        self._queue_input(CMD.MOUSE_EVENT, {
            "action": "scroll",
            "x": x, "y": y,
            "scroll": 3 if e.delta > 0 else -3,
        })

    def _on_key(self, e) -> None:
        key  = e.keysym.lower()
        char = e.char
        if not key or key == "??":
            return
        if char and char.isprintable() and len(char) == 1:
            self._queue_input(CMD.KEY_EVENT, {"action": "type", "text": char})
        else:
            self._queue_input(CMD.KEY_EVENT, {"action": "press", "key": key})

    def _send_cad(self) -> None:
        """
        Send Ctrl+Alt+Delete.

        Windows reserves the real secure-attention sequence for the OS, so an
        injected hotkey cannot summon the security screen. It is still useful
        for applications that bind the combination themselves; the button is
        labelled accordingly.
        """
        self._queue_input(CMD.KEY_EVENT,
                          {"action": "hotkey", "keys": ["ctrl", "alt", "delete"]})

    def _force_refresh(self) -> None:
        """Force an immediate screenshot fetch, off the GUI thread."""
        def _do():
            try:
                resp = self.manager.send(self.client, CMD.SCREENSHOT, {"quality": 70})
                if resp and resp.get("status") == "ok":
                    img_b64 = (resp.get("data") or {}).get("image_b64", "")
                    if img_b64:
                        self._safe_after(0, lambda b=img_b64: self._display_frame(b))
            except Exception as e:
                logger.debug(f"Force refresh error: {e}")

        threading.Thread(target=_do, daemon=True, name="ForceRefresh").start()

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def destroy(self) -> None:
        self._running = False
        self._connected = False
        try:
            self._input_q.put_nowait(None)   # wake the sender so it can exit
        except Exception:
            pass
        try:
            super().destroy()
        except Exception:
            pass
