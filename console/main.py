"""
ClassroomOS — Console Entry Point
=====================================
Bootstraps the application:
  1. Load or create config
  2. Show login screen (or first-run password setup)
  3. On auth success, launch the main dashboard
"""

import sys
import os
import logging


# Add shared to path
BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR  = os.path.dirname(BASE_DIR)
SHARED    = os.path.join(ROOT_DIR, "shared")
sys.path.insert(0, SHARED)
sys.path.insert(0, ROOT_DIR)

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_PATH = os.path.join(BASE_DIR, "data", "console.log")
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("console")

import customtkinter as ctk
from console.core.auth import load_config, save_config, is_first_run

from console.core.client_manager import ClientManager
from console.gui.login import LoginWindow
from console.gui.dashboard import Dashboard


def main():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    config = load_config()

    # ── First run: set admin password ─────────────────────────────────────────
    if is_first_run(config):
        logger.info("First run detected — prompting for password setup")
        root = ctk.CTk()
        root.withdraw()
        win = LoginWindow(root, config, first_run=True)
        root.wait_window(win)
        config = win.app_config   # Updated with new password
        if not config.get("password_hash"):
            logger.warning("Password not set — exiting")
            sys.exit(0)
        save_config(config)
        root.destroy()

    # ── Login ─────────────────────────────────────────────────────────────────
    root = ctk.CTk()
    root.withdraw()
    login = LoginWindow(root, config, first_run=False)
    root.wait_window(login)

    if not login.authenticated:
        logger.info("Login cancelled or failed — exiting")
        sys.exit(0)

    root.destroy()

    # ── Main dashboard ────────────────────────────────────────────────────────
    manager = ClientManager(config)
    manager.load_clients()
    manager.start()

    app = Dashboard(manager, config)
    app.mainloop()

    manager.stop()
    logger.info("Console exited cleanly")


if __name__ == "__main__":
    main()
