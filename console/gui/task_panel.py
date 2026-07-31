"""
ClassroomOS — Scheduled Tasks Panel
======================================
Front end for the agent-side maintenance queue (`agent/scheduler.py`).

The teacher defines a job here — "clean temp files every night at 02:00",
"restart the lab every Monday at 07:00" — and it is pushed to the selected
machines, where it is persisted to disk. The agent runs it even with the
console closed, and catches up on the run if the machine happened to be
switched off when the slot came round.

All network calls run on daemon threads; the window never blocks.
"""

import customtkinter as ctk
from tkinter import messagebox
import threading
import logging

from console.core.client_manager import ClientManager
from shared.protocol import CMD

logger = logging.getLogger("gui.task_panel")

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

# Label → (command, default payload). Kept in step with the agent's
# ALLOWED_TASK_COMMANDS: anything needing a logged-in student is excluded,
# because these run at times when nobody is sitting at the machine.
TASK_TYPES = {
    "Clean temp files + recycle bin": (
        CMD.CLEANUP,
        {"options": {"temp_files": True, "recycle_bin": True,
                     "downloads": False, "browser_chrome": False,
                     "browser_edge": False, "browser_firefox": False}},
    ),
    "Deep clean (incl. downloads + browsers)": (
        CMD.CLEANUP,
        {"options": {"temp_files": True, "recycle_bin": True,
                     "downloads": True, "browser_chrome": True,
                     "browser_edge": True, "browser_firefox": True}},
    ),
    "Restart the machine": (CMD.RESTART, {"delay": 60}),
    "Shut the machine down":  (CMD.SHUTDOWN, {"delay": 60}),
    "Log the student off":    (CMD.LOGOFF, {}),
    "Run the configured backup": (CMD.BACKUP_NOW, {}),
}

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday",
            "Friday", "Saturday", "Sunday"]


class TaskPanel(ctk.CTkToplevel):

    def __init__(self, parent, manager: ClientManager):
        super().__init__(parent)
        self.manager = manager

        self.title("Scheduled Tasks")
        self.configure(fg_color=BG_DARK)
        self.geometry("880x660")
        self.minsize(780, 600)
        self.grab_set()

        self._build_ui()
        self._refresh_tasks()

    # ── UI ─────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        hdr = ctk.CTkFrame(self, fg_color=BG_TOP, height=56)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(
            hdr, text="⏰  Scheduled Tasks",
            font=ctk.CTkFont(family="Segoe UI", size=17, weight="bold"),
            text_color=TEXT_PRI,
        ).pack(side="left", padx=20, pady=12)

        body = ctk.CTkScrollableFrame(self, fg_color=BG_DARK)
        body.pack(fill="both", expand=True)

        ctk.CTkLabel(
            body,
            text="Tasks are stored on each client and run even when this console is closed.\n"
                 "If a machine is switched off at the scheduled time, the task runs at next boot.",
            font=ctk.CTkFont(size=11), text_color=TEXT_SEC,
            justify="left", anchor="w",
        ).pack(fill="x", padx=24, pady=(14, 10))

        # ── New task ──────────────────────────────────────────────────────────
        ctk.CTkLabel(body, text="Create / Update a Task",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=TEXT_PRI, anchor="w").pack(fill="x", padx=24, pady=(4, 6))

        form = ctk.CTkFrame(body, fg_color=BG_CARD, corner_radius=8)
        form.pack(fill="x", padx=24, pady=(0, 12))

        self._id_entry = self._row_entry(form, "Task name", "nightly-cleanup")

        type_row = self._new_row(form, "What to run")
        self._type_var = ctk.StringVar(value=list(TASK_TYPES)[0])
        ctk.CTkOptionMenu(
            type_row, values=list(TASK_TYPES), variable=self._type_var,
            fg_color=BG_INPUT, button_color=BORDER, button_hover_color=ACCENT,
            text_color=TEXT_PRI, font=ctk.CTkFont(size=12), width=340,
        ).pack(side="left")

        interval_row = self._new_row(form, "How often")
        self._interval_var = ctk.StringVar(value="daily")
        for val, label in [("daily", "Daily"), ("weekly", "Weekly"), ("manual", "Manual only")]:
            ctk.CTkRadioButton(
                interval_row, text=label, value=val, variable=self._interval_var,
                font=ctk.CTkFont(size=12), text_color=TEXT_PRI,
                fg_color=ACCENT, border_color=BORDER,
                command=self._on_interval_change,
            ).pack(side="left", padx=(0, 16))

        time_row = self._new_row(form, "At what time")
        self._hour_entry = ctk.CTkEntry(time_row, width=52, height=30, fg_color=BG_INPUT,
                                        text_color=TEXT_PRI, border_color=BORDER,
                                        font=ctk.CTkFont(size=12))
        self._hour_entry.insert(0, "2")
        self._hour_entry.pack(side="left")
        ctk.CTkLabel(time_row, text=":", text_color=TEXT_SEC).pack(side="left", padx=3)
        self._minute_entry = ctk.CTkEntry(time_row, width=52, height=30, fg_color=BG_INPUT,
                                          text_color=TEXT_PRI, border_color=BORDER,
                                          font=ctk.CTkFont(size=12))
        self._minute_entry.insert(0, "00")
        self._minute_entry.pack(side="left")
        self._weekday_var = ctk.StringVar(value=WEEKDAYS[0])
        self._weekday_menu = ctk.CTkOptionMenu(
            time_row, values=WEEKDAYS, variable=self._weekday_var,
            fg_color=BG_INPUT, button_color=BORDER, button_hover_color=ACCENT,
            text_color=TEXT_PRI, font=ctk.CTkFont(size=12), width=130,
        )

        catchup_row = self._new_row(form, "If the PC was off")
        self._catchup_var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(
            catchup_row, text="Run the task at next boot",
            variable=self._catchup_var, onvalue=True, offvalue=False,
            font=ctk.CTkFont(size=12), text_color=TEXT_PRI,
            fg_color=BORDER, progress_color=ACCENT,
        ).pack(side="left")

        target_row = self._new_row(form, "Apply to")
        self._target_var = ctk.StringVar(value="all")
        ctk.CTkRadioButton(target_row, text="All machines", value="all",
                           variable=self._target_var, font=ctk.CTkFont(size=12),
                           text_color=TEXT_PRI, fg_color=ACCENT,
                           border_color=BORDER).pack(side="left", padx=(0, 16))
        ctk.CTkRadioButton(target_row, text="Online machines only", value="online",
                           variable=self._target_var, font=ctk.CTkFont(size=12),
                           text_color=TEXT_PRI, fg_color=ACCENT,
                           border_color=BORDER).pack(side="left")

        btn_row = ctk.CTkFrame(form, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(6, 14))
        ctk.CTkButton(btn_row, text="💾  Save Task", height=38, width=150,
                      fg_color=ACCENT_G, hover_color="#33dd99",
                      font=ctk.CTkFont(size=13, weight="bold"),
                      command=self._save_task).pack(side="left", padx=(0, 10))
        ctk.CTkButton(btn_row, text="▶  Run Now", height=38, width=130,
                      fg_color=ACCENT, hover_color="#6ab4ff",
                      font=ctk.CTkFont(size=13),
                      command=self._run_now).pack(side="left", padx=(0, 10))
        ctk.CTkButton(btn_row, text="🗑  Delete Task", height=38, width=140,
                      fg_color=ACCENT_R, hover_color="#ff6080",
                      font=ctk.CTkFont(size=13),
                      command=self._delete_task).pack(side="left")

        # ── Existing tasks ────────────────────────────────────────────────────
        header_row = ctk.CTkFrame(body, fg_color="transparent")
        header_row.pack(fill="x", padx=24, pady=(4, 4))
        ctk.CTkLabel(header_row, text="Tasks on Machines",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=TEXT_PRI).pack(side="left")
        ctk.CTkButton(header_row, text="⟳ Refresh", width=90, height=28,
                      fg_color="#223355", hover_color="#334466",
                      font=ctk.CTkFont(size=11),
                      command=self._refresh_tasks).pack(side="right")

        self._task_list = ctk.CTkFrame(body, fg_color=BG_CARD, corner_radius=8)
        self._task_list.pack(fill="both", expand=True, padx=24, pady=(0, 16))

        self._status_lbl = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=11),
            text_color=TEXT_SEC, anchor="w",
        )
        self._status_lbl.pack(fill="x", padx=16, pady=4)

        self._on_interval_change()

    def _new_row(self, parent, label: str):
        """A labelled form row; returns the frame to place the control in."""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=5)
        ctk.CTkLabel(row, text=label, width=130, anchor="w",
                     font=ctk.CTkFont(size=12), text_color=TEXT_SEC).pack(side="left")
        return row

    def _row_entry(self, parent, label: str, placeholder: str):
        row = self._new_row(parent, label)
        entry = ctk.CTkEntry(row, placeholder_text=placeholder, width=340, height=32,
                             fg_color=BG_INPUT, text_color=TEXT_PRI,
                             border_color=BORDER, font=ctk.CTkFont(size=12))
        entry.pack(side="left")
        return entry

    def _on_interval_change(self):
        """Weekly needs a weekday; daily and manual do not."""
        interval = self._interval_var.get()
        if interval == "weekly":
            self._weekday_menu.pack(side="left", padx=(12, 0))
        else:
            self._weekday_menu.pack_forget()

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _targets(self):
        clients = list(self.manager.clients.values())
        if self._target_var.get() == "online":
            clients = [c for c in clients if c.online]
        return clients

    def _set_status(self, msg: str):
        self._safe_after(0, lambda: self._status_lbl.configure(text=msg))

    def _safe_after(self, ms: int, fn):
        try:
            if self.winfo_exists():
                self.after(ms, fn)
        except Exception:
            pass

    def _read_form(self) -> dict | None:
        task_id = self._id_entry.get().strip()
        if not task_id:
            messagebox.showwarning("Task Name", "Give the task a name first.")
            return None

        try:
            hour = int(self._hour_entry.get().strip() or 0)
            minute = int(self._minute_entry.get().strip() or 0)
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid Time", "Hour must be 0–23 and minute 0–59.")
            return None

        cmd, task_payload = TASK_TYPES[self._type_var.get()]
        interval = self._interval_var.get()

        return {
            "id":       task_id,
            "cmd":      cmd,
            "payload":  task_payload,
            "interval": interval,
            "hour":     hour,
            "minute":   minute,
            "weekday":  WEEKDAYS.index(self._weekday_var.get()),
            "enabled":  interval != "manual",
            "catch_up": bool(self._catchup_var.get()),
        }

    def _broadcast(self, cmd: str, payload: dict, verb: str):
        clients = self._targets()
        if not clients:
            messagebox.showwarning("No Machines", "No machines match the selected target.")
            return

        def _do():
            ok = 0
            errors: list[str] = []
            for client in clients:
                try:
                    resp = self.manager.send(client, cmd, payload)
                    if resp and resp.get("status") == "ok":
                        ok += 1
                    elif resp:
                        errors.append(f"{client.name}: {resp.get('error', 'failed')}")
                except Exception as e:
                    errors.append(f"{client.name}: {e}")

            self._set_status(f"{verb} on {ok}/{len(clients)} machines")
            if errors:
                detail = "\n".join(errors[:8])
                self._safe_after(0, lambda: messagebox.showwarning(
                    verb, f"{verb} succeeded on {ok}/{len(clients)}.\n\n{detail}"))
            self._safe_after(0, self._refresh_tasks)

        threading.Thread(target=_do, daemon=True, name="TaskBroadcast").start()
        self._set_status(f"{verb}...")

    # ── Actions ────────────────────────────────────────────────────────────────

    def _save_task(self):
        payload = self._read_form()
        if payload:
            self._broadcast(CMD.SCHEDULE_TASK, payload, "Task saved")

    def _delete_task(self):
        task_id = self._id_entry.get().strip()
        if not task_id:
            messagebox.showwarning("Task Name", "Enter the name of the task to delete.")
            return
        if not messagebox.askyesno("Delete Task", f"Delete task '{task_id}' from the selected machines?"):
            return
        self._broadcast(CMD.DELETE_TASK, {"id": task_id}, "Task deleted")

    def _run_now(self):
        task_id = self._id_entry.get().strip()
        if not task_id:
            messagebox.showwarning("Task Name", "Enter the name of the task to run.")
            return
        if not messagebox.askyesno("Run Now", f"Run task '{task_id}' immediately?"):
            return
        self._broadcast(CMD.RUN_TASK_NOW, {"id": task_id}, "Task run")

    def _refresh_tasks(self):
        def _do():
            results = self.manager.send_to_all(CMD.LIST_TASKS)
            rows = []
            for name, resp in results.items():
                if not resp or resp.get("status") != "ok":
                    continue
                for task in (resp.get("data") or {}).get("tasks", []):
                    rows.append((name, task))
            self._safe_after(0, lambda: self._render_tasks(rows))

        threading.Thread(target=_do, daemon=True, name="ListTasks").start()
        self._set_status("Loading tasks...")

    def _render_tasks(self, rows: list):
        for w in self._task_list.winfo_children():
            w.destroy()

        if not rows:
            ctk.CTkLabel(self._task_list, text="No scheduled tasks found on any online machine.",
                         font=ctk.CTkFont(size=11), text_color=TEXT_SEC).pack(pady=18)
            self._set_status("No tasks found")
            return

        header = ctk.CTkFrame(self._task_list, fg_color="#1a1d30")
        header.pack(fill="x", pady=(0, 2))
        for text, width in [("Machine", 90), ("Task", 150), ("Command", 120),
                            ("When", 110), ("Next run", 140), ("Last result", 110)]:
            ctk.CTkLabel(header, text=text, width=width, anchor="w",
                         font=ctk.CTkFont(size=11, weight="bold"),
                         text_color=ACCENT).pack(side="left", padx=4, pady=6)

        for machine, task in sorted(rows, key=lambda r: (r[0], r[1].get("id", ""))):
            row = ctk.CTkFrame(self._task_list, fg_color="transparent")
            row.pack(fill="x", pady=1)

            interval = task.get("interval", "?")
            when = interval
            if interval == "daily":
                when = f"daily {task.get('hour', 0):02d}:{task.get('minute', 0):02d}"
            elif interval == "weekly":
                day = WEEKDAYS[task.get("weekday", 0) % 7][:3]
                when = f"{day} {task.get('hour', 0):02d}:{task.get('minute', 0):02d}"

            next_due = (task.get("next_due") or "—")[:16].replace("T", " ")
            result = task.get("last_result") or "—"
            if not task.get("enabled", True):
                result = "disabled"

            color = ACCENT_R if str(result).startswith("error") else (
                ACCENT_Y if result in ("disabled", "—") else TEXT_PRI
            )

            for text, width, col in [
                (machine, 90, TEXT_SEC),
                (task.get("id", "?"), 150, TEXT_PRI),
                (task.get("cmd", "?"), 120, TEXT_SEC),
                (when, 110, TEXT_SEC),
                (next_due, 140, TEXT_SEC),
                (str(result)[:24], 110, color),
            ]:
                ctk.CTkLabel(row, text=text, width=width, anchor="w",
                             font=ctk.CTkFont(size=11),
                             text_color=col).pack(side="left", padx=4, pady=3)

        self._set_status(f"{len(rows)} task(s) across the lab")
