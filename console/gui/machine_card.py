"""
ClassroomOS — Machine Card Widget
=====================================
A single tile in the 4×3 dashboard grid representing one client PC.

English UI. RTL support only in client-facing payloads (messages, lock text).
Error handling: all callbacks wrapped — a broken card never kills the dashboard.
"""

import customtkinter as ctk
from PIL import Image
import io
import base64
import logging

from console.core.client_manager import ClientInfo

logger = logging.getLogger("gui.machine_card")

# ── Palette ────────────────────────────────────────────────────────────────────
BG_CARD      = "#14162a"
BG_CARD_OFF  = "#0f1020"
BG_THUMB     = "#0a0c1a"
ACCENT       = "#4a9eff"
ACCENT_G     = "#22cc88"
ACCENT_R     = "#ff4a6a"
ACCENT_Y     = "#ffb84a"
TEXT_PRI     = "#ffffff"
TEXT_SEC     = "#6677aa"
BORDER_ON    = "#2a3a6a"
BORDER_OFF   = "#1a1c2a"

THUMB_W = 280
THUMB_H = 157    # 16:9 aspect ratio


class MachineCard(ctk.CTkFrame):

    def __init__(
        self,
        parent,
        client: ClientInfo,
        on_remote_control=None,
        on_shutdown=None,
        on_restart=None,
        on_message=None,
        on_lock=None,
    ):
        super().__init__(
            parent,
            fg_color=BG_CARD_OFF,
            corner_radius=12,
            border_width=1,
            border_color=BORDER_OFF,
        )
        self.client           = client
        self._cb_remote       = on_remote_control
        self._cb_shutdown     = on_shutdown
        self._cb_restart      = on_restart
        self._cb_message      = on_message
        self._cb_lock         = on_lock
        self._thumb_photo     = None
        self._online          = client.online

        self._build()
        self.update_status(client)

    # ── Layout ─────────────────────────────────────────────────────────────────

    def _build(self):
        # ── Header: status dot + machine name ─────────────────────────────────
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(12, 4))

        self._dot = ctk.CTkLabel(
            header, text="●",
            font=ctk.CTkFont(size=12), text_color=ACCENT_R,
        )
        self._dot.pack(side="left")

        self._name_lbl = ctk.CTkLabel(
            header,
            text=self.client.name,
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color=TEXT_PRI, anchor="w",
        )
        self._name_lbl.pack(side="left", padx=(6, 0))

        # ── Thumbnail ─────────────────────────────────────────────────────────
        self._thumb = ctk.CTkLabel(
            self,
            text="",
            width=THUMB_W, height=THUMB_H,
            fg_color=BG_THUMB, corner_radius=6,
        )
        self._thumb.pack(padx=12, pady=4)
        self._show_placeholder()

        # ── Info row ──────────────────────────────────────────────────────────
        info = ctk.CTkFrame(self, fg_color="transparent")
        info.pack(fill="x", padx=12, pady=(2, 0))

        self._user_lbl = ctk.CTkLabel(
            info,
            text=f"👤 {self.client.logged_user or '—'}",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=TEXT_SEC, anchor="w",
        )
        self._user_lbl.pack(side="left")

        self._ip_lbl = ctk.CTkLabel(
            info,
            text=self.client.ip,
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=TEXT_SEC, anchor="e",
        )
        self._ip_lbl.pack(side="right")

        # ── Action buttons ────────────────────────────────────────────────────
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=12, pady=(8, 12))

        self._mk_btn(btn_frame, "🖥️", "Remote",  ACCENT,    self._safe_cb(self._cb_remote))
        self._mk_btn(btn_frame, "⏻",  "Off",     "#445566", self._safe_cb(self._cb_shutdown))
        self._mk_btn(btn_frame, "🔄", "Restart", "#334455", self._safe_cb(self._cb_restart))
        self._mk_btn(btn_frame, "💬", "Message", "#335566", self._safe_cb(self._cb_message))
        self._mk_btn(btn_frame, "🔒", "Lock",    ACCENT_Y,  self._safe_cb(self._cb_lock))

    def _mk_btn(self, parent, icon: str, tooltip: str, color: str, command) -> ctk.CTkButton:
        btn = ctk.CTkButton(
            parent,
            text=icon,
            width=44, height=30,
            font=ctk.CTkFont(size=14),
            fg_color=color,
            hover_color=self._lighten(color),
            corner_radius=6,
            command=command,
        )
        btn.pack(side="left", padx=2)
        return btn

    def _safe_cb(self, cb):
        """Wrap a callback so exceptions don't crash the dashboard."""
        def _wrapper():
            if cb:
                try:
                    cb(self.client)
                except Exception as e:
                    logger.error(f"Card callback error for {self.client.name}: {e}")
        return _wrapper

    # ── Thumbnail handling ─────────────────────────────────────────────────────

    def _show_placeholder(self):
        """Display offline placeholder."""
        img = Image.new("RGB", (THUMB_W, THUMB_H), color=(12, 14, 30))
        self._set_image(img)

    def _set_image(self, img: Image.Image):
        try:
            photo = ctk.CTkImage(img, size=(THUMB_W, THUMB_H))
            self._thumb.configure(image=photo, text="")
            self._thumb_photo = photo
        except Exception as e:
            logger.debug(f"Thumbnail set error: {e}")

    def update_thumbnail(self, image_b64: str):
        """Called from background thread — schedules GUI update safely."""
        try:
            if self.winfo_exists():
                self.after(0, lambda: self._apply_thumbnail(image_b64))
        except Exception:
            pass

    def _apply_thumbnail(self, image_b64: str):
        try:
            data = base64.b64decode(image_b64)
            img  = Image.open(io.BytesIO(data)).resize((THUMB_W, THUMB_H), Image.LANCZOS)
            self._set_image(img)
        except Exception as e:
            logger.debug(f"Thumbnail decode error for {self.client.name}: {e}")

    # ── Status updates ─────────────────────────────────────────────────────────

    def update_status(self, client: ClientInfo):
        """Update card visuals for online/offline state. Safe to call from any thread."""
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return

        self.client = client
        online = client.online

        try:
            if online:
                self.configure(fg_color=BG_CARD, border_color=BORDER_ON)
                self._dot.configure(text_color=ACCENT_G)
                self._user_lbl.configure(text=f"👤 {client.logged_user or '—'}")
            else:
                self.configure(fg_color=BG_CARD_OFF, border_color=BORDER_OFF)
                self._dot.configure(text_color=ACCENT_R)
                self._user_lbl.configure(text="👤 —")
                if self._online:
                    self._show_placeholder()
        except Exception as e:
            logger.debug(f"update_status error: {e}")

        self._online = online

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _lighten(hex_color: str) -> str:
        try:
            r = min(255, int(hex_color[1:3], 16) + 25)
            g = min(255, int(hex_color[3:5], 16) + 25)
            b = min(255, int(hex_color[5:7], 16) + 25)
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return hex_color
