#!/usr/bin/env bash
# Install credit-mon for the current user. No sudo, no system-wide changes.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
UNIT_DIR="$HOME/.config/systemd/user"
EXT_DIR="$HOME/.local/share/gnome-shell/extensions/credit-mon@local"
UUID="credit-mon@local"

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m warning:\033[0m %s\n' "$*" >&2; }

say "checking prerequisites"
command -v python3 >/dev/null || { echo "python3 missing"; exit 1; }
python3 -c 'import gi; gi.require_version("Secret","1"); from gi.repository import Secret' 2>/dev/null \
    || warn "python3-gi Secret typelib missing — keys will fall back to systemd-creds"

# ---------------------------------------------------------------- CLI shim ---
say "installing CLI at $BIN_DIR/credit-mon"
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/credit-mon" <<EOF
#!/usr/bin/env bash
exec /usr/bin/python3 "$REPO_DIR/run.py" "\$@"
EOF
chmod 755 "$BIN_DIR/credit-mon"

# ------------------------------------------------------------------ config ---
say "preparing config"
python3 "$REPO_DIR/run.py" config path >/dev/null
REFRESH_MIN="$(python3 - <<EOF
import json, pathlib
p = pathlib.Path("$HOME/.config/credit-mon/config.json")
print(json.loads(p.read_text()).get("refresh_minutes", 10))
EOF
)"
say "refresh interval: ${REFRESH_MIN} min"

# ---------------------------------------------------------------- systemd ----
say "installing systemd user units"
mkdir -p "$UNIT_DIR"
sed "s|@REPO_DIR@|$REPO_DIR|g" "$REPO_DIR/systemd/credit-mon.service" \
    > "$UNIT_DIR/credit-mon.service"
chmod 644 "$UNIT_DIR/credit-mon.service"
# The unit runs this exact path, so make sure it exists before enabling.
grep -q "^ExecStart=$REPO_DIR/run.py" "$UNIT_DIR/credit-mon.service" \
    || { echo "unit generation failed"; exit 1; }
sed "s/@REFRESH_INTERVAL@/${REFRESH_MIN}min/" "$REPO_DIR/systemd/credit-mon.timer" \
    > "$UNIT_DIR/credit-mon.timer"
chmod 644 "$UNIT_DIR/credit-mon.timer"
systemctl --user daemon-reload
systemctl --user enable --now credit-mon.timer >/dev/null
say "timer enabled (starts at login, every ${REFRESH_MIN} min)"

# -------------------------------------------------------------- extension ----
say "installing GNOME Shell extension"
mkdir -p "$(dirname "$EXT_DIR")"
rm -rf "$EXT_DIR"
cp -r "$REPO_DIR/extension" "$EXT_DIR"

if command -v gnome-extensions >/dev/null; then
    if ! gnome-extensions list 2>/dev/null | grep -qx "$UUID"; then
        warn "this GNOME session has not scanned $UUID yet"
        warn "log out and back in (or reboot) to activate the panel item"
    else
        # `gnome-extensions disable` removes the uuid from enabled-extensions, so
        # re-assert it: otherwise nothing loads at the next login.
        ENABLED="$(gsettings get org.gnome.shell enabled-extensions 2>/dev/null || echo '[]')"
        if [[ "$ENABLED" != *"$UUID"* ]]; then
            ENABLED="$(python3 -c 'import ast,sys
cur = ast.literal_eval(sys.argv[1]) if sys.argv[1].strip() else []
uuid = sys.argv[2]
if uuid not in cur:
    cur.append(uuid)
print("[" + ", ".join(repr(x) for x in cur) + "]")' "$ENABLED" "$UUID")"
            gsettings set org.gnome.shell enabled-extensions "$ENABLED"
        fi
        gnome-extensions enable "$UUID" >/dev/null 2>&1 || true
        sleep 1
        STATE="$(gnome-extensions info "$UUID" 2>/dev/null | awk -F': ' '/State:/{print $2}')"
        case "$STATE" in
            ACTIVE) say "extension $UUID active" ;;
            "")     warn "extension state unknown: gnome-extensions info $UUID" ;;
            *)      warn "extension state: $STATE — see GetExtensionErrors for $UUID" ;;
        esac
    fi
fi

# ------------------------------------------------------------ first poll ----
say "running first poll"
systemctl --user start credit-mon.service || true

echo
say "done"
echo
"$BIN_DIR/credit-mon" status || true
echo
cat <<EOF
Next: store one dedicated API key per provider (prompted, hidden input):

    credit-mon keys set deepseek
    credit-mon keys set zai

Then: systemctl --user start credit-mon.service
EOF
