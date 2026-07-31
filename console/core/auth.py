"""
ClassroomOS — Console Auth Module
===================================
Handles admin password hashing and verification.
Password is stored as a bcrypt hash in config.json — never plaintext.
"""

import bcrypt
import json
import os
import socket
import logging

logger = logging.getLogger("auth")

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "config.json")


def detect_console_ip() -> str:
    """
    Best guess at this machine's LAN address.

    Agents need it to punch an exception through the "block all internet"
    firewall rule. Opening a UDP socket toward an off-subnet address makes
    Windows pick the interface it would actually route over, without sending
    anything — which is what we want on a lab network with no internet.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return ""


def hash_password(plain: str) -> str:
    """Hash a plaintext password with bcrypt. Returns the hash as a string."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Check a plaintext password against a stored bcrypt hash."""
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


def load_config() -> dict:
    """Load console config. Returns defaults if file doesn't exist."""
    defaults = {
        "password_hash": None,
        "shared_secret": None,
        "theme": "dark",
        "agent_port": 9000,
        "console_ip": "",
        "thumbnail_interval_ms": 2000,
        "stream_fps": 10,
        "autologoff_idle_minutes": 0,
    }
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        defaults.update(data)

    # Without this the SET_INTERNET exception rule is built with an empty
    # remote IP and the block rule ends up with no way back to the console.
    if not defaults.get("console_ip"):
        defaults["console_ip"] = detect_console_ip()
        logger.info(f"Detected console IP: {defaults['console_ip'] or 'unknown'}")

    return defaults


def save_config(config: dict) -> None:
    """Persist the config dict to disk."""
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def is_first_run(config: dict) -> bool:
    """True if no password has been set yet."""
    return not config.get("password_hash")


def set_password(config: dict, plain: str) -> dict:
    """
    Hash *plain* and store it (plus a derived shared secret) in *config*.
    The shared_secret is what agents use to verify commands.
    Returns the updated config.
    """
    import hashlib
    config["password_hash"] = hash_password(plain)
    # Derive a stable shared secret from the password (not the hash itself,
    # but a deterministic HMAC key so agents can be updated without re-hashing)
    config["shared_secret"] = hashlib.sha256(plain.encode()).hexdigest()
    return config
