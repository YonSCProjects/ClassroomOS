"""
ClassroomOS — Handler Loader
===============================
Lazy, cached, failure-tolerant loading of the modules in ``agent/handlers/``.

Handlers are imported on first use rather than at startup so that a missing
optional dependency (no ``pyautogui``, no ``mss``) disables exactly one feature
instead of preventing the agent from starting at all. A handler that fails to
import is remembered as ``None`` so we do not retry the import on every command.

Both the agent process and the in-session UI helper resolve handlers through
this module, so they share one cache and one failure policy.
"""

import importlib
import logging
import threading

logger = logging.getLogger("handler_loader")

_cache: dict[str, object | None] = {}
_lock = threading.Lock()


def get_handler(name: str):
    """
    Import ``handlers.<name>`` and return the module, or None if unavailable.
    The result (including failure) is cached.
    """
    with _lock:
        if name in _cache:
            return _cache[name]

    module = None
    try:
        module = importlib.import_module(f"handlers.{name}")
    except ImportError as e:
        logger.error(f"Cannot load handler '{name}': {e}")
    except Exception as e:
        # A handler that raises at import time (e.g. pyautogui failing to reach
        # a display) must not take the agent down with it.
        logger.error(f"Handler '{name}' failed to initialise: {e}")

    with _lock:
        _cache[name] = module
    return module


def reset_cache() -> None:
    """Forget cached handlers so the next call re-attempts the import."""
    with _lock:
        _cache.clear()
