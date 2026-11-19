"""Small, dependency-free persistence helpers for runtime JSON data."""
import json
import os
import shutil
import tempfile


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default


def atomic_write_json(path, payload, *, backup=False):
    """Write JSON atomically and optionally retain the previous file as .bak."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    if backup and os.path.isfile(path):
        try:
            shutil.copy2(path, path + ".bak")
        except OSError:
            pass
    fd, temporary_path = tempfile.mkstemp(suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(temporary_path, path)
    except Exception:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
        raise
