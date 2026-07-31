"""
ClassroomOS — Backup Panel
============================
A dedicated window for managing folder backups across client machines.

Features
--------
* Per-machine backup: pick machine, choose source folders, back up now or schedule
* Compress toggle (DEFLATED vs STORED) with a clear explanation
* Multi-folder selection — add as many source paths as needed
* Schedule: manual / daily at HH:00 / weekly
* Status: shows last backup time and size per machine
* Downloads zip files to a chosen local directory, named as
  <machine>_<label>_<timestamp>.zip so nothing overwrites anything

All network calls run in daemon threads — the UI never freezes.
"""

import customtkinter as ctk
from tkinter import messagebox, filedialog
import threading
import os
import base64
import logging

from datetime import datetime
from console.core.client_manager import ClientManager, ClientInfo
from shared.protocol import CMD

logger = logging.getLogger("gui.backup_panel")


def _error_text(resp: dict | None) -> str:
    """Pull a human-readable failure out of a protocol response."""
    if not resp:
        return "No response — machine offline?"
    return resp.get("error") or resp.get("message") or "Unknown error"

BG_DARK   = "#0d0e1a"
BG_CARD   = "#14162a"
BG_TOP    = "#0f1120"
BG_INPUT  = "#0d1020"
ACCENT    = "#4a9eff"
ACCENT_G  = "#22cc88"
ACCENT_R  = "#ff4a6a"
ACCENT_Y  = "#ffb84a"
TEXT_PRI  = "#ffffff"
TEXT_SEC  = "#8899bb"
BORDER    = "#1e2240"


class BackupPanel(ctk.CTkToplevel):

    def __init__(self, parent, manager: ClientManager):
        super().__init__(parent)
        self.manager = manager

        self.title("Backup Manager")
        self.configure(fg_color=BG_DARK)
        self.geometry("900x680")
        self.minsize(800, 600)
        self.grab_set()

        # State
        self._selected_client: ClientInfo | None = None
        self._source_folders:  list[str]          = []
        self._save_dir:        str                 = os.path.expanduser("~/Desktop/Backups")

        self._build_ui()
        self._populate_machine_list()

    # ── UI ─────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Title bar
        hdr = ctk.CTkFrame(self, fg_color=BG_TOP, height=56)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(
            hdr, text="📦  Backup Manager",
            font=ctk.CTkFont(family="Segoe UI", size=17, weight="bold"),
            text_color=TEXT_PRI,
        ).pack(side="left", padx=20, pady=12)

        # Body split: machine list | config panel
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=0, pady=0)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        self._build_machine_list(body)
        self._build_config_panel(body)

        # Status bar
        self._status_lbl = ctk.CTkLabel(
            self, text="Select a machine to configure its backup.",
            font=ctk.CTkFont(size=11), text_color=TEXT_SEC, anchor="w",
        )
        self._status_lbl.pack(fill="x", padx=16, pady=4)

    def _build_machine_list(self, parent):
        frame = ctk.CTkFrame(parent, fg_color=BG_CARD, width=210,
                             corner_radius=0, border_width=1, border_color=BORDER)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.grid_propagate(False)

        ctk.CTkLabel(
            frame, text="Machines",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=TEXT_SEC, anchor="w",
        ).pack(padx=14, pady=(14, 6), anchor="w")

        self._machine_scroll = ctk.CTkScrollableFrame(frame, fg_color="transparent")
        self._machine_scroll.pack(fill="both", expand=True, padx=4, pady=4)

    def _build_config_panel(self, parent):
        right = ctk.CTkScrollableFrame(parent, fg_color=BG_DARK)
        right.grid(row=0, column=1, sticky="nsew", padx=0)

        pad = {"padx": 24, "pady": 6}

        # ── Machine info ──────────────────────────────────────────────────────
        self._machine_lbl = ctk.CTkLabel(
            right, text="No machine selected",
            font=ctk.CTkFont(size=16, weight="bold"), text_color=TEXT_PRI, anchor="w",
        )
        self._machine_lbl.pack(fill="x", padx=24, pady=(20, 2))

        self._machine_status_lbl = ctk.CTkLabel(
            right, text="",
            font=ctk.CTkFont(size=11), text_color=TEXT_SEC, anchor="w",
        )
        self._machine_status_lbl.pack(fill="x", padx=24, pady=(0, 10))

        ctk.CTkFrame(right, height=1, fg_color=BORDER).pack(fill="x", padx=24, pady=4)

        # ── Source folders ────────────────────────────────────────────────────
        ctk.CTkLabel(right, text="Source Folders",
                     font=ctk.CTkFont(size=13, weight="bold"), text_color=TEXT_PRI,
                     anchor="w").pack(fill="x", **pad)

        ctk.CTkLabel(
            right,
            text="All listed folders will be packed into one ZIP archive.",
            font=ctk.CTkFont(size=11), text_color=TEXT_SEC, anchor="w",
        ).pack(fill="x", padx=24)

        folder_row = ctk.CTkFrame(right, fg_color="transparent")
        folder_row.pack(fill="x", padx=24, pady=6)

        self._folder_entry = ctk.CTkEntry(
            folder_row, placeholder_text="C:\\MinecraftServer\\world",
            fg_color=BG_INPUT, text_color=TEXT_PRI, border_color=BORDER,
            font=ctk.CTkFont(size=12), height=34,
        )
        self._folder_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

        ctk.CTkButton(folder_row, text="Browse",
                      width=80, height=34,
                      fg_color="#223355", hover_color="#334466",
                      font=ctk.CTkFont(size=12),
                      command=self._browse_folder).pack(side="left", padx=(0, 6))
        ctk.CTkButton(folder_row, text="＋ Add",
                      width=70, height=34,
                      fg_color=ACCENT, hover_color="#6ab4ff",
                      font=ctk.CTkFont(size=12),
                      command=self._add_folder).pack(side="left")

        # Folder list
        self._folder_list_frame = ctk.CTkFrame(right, fg_color=BG_CARD, corner_radius=8)
        self._folder_list_frame.pack(fill="x", padx=24, pady=(2, 8))

        self._empty_folders_lbl = ctk.CTkLabel(
            self._folder_list_frame,
            text="No folders added yet.",
            font=ctk.CTkFont(size=11), text_color=TEXT_SEC,
        )
        self._empty_folders_lbl.pack(pady=12)

        ctk.CTkFrame(right, height=1, fg_color=BORDER).pack(fill="x", padx=24, pady=4)

        # ── Compression ───────────────────────────────────────────────────────
        ctk.CTkLabel(right, text="Compression",
                     font=ctk.CTkFont(size=13, weight="bold"), text_color=TEXT_PRI,
                     anchor="w").pack(fill="x", **pad)

        compress_row = ctk.CTkFrame(right, fg_color="transparent")
        compress_row.pack(fill="x", padx=24)

        self._compress_var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(
            compress_row,
            text="Compress (ZIP_DEFLATED)",
            variable=self._compress_var,
            font=ctk.CTkFont(size=12),
            text_color=TEXT_PRI,
            onvalue=True, offvalue=False,
            fg_color=BORDER, progress_color=ACCENT,
            command=self._on_compress_toggle,
        ).pack(side="left")

        self._compress_hint = ctk.CTkLabel(
            right,
            text="💡 Smaller file, slower transfer. Turn OFF for game worlds, videos, images.",
            font=ctk.CTkFont(size=11), text_color=TEXT_SEC, anchor="w",
        )
        self._compress_hint.pack(fill="x", padx=24, pady=(2, 8))

        ctk.CTkFrame(right, height=1, fg_color=BORDER).pack(fill="x", padx=24, pady=4)

        # ── Schedule ──────────────────────────────────────────────────────────
        ctk.CTkLabel(right, text="Auto-Backup Schedule",
                     font=ctk.CTkFont(size=13, weight="bold"), text_color=TEXT_PRI,
                     anchor="w").pack(fill="x", **pad)

        sched_row = ctk.CTkFrame(right, fg_color="transparent")
        sched_row.pack(fill="x", padx=24, pady=4)

        self._schedule_var = ctk.StringVar(value="manual")
        for val, label in [("manual", "Manual only"), ("daily", "Daily"), ("weekly", "Weekly")]:
            ctk.CTkRadioButton(
                sched_row, text=label, value=val,
                variable=self._schedule_var,
                font=ctk.CTkFont(size=12), text_color=TEXT_PRI,
                fg_color=ACCENT, border_color=BORDER,
            ).pack(side="left", padx=(0, 18))

        hour_row = ctk.CTkFrame(right, fg_color="transparent")
        hour_row.pack(fill="x", padx=24, pady=4)
        ctk.CTkLabel(hour_row, text="Run at hour (0-23):",
                     font=ctk.CTkFont(size=12), text_color=TEXT_SEC).pack(side="left")
        self._hour_entry = ctk.CTkEntry(
            hour_row, width=60, height=30, fg_color=BG_INPUT,
            text_color=TEXT_PRI, border_color=BORDER, font=ctk.CTkFont(size=12),
        )
        self._hour_entry.insert(0, "2")
        self._hour_entry.pack(side="left", padx=8)
        ctk.CTkLabel(hour_row, text=":00",
                     font=ctk.CTkFont(size=12), text_color=TEXT_SEC).pack(side="left")

        ctk.CTkFrame(right, height=1, fg_color=BORDER).pack(fill="x", padx=24, pady=8)

        # ── Save location ─────────────────────────────────────────────────────
        ctk.CTkLabel(right, text="Save Backups To",
                     font=ctk.CTkFont(size=13, weight="bold"), text_color=TEXT_PRI,
                     anchor="w").pack(fill="x", **pad)

        save_row = ctk.CTkFrame(right, fg_color="transparent")
        save_row.pack(fill="x", padx=24, pady=4)

        self._save_dir_lbl = ctk.CTkLabel(
            save_row, text=self._save_dir,
            font=ctk.CTkFont(size=11), text_color=TEXT_SEC, anchor="w",
        )
        self._save_dir_lbl.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(save_row, text="Change",
                      width=80, height=30,
                      fg_color="#223355", hover_color="#334466",
                      font=ctk.CTkFont(size=12),
                      command=self._choose_save_dir).pack(side="right")

        ctk.CTkFrame(right, height=1, fg_color=BORDER).pack(fill="x", padx=24, pady=8)

        # ── Action buttons ────────────────────────────────────────────────────
        btn_row = ctk.CTkFrame(right, fg_color="transparent")
        btn_row.pack(fill="x", padx=24, pady=(4, 20))

        ctk.CTkButton(
            btn_row, text="💾  Backup Now",
            height=40, width=160,
            fg_color=ACCENT_G, hover_color="#33dd99",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._backup_now,
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            btn_row, text="📅  Save Schedule",
            height=40, width=160,
            fg_color=ACCENT, hover_color="#6ab4ff",
            font=ctk.CTkFont(size=13),
            command=self._save_schedule,
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            btn_row, text="📊  Check Status",
            height=40, width=160,
            fg_color="#334466", hover_color="#445577",
            font=ctk.CTkFont(size=13),
            command=self._check_status,
        ).pack(side="left")

    # ── Machine list ───────────────────────────────────────────────────────────

    def _populate_machine_list(self):
        for widget in self._machine_scroll.winfo_children():
            widget.destroy()

        for name, client in self.manager.clients.items():
            dot_color = ACCENT_G if client.online else ACCENT_R
            row = ctk.CTkFrame(self._machine_scroll, fg_color="transparent")
            row.pack(fill="x", pady=1)

            ctk.CTkLabel(row, text="●", text_color=dot_color,
                         font=ctk.CTkFont(size=10)).pack(side="left", padx=(4, 2))
            btn = ctk.CTkButton(
                row, text=name,
                anchor="w", height=30,
                fg_color="transparent", hover_color="#1e2240",
                text_color=TEXT_PRI, font=ctk.CTkFont(size=12),
                command=lambda c=client: self._select_machine(c),
            )
            btn.pack(side="left", fill="x", expand=True)

    def _select_machine(self, client: ClientInfo):
        self._selected_client  = client
        self._source_folders   = []
        self._machine_lbl.configure(text=f"🖥  {client.name}  ({client.ip})")
        status = "🟢 Online" if client.online else "🔴 Offline"
        self._machine_status_lbl.configure(text=status)
        self._refresh_folder_list()
        self._check_status()

    # ── Folder management ──────────────────────────────────────────────────────

    def _browse_folder(self):
        path = filedialog.askdirectory(title="Select source folder")
        if path:
            self._folder_entry.delete(0, "end")
            self._folder_entry.insert(0, path.replace("/", "\\"))

    def _add_folder(self):
        path = self._folder_entry.get().strip()
        if not path:
            return
        if path not in self._source_folders:
            self._source_folders.append(path)
        self._folder_entry.delete(0, "end")
        self._refresh_folder_list()

    def _remove_folder(self, path: str):
        if path in self._source_folders:
            self._source_folders.remove(path)
        self._refresh_folder_list()

    def _refresh_folder_list(self):
        for w in self._folder_list_frame.winfo_children():
            w.destroy()

        if not self._source_folders:
            ctk.CTkLabel(
                self._folder_list_frame,
                text="No folders added yet.",
                font=ctk.CTkFont(size=11), text_color=TEXT_SEC,
            ).pack(pady=12)
            return

        for folder in self._source_folders:
            row = ctk.CTkFrame(self._folder_list_frame, fg_color="transparent")
            row.pack(fill="x", padx=8, pady=2)
            ctk.CTkLabel(
                row, text=f"📁  {folder}",
                font=ctk.CTkFont(size=11), text_color=TEXT_PRI, anchor="w",
            ).pack(side="left", fill="x", expand=True)
            ctk.CTkButton(
                row, text="✕", width=28, height=24,
                fg_color=ACCENT_R, hover_color="#ff6080",
                font=ctk.CTkFont(size=11),
                command=lambda f=folder: self._remove_folder(f),
            ).pack(side="right", padx=4)

    def _on_compress_toggle(self):
        if self._compress_var.get():
            self._compress_hint.configure(
                text="💡 Smaller file, slower transfer. Turn OFF for game worlds, videos, images."
            )
        else:
            self._compress_hint.configure(
                text="⚡ Fast mode — ZIP_STORED (no compression). Best for Minecraft worlds, media."
            )

    # ── Actions ────────────────────────────────────────────────────────────────

    def _require_machine(self) -> ClientInfo | None:
        if not self._selected_client:
            messagebox.showwarning("No Machine", "Please select a machine first.")
            return None
        return self._selected_client

    def _require_folders(self) -> bool:
        if not self._source_folders:
            messagebox.showwarning("No Folders", "Add at least one source folder.")
            return False
        return True

    def _backup_now(self):
        client = self._require_machine()
        if not client:
            return
        if not self._require_folders():
            return

        compress = self._compress_var.get()
        folders  = list(self._source_folders)
        algo     = "DEFLATED" if compress else "STORED (fast)"

        self._set_status(f"Backing up {len(folders)} folder(s) from {client.name} ({algo})...")

        def _do():
            try:
                resp = self.manager.send(client, CMD.BACKUP_NOW, {
                    "folders":       folders,
                    "label":         client.name,
                    "compress":      compress,
                    "compresslevel": 6,
                })
                if resp and resp.get("status") == "ok":
                    data     = resp.get("data") or {}
                    zip_b64  = data.get("zip_b64", "")
                    count    = data.get("file_count", 0)
                    size     = data.get("size_bytes", 0)
                    ts       = data.get("timestamp", datetime.now().isoformat())[:19].replace(":", "-")

                    if not zip_b64:
                        msg = "The machine reported no files in the selected folder(s)."
                        self._set_status(f"⚠  {msg}")
                        self._safe_after(0, lambda: messagebox.showwarning("Backup Empty", msg))
                        return

                    os.makedirs(self._save_dir, exist_ok=True)
                    fname    = f"{client.name}_backup_{ts}.zip"
                    out_path = os.path.join(self._save_dir, fname)

                    with open(out_path, "wb") as f:
                        f.write(base64.b64decode(zip_b64))

                    size_kb = size / 1024
                    self._set_status(
                        f"✅  Backup saved: {fname}  ({count} files, {size_kb:.1f} KB)"
                    )
                    self._safe_after(0, lambda: messagebox.showinfo(
                        "Backup Complete",
                        f"Saved to:\n{out_path}\n\n"
                        f"Files: {count}\nSize: {size_kb:.1f} KB\nAlgorithm: {algo}",
                    ))
                else:
                    err = _error_text(resp)
                    self._set_status(f"❌  Backup failed: {err}")
                    self._safe_after(0, lambda e=err: messagebox.showerror("Backup Failed", e))
            except Exception as e:
                logger.error(f"Backup error: {e}")
                self._set_status(f"❌  Backup error: {e}")

        threading.Thread(target=_do, daemon=True, name="BackupNow").start()

    def _save_schedule(self):
        client = self._require_machine()
        if not client:
            return
        if not self._require_folders():
            return

        try:
            hour = int(self._hour_entry.get().strip())
            if not (0 <= hour <= 23):
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid Hour", "Hour must be 0–23.")
            return

        payload = {
            "folders":       list(self._source_folders),
            "label":         client.name,
            "interval":      self._schedule_var.get(),
            "hour":          hour,
            "enabled":       self._schedule_var.get() != "manual",
            "compress":      self._compress_var.get(),
            "compresslevel": 6,
        }

        def _do():
            try:
                resp = self.manager.send(client, CMD.BACKUP_CONFIG, payload)
                ok   = bool(resp and resp.get("status") == "ok")
                msg  = "Schedule saved!" if ok else f"Failed: {_error_text(resp)}"
                self._set_status(msg)
                self._safe_after(
                    0,
                    lambda m=msg, k=ok: (messagebox.showinfo("Schedule", m) if k
                                         else messagebox.showerror("Schedule", m)),
                )
            except Exception as e:
                self._set_status(f"Schedule error: {e}")

        threading.Thread(target=_do, daemon=True, name="SaveSchedule").start()

    def _check_status(self):
        client = self._require_machine()
        if not client:
            return

        def _do():
            try:
                resp = self.manager.send(client, CMD.BACKUP_STATUS, {})
                if resp and resp.get("status") == "ok":
                    d        = resp.get("data", {})
                    last     = d.get("last_backup") or "Never"
                    size_kb  = d.get("last_size", 0) / 1024
                    interval = d.get("interval", "manual")
                    enabled  = d.get("enabled", False)
                    folders  = d.get("folders", [])
                    compress = d.get("compress", True)
                    algo     = "DEFLATED" if compress else "STORED"
                    sched    = f"{interval} (enabled)" if enabled else f"{interval} (disabled)"

                    next_due = (d.get("next_due") or "")[:16].replace("T", " ")
                    next_txt = f" — next {next_due}" if next_due else ""
                    self._set_status(
                        f"Last backup: {last[:19] if last != 'Never' else 'Never'}  "
                        f"({size_kb:.1f} KB) — {sched} — {algo}{next_txt}"
                    )
                    if folders:
                        self._safe_after(0, lambda fols=folders: self._set_folders_from_status(fols))
                else:
                    self._set_status("Machine offline or backup handler not available.")
            except Exception as e:
                self._set_status(f"Status check failed: {e}")

        threading.Thread(target=_do, daemon=True, name="BackupStatus").start()

    def _set_folders_from_status(self, folders: list[str]):
        """Populate the folder list from a status response (if machine has a saved config)."""
        if not self._source_folders:  # Don't overwrite manually added ones
            self._source_folders = list(folders)
            self._refresh_folder_list()

    def _choose_save_dir(self):
        d = filedialog.askdirectory(title="Choose where to save backup ZIPs")
        if d:
            self._save_dir = d
            self._save_dir_lbl.configure(text=d)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _set_status(self, msg: str):
        self._safe_after(0, lambda: self._status_lbl.configure(text=msg))

    def _safe_after(self, ms: int, fn):
        try:
            if self.winfo_exists():
                self.after(ms, fn)
        except Exception:
            pass
