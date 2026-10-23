# credit-mon

GNOME Shell panel widget that shows your DeepSeek and Z.ai (GLM) API credit
balances, your observed spend, and your coding-plan quota.

```
● DeepSeek  $19.27  │  ● Z.ai  $18.45      ← panel capsule
```

Clicking it opens a menu with a card per provider: current balance, spend today,
a 7-day balance sparkline, estimated burn rate and days left, coding-plan quota
bars, plan renewal date, plus refresh, dashboard and API-key actions.

Balances are fetched by a `systemd --user` timer; API keys live in the GNOME
Keyring (or sealed with a machine-bound key) and are never read by the shell
process.

## Why it is built this way

Neither DeepSeek nor Z.ai offers a read-only or scoped API key, so any key that
can read a balance can also spend money. The useful protection is therefore
isolation rather than scoping:

- a **dedicated key** per provider, never the one your coding agent uses, so
  revoking the panel key cannot break your agent and vice versa;
- the key is read only by a sandboxed oneshot service, never by the Shell, so no
  extension can see it;
- the widget renders a snapshot file from `$XDG_RUNTIME_DIR`, so a bug in the UI
  cannot leak a credential or stall the compositor;
- keys are prompted for interactively, so they never appear in `argv`, shell
  history, or `journalctl`.

Level 2 and 3 below go further if you want the key to be unreadable by your own
user account.

## Requirements

- Linux with GNOME Shell 45–50 (developed and tested on Ubuntu 26.04, GNOME 50.1)
- Python 3.9+ with PyGObject (`python3-gi`), which ships with Ubuntu/GNOME
- systemd user services (`systemd --user`), systemd 250+
- For the keyring backend: the `Secret-1` GObject typelib (`gir1.2-secret-1`,
  installed on standard GNOME desktops)

No pip packages are required; everything is standard library plus PyGObject.

## Install

```bash
git clone https://github.com/rameshjs/credit-mon.git ~/credit-mon
cd ~/credit-mon
./install.sh
```

`install.sh` installs the CLI shim, the systemd units, the GNOME extension and a
config file. It needs no sudo and makes no changes outside your home directory.

If the panel item does not appear immediately, log out and back in: a GNOME
session only scans a newly created extension directory at login.

## Add your API keys

Create a **dedicated** key for this widget, separate from your agent's key:

- DeepSeek: https://platform.deepseek.com/api_keys
- Z.ai: https://z.ai/manage-apikey/apikey-list. Keys are split by type since
  October 2026: a *Standard* key bills pay-as-you-go, a *Coding Plan* key belongs
  to your plan. Use the entry point matching what you want to monitor.

```bash
credit-mon keys set deepseek     # prompted, input hidden
credit-mon keys set zai
systemctl --user start credit-mon.service
```

`credit-mon keys set <provider> --backend creds` seals the key with
`systemd-creds --with-key=host` instead of the keyring.

## Panel and menu

Panel capsule: one segment per provider with an accent dot, provider tag and
balance in tabular figures. Amber below the low-balance threshold, dimmed when
the snapshot goes stale, red dot with `—` when a provider fails (the error is in
the tooltip and the menu).

Menu: `CREDITS` header with a relative timestamp, then one card per provider
(balance, spend chip, sparkline, burn rate, quota bars, plan), then Refresh now,
Dashboards, API keys and Diagnostics, and the exact snapshot time.

Colours are alpha-blended greys so the widget is correct on both the light and
the dark Shell theme; only the accent dots carry hue.

## Configuration

`~/.config/credit-mon/config.json`:

```json
{
  "refresh_minutes": 10,
  "request_timeout": 20,
  "providers": {
    "deepseek": {"enabled": true, "currency": null},
    "zai": {"enabled": true, "region": "intl", "show_quota": true}
  },
  "alerts": {"enabled": true, "threshold": {"deepseek": 2.0, "zai": 5.0}},
  "panel": {
    "order": ["deepseek", "zai"],
    "show_provider_names": true,
    "low_balance": {"deepseek": 2.0, "zai": 5.0},
    "accents": {"deepseek": "#4d6bfe", "zai": "#2dd4bf"}
  }
}
```

- `currency: null` uses the first currency the provider reports (DeepSeek
  returns CNY or USD depending on the account).
- `region: "cn"` switches Z.ai endpoints from `api.z.ai` to `www.bigmodel.cn`.

`refresh_minutes` is baked into the systemd timer at install time, so after
changing it regenerate the schedule:

```bash
./install.sh                       # rewrites credit-mon.timer from config.json
systemctl --user edit --full credit-mon.timer   # or edit the cadence by hand
```

`credit-mon doctor` reports drift between the config value and the installed
timer.

Polling costs nothing: the endpoints are account-metadata reads, no model
tokens are consumed. One cycle is 1 request to DeepSeek and 3 to Z.ai (up to 6
if the auth-scheme fallback retries), so at the default 10 minutes that is
roughly 600 requests per day.

## Security model

| Level | How | Key at rest | Stops offline theft / backups | Stops code running as you |
|---|---|---|---|---|
| 1. Keyring (default) | `credit-mon keys set zai` | encrypted with your login password | yes | no (unlocked after login) |
| 2. Machine-bound | `--backend creds` | `systemd-creds` envelope, root-only host key (+TPM2 if present) | yes, and no offline brute force | no (the helper will decrypt for your uid) |
| 3. System-owned | `sudo bash install-system.sh` | `/etc/credit-mon/credentials/*.cred`, `0400 root`, sealed with host key + TPM2 | yes | yes: decrypted into a directory only the `creditmon` system user can read |

```bash
sudo bash install-system.sh     # level 3; disables the per-user timer
sudo bash uninstall-system.sh   # back to level 1/2
```

Level 3 note: a dedicated `creditmon` system user runs the sandboxed system unit
(`ProtectSystem=strict`, `ProtectHome=yes`, `NoNewPrivileges`), and the widget
reads `/run/credit-mon/state.json` (balances only). TPM sealing is not bound to
PCRs, so firmware updates are fine; a TPM state reset requires re-running the
installer to re-seal.

What none of this protects against: root, or malware already running as you
(level 1 and 2 keys are readable by your user). For hard isolation of the money
itself, put the dedicated keys on a second account with its own small prepaid
balance.

Rejected approach: scraping the web console with a session cookie. It grants
full account access including top-ups, and breaks on every logout.

## Endpoints

| Provider | Call | Status |
|---|---|---|
| DeepSeek | `GET https://api.deepseek.com/user/balance` | documented |
| Z.ai | `GET /api/biz/account/query-customer-account-report` | console-internal, balance |
| Z.ai | `GET /api/monitor/usage/quota/limit` | console-internal, 5h / weekly quota |
| Z.ai | `GET /api/biz/subscription/list` | console-internal, plan and renewal |

The Z.ai routes are used by its own console and are undocumented, so every
response is parsed defensively and a changed shape produces a per-provider
warning rather than a broken panel.

Neither provider exposes spend history in a consistent way, so "spend today",
burn rate and days-left are derived from this tool's own snapshots
(`~/.local/state/credit-mon/history.jsonl`), ignoring top-ups.

## Adding a provider

Add a class to `credit_mon/providers.py` and register it:

```python
class MyProvider:
    name = "myprovider"
    display = "My Provider"
    dashboard = "https://example.com/billing"

    def fetch(self, cfg: dict, key: str, timeout: int = 20) -> Reading:
        raw = http_json("https://api.example.com/balance",
                        {"Authorization": f"Bearer {key}"}, timeout)
        reading = Reading(provider=self.name, ok=True)
        reading.total = to_float(raw.get("credits"))
        reading.currency = "USD"
        reading.headline = fmt_money(reading.total, reading.currency)
        reading.rows.append(("Balance", reading.headline))
        return reading


REGISTRY = {DeepSeekProvider.name: DeepSeekProvider,
            ZaiProvider.name: ZaiProvider,
            MyProvider.name: MyProvider}
```

Optional fields: `quota` (list of `{"label", "percent", "reset"}` rendered as
bars), `warnings`, `dashboard`. The CLI, panel, alerts and history pick the
provider up automatically. Add `myprovider` to `config.json` under `providers`.

## CLI

```
credit-mon refresh [-v] [--json]   poll providers and write the snapshot
credit-mon status                  print the current snapshot
credit-mon keys set|list|delete P  manage stored keys
credit-mon doctor                  check storage, extension, timer, network
credit-mon config [path|edit]      locate or edit the config file
```

Exit codes: `refresh` returns 0 even when providers fail (the panel shows
per-provider errors); `doctor` returns 1 if something is wrong.

## Tests

```bash
python3 -m unittest discover -s tests -v    # provider parsing against a local HTTP stub
gjs -m tests/extension-test.js              # panel label and formatting logic
```

`dev/probe.sh` boots a throwaway headless GNOME Shell on a virtual monitor,
loads the widget against a synthetic snapshot, opens the menu and logs real
widget allocations, so layout changes can be checked without touching the live
session:

```bash
dev/probe.sh
# PROBE pill 203x24
# PROBE menubox 334x546
```

It isolates its dconf database and refuses to run if it would modify the live
session.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `DS —` in the panel | no key or a rejected key: `credit-mon status`, then `credit-mon doctor` |
| Panel shows `···` | the poller never ran: `systemctl --user start credit-mon.service` |
| Extension missing from the panel | `gnome-extensions info credit-mon@local`; if absent from `gnome-extensions list`, log out and back in |
| Extension disappeared after a session restart | the uuid is not in `enabled-extensions`; `gnome-extensions enable credit-mon@local`, then confirm with `credit-mon doctor` |
| `keyring lookup failed` | login keyring locked (boot-time polling before login); use `--backend creds` |
| Z.ai returns 401 | key type does not match the endpoint (Standard vs Coding Plan), or `region` is wrong |
| Edited the JS and nothing changed | GNOME imports an extension's JS once per Shell process; log out and back in (CSS and config reload on `gnome-extensions disable/enable`) |

## Files installed

| Path | Purpose |
|---|---|
| `~/.local/bin/credit-mon` | CLI shim |
| `~/.config/systemd/user/credit-mon.service` | oneshot poller |
| `~/.config/systemd/user/credit-mon.timer` | schedule |
| `~/.local/share/gnome-shell/extensions/credit-mon@local/` | panel widget |
| `~/.config/credit-mon/config.json` | settings (never secrets) |
| `~/.local/state/credit-mon/history.jsonl` | balance history for spend/burn |
| `$XDG_RUNTIME_DIR/credit-mon/state.json` | snapshot rendered by the panel |

## Uninstall

```bash
./uninstall.sh           # keeps keys, config and history
./uninstall.sh --purge   # also deletes keys, config and history
```

## License

MIT, see [LICENSE](LICENSE).
