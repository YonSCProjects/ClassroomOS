"""
ClassroomOS — Agent Input Replay Handler
===========================================
Replays mouse and keyboard events received from the console,
enabling full remote control of the client machine.

Uses pyautogui for input simulation. Screen coordinates from the
console are sent as percentages (0.0–1.0) of screen dimensions
so they work correctly across different screen resolutions.
"""

import logging
import pyautogui

logger = logging.getLogger("handler.input_replay")

# Safety: disable pyautogui's failsafe (moving mouse to corner won't crash agent)
pyautogui.FAILSAFE = False
pyautogui.PAUSE    = 0  # No pause between actions (we handle timing on console side)

# Get screen dimensions once at startup
SCREEN_W, SCREEN_H = pyautogui.size()


def replay_mouse(payload: dict) -> None:
    """
    Replay a mouse event on this machine.

    Payload keys:
        action:  "move" | "click" | "double_click" | "right_click" | "scroll"
        x:       float (0.0–1.0, relative to screen width)
        y:       float (0.0–1.0, relative to screen height)
        button:  "left" | "right" | "middle"  (for click events)
        clicks:  int (default 1)
        scroll:  int (positive = up, negative = down, for scroll events)
    """
    action = payload.get("action", "move")
    # Convert relative coordinates to absolute pixels
    x = int(payload.get("x", 0.5) * SCREEN_W)
    y = int(payload.get("y", 0.5) * SCREEN_H)
    button = payload.get("button", "left")
    clicks = payload.get("clicks", 1)

    try:
        if action == "move":
            pyautogui.moveTo(x, y, duration=0)

        elif action == "click":
            pyautogui.click(x, y, button=button, clicks=clicks)

        elif action == "double_click":
            pyautogui.doubleClick(x, y)

        elif action == "right_click":
            pyautogui.rightClick(x, y)

        elif action == "mousedown":
            pyautogui.mouseDown(x, y, button=button)

        elif action == "mouseup":
            pyautogui.mouseUp(x, y, button=button)

        elif action == "scroll":
            amount = payload.get("scroll", 0)
            pyautogui.scroll(amount, x=x, y=y)

        else:
            logger.warning(f"Unknown mouse action: {action!r}")

    except Exception as e:
        logger.error(f"Mouse replay error: {e}")


# Key name mapping: console sends friendly names → pyautogui names
KEY_MAP = {
    "backspace": "backspace",
    "tab":       "tab",
    "enter":     "enter",
    "shift":     "shift",
    "ctrl":      "ctrl",
    "alt":       "alt",
    "pause":     "pause",
    "capslock":  "capslock",
    "escape":    "esc",
    "space":     "space",
    "pageup":    "pageup",
    "pagedown":  "pagedown",
    "end":       "end",
    "home":      "home",
    "left":      "left",
    "up":        "up",
    "right":     "right",
    "down":      "down",
    "delete":    "delete",
    "win":       "win",
    "f1":  "f1",  "f2":  "f2",  "f3":  "f3",  "f4":  "f4",
    "f5":  "f5",  "f6":  "f6",  "f7":  "f7",  "f8":  "f8",
    "f9":  "f9",  "f10": "f10", "f11": "f11", "f12": "f12",
}


def replay_key(payload: dict) -> None:
    """
    Replay a keyboard event.

    Payload keys:
        action:  "press" | "down" | "up" | "hotkey" | "type"
        key:     str  (key name, e.g. "a", "enter", "ctrl")
        keys:    list[str]  (for hotkey: e.g. ["ctrl", "c"])
        text:    str  (for type action: types a full string)
    """
    action = payload.get("action", "press")
    key    = payload.get("key", "")
    keys   = payload.get("keys", [])
    text   = payload.get("text", "")

    try:
        if action == "type" and text:
            pyautogui.write(text, interval=0.02)

        elif action == "hotkey" and keys:
            mapped = [KEY_MAP.get(k.lower(), k.lower()) for k in keys]
            pyautogui.hotkey(*mapped)

        elif action == "press" and key:
            mapped_key = KEY_MAP.get(key.lower(), key.lower())
            pyautogui.press(mapped_key)

        elif action == "down" and key:
            mapped_key = KEY_MAP.get(key.lower(), key.lower())
            pyautogui.keyDown(mapped_key)

        elif action == "up" and key:
            mapped_key = KEY_MAP.get(key.lower(), key.lower())
            pyautogui.keyUp(mapped_key)

        else:
            logger.warning(f"Unknown key action: {action!r}")

    except Exception as e:
        logger.error(f"Key replay error: {e}")
