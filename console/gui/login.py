"""
ClassroomOS — Login Window
=============================
Admin login and first-run password setup.
English UI. Hebrew/RTL rendering is only needed on client-facing windows.
"""

import customtkinter as ctk
import logging

from console.core.auth import verify_password, set_password, save_config

logger = logging.getLogger("gui.login")

BG_DARK    = "#0d0e1a"
BG_CARD    = "#14162a"
ACCENT     = "#4a9eff"
ACCENT_HOV = "#6ab4ff"
TEXT_PRI   = "#ffffff"
TEXT_SEC   = "#8899bb"
BORDER     = "#1e2240"
ERROR_RED  = "#ff4a6a"


class LoginWindow(ctk.CTkToplevel):
    """
    Modal login / first-run setup window.
    Check `self.authenticated` (bool) after the window closes.
    """

    def __init__(self, parent, config: dict, first_run: bool = False):
        super().__init__(parent)
        # Named app_config, not config: CTkToplevel inherits Tkinter's config()
        # method and assigning a dict over it breaks any internal caller.
        self.app_config    = config
        self.first_run     = first_run
        self.authenticated = False

        self._build_ui()
        self.grab_set()
        self.focus()

    # ── UI Construction ────────────────────────────────────────────────────────

    def _build_ui(self):
        self.title("ClassroomOS")
        self.resizable(False, False)
        self.configure(fg_color=BG_DARK)

        w, h = 440, 520 if self.first_run else 460
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

        # ── Branding ──────────────────────────────────────────────────────────
        logo_frame = ctk.CTkFrame(self, fg_color="transparent")
        logo_frame.pack(pady=(44, 0))

        ctk.CTkLabel(logo_frame, text="🖥️", font=ctk.CTkFont(size=52)).pack()

        ctk.CTkLabel(
            logo_frame, text="ClassroomOS",
            font=ctk.CTkFont(family="Segoe UI", size=26, weight="bold"),
            text_color=TEXT_PRI,
        ).pack(pady=(8, 2))

        subtitle = "Computer Lab Management System"
        ctk.CTkLabel(
            logo_frame, text=subtitle,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=TEXT_SEC,
        ).pack()

        # ── Card ──────────────────────────────────────────────────────────────
        card = ctk.CTkFrame(
            self,
            fg_color=BG_CARD,
            corner_radius=16,
            border_width=1,
            border_color=BORDER,
        )
        card.pack(padx=36, pady=24, fill="both", expand=True)

        title_text = "First-Time Setup" if self.first_run else "Admin Login"
        ctk.CTkLabel(
            card, text=title_text,
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color=TEXT_PRI, anchor="w",
        ).pack(padx=24, pady=(22, 2), anchor="w")

        sub_text = "Create your admin password to get started" if self.first_run else "Enter your password to continue"
        ctk.CTkLabel(
            card, text=sub_text,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=TEXT_SEC, anchor="w",
        ).pack(padx=24, anchor="w")

        ctk.CTkFrame(card, height=1, fg_color=BORDER).pack(fill="x", padx=24, pady=14)

        # Password field
        ctk.CTkLabel(card, text="Password",
                     font=ctk.CTkFont(size=12), text_color=TEXT_SEC,
                     anchor="w").pack(padx=24, anchor="w")

        self._pw_entry = ctk.CTkEntry(
            card, placeholder_text="Enter password",
            show="●", height=40, corner_radius=8,
            border_color=BORDER, fg_color="#0d1020",
            text_color=TEXT_PRI,
            font=ctk.CTkFont(family="Segoe UI", size=13),
        )
        self._pw_entry.pack(padx=24, pady=(4, 0), fill="x")
        self._pw_entry.bind("<Return>", lambda e: self._on_submit())

        # Confirm field (first-run only)
        self._confirm_entry = None
        if self.first_run:
            ctk.CTkLabel(card, text="Confirm Password",
                         font=ctk.CTkFont(size=12), text_color=TEXT_SEC,
                         anchor="w").pack(padx=24, pady=(12, 0), anchor="w")

            self._confirm_entry = ctk.CTkEntry(
                card, placeholder_text="Re-enter password",
                show="●", height=40, corner_radius=8,
                border_color=BORDER, fg_color="#0d1020",
                text_color=TEXT_PRI,
                font=ctk.CTkFont(family="Segoe UI", size=13),
            )
            self._confirm_entry.pack(padx=24, pady=(4, 0), fill="x")
            self._confirm_entry.bind("<Return>", lambda e: self._on_submit())

        # Error label
        self._error_label = ctk.CTkLabel(
            card, text="",
            font=ctk.CTkFont(size=12), text_color=ERROR_RED, anchor="w",
        )
        self._error_label.pack(padx=24, pady=(8, 0), anchor="w")

        # Submit button
        btn_text = "Create Password" if self.first_run else "Login"
        self._submit_btn = ctk.CTkButton(
            card, text=btn_text,
            height=42, corner_radius=8,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            fg_color=ACCENT, hover_color=ACCENT_HOV,
            command=self._on_submit,
        )
        self._submit_btn.pack(padx=24, pady=18, fill="x")

        ctk.CTkLabel(
            self, text="ClassroomOS v1.0",
            font=ctk.CTkFont(size=10), text_color="#334455",
        ).pack(pady=(0, 14))

        self._pw_entry.focus()

    # ── Logic ──────────────────────────────────────────────────────────────────

    def _show_error(self, msg: str) -> None:
        self._error_label.configure(text=f"⚠  {msg}")

    def _on_submit(self) -> None:
        pw = self._pw_entry.get().strip()
        if not pw:
            self._show_error("Please enter a password")
            return

        if self.first_run:
            confirm = self._confirm_entry.get().strip() if self._confirm_entry else ""
            if pw != confirm:
                self._show_error("Passwords do not match")
                return
            if len(pw) < 6:
                self._show_error("Password must be at least 6 characters")
                return
            self.app_config = set_password(self.app_config, pw)
            save_config(self.app_config)
            self.authenticated = True
            logger.info("Admin password created")
            self.destroy()
        else:
            if verify_password(pw, self.app_config.get("password_hash", "")):
                self.authenticated = True
                logger.info("Admin logged in")
                self.destroy()
            else:
                self._show_error("Incorrect password")
                self._pw_entry.delete(0, "end")
                self._pw_entry.focus()
                logger.warning("Failed login attempt")
