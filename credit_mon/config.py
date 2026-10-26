"""Config loading with sane defaults.

Layering: DEFAULTS <- ~/.config/credit-mon/config.json
Secrets never live here (see secrets.py).
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

DEFAULTS: dict = {
    "refresh_minutes": 10,
    "request_timeout": 20,
    # Per-provider blocks. `enabled: false` keeps the row configured but idle.
    "providers": {
        "deepseek": {
            "enabled": True,
            "currency": None,  # None -> first currency the API reports (CNY or USD)
        },
        "moonshot": {
            "enabled": True,
            "region": "intl",  # intl -> api.moonshot.ai (USD) | cn -> api.moonshot.cn (CNY)
        },
        "zai": {
            "enabled": True,
            "region": "intl",  # intl -> api.z.ai | cn -> www.bigmodel.cn
            "show_quota": True,  # also read GLM Coding Plan 5h/weekly quota
        },
    },
    # Low-balance desktop notification, fires once per downward crossing.
    "alerts": {
        "enabled": True,
        "threshold": {"deepseek": 2.0, "moonshot": 5.0, "zai": 5.0},
    },
    # Panel rendering hints consumed by the GNOME extension.
    "panel": {
        "order": ["deepseek", "moonshot", "zai"],
        "show_provider_names": True,
        "low_balance": {"deepseek": 2.0, "moonshot": 5.0, "zai": 5.0},
        "accents": {"deepseek": "#4d6bfe", "moonshot": "#c084fc", "zai": "#2dd4bf"},
    },
}


def config_dir() -> Path:
    override = os.environ.get("CREDIT_MON_CONFIG_DIR")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "credit-mon"


def config_path() -> Path:
    return config_dir() / "config.json"


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    path = config_path()
    if path.exists():
        try:
            user = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(user, dict):
                cfg = _deep_merge(cfg, user)
        except (OSError, json.JSONDecodeError) as exc:
            # A broken config must not take down the poller.
            cfg["_config_error"] = f"{path}: {exc}"
    return cfg


def ensure_config_file() -> Path:
    path = config_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(DEFAULTS, indent=2) + "\n", encoding="utf-8")
        path.chmod(0o600)
    return path


def provider_cfg(cfg: dict, name: str) -> dict:
    return (cfg.get("providers") or {}).get(name) or {}


def enabled_providers(cfg: dict) -> list[str]:
    provs = cfg.get("providers") or {}
    return [n for n, p in provs.items() if (p or {}).get("enabled", True)]
