#!/usr/bin/env python3
"""Write a realistic fake snapshot so the UI can be inspected without keys."""
import json
import math
import sys
import time

now = time.time()


def spark(start, end, n=28, wobble=1.0):
    pts = []
    for i in range(n):
        t = now - (n - 1 - i) * 21600
        v = start + (end - start) * i / (n - 1)
        v += math.sin(i / 2.1) * wobble * abs(start - end) / n
        pts.append([round(t, 1), round(v, 4)])
    return pts


payload = {
    "schema": 1,
    "version": "1.1.0",
    "generated_at": now - 240,
    "refresh_seconds": 600,
    "panel": {
        "show_provider_names": True,
        "low_balance": {"deepseek": 2.0, "moonshot": 5.0, "zai": 5.0},
        "accents": {"deepseek": "#4d6bfe", "moonshot": "#c084fc", "zai": "#2dd4bf"},
    },
    "providers": [
        {
            "provider": "deepseek", "ok": True, "currency": "USD", "total": 15.42,
            "headline": "$15.42",
            "rows": [["Balance", "$15.42"], ["Topped up", "$15.42"], ["Granted", "$0.00"]],
            "quota": [],
            "spark": spark(19.4, 15.42),
            "spend_today": 0.36, "burn_per_day": 2.10, "days_left": 7.3,
            "warnings": [], "error": None, "fetched_at": now,
            "dashboard": "https://platform.deepseek.com/usage",
        },
        {
            "provider": "moonshot", "ok": True, "currency": "USD", "total": 49.58894,
            "headline": "$49.59",
            "rows": [["Available", "$49.59"], ["Cash", "$3.00"], ["Voucher", "$46.59"]],
            "quota": [],
            "spark": spark(57.0, 49.58894, wobble=0.5),
            "spend_today": 1.12, "burn_per_day": 3.25, "days_left": 15.3,
            "warnings": [], "error": None, "fetched_at": now,
            "dashboard": "https://platform.moonshot.ai/console/account",
        },
        {
            "provider": "zai", "ok": True, "currency": "CNY", "total": 42.5,
            "headline": "¥42.50",
            "rows": [["Available", "¥42.50"], ["Lifetime spend", "¥170.00"],
                     ["5h quota", "15% used"], ["Plan", "GLM Coding Max"]],
            "quota": [{"label": "5h quota", "percent": 15, "reset": "Mon 09:12"},
                      {"label": "Weekly quota", "percent": 50, "reset": "Fri 00:00"},
                      {"label": "Web tools", "percent": 45, "reset": None, "detail": "1,828/4,000"}],
            "spark": spark(46.0, 42.5, wobble=0.4),
            "spend_today": None, "burn_per_day": None, "days_left": None,
            "warnings": [], "error": None, "fetched_at": now,
            "dashboard": "https://z.ai/manage-apikey/apikey-list",
        },
    ],
}

path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/state.json"
with open(path, "w") as fh:
    json.dump(payload, fh)
print(path)
