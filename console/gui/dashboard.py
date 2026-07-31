"""
ClassroomOS — Main Dashboard
===============================
Primary admin console window. English UI throughout.
Hebrew/RTL text only appears in payloads SENT TO student machines
(lock screen messages, teacher popups, etc.)

Error handling contract:
  - All background-thread callbacks use _safe_after() before touching any widget
  - All per-machine actions wrapped in try/except — one broken machine never freezes the UI
  - All simpledialog / filedialog calls checked for None (user cancelled)
  - Audit log row parsing is fault-tolerant (bad CSV rows skipped)

Layout:
  ┌──────────────────────────────────────────────────┐
  │  TopBar: Logo | Global actions                   │
  ├───────────────┬──────────────────────────────────┤
  │  Sidebar      │  4×3 Machine Card Grid           │
  │  Power        │                                  │
  │  Comms        │                                  │
  │  Files        │                                  │
  │  Maintenance  │                                  │
  │  Restrictions │                                  │
  └───────────────┴──────────────────────────────────┘
  │  Status bar: N/12 online | last action           │
  └──────────────────────────────────────────────────┘
"""

import customtkinter as ctk
from tkinter import messagebox, simpledialog, filedialog
import threading
import os
import logging
import time
import base64
import csv
import io
import mss

from datetime import datetime
from PIL import Image

from console.core.client_manager import ClientManager, ClientInfo
from console.core.wol import wake_all
from shared.protocol import CMD
from console.gui.machine_card import MachineCard
from console.gui.remote_view import RemoteControlWindow
from console.gui.backup_panel import BackupPanel
from console.gui.task_panel import TaskPanel

logger = logging.getLogger("gui.dashboard")

# ── Palette ────────────────────────────────────────────────────────────────────
BG_DARK  = "#0d0e1a"
BG_SIDE  = "#10121f"
BG_TOP   = "#0f1120"
BG_CARD  = "#14162a"
ACCENT   = "#4a9eff"
ACCENT_G = "#22cc88"
ACCENT_R = "#ff4a6a"
ACCENT_Y = "#ffb84a"
TEXT_PRI = "#ffffff"
TEXT_SEC = "#8899bb"
BORDER   = "#1e2240"

AUDIT_LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "audit_log.csv")


class Dashboard(ctk.CTk):

    def __init__(self, manager: ClientManager, config: dict):
        super().__init__()
        self.manager = manager
        # NOT self.config — every Tkinter widget already has a config() method
        # (the alias for configure()), and overwriting it with a dict turns any
        # internal self.config(...) call into "dict is not callable".
        self.app_config = config
        self._cards: dict[str, MachineCard]             = {}
        self._status_msg                                 = ""
        self._remote_windows: dict[str, RemoteControlWindow] = {}

        self.manager.on_status_change(self._on_client_status_change)

        self._build_ui()
        self._start_thumbnail_loop()
        self.after(500, self._refresh_status_bar)

    # ── UI Construction ────────────────────────────────────────────────────────

    def _build_ui(self):
        self.title("ClassroomOS — Dashboard")
        self.configure(fg_color=BG_DARK)
        self.state("zoomed")
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.minsize(1200, 700)

        self._build_topbar()

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True)
        content.grid_columnconfigure(1, weight=1)
        content.grid_rowconfigure(0, weight=1)

        self._build_sidebar(content)
        self._build_grid(content)
        self._build_statusbar()

    # ── Top Bar ────────────────────────────────────────────────────────────────

    def _build_topbar(self):
        bar = ctk.CTkFrame(self, fg_color=BG_TOP, height=64, corner_radius=0)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        logo = ctk.CTkFrame(bar, fg_color="transparent")
        logo.pack(side="left", padx=20)
        ctk.CTkLabel(logo, text="🖥️", font=ctk.CTkFont(size=24)).pack(side="left")
        ctk.CTkLabel(logo, text="ClassroomOS",
                     font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"),
                     text_color=TEXT_PRI).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(logo, text="v1.0",
                     font=ctk.CTkFont(size=10), text_color=TEXT_SEC).pack(side="left", padx=(4, 0))

        btn_frame = ctk.CTkFrame(bar, fg_color="transparent")
        btn_frame.pack(side="right", padx=20)

        top_actions = [
            ("🌅  Start of Day",  ACCENT_G,  self._start_of_day),
            ("🌙  End of Day",    "#5566aa", self._end_of_day),
            ("🔒  Lock All",      ACCENT_Y,  self._lock_all),
            ("🔓  Unlock All",    ACCENT_G,  self._unlock_all),
            ("🚨  Panic Reset",   ACCENT_R,  self._panic_reset),
        ]
        for text, color, cmd in top_actions:
            ctk.CTkButton(
                btn_frame, text=text,
                width=128, height=36,
                font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
                fg_color=color,
                hover_color=self._lighten(color),
                corner_radius=8,
                command=cmd,
            ).pack(side="left", padx=4)

    # ── Sidebar ────────────────────────────────────────────────────────────────

    def _build_sidebar(self, parent):
        side = ctk.CTkFrame(
            parent, fg_color=BG_SIDE,
            width=220, corner_radius=0,
            border_width=1, border_color=BORDER,
        )
        side.grid(row=0, column=0, sticky="ns")
        side.grid_propagate(False)

        sep = lambda: ctk.CTkFrame(side, height=1, fg_color=BORDER).pack(fill="x", padx=12, pady=8)  # noqa: E731

        self._sb_section(side, "⚡  Power")
        self._sb_btn(side, "🔆  Wake All (WOL)",      self._wake_all)
        self._sb_btn(side, "⏻   Shut Down All",       lambda: self._shutdown_all(restart=False))
        self._sb_btn(side, "🔄  Restart All",         lambda: self._shutdown_all(restart=True))
        self._sb_btn(side, "🚪  Log Off All",         self._logoff_all)
        sep()

        self._sb_section(side, "💬  Communication")
        self._sb_btn(side, "📢  Message All",         self._msg_all)
        self._sb_btn(side, "🧹  Dismiss Messages",    self._dismiss_msgs)
        self._sb_btn(side, "📡  Broadcast Screen",    self._broadcast_screen)
        self._sb_btn(side, "⏹   Stop Broadcast",      self._stop_broadcast)
        sep()

        self._sb_section(side, "📁  Files")
        self._sb_btn(side, "⬆️   Push File to All",   self._push_file_all)
        self._sb_btn(side, "⬇️   Collect Assignments", self._collect_all)
        sep()

        self._sb_section(side, "🔧  Maintenance")
        self._sb_btn(side, "🧹  Clean All",           self._cleanup_all)
        self._sb_btn(side, "📊  Health Report",       self._health_all)
        self._sb_btn(side, "🔍  Test WOL",            self._check_wol_all)
        self._sb_btn(side, "⏰  Scheduled Tasks",     self._open_task_panel)
        self._sb_btn(side, "💤  Auto-Logoff",         self._set_autologoff)
        self._sb_btn(side, "📋  Audit Log",           self._show_audit_log)
        sep()

        self._sb_section(side, "🚫  Restrictions")
        self._sb_btn(side, "🌐  Block Internet",      lambda: self._set_internet(False))
        self._sb_btn(side, "🌐  Allow Internet",      lambda: self._set_internet(True))
        self._sb_btn(side, "🔌  Block USB",           lambda: self._set_usb(False))
        self._sb_btn(side, "🔌  Allow USB",           lambda: self._set_usb(True))
        sep()

        self._sb_section(side, "📦  Backups")
        self._sb_btn(side, "📦  Backup Manager",      self._open_backup_panel)

    def _sb_section(self, parent, text: str):
        ctk.CTkLabel(
            parent, text=text,
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color=TEXT_SEC, anchor="w",
        ).pack(fill="x", padx=16, pady=(10, 2))

    def _sb_btn(self, parent, text: str, command) -> ctk.CTkButton:
        btn = ctk.CTkButton(
            parent, text=text,
            height=32, anchor="w",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            fg_color="transparent", hover_color="#1e2240",
            text_color=TEXT_PRI, corner_radius=6,
            command=self._guarded(command),
        )
        btn.pack(fill="x", padx=8, pady=1)
        return btn

    # ── Grid ───────────────────────────────────────────────────────────────────

    def _build_grid(self, parent):
        grid_outer = ctk.CTkScrollableFrame(parent, fg_color=BG_DARK, corner_radius=0)
        grid_outer.grid(row=0, column=1, sticky="nsew")

        grid_frame = ctk.CTkFrame(grid_outer, fg_color="transparent")
        grid_frame.pack(fill="both", expand=True, padx=16, pady=16)

        for col in range(4):
            grid_frame.grid_columnconfigure(col, weight=1, uniform="col")

        clients = list(self.manager.clients.values())
        for idx, client in enumerate(clients):
            row, col = divmod(idx, 4)
            card = MachineCard(
                grid_frame, client=client,
                on_remote_control=self._open_remote_control,
                on_shutdown=self._shutdown_one,
                on_restart=self._restart_one,
                on_message=self._message_one,
                on_lock=self._lock_one,
            )
            card.grid(row=row, column=col, padx=8, pady=8, sticky="nsew")
            self._cards[client.name] = card

    # ── Status Bar ─────────────────────────────────────────────────────────────

    def _build_statusbar(self):
        bar = ctk.CTkFrame(self, fg_color=BG_TOP, height=28, corner_radius=0)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)

        self._status_online_lbl = ctk.CTkLabel(
            bar, text="",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=TEXT_SEC,
        )
        self._status_online_lbl.pack(side="left", padx=16)

        self._status_action_lbl = ctk.CTkLabel(
            bar, text="",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=TEXT_SEC,
        )
        self._status_action_lbl.pack(side="right", padx=16)

    # ── Thumbnail Loop ─────────────────────────────────────────────────────────

    def _start_thumbnail_loop(self):
        """Background thread: fetch low-quality screenshots for grid thumbnails."""
        interval_ms = self.app_config.get("thumbnail_interval_ms", 3000)

        def _fetch_one(name: str, card: MachineCard, client: ClientInfo):
            try:
                resp = self.manager.send(client, CMD.SCREENSHOT, {"quality": 30})
                if resp and resp.get("status") == "ok":
                    img_b64 = resp["data"].get("image_b64", "")
                    if img_b64:
                        card.update_thumbnail(img_b64)
            except Exception as e:
                logger.debug(f"Thumbnail fetch error for {name}: {e}")

        def _loop():
            while True:
                threads = []
                for name, card in list(self._cards.items()):
                    client = self.manager.get_client(name)
                    if client and client.online:
                        t = threading.Thread(
                            target=_fetch_one,
                            args=(name, card, client),
                            daemon=True,
                        )
                        threads.append(t)
                        t.start()
                for t in threads:
                    t.join(timeout=5.0)
                time.sleep(interval_ms / 1000)

        threading.Thread(target=_loop, daemon=True, name="ThumbnailLoop").start()

    # ── Safe helpers ───────────────────────────────────────────────────────────

    def _safe_after(self, ms: int, func) -> None:
        """Schedule a tkinter callback only if this window still exists."""
        try:
            if self.winfo_exists():
                self.after(ms, func)
        except Exception:
            pass

    def _guarded(self, fn):
        """Wrap any sidebar/topbar callback so exceptions show a dialog, not a crash."""
        def _wrapper(*args, **kwargs):
            try:
                fn(*args, **kwargs)
            except Exception as e:
                logger.exception(f"Action error: {e}")
                try:
                    messagebox.showerror("Error", f"Action failed:\n{e}")
                except Exception:
                    pass
        return _wrapper

    def _on_client_status_change(self, client: ClientInfo):
        """Called from background thread — must route to main thread."""
        self._safe_after(0, lambda: self._update_card_status(client))

    def _update_card_status(self, client: ClientInfo):
        try:
            card = self._cards.get(client.name)
            if card:
                card.update_status(client)
            self._refresh_status_bar()
        except Exception as e:
            logger.debug(f"Card status update error: {e}")

    def _refresh_status_bar(self):
        try:
            if not self.winfo_exists():
                return
            n_on  = len(self.manager.online_clients)
            n_tot = len(self.manager.clients)
            color = ACCENT_G if n_on == n_tot else (ACCENT_Y if n_on > 0 else ACCENT_R)
            self._status_online_lbl.configure(
                text=f"● {n_on}/{n_tot} machines online",
                text_color=color,
            )
            if self._status_msg:
                self._status_action_lbl.configure(text=self._status_msg)
        except Exception:
            pass

    def _set_status(self, msg: str):
        """Update the action status label. Safe to call from any thread."""
        self._status_msg = f"[{datetime.now().strftime('%H:%M:%S')}]  {msg}"
        self._safe_after(0, self._refresh_status_bar)

    # ── Audit logging ──────────────────────────────────────────────────────────

    def _audit(self, action: str, target: str = "all", details: str = ""):
        try:
            ts = datetime.now().isoformat()
            os.makedirs(os.path.dirname(AUDIT_LOG_PATH), exist_ok=True)
            with open(AUDIT_LOG_PATH, "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow([ts, target, action, details])
            logger.info(f"[AUDIT] {action} → {target}  {details}")
        except Exception as e:
            logger.warning(f"Audit log write failed: {e}")

    # ── Global Actions ─────────────────────────────────────────────────────────

    def _start_of_day(self):
        if not messagebox.askyesno("Start of Day", "Send Wake-on-LAN to all machines?"):
            return
        try:
            results = wake_all(list(self.manager.clients.values()))
            ok = sum(1 for v in results.values() if v)
            self._set_status(f"WOL sent to {ok}/{len(results)} machines")
            self._audit("START_OF_DAY", "all", f"WOL ok={ok}")
        except Exception as e:
            messagebox.showerror("Error", f"Wake-on-LAN failed:\n{e}")

    def _end_of_day(self):
        if not messagebox.askyesno("End of Day", "Shut down all machines?\n(60-second delay)"):
            return
        try:
            self.manager.send_to_all(CMD.SHUTDOWN, {"delay": 60})
            self._set_status("Shutdown sent to all machines (60 s delay)")
            self._audit("END_OF_DAY", "all")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _lock_all(self):
        msg = simpledialog.askstring(
            "Lock All Screens",
            "Message to display on locked screens\n(leave blank for default):",
        )
        if msg is None:
            return  # User cancelled
        # Lock-screen message stays in Hebrew — it's student-facing
        lock_text = msg.strip() or "המסך נעול — המתן להוראות המורה"
        try:
            self.manager.send_to_all(CMD.LOCK_SCREEN, {"message": lock_text})
            self._set_status("All screens locked")
            self._audit("LOCK_ALL", "all", lock_text)
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _unlock_all(self):
        try:
            self.manager.send_to_all(CMD.UNLOCK_SCREEN)
            self._set_status("All screens unlocked")
            self._audit("UNLOCK_ALL", "all")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _panic_reset(self):
        if not messagebox.askyesno(
            "⚠  Panic Reset",
            "This will close ALL applications on ALL machines.\n\nContinue?",
            icon="warning",
        ):
            return
        try:
            self.manager.send_to_all(CMD.PANIC)
            self._set_status("Panic reset sent to all machines")
            self._audit("PANIC_RESET", "all")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _wake_all(self):
        try:
            results = wake_all(list(self.manager.clients.values()))
            ok = sum(1 for v in results.values() if v)
            self._set_status(f"WOL sent to {ok}/{len(results)} machines")
            self._audit("WAKE_ALL", "all")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _shutdown_all(self, restart: bool = False):
        action = "restart" if restart else "shut down"
        if not messagebox.askyesno("Confirm", f"Confirm {action} all machines? (30 s delay)"):
            return
        try:
            cmd  = CMD.RESTART if restart else CMD.SHUTDOWN
            verb = "Restart" if restart else "Shutdown"
            self.manager.send_to_all(cmd, {"delay": 30})
            self._set_status(f"{verb} sent to all machines (30 s delay)")
            self._audit(f"{'RESTART' if restart else 'SHUTDOWN'}_ALL", "all")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _logoff_all(self):
        if not messagebox.askyesno("Log Off All", "Log off all users?"):
            return
        try:
            self.manager.send_to_all(CMD.LOGOFF)
            self._set_status("Logged off all users")
            self._audit("LOGOFF_ALL", "all")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _msg_all(self):
        title = simpledialog.askstring("Message All", "Message title:")
        if title is None:
            return
        body = simpledialog.askstring("Message All", "Message body:")
        if body is None:
            return
        try:
            self.manager.send_to_all(CMD.SEND_MSG, {
                "title":   title or "Message from Teacher",
                "message": body,
                "timeout": 0,
            })
            self._set_status(f"Message sent to all: {title!r}")
            self._audit("MSG_ALL", "all", f"{title}: {body}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _dismiss_msgs(self):
        """
        Close teacher popups still on screen.

        Messages are sent with no timeout and students cannot close them
        themselves, so without this they stay up until the machine reboots.
        """
        try:
            self.manager.send_to_all(CMD.DISMISS_MSG)
            self._set_status("Messages dismissed on all machines")
            self._audit("DISMISS_MSG_ALL", "all")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _stop_broadcast(self):
        """Take the fullscreen broadcast overlay off the students' screens."""
        try:
            self.manager.send_to_all(CMD.STOP_BROADCAST)
            self._set_status("Broadcast stopped on all machines")
            self._audit("STOP_BROADCAST", "all")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _set_autologoff(self):
        current = self.app_config.get("autologoff_idle_minutes", 0)
        minutes = simpledialog.askinteger(
            "Auto-Logoff",
            "Log students off after this many minutes of inactivity.\n"
            "Use 0 to disable.\n\n"
            "A warning popup appears shortly before the logoff.",
            initialvalue=current,
            minvalue=0,
            maxvalue=480,
        )
        if minutes is None:
            return
        try:
            self.manager.send_to_all(CMD.SET_AUTOLOGOFF, {"minutes": minutes})
            self.app_config["autologoff_idle_minutes"] = minutes
            state = f"{minutes} min" if minutes else "disabled"
            self._set_status(f"Auto-logoff set to {state} on all machines")
            self._audit("SET_AUTOLOGOFF", "all", state)
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _open_task_panel(self):
        try:
            TaskPanel(self, self.manager)
        except Exception as e:
            messagebox.showerror("Error", f"Could not open Scheduled Tasks:\n{e}")

    def _broadcast_screen(self):
        """Capture the teacher's screen and broadcast it to all clients."""
        try:
            with mss.mss() as sct:
                shot = sct.grab(sct.monitors[1])
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=40)
            img_b64 = base64.b64encode(buf.getvalue()).decode()
        except Exception as e:
            messagebox.showerror("Screen Capture Failed", str(e))
            return
        try:
            self.manager.send_to_all(CMD.BROADCAST_FRAME, {"image_b64": img_b64})
            self._set_status("Teacher screen broadcast to all machines")
            self._audit("BROADCAST_SCREEN", "all")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _push_file_all(self):
        path = filedialog.askopenfilename(title="Select file to push")
        if not path:
            return
        filename = os.path.basename(path)
        dest = simpledialog.askstring(
            "Destination", "Destination folder (default: Desktop):"
        )
        dest = (dest or "Desktop").strip()
        try:
            with open(path, "rb") as f:
                data_b64 = base64.b64encode(f.read()).decode()
        except OSError as e:
            messagebox.showerror("File Error", f"Could not read file:\n{e}")
            return
        try:
            self.manager.send_to_all(CMD.PUSH_FILE, {
                "filename":  filename,
                "dest_path": dest,
                "data_b64":  data_b64,
            })
            self._set_status(f"'{filename}' pushed to all → {dest}")
            self._audit("PUSH_FILE_ALL", "all", f"{filename} → {dest}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _collect_all(self):
        folder = simpledialog.askstring(
            "Collect Assignments",
            "Submission folder on client machines (default: C:\\HandIn):",
        )
        folder = (folder or "C:\\HandIn").strip()
        save_dir = filedialog.askdirectory(title="Save collected files to...")
        if not save_dir:
            return

        # Compress toggle — default yes; say no for binary/media-heavy projects
        compress = messagebox.askyesno(
            "Compression",
            "Compress the collected archives? (ZIP_DEFLATED)\n\n"
            "Choose 'No' for faster transfer if the folder\n"
            "contains videos, images, or other pre-compressed files.",
            default="yes",
        )
        compresslevel = 6 if compress else None

        def _do():
            try:
                payload = {
                    "folder":        folder,
                    "compress":      compress,
                    "compresslevel": compresslevel or 0,
                }
                results = self.manager.send_to_all(CMD.COLLECT_FILES, payload)
                saved = 0
                for name, resp in results.items():
                    try:
                        if resp and resp.get("status") == "ok":
                            zip_b64 = resp["data"].get("zip_b64", "")
                            if zip_b64:
                                out = os.path.join(save_dir, f"{name}_handin.zip")
                                with open(out, "wb") as fh:
                                    fh.write(base64.b64decode(zip_b64))
                                saved += 1
                    except Exception as e:
                        logger.warning(f"Collect from {name} failed: {e}")
                algo = "compressed" if compress else "uncompressed"
                self._set_status(
                    f"Collected ({algo}) from {saved}/{len(results)} machines → {save_dir}"
                )
                self._audit("COLLECT_ALL", "all", f"{folder} → {save_dir} ({algo})")
            except Exception as e:
                logger.error(f"Collect error: {e}")
                self._set_status(f"Collect failed: {e}")

        threading.Thread(target=_do, daemon=True, name="CollectAll").start()
        self._set_status("Collecting assignments...")

    def _cleanup_all(self):
        opts: dict[str, bool] = {
            "temp_files":      True,
            "recycle_bin":     True,
            "downloads":       False,
            "browser_chrome":  False,
            "browser_edge":    False,
            "browser_firefox": False,
        }
        if messagebox.askyesno("Cleanup", "Also clean Downloads folder?"):
            opts["downloads"] = True
        if messagebox.askyesno("Cleanup", "Also clean browser data (Chrome, Edge, Firefox)?"):
            opts["browser_chrome"] = opts["browser_edge"] = opts["browser_firefox"] = True
        try:
            self.manager.send_to_all(CMD.CLEANUP, {"options": opts})
            self._set_status("Cleanup triggered on all machines")
            self._audit("CLEANUP_ALL", "all", str(opts))
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _health_all(self):
        def _do():
            try:
                results = self.manager.send_to_all(CMD.HEALTH_REPORT)
                self._safe_after(0, lambda: HealthReportWindow(self, results))
            except Exception as e:
                logger.error(f"Health report error: {e}")
                self._set_status(f"Health report failed: {e}")

        threading.Thread(target=_do, daemon=True, name="HealthReport").start()
        self._set_status("Fetching health reports...")

    def _check_wol_all(self):
        """Query all online machines for WOL NIC configuration."""
        def _do():
            try:
                results = self.manager.send_to_all(CMD.GET_INFO)
                lines = []
                for name, resp in results.items():
                    if resp and resp.get("status") == "ok":
                        wol = resp["data"].get("wol_enabled")
                        if wol is True:
                            icon = "✅"
                        elif wol is False:
                            icon = "⚠️  WOL DISABLED"
                        else:
                            icon = "❓  Unknown"
                        lines.append(f"{icon}  {name}")
                    else:
                        lines.append(f"🔴  {name}  (offline)")
                msg = "\n".join(lines) or "No machines online."
                self._safe_after(0, lambda: messagebox.showinfo("Wake-on-LAN Check", msg))
            except Exception as e:
                logger.error(f"WOL check error: {e}")

        threading.Thread(target=_do, daemon=True, name="WOLCheck").start()

    def _set_internet(self, enabled: bool):
        verb = "allow" if enabled else "block"
        if not messagebox.askyesno("Internet Access", f"Confirm {verb} internet on all machines?"):
            return
        try:
            self.manager.send_to_all(CMD.SET_INTERNET, {
                "enabled":    enabled,
                "console_ip": self.app_config.get("console_ip", ""),
            })
            state = "allowed" if enabled else "blocked"
            self._set_status(f"Internet {state} on all machines")
            self._audit(f"SET_INTERNET_{'ON' if enabled else 'OFF'}", "all")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _set_usb(self, enabled: bool):
        verb = "allow" if enabled else "block"
        if not messagebox.askyesno("USB Storage", f"Confirm {verb} USB storage on all machines?"):
            return
        try:
            self.manager.send_to_all(CMD.SET_USB, {"enabled": enabled})
            state = "allowed" if enabled else "blocked"
            self._set_status(f"USB storage {state} on all machines")
            self._audit(f"SET_USB_{'ON' if enabled else 'OFF'}", "all")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _show_audit_log(self):
        try:
            AuditLogWindow(self)
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _open_backup_panel(self):
        try:
            BackupPanel(self, self.manager)
        except Exception as e:
            messagebox.showerror("Error", f"Could not open Backup Manager:\n{e}")

    # ── Per-machine callbacks ──────────────────────────────────────────────────

    def _open_remote_control(self, client: ClientInfo):
        # If a window for this machine is already open, bring it forward
        existing = self._remote_windows.get(client.name)
        if existing:
            try:
                if existing.winfo_exists():
                    existing.lift()
                    return
            except Exception:
                pass
            self._remote_windows.pop(client.name, None)

        try:
            win = RemoteControlWindow(self, client, self.manager)
            self._remote_windows[client.name] = win

            def _on_close():
                self._remote_windows.pop(client.name, None)
                try:
                    win.destroy()
                except Exception:
                    pass

            win.protocol("WM_DELETE_WINDOW", _on_close)
            self._audit("REMOTE_CONTROL", client.name)
        except Exception as e:
            logger.error(f"Could not open remote control for {client.name}: {e}")
            messagebox.showerror(
                "Remote Control Error",
                f"Could not open remote view for {client.name}:\n{e}",
            )

    def _shutdown_one(self, client: ClientInfo):
        try:
            self.manager.send(client, CMD.SHUTDOWN, {"delay": 30})
            self._set_status(f"Shutdown: {client.name} (30 s)")
            self._audit("SHUTDOWN", client.name)
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _restart_one(self, client: ClientInfo):
        try:
            self.manager.send(client, CMD.RESTART, {"delay": 30})
            self._set_status(f"Restart: {client.name} (30 s)")
            self._audit("RESTART", client.name)
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _message_one(self, client: ClientInfo):
        title = simpledialog.askstring("Send Message", f"Title for {client.name}:")
        if title is None:
            return
        body = simpledialog.askstring("Send Message", "Message body:")
        if body is None:
            return
        try:
            self.manager.send(client, CMD.SEND_MSG, {
                "title":   title or "Message",
                "message": body,
                "timeout": 0,
            })
            self._set_status(f"Message sent to {client.name}")
            self._audit("SEND_MSG", client.name, f"{title}: {body}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _lock_one(self, client: ClientInfo):
        try:
            # Default lock message is student-facing → stays Hebrew
            self.manager.send(client, CMD.LOCK_SCREEN, {"message": "המסך נעול"})
            self._set_status(f"Screen locked: {client.name}")
            self._audit("LOCK", client.name)
        except Exception as e:
            messagebox.showerror("Error", str(e))

    # ── Utilities ──────────────────────────────────────────────────────────────

    @staticmethod
    def _lighten(hex_color: str) -> str:
        try:
            r = min(255, int(hex_color[1:3], 16) + 30)
            g = min(255, int(hex_color[3:5], 16) + 30)
            b = min(255, int(hex_color[5:7], 16) + 30)
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return hex_color

    def _on_close(self):
        if messagebox.askyesno("Exit", "Exit ClassroomOS?"):
            try:
                self.manager.stop()
            except Exception:
                pass
            self.destroy()


# ── Health Report Window ───────────────────────────────────────────────────────

class HealthReportWindow(ctk.CTkToplevel):

    def __init__(self, parent, results: dict):
        super().__init__(parent)
        self.title("Health Report")
        self.configure(fg_color=BG_DARK)
        self.geometry("920x600")
        self.grab_set()

        ctk.CTkLabel(
            self, text="Machine Health Report",
            font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"),
            text_color=TEXT_PRI,
        ).pack(pady=16)

        scroll = ctk.CTkScrollableFrame(self, fg_color=BG_CARD)
        scroll.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        headers    = ["Machine", "CPU %", "RAM %", "Free Disk (GB)", "Uptime (h)", "Pending Updates", "User"]
        col_widths = [100, 60, 60, 120, 100, 140, 120]

        hdr = ctk.CTkFrame(scroll, fg_color="#1a1d30")
        hdr.pack(fill="x", pady=(0, 4))
        for h, w in zip(headers, col_widths, strict=False):
            ctk.CTkLabel(hdr, text=h, width=w,
                         font=ctk.CTkFont(weight="bold", size=11),
                         text_color=ACCENT).pack(side="left", padx=4, pady=6)

        for name, resp in results.items():
            row = ctk.CTkFrame(scroll, fg_color="#14162a", corner_radius=6)
            row.pack(fill="x", pady=2)
            try:
                if resp and resp.get("status") == "ok":
                    d    = resp.get("data", {})
                    ram  = d.get("ram", {})
                    disk = (d.get("disks") or [{}])[0]
                    vals = [
                        name,
                        f"{d.get('cpu_percent', '—')}%",
                        f"{ram.get('percent', '—')}%",
                        f"{disk.get('free_gb', '—')}",
                        f"{d.get('uptime_hours', '—')}",
                        str(d.get("pending_updates", "—")),
                        d.get("user", "—"),
                    ]
                    color = TEXT_PRI
                else:
                    vals  = [name, "—", "—", "—", "—", "—", "Offline"]
                    color = TEXT_SEC
            except Exception:
                vals  = [name, "ERR", "ERR", "ERR", "ERR", "ERR", "ERR"]
                color = ACCENT_R

            for val, w in zip(vals, col_widths, strict=False):
                ctk.CTkLabel(row, text=str(val), width=w,
                             font=ctk.CTkFont(size=11),
                             text_color=color).pack(side="left", padx=4, pady=4)


# ── Audit Log Window ───────────────────────────────────────────────────────────

class AuditLogWindow(ctk.CTkToplevel):

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Admin Audit Log")
        self.configure(fg_color=BG_DARK)
        self.geometry("820x520")
        self.grab_set()

        ctk.CTkLabel(
            self, text="Admin Audit Log",
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"),
            text_color=TEXT_PRI,
        ).pack(pady=16)

        scroll = ctk.CTkScrollableFrame(self, fg_color=BG_CARD)
        scroll.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        if not os.path.exists(AUDIT_LOG_PATH):
            ctk.CTkLabel(scroll, text="No audit entries yet.", text_color=TEXT_SEC).pack(pady=20)
            return

        try:
            with open(AUDIT_LOG_PATH, encoding="utf-8") as f:
                rows = list(csv.reader(f))
        except Exception as e:
            ctk.CTkLabel(scroll, text=f"Could not read log: {e}", text_color=ACCENT_R).pack(pady=20)
            return

        for row_data in reversed(rows[-200:]):
            try:
                # Tolerate rows with fewer columns
                ts      = row_data[0][:19] if len(row_data) > 0 else "—"
                target  = row_data[1]      if len(row_data) > 1 else "—"
                action  = row_data[2]      if len(row_data) > 2 else "—"
                details = row_data[3]      if len(row_data) > 3 else ""

                row_frame = ctk.CTkFrame(scroll, fg_color="#14162a", corner_radius=4)
                row_frame.pack(fill="x", pady=1)

                ctk.CTkLabel(row_frame, text=ts,      width=140, text_color=TEXT_SEC,
                             font=ctk.CTkFont(size=10)).pack(side="left", padx=4, pady=3)
                ctk.CTkLabel(row_frame, text=action,  width=180, text_color=ACCENT,
                             font=ctk.CTkFont(size=11, weight="bold")).pack(side="left", padx=4)
                ctk.CTkLabel(row_frame, text=target,  width=90,  text_color=TEXT_PRI,
                             font=ctk.CTkFont(size=11)).pack(side="left", padx=4)
                ctk.CTkLabel(row_frame, text=details, text_color=TEXT_SEC,
                             font=ctk.CTkFont(size=10), anchor="w").pack(side="left", padx=4, fill="x")
            except Exception:
                continue  # Skip malformed rows
