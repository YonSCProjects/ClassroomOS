"""
ClassroomOS — Agent Files Handler
=====================================
Handles bidirectional file transfer:
  - PUSH_FILE:      Console → Client (push a file/zip to any path)
  - COLLECT_FILES:  Client → Console (zip one or more folders and return them)

Compression is always used for the wire format (ZIP container), but the
compression *algorithm* is configurable:
  compress=True  (default) → ZIP_DEFLATED  — smaller, slower
  compress=False           → ZIP_STORED    — larger, but fast (good for
                                             already-compressed data like
                                             Minecraft worlds, video, etc.)
"""

import os
import base64
import zipfile
import io
import logging
from pathlib import Path

import session

logger = logging.getLogger("handler.files")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _student_home() -> Path:
    """
    Home directory of the student sitting at this machine.

    Deliberately not ``Path.home()``: as a service the agent runs as SYSTEM, so
    ``Path.home()`` is ``C:\\Windows\\system32\\config\\systemprofile`` and every
    file the teacher "pushed to the Desktop" would land somewhere no student can
    ever see.
    """
    profile = session.user_profile_dir()
    return Path(profile) if profile else Path.home()


def _resolve_dest(dest_path: str, filename: str) -> str:
    """Expand shortcut names like 'Desktop' to full paths."""
    home = _student_home()
    shortcuts = {
        "desktop":           home / "Desktop",
        "שולחן העבודה":     home / "Desktop",
        "documents":         home / "Documents",
        "downloads":         home / "Downloads",
        "handin":            Path("C:/HandIn"),
    }
    lowered = dest_path.strip().lower()
    if lowered in shortcuts:
        dest_path = str(shortcuts[lowered])

    # If dest_path looks like a directory (no extension), append filename
    if os.path.isdir(dest_path) or not os.path.splitext(dest_path)[1]:
        os.makedirs(dest_path, exist_ok=True)
        return os.path.join(dest_path, filename)
    else:
        os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
        return dest_path


def _zip_paths(
    paths: list[str],
    compress: bool = True,
    compresslevel: int = 6,
) -> tuple[bytes, int]:
    """
    Zip all files under the given paths into one in-memory archive.

    Args:
        paths:          List of file or directory paths to include.
        compress:       True → ZIP_DEFLATED, False → ZIP_STORED (fast, no compression).
        compresslevel:  Deflate compression level 1-9 (ignored when compress=False).

    Returns:
        (zip_bytes, total_file_count)
    """
    method = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    level  = compresslevel if compress else None

    buf        = io.BytesIO()
    file_count = 0

    open_kwargs: dict = {"compression": method, "allowZip64": True}
    if compress and level is not None:
        open_kwargs["compresslevel"] = level

    with zipfile.ZipFile(buf, "w", **open_kwargs) as zf:
        for path_str in paths:
            p = Path(path_str)
            if not p.exists():
                logger.warning(f"collect: path not found, skipping: {p}")
                continue

            if p.is_file():
                zf.write(p, p.name)
                file_count += 1
            elif p.is_dir():
                # Prefix with folder name so multiple roots don't collide
                root_name = p.name
                for item in p.rglob("*"):
                    if item.is_file():
                        try:
                            arcname = root_name / item.relative_to(p)
                            zf.write(item, arcname)
                            file_count += 1
                        except PermissionError:
                            logger.warning(f"collect: skipping locked file {item}")
                        except Exception as e:
                            logger.warning(f"collect: skipping {item}: {e}")

    return buf.getvalue(), file_count


# ── Public handlers ────────────────────────────────────────────────────────────

def receive_file(filename: str, dest_path: str, data_b64: str) -> dict:
    """
    Save a pushed file to *dest_path* on this machine.

    If the data is a ZIP (filename ends in .zip or contains multiple files),
    it is saved as-is; the console decides the format.

    Returns:
        {"path": str, "size_bytes": int}
    """
    full_path  = _resolve_dest(dest_path, filename)
    file_bytes = base64.b64decode(data_b64)

    with open(full_path, "wb") as f:
        f.write(file_bytes)

    logger.info(f"File received: {full_path} ({len(file_bytes):,} bytes)")
    return {"path": full_path, "size_bytes": len(file_bytes)}


def collect_folder(
    folder_path: str | list[str],
    compress: bool = True,
    compresslevel: int = 6,
) -> str:
    """
    ZIP the contents of one or more folders and return as base64.

    Args:
        folder_path:    A single path string OR a list of paths.
        compress:       True → DEFLATED (smaller), False → STORED (faster).
        compresslevel:  Deflate level 1-9 (ignored when compress=False).

    Returns:
        Base64-encoded ZIP archive. Empty string if nothing was found.
    """
    # Normalise to list
    if isinstance(folder_path, str):
        paths = [folder_path]
    else:
        paths = list(folder_path)

    # Create any missing folders so they exist for next time
    for p in paths:
        if not os.path.exists(p):
            logger.warning(f"collect: folder not found — creating: {p}")
            os.makedirs(p, exist_ok=True)

    zip_bytes, file_count = _zip_paths(paths, compress=compress, compresslevel=compresslevel)

    if file_count == 0:
        logger.info("collect: no files found in requested path(s)")
        return ""

    algo = "DEFLATED" if compress else "STORED"
    logger.info(
        f"collect: {file_count} files from {paths} → "
        f"{len(zip_bytes):,} bytes ({algo})"
    )
    return base64.b64encode(zip_bytes).decode("ascii")


def push_file_to_all_users(
    filename: str,
    data_b64: str,
    target_subfolder: str = "Desktop",
) -> dict:
    """
    Push a file to all user profiles' Desktop (or another subfolder).
    Useful when the logged-in user is unknown.

    Returns a dict of {profile_name: success_bool}
    """
    profiles_root = os.environ.get("SYSTEMDRIVE", "C:") + "\\Users"
    results: dict[str, bool] = {}
    file_bytes = base64.b64decode(data_b64)

    for profile in os.listdir(profiles_root):
        profile_dir = os.path.join(profiles_root, profile)
        if not os.path.isdir(profile_dir):
            continue
        # Skip system accounts
        if profile.lower() in ("default", "all users", "public", "default user"):
            continue
        dest_dir = os.path.join(profile_dir, target_subfolder)
        try:
            os.makedirs(dest_dir, exist_ok=True)
            out_path = os.path.join(dest_dir, filename)
            with open(out_path, "wb") as f:
                f.write(file_bytes)
            results[profile] = True
        except Exception as e:
            logger.warning(f"push_to_all_users: {profile} — {e}")
            results[profile] = False

    return results
