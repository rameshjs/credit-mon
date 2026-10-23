"""Snapshot written by the poller, read by the GNOME panel extension.

Default location is $XDG_RUNTIME_DIR (tmpfs, 0700, gone after reboot) so the
widget cannot display stale balances from days ago.

A system-level poller (see install-system.sh) writes to /run/credit-mon instead,
where the key is only readable by a dedicated system user. That mode sets
CREDIT_MON_STATE_DIR and CREDIT_MON_SHARED=1, which relaxes the permissions to
0755/0644 — balances are not secrets, the API key is.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def _shared() -> bool:
    return os.environ.get("CREDIT_MON_SHARED", "").strip() in ("1", "true", "yes")


def state_dir() -> Path:
    override = os.environ.get("CREDIT_MON_STATE_DIR")
    if override:
        return Path(override)
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    base = Path(runtime) if runtime else Path(f"/tmp/credit-mon-{os.getuid()}")
    return base / "credit-mon"


def state_path() -> Path:
    return state_dir() / "state.json"


def write(payload: dict) -> Path:
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o755 if _shared() else 0o700)
    target = state_path()
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".state-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1)
        os.chmod(tmp, 0o644 if _shared() else 0o600)
        os.replace(tmp, target)  # atomic: the extension never sees half a file
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return target


def read() -> dict | None:
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
