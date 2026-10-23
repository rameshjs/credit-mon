"""Credential storage. Two backends, both better than an env var.

1. keyring  -> GNOME Keyring (libsecret / org.freedesktop.secrets), encrypted at rest
               with your login password, unlocked automatically during login.
               Preferred: the secret never touches disk in plaintext and is never
               exported into any process environment.
2. creds    -> systemd-creds --user --with-key=host. Ciphertext bound to this
               machine's host key, so a copied file is useless elsewhere.
               Used as fallback when the keyring is locked/unavailable
               (e.g. boot-time polling via systemd lingering, before login).

Nothing here is ever passed on a command line, so keys never appear in
`ps`, shell history, or journald.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import config_dir

KEYRING_SCHEMA_ID = "org.local.creditmon.api-key"
KEYRING_LABEL = "credit-mon: {provider} API key"
SYSTEM_CRED_DIR = "/etc/credit-mon/credentials"


class SecretError(RuntimeError):
    pass


# ---------------------------------------------------------------- keyring ---

def _schema():
    import gi

    gi.require_version("Secret", "1")
    from gi.repository import Secret

    return Secret.Schema.new(
        KEYRING_SCHEMA_ID,
        Secret.SchemaFlags.NONE,
        {"provider": Secret.SchemaAttributeType.STRING},
    )


def _keyring():
    import gi

    gi.require_version("Secret", "1")
    from gi.repository import Secret

    return Secret


def keyring_available() -> bool:
    try:
        _keyring()
        return True
    except (ImportError, ValueError):
        return False


def keyring_get(provider: str) -> str | None:
    try:
        Secret = _keyring()
        val = Secret.password_lookup_sync(_schema(), {"provider": provider}, None)
        return val or None
    except Exception as exc:  # locked keyring, no session bus, ...
        raise SecretError(f"keyring lookup failed: {exc}") from exc


def keyring_set(provider: str, secret: str) -> bool:
    Secret = _keyring()
    ok = Secret.password_store_sync(
        _schema(),
        {"provider": provider},
        Secret.COLLECTION_DEFAULT,
        KEYRING_LABEL.format(provider=provider),
        secret,
        None,
    )
    if not ok:
        raise SecretError("keyring refused to store the secret")
    return True


def keyring_delete(provider: str) -> bool:
    Secret = _keyring()
    return bool(Secret.password_clear_sync(_schema(), {"provider": provider}, None))


# ------------------------------------------------------------ systemd-creds ---

def creds_path(provider: str) -> Path:
    return config_dir() / "credentials" / f"{provider}.cred"


def creds_available() -> bool:
    return shutil.which("systemd-creds") is not None


# ------------------------------------------------ systemd credentials (root) ---

def systemd_credential_get(provider: str) -> str | None:
    """Key supplied by a *system* unit via LoadCredentialEncrypted=.

    systemd decrypts the sealed file with the root-only host/TPM key and hands
    the plaintext to the unit through a tmpfs directory readable only by that
    unit's user. The keying material is never available to an interactive
    session, so this is the only path where the secret is not readable by your
    own user account.
    """
    cred_dir = os.environ.get("CREDENTIALS_DIRECTORY")
    if not cred_dir:
        return None
    path = Path(cred_dir) / f"credit-mon-{provider}"
    for candidate in (path, Path(cred_dir) / provider):
        try:
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8").strip() or None
        except OSError:
            continue
    return None


def system_credentials_present() -> dict[str, bool]:
    """Which providers have a root-sealed credential on this machine."""
    base = Path(SYSTEM_CRED_DIR)
    out = {}
    for provider in ("deepseek", "zai"):
        out[provider] = (base / f"{provider}.cred").exists()
    return out


def creds_set(provider: str, secret: str) -> Path:
    if not creds_available():
        raise SecretError("systemd-creds not installed")
    path = creds_path(provider)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    name = f"credit-mon-{provider}"
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out.cred"
        proc = subprocess.run(
            ["systemd-creds", "--user", "encrypt", f"--name={name}",
             "--with-key=host", "-", str(out)],
            input=secret.encode(),
            capture_output=True,
        )
        if proc.returncode != 0:
            raise SecretError(
                f"systemd-creds encrypt failed: {proc.stderr.decode().strip()}"
            )
        shutil.move(str(out), path)
    path.chmod(0o600)
    return path


def creds_get(provider: str) -> str | None:
    path = creds_path(provider)
    if not path.exists():
        return None
    proc = subprocess.run(
        ["systemd-creds", "--user", "decrypt",
         f"--name=credit-mon-{provider}", "--with-key=host", str(path), "-"],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise SecretError(
            f"systemd-creds decrypt failed: {proc.stderr.decode().strip()}"
        )
    return proc.stdout.decode().strip() or None


def creds_delete(provider: str) -> bool:
    path = creds_path(provider)
    if path.exists():
        path.unlink()
        return True
    return False


# ---------------------------------------------------------------- facade ----

def get_key(provider: str) -> tuple[str | None, str | None]:
    """Return (secret, source) where source names the store that answered."""
    # 1. Root-sealed credential injected by a system unit (strongest; the key is
    #    never readable by an interactive login of this user).
    try:
        val = systemd_credential_get(provider)
        if val:
            return val, "systemd-credential"
    except OSError:
        pass

    errors = []
    if keyring_available():
        try:
            val = keyring_get(provider)
            if val:
                return val, "keyring"
        except SecretError as exc:
            errors.append(str(exc))
    if creds_available():
        try:
            val = creds_get(provider)
            if val:
                return val, "creds"
        except SecretError as exc:
            errors.append(str(exc))
    if errors:
        raise SecretError("; ".join(errors))
    return None, None


def set_key(provider: str, secret: str, backend: str = "keyring") -> str:
    if backend == "keyring":
        keyring_set(provider, secret)
        return "keyring"
    if backend == "creds":
        creds_set(provider, secret)
        return "creds"
    raise SecretError(f"unknown backend {backend!r} (use keyring|creds)")


def delete_key(provider: str) -> list[str]:
    removed = []
    if keyring_available():
        try:
            if keyring_delete(provider):
                removed.append("keyring")
        except SecretError:
            pass
    if creds_delete(provider):
        removed.append("creds")
    return removed


def describe(provider: str) -> dict:
    out = {"provider": provider, "keyring": False, "creds": creds_path(provider).exists()}
    if keyring_available():
        try:
            out["keyring"] = bool(keyring_get(provider))
        except SecretError as exc:
            out["keyring_error"] = str(exc)
    return out
