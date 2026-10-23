"""Local balance history -> observed spend and burn rate.

Neither DeepSeek nor Z.ai reports "what did I spend today" in a way both
providers agree on, so we derive it from our own snapshots:
spend over an interval = previous balance - current balance, ignoring
intervals where the balance went *up* (a top-up).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

MAX_LINES = 20000
TOPUP_EPSILON = 0.005  # ignore rounding noise below half a cent


def _state_home() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "credit-mon"


def history_path() -> Path:
    return _state_home() / "history.jsonl"


def _today_start() -> float:
    lt = time.localtime()
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def record(provider: str, total: float | None, currency: str | None, ts: float | None = None) -> None:
    if total is None:
        return
    path = history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": ts or time.time(), "p": provider, "total": round(float(total), 6), "cur": currency}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, separators=(",", ":")) + "\n")
    _trim(path)


def _trim(path: Path) -> None:
    try:
        if path.stat().st_size < 4_000_000:
            return
        lines = path.read_text(encoding="utf-8").splitlines()[-MAX_LINES:]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass


def entries(provider: str, since: float = 0.0) -> list[tuple[float, float]]:
    path = history_path()
    if not path.exists():
        return []
    out: list[tuple[float, float]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("p") != provider:
                    continue
                ts = float(obj.get("ts") or 0)
                if ts < since:
                    continue
                out.append((ts, float(obj["total"])))
    except OSError:
        return []
    out.sort(key=lambda e: e[0])
    return out


def _spend_between(series: list[tuple[float, float]]) -> float:
    spent = 0.0
    for (_, prev), (_, cur) in zip(series, series[1:]):
        delta = prev - cur
        if delta > TOPUP_EPSILON:
            spent += delta
    return spent


def stats(provider: str, total: float | None) -> dict:
    """Return {'spend_today', 'burn_per_day', 'days_left'}."""
    now = time.time()
    out: dict = {"spend_today": None, "burn_per_day": None, "days_left": None}

    today = entries(provider, since=_today_start())
    if len(today) >= 2 and total is not None:
        # Include the live reading as the newest point.
        series = today + ([(now, total)] if now > today[-1][0] else [])
        out["spend_today"] = round(_spend_between(series), 4)

    week = entries(provider, since=now - 8 * 86400)
    if len(week) >= 3 and total is not None:
        series = week + [(now, total)]
        span = series[-1][0] - series[0][0]
        spent = _spend_between(series)
        if span > 3600 and spent > 0:
            per_day = spent / (span / 86400.0)
            out["burn_per_day"] = round(per_day, 4)
            if total is not None and per_day > 0:
                out["days_left"] = round(total / per_day, 1)
    return out


def recent(provider: str, hours: float = 168, max_points: int = 40) -> list[list[float]]:
    """Downsampled balance series for the sparkline (oldest -> newest)."""
    now = time.time()
    series = entries(provider, since=now - hours * 3600)
    if len(series) < 2:
        return []
    if len(series) > max_points:
        step = len(series) / max_points
        series = [series[min(len(series) - 1, int(i * step))] for i in range(max_points)]
    return [[round(ts, 1), round(total, 4)] for ts, total in series]
