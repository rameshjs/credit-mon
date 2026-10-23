"""Command line entry point.

    credit-mon refresh            poll providers, write the panel snapshot
    credit-mon keys set PROVIDER  store an API key (prompted, never echoed)
    credit-mon keys list          show which backends hold a key
    credit-mon keys delete PROVIDER
    credit-mon status             pretty-print the current snapshot
    credit-mon doctor             check environment, storage and reachability
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

from . import __version__, config as cfg_mod, history, secrets, state
from .providers import REGISTRY, ProviderError, Reading, fmt_duration, fmt_money, get_provider

GREEN, RED, YELLOW, DIM, BOLD, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m"
)


def _color(text: str, code: str, enabled: bool) -> str:
    return f"{code}{text}{RESET}" if enabled else text


def _use_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


# ------------------------------------------------------------------ refresh ---

def _notify(title: str, body: str) -> None:
    if shutil.which("notify-send"):
        try:
            subprocess.run(
                ["notify-send", "-a", "credit-mon", "-u", "normal", "--", title, body],
                check=False, timeout=10,
            )
            return
        except (OSError, subprocess.SubprocessError):
            pass
    print(f"{title}: {body}", file=sys.stderr)


def _alerts_path():
    return history.history_path().parent / "alerts.json"


def _load_alerts() -> dict:
    try:
        return json.loads(_alerts_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_alerts(data: dict) -> None:
    try:
        path = _alerts_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError:
        pass


def _handle_alert(name: str, read: Reading, cfg: dict, alerts: dict) -> None:
    alerts_cfg = cfg.get("alerts") or {}
    if not alerts_cfg.get("enabled", True):
        return
    threshold = (alerts_cfg.get("threshold") or {}).get(name)
    if threshold is None or read.total is None:
        return
    prev = alerts.get(name) or {}
    below = read.total < float(threshold)
    if below and not prev.get("below"):
        _notify(
            f"{REGISTRY[name].display if name in REGISTRY else name} balance low",
            f"{fmt_money(read.total, read.currency)} left (threshold {fmt_money(float(threshold), read.currency)})",
        )
    if prev.get("below") != below:
        alerts[name] = {"below": below, "since": time.time()}
        _save_alerts(alerts)


def poll(cfg: dict, verbose: bool = False) -> dict:
    timeout = int(cfg.get("request_timeout") or 20)
    alerts = _load_alerts()
    readings: list[dict] = []

    for name in cfg_mod.enabled_providers(cfg):
        pcfg = cfg_mod.provider_cfg(cfg, name)
        if name not in REGISTRY:
            readings.append(Reading(provider=name, ok=False, error="unknown provider").as_dict())
            continue
        provider = get_provider(name)
        try:
            key, source = secrets.get_key(name)
        except secrets.SecretError as exc:
            readings.append(
                Reading(provider=name, ok=False, error=f"credential store: {exc}").as_dict()
            )
            continue
        if not key:
            readings.append(
                Reading(
                    provider=name, ok=False,
                    error=f"no API key stored — run: credit-mon keys set {name}",
                    dashboard=provider.dashboard,
                ).as_dict()
            )
            continue

        try:
            read = provider.fetch(pcfg, key, timeout)
            stats = history.stats(name, read.total)
            read.spend_today = stats["spend_today"]
            read.burn_per_day = stats["burn_per_day"]
            read.days_left = stats["days_left"]
            history.record(name, read.total, read.currency)
            read.spark = history.recent(name)
            _handle_alert(name, read, cfg, alerts)
            if verbose:
                print(f"  {name}: {read.headline} (key from {source})")
        except ProviderError as exc:
            read = Reading(provider=name, ok=False, error=str(exc), dashboard=provider.dashboard)
            if verbose:
                print(f"  {name}: FAILED {exc}")
        readings.append(read.as_dict())

    panel_cfg = cfg.get("panel") or {}
    order = [n for n in (panel_cfg.get("order") or []) if any(r["provider"] == n for r in readings)]
    order += [r["provider"] for r in readings if r["provider"] not in order]
    readings.sort(key=lambda r: order.index(r["provider"]))

    payload = {
        "schema": 1,
        "version": __version__,
        "generated_at": time.time(),
        "refresh_seconds": int(cfg.get("refresh_minutes") or 10) * 60,
        "panel": {
            "show_provider_names": panel_cfg.get("show_provider_names", True),
            "low_balance": panel_cfg.get("low_balance") or {},
        },
        "providers": readings,
    }
    state.write(payload)
    return payload


def cmd_refresh(args) -> int:
    cfg = cfg_mod.load()
    if verbose := getattr(args, "verbose", False):
        print(f"polling {len(cfg_mod.enabled_providers(cfg))} provider(s)")
    payload = poll(cfg, verbose)
    if getattr(args, "print_json", False):
        print(json.dumps(payload, indent=2))
    failed = [r for r in payload["providers"] if not r["ok"]]
    if payload["providers"] and len(failed) == len(payload["providers"]):
        # Report, but exit 0: the panel shows per-provider errors and the timer
        # should not accumulate systemd failures while keys are unconfigured.
        print("all providers failed: " +
              "; ".join(f"{r['provider']}: {r['error']}" for r in failed), file=sys.stderr)
    return 0


# --------------------------------------------------------------------- keys ---

def cmd_keys_set(args) -> int:
    name = args.provider
    if name not in REGISTRY:
        print(f"unknown provider {name!r}; known: {', '.join(REGISTRY)}", file=sys.stderr)
        return 2
    backend = args.backend
    if backend == "keyring" and not secrets.keyring_available():
        print("keyring backend unavailable (missing python3-gi Secret typelib); "
              "falling back to systemd-creds", file=sys.stderr)
        backend = "creds"
    if sys.stdin.isatty():
        print(f"Paste the API key for {REGISTRY[name].display} "
              f"(input hidden, stored in {backend}).")
    key = getpass.getpass("API key: ").strip()
    if not key:
        print("empty key, nothing stored", file=sys.stderr)
        return 2
    try:
        used = secrets.set_key(name, key, backend)
    except secrets.SecretError as exc:
        print(f"failed to store key: {exc}", file=sys.stderr)
        return 1
    print(f"stored {name} key in {used} ({len(key)} chars, prefix {key[:4]}…)")
    print("run `credit-mon refresh` to populate the panel")
    return 0


def cmd_keys_list(args) -> int:
    color = _use_color()
    system = secrets.system_credentials_present()
    for name in REGISTRY:
        row = secrets.describe(name)
        where = []
        if system.get(name):
            where.append("root-sealed (system unit)")
        if row["keyring"]:
            where.append("keyring")
        if row["creds"]:
            where.append("systemd-creds")
        status = _color(", ".join(where), GREEN, color) if where else _color("none", RED, color)
        print(f"  {row['provider']:<10} {status}")
        if "keyring_error" in row:
            print(f"  {'':<10} {_color(row['keyring_error'], YELLOW, color)}")
    return 0


def cmd_keys_delete(args) -> int:
    removed = secrets.delete_key(args.provider)
    if removed:
        print(f"removed {args.provider} key from {', '.join(removed)}")
        return 0
    print(f"no stored key for {args.provider}", file=sys.stderr)
    return 1


# ------------------------------------------------------------------- status ---

def cmd_status(args) -> int:
    color = _use_color()
    snap = state.read()
    if not snap:
        print("no snapshot yet — run: credit-mon refresh", file=sys.stderr)
        return 1
    age = time.time() - snap.get("generated_at", 0)
    print(_color(f"snapshot {fmt_duration(age)} old  ({state.state_path()})", DIM, color))
    for row in snap.get("providers", []):
        head = _color(f"{row['provider']:<10}", BOLD, color)
        if not row.get("ok"):
            print(f"{head} {_color('FAIL', RED, color)}  {row.get('error')}")
            continue
        print(f"{head} {_color(row.get('headline', '—'), GREEN, color)}")
        for k, v in row.get("rows", []):
            print(f"{'':<11} {_color(k + ':', DIM, color):<28} {v}")
        extra = []
        if row.get("spend_today") is not None:
            extra.append(f"today {fmt_money(row['spend_today'], row.get('currency'))}")
        if row.get("burn_per_day"):
            extra.append(f"burn {fmt_money(row['burn_per_day'], row.get('currency'))}/day")
        if row.get("days_left") is not None:
            extra.append(f"~{row['days_left']:.0f}d left")
        if extra:
            print(f"{'':<11} {_color(' · '.join(extra), DIM, color)}")
        for warn in row.get("warnings", []):
            print(f"{'':<11} {_color('warn: ' + warn, YELLOW, color)}")
    return 0


# ------------------------------------------------------------------- doctor ---

def _reach(url: str, timeout: int = 8) -> tuple[bool, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "credit-mon/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        return True, f"HTTP {exc.code} (reachable)"
    except Exception as exc:  # noqa: BLE001 - diagnostics only
        return False, str(exc)


def cmd_doctor(args) -> int:
    color = _use_color()
    ok = True
    print(_color(f"credit-mon {__version__}", BOLD, color))

    def line(label: str, value: str, good: bool = True) -> None:
        nonlocal ok
        mark = _color("ok  ", GREEN, color) if good else _color("FAIL", RED, color)
        print(f"  [{mark}] {label:<34} {value}")
        ok = ok and good

    line("config", str(cfg_mod.config_path()), cfg_mod.config_path().exists() or True)
    line("snapshot dir", str(state.state_dir()), True)
    line("python gi (PyGObject)", "present" if secrets.keyring_available() else "missing", secrets.keyring_available())
    line("keyring backend", "usable" if secrets.keyring_available() else "unavailable", True)
    line("systemd-creds", shutil.which("systemd-creds") or "missing", bool(shutil.which("systemd-creds")))

    for name in REGISTRY:
        if secrets.system_credentials_present().get(name):
            line(f"{name} key", f"root-sealed in {secrets.SYSTEM_CRED_DIR}", True)
            continue
        try:
            key, source = secrets.get_key(name)
        except secrets.SecretError as exc:
            line(f"{name} key", str(exc)[:60], False)
            continue
        line(f"{name} key", f"stored ({source})" if key else "not stored", bool(key))

    for label, url in (
        ("api.deepseek.com", "https://api.deepseek.com/user/balance"),
        ("api.z.ai", "https://api.z.ai/api/biz/account/query-customer-account-report"),
        ("www.bigmodel.cn", "https://www.bigmodel.cn/api/biz/account/query-customer-account-report"),
    ):
        good, detail = _reach(url)
        line(f"reach {label}", detail, good)

    exts = subprocess.run(["gnome-extensions", "list"], capture_output=True, text=True).stdout
    line("gnome-extension installed", "yes" if "credit-mon@local" in exts else "no", "credit-mon@local" in exts)

    enabled = subprocess.run(["gsettings", "get", "org.gnome.shell", "enabled-extensions"],
                             capture_output=True, text=True).stdout
    in_enabled = "credit-mon@local" in enabled
    line("extension in enabled-extensions", "yes" if in_enabled else
         "no — run: gnome-extensions enable credit-mon@local", in_enabled)

    info = subprocess.run(["gnome-extensions", "info", "credit-mon@local"],
                          capture_output=True, text=True).stdout
    ext_state = next((ln.split(":", 1)[1].strip() for ln in info.splitlines()
                      if ln.startswith("  State:")), "?")
    line("extension state", ext_state, ext_state == "ACTIVE")

    installed = set(exts.split())
    listed = enabled.strip().strip("[]").replace("'", "").split(", ") if enabled.strip() else []
    stray = [u for u in listed if u and u not in installed]
    if stray:
        line("stray uuids in enabled-extensions", ", ".join(stray) + " (not installed)", False)
    else:
        line("stray uuids in enabled-extensions", "none", True)
    unit = subprocess.run(["systemctl", "--user", "is-enabled", "credit-mon.timer"],
                          capture_output=True, text=True).stdout.strip()
    line("systemd timer", unit or "not enabled", unit == "enabled")

    timer_cat = subprocess.run(["systemctl", "--user", "cat", "credit-mon.timer"],
                               capture_output=True, text=True).stdout
    match = re.search(r"^OnUnitActiveSec=(\d+)(min|s|h)$", timer_cat, re.MULTILINE)
    wanted = int(cfg_mod.load().get("refresh_minutes") or 10)
    if match:
        value, scale = int(match.group(1)), match.group(2)
        actual = {"s": value / 60, "min": value, "h": value * 60}[scale]
        drift = abs(actual - wanted) > 0.5
        line("timer interval", f"{actual:g} min (config: {wanted} min)",
             not drift)
        if drift:
            print("       run ./install.sh to regenerate the timer from config.json")

    print()
    print(_color("looks good" if ok else "problems above", GREEN if ok else RED, color))
    return 0 if ok else 1


# --------------------------------------------------------------------- main ---

def cmd_config(args) -> int:
    path = cfg_mod.ensure_config_file()
    if args.action == "path":
        print(path)
        return 0
    editor = os.environ.get("EDITOR") or "nano"
    subprocess.run([editor, str(path)], check=False)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="credit-mon", description="Credits and spend in your taskbar.")
    p.add_argument("--version", action="version", version=f"credit-mon {__version__}")
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("refresh", help="poll providers and update the panel snapshot")
    r.add_argument("-v", "--verbose", action="store_true")
    r.add_argument("--json", dest="print_json", action="store_true", help="print the snapshot")
    r.set_defaults(func=cmd_refresh)

    k = sub.add_parser("keys", help="manage stored API keys")
    ksub = k.add_subparsers(dest="keys_cmd", required=True)
    ks = ksub.add_parser("set", help="store a key (prompted, hidden)")
    ks.add_argument("provider", choices=sorted(REGISTRY))
    ks.add_argument("--backend", choices=["keyring", "creds"], default="keyring")
    ks.set_defaults(func=cmd_keys_set)
    kl = ksub.add_parser("list", help="show where keys live")
    kl.set_defaults(func=cmd_keys_list)
    kd = ksub.add_parser("delete", help="remove a stored key")
    kd.add_argument("provider", choices=sorted(REGISTRY))
    kd.set_defaults(func=cmd_keys_delete)

    s = sub.add_parser("status", help="print the current snapshot")
    s.set_defaults(func=cmd_status)

    d = sub.add_parser("doctor", help="diagnose storage, extension and network")
    d.set_defaults(func=cmd_doctor)

    c = sub.add_parser("config", help="show or edit the config file")
    c.add_argument("action", nargs="?", choices=["path", "edit"], default="path")
    c.set_defaults(func=cmd_config)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args = parser.parse_args(["refresh"])
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
