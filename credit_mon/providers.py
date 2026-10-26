"""Provider adapters: turn an API key into a normalised Reading.

DeepSeek    GET /user/balance                       (documented)
Moonshot AI GET /v1/users/me/balance                (documented)
Z.ai        GET /api/biz/account/query-customer-account-report   (balance, undocumented)
          GET /api/monitor/usage/quota/limit                   (coding-plan quota)
          GET /api/biz/subscription/list                       (plan / renewal)

The Z.ai endpoints are the ones its own console calls. They are stable in
practice but undocumented, so every shape is parsed defensively and any
endpoint that fails is reported as a warning instead of killing the row.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

USER_AGENT = "credit-mon/1.0 (local panel)"
SYMBOLS = {"USD": "$", "CNY": "¥", "EUR": "€", "GBP": "£"}


class ProviderError(Exception):
    pass


@dataclass
class Reading:
    provider: str
    ok: bool = False
    headline: str = "—"
    currency: str | None = None
    total: float | None = None
    rows: list[tuple[str, str]] = field(default_factory=list)
    quota: list[dict] = field(default_factory=list)
    spark: list[list[float]] = field(default_factory=list)
    spend_today: float | None = None
    burn_per_day: float | None = None
    days_left: float | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    fetched_at: float = field(default_factory=time.time)
    dashboard: str | None = None

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["rows"] = [[k, v] for k, v in self.rows]
        return d


# --------------------------------------------------------------- helpers ----

def http_json(url: str, headers: dict, timeout: int = 20) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
            **headers,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
        return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as exc:
        snippet = ""
        try:
            snippet = exc.read().decode("utf-8", "replace")[:200].replace("\n", " ")
        except Exception:
            pass
        snippet = " ".join(snippet.split())[:80]
        detail = f": {snippet}" if snippet else ""
        raise ProviderError(f"HTTP {exc.code} {url.split('/')[2]}{detail}") from None
    except urllib.error.URLError as exc:
        raise ProviderError(f"network error ({exc.reason})") from None
    except TimeoutError:
        raise ProviderError(f"timeout after {timeout}s") from None
    except json.JSONDecodeError:
        raise ProviderError("response was not JSON") from None
    except OSError as exc:
        raise ProviderError(f"connection failed: {exc}") from None


def pick(obj, *names, default=None):
    """Case-insensitive key lookup inside a dict."""
    if not isinstance(obj, dict):
        return default
    lowered = {str(k).lower(): v for k, v in obj.items()}
    for name in names:
        if name.lower() in lowered and lowered[name.lower()] is not None:
            return lowered[name.lower()]
    return default


def deep_find(obj, names, max_depth=4, _depth=0):
    """Locate the first value for any of `names` anywhere in a nested payload."""
    if _depth > max_depth:
        return None
    found = pick(obj, *names)
    if found is not None:
        return found
    if isinstance(obj, dict):
        for value in obj.values():
            hit = deep_find(value, names, max_depth, _depth + 1)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for value in obj:
            hit = deep_find(value, names, max_depth, _depth + 1)
            if hit is not None:
                return hit
    return None


def to_float(value) -> float | None:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def fmt_money(amount: float | None, currency: str | None) -> str:
    if amount is None:
        return "—"
    symbol = SYMBOLS.get((currency or "").upper(), "")
    magnitude = abs(amount)
    digits = 3 if 0 < magnitude < 0.10 else 2
    text = f"{amount:,.{digits}f}"
    return f"{symbol}{text}" if symbol else f"{text} {currency or ''}".strip()


def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _ms_to_str(ms) -> str | None:
    if not isinstance(ms, (int, float)) or ms <= 0:
        return None
    return time.strftime("%a %H:%M", time.localtime(ms / 1000.0))


def _auth_schemes(key: str) -> list[dict]:
    """Z.ai accepts both `Bearer <key>` and a bare key depending on the route."""
    return [
        {"Authorization": f"Bearer {key}"},
        {"Authorization": key},
        {"Authorization": key, "X-Api-Key": key},
    ]


def http_json_any_auth(url: str, key: str, timeout: int = 20) -> dict:
    last: ProviderError | None = None
    for headers in _auth_schemes(key):
        try:
            return http_json(url, headers, timeout)
        except ProviderError as exc:
            last = exc
            if "HTTP 401" not in str(exc) and "HTTP 403" not in str(exc):
                raise
    raise last or ProviderError("authentication failed")


def _payload_ok(raw: dict) -> bool:
    if not isinstance(raw, dict):
        return False
    if raw.get("success") is False:
        return False
    code = raw.get("code")
    if code is not None and str(code) not in ("200", "0"):
        return False
    return True


def _payload_error(raw: dict) -> str:
    if not isinstance(raw, dict):
        return "unexpected response"
    return str(
        pick(raw, "message", "msg", "error", "detail", default=f"code={raw.get('code')}")
    )[:160]


# -------------------------------------------------------------- DeepSeek ----

class DeepSeekProvider:
    name = "deepseek"
    display = "DeepSeek"
    BASE = "https://api.deepseek.com"
    dashboard = "https://platform.deepseek.com/usage"

    def fetch(self, cfg: dict, key: str, timeout: int = 20) -> Reading:
        r = Reading(provider=self.name, dashboard=self.dashboard)
        raw = http_json(f"{self.BASE}/user/balance", {"Authorization": f"Bearer {key}"}, timeout)
        infos = raw.get("balance_infos") or []
        if not infos:
            raise ProviderError("no balance_infos in response (wrong key type?)")

        want = (cfg.get("currency") or "").upper()
        info = next((i for i in infos if str(pick(i, "currency", default="")).upper() == want), infos[0])
        currency = str(pick(info, "currency", default="USD")).upper()
        total = to_float(pick(info, "total_balance"))
        granted = to_float(pick(info, "granted_balance"))
        topped = to_float(pick(info, "topped_up_balance"))

        r.ok = True
        r.currency = currency
        r.total = total
        r.headline = fmt_money(total, currency)
        r.rows.append(("Balance", fmt_money(total, currency)))
        if topped is not None:
            r.rows.append(("Topped up", fmt_money(topped, currency)))
        if granted is not None:
            r.rows.append(("Granted", fmt_money(granted, currency)))
        if raw.get("is_available") is False:
            r.warnings.append("balance too low for API calls")
        return r


# ------------------------------------------------------------- Moonshot AI ----

class MoonshotProvider:
    name = "moonshot"
    display = "Moonshot AI"
    BASES = {"intl": "https://api.moonshot.ai", "cn": "https://api.moonshot.cn"}
    DASHBOARDS = {
        "intl": "https://platform.moonshot.ai/console/account",
        "cn": "https://platform.moonshot.cn/console/account",
    }
    dashboard = DASHBOARDS["intl"]

    def fetch(self, cfg: dict, key: str, timeout: int = 20) -> Reading:
        region = str(cfg.get("region") or "intl").lower()
        base = self.BASES.get(region, self.BASES["intl"])
        r = Reading(provider=self.name, dashboard=self.DASHBOARDS.get(region, self.DASHBOARDS["intl"]))
        raw = http_json(
            f"{base}/v1/users/me/balance",
            {"Authorization": f"Bearer {key}"},
            timeout,
        )
        if not _payload_ok(raw) or raw.get("status") is False:
            raise ProviderError(_payload_error(raw))
        data = raw.get("data") or {}
        available = to_float(pick(data, "available_balance"))
        if available is None:
            raise ProviderError("no available_balance in response")

        # The international API bills in USD; the China API bills in CNY.
        currency = "CNY" if region == "cn" else "USD"
        cash = to_float(pick(data, "cash_balance"))
        voucher = to_float(pick(data, "voucher_balance"))
        r.ok = True
        r.currency = currency
        r.total = available
        r.headline = fmt_money(available, currency)
        r.rows.append(("Available", r.headline))
        if cash is not None:
            r.rows.append(("Cash", fmt_money(cash, currency)))
        if voucher is not None:
            r.rows.append(("Voucher", fmt_money(voucher, currency)))
        if available <= 0:
            r.warnings.append("balance too low for API calls")
        return r


# ------------------------------------------------------------------ Z.ai ----

class ZaiProvider:
    name = "zai"
    display = "Z.ai"
    BASES = {"intl": "https://api.z.ai", "cn": "https://www.bigmodel.cn"}
    dashboard = "https://z.ai/manage-apikey/apikey-list"

    PATH_ACCOUNT = "/api/biz/account/query-customer-account-report"
    PATH_QUOTA = "/api/monitor/usage/quota/limit"
    PATH_SUBS = "/api/biz/subscription/list"

    def fetch(self, cfg: dict, key: str, timeout: int = 20) -> Reading:
        region = str(cfg.get("region") or "intl").lower()
        base = self.BASES.get(region, self.BASES["intl"])
        if region == "cn":
            self.dashboard = "https://bigmodel.cn/usercenter/proj-mgmt/apikeys"
        r = Reading(provider=self.name, dashboard=self.dashboard)

        got_balance = False
        try:
            raw = http_json_any_auth(f"{base}{self.PATH_ACCOUNT}", key, timeout)
            if not _payload_ok(raw):
                raise ProviderError(_payload_error(raw))
            available = to_float(deep_find(raw, ["availableBalance", "available_balance", "balance"]))
            if available is None:
                raise ProviderError("no balance field in account report")
            currency = str(deep_find(raw, ["currency", "currencyCode"]) or ("CNY" if region == "cn" else "USD")).upper()
            recharged = to_float(deep_find(raw, ["rechargeAmount", "totalRecharge"]))
            granted = to_float(deep_find(raw, ["giveAmount", "grantedAmount"]))
            spent = to_float(deep_find(raw, ["totalSpendAmount", "totalSpend"]))
            frozen = to_float(deep_find(raw, ["frozenBalance"]))
            raw_balance = to_float(deep_find(raw, ["balance"]))

            got_balance = True
            r.currency = currency
            r.total = available
            r.headline = fmt_money(available, currency)
            r.rows.append(("Available", fmt_money(available, currency)))
            if raw_balance is not None and raw_balance != available:
                r.rows.append(("Balance", fmt_money(raw_balance, currency)))
            if granted is not None:
                r.rows.append(("Granted", fmt_money(granted, currency)))
            if recharged is not None:
                r.rows.append(("Topped up", fmt_money(recharged, currency)))
            if spent is not None:
                r.rows.append(("Lifetime spend", fmt_money(spent, currency)))
            if frozen:
                r.rows.append(("Frozen", fmt_money(frozen, currency)))
        except ProviderError as exc:
            r.warnings.append(f"balance: {exc}")

        if cfg.get("show_quota", True):
            try:
                self._add_quota(base, key, timeout, r)
            except ProviderError as exc:
                r.warnings.append(f"quota: {exc}")

        try:
            self._add_plan(base, key, timeout, r)
        except ProviderError as exc:
            r.warnings.append(f"plan: {exc}")

        if not r.rows:
            raise ProviderError("; ".join(r.warnings) or "no data returned")
        if not got_balance:
            # Quota-only accounts (Coding Plan): promote the quota line to headline.
            r.headline = next((v for k, v in r.rows if k.startswith(("5h", "Weekly"))), "—")
            r.currency = None
        r.ok = True
        return r

    def _add_quota(self, base: str, key: str, timeout: int, r: Reading) -> None:
        raw = http_json_any_auth(f"{base}{self.PATH_QUOTA}", key, timeout)
        if not _payload_ok(raw):
            raise ProviderError(_payload_error(raw))
        limits = deep_find(raw, ["limits"]) or []
        if not isinstance(limits, list) or not limits:
            raise ProviderError("no limits in response")
        for lim in limits:
            ltype = str(pick(lim, "type", default="")).upper()
            pct = to_float(pick(lim, "percentage"))
            if ltype == "TOKENS_LIMIT":
                unit = str(pick(lim, "unit", default=""))
                number = pick(lim, "number", default="")
                label = "5h quota" if (unit, str(number)) == ("3", "5") else (
                    "Weekly quota" if (unit, str(number)) == ("6", "7") else "Token quota"
                )
                if pct is None:
                    used = to_float(pick(lim, "currentValue"))
                    cap = to_float(pick(lim, "usage"))
                    pct = (used / cap * 100.0) if used and cap else None
                reset = _ms_to_str(pick(lim, "nextResetTime"))
                val = f"{pct:.0f}% used" if pct is not None else "—"
                if reset:
                    val += f" · resets {reset}"
                r.rows.append((label, val))
                if pct is not None:
                    r.quota.append({"label": label, "percent": round(pct, 1), "reset": reset})
            elif ltype == "TIME_LIMIT":
                used = to_float(pick(lim, "currentValue"))
                cap = to_float(pick(lim, "usage"))
                if used is not None and cap:
                    r.rows.append(("Web tools", f"{used:,.0f}/{cap:,.0f} this month"))
                    r.quota.append({
                        "label": "Web tools",
                        "percent": round(used / cap * 100.0, 1),
                        "reset": None,
                        "detail": f"{used:,.0f}/{cap:,.0f}",
                    })

    def _add_plan(self, base: str, key: str, timeout: int, r: Reading) -> None:
        raw = http_json_any_auth(f"{base}{self.PATH_SUBS}", key, timeout)
        if not _payload_ok(raw):
            raise ProviderError(_payload_error(raw))
        subs = raw.get("data") or []
        if not isinstance(subs, list) or not subs:
            return
        active = next((s for s in subs if str(pick(s, "status", default="")).upper() == "VALID"), subs[0])
        name = pick(active, "productName")
        renew = pick(active, "nextRenewTime")
        if name:
            r.rows.append(("Plan", str(name) + (f" · renews {renew}" if renew else "")))


REGISTRY = {
    DeepSeekProvider.name: DeepSeekProvider,
    MoonshotProvider.name: MoonshotProvider,
    ZaiProvider.name: ZaiProvider,
}


def get_provider(name: str):
    try:
        return REGISTRY[name]()
    except KeyError:
        raise ProviderError(f"unknown provider {name!r}") from None
