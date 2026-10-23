#!/usr/bin/env bash
# OPTIONAL hardening: move the poller out of your user account.
#
#   sudo bash install-system.sh
#
# After this, the API keys are sealed with a root-only host key (plus the TPM if
# present) and decrypted by systemd into a directory that only a dedicated
# `creditmon` system user can read. Your own login can no longer read the keys —
# it can only read the balance snapshot in /run/credit-mon/state.json.
#
# This is the only way to make the keys unreadable by code running as you, short
# of typing a passphrase on every login (which defeats autostart).
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "run with sudo: sudo bash $0" >&2; exit 1; }

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_USER="${SUDO_USER:-}"
[[ -n "$TARGET_USER" && "$TARGET_USER" != "root" ]] || {
    echo "run as a normal user via sudo (SUDO_USER is unset)" >&2; exit 1; }
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"

LIB_DIR=/usr/local/lib/credit-mon
ETC_DIR=/etc/credit-mon
CRED_DIR="$ETC_DIR/credentials"
SVC_USER=creditmon

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m warning:\033[0m %s\n' "$*" >&2; }

# ------------------------------------------------------------------- user ---
if id -u "$SVC_USER" >/dev/null 2>&1; then
    say "system user $SVC_USER already exists"
else
    say "creating system user $SVC_USER"
    useradd --system --home-dir /var/lib/credit-mon --no-create-home \
            --shell /usr/sbin/nologin --comment "credit-mon poller" "$SVC_USER"
fi

# ------------------------------------------------------------------- code ---
say "installing code to $LIB_DIR"
install -d -m 0755 "$LIB_DIR"
rm -rf "$LIB_DIR/credit_mon"
cp -r "$REPO_DIR/credit_mon" "$LIB_DIR/"
install -m 0644 "$REPO_DIR/run.py" "$LIB_DIR/run.py"
install -m 0644 "$REPO_DIR/README.md" "$LIB_DIR/README.md" 2>/dev/null || true

# ----------------------------------------------------------------- config ---
say "preparing $ETC_DIR"
install -d -m 0750 -o root -g "$SVC_USER" "$ETC_DIR"
if [[ -f "$TARGET_HOME/.config/credit-mon/config.json" ]]; then
    install -m 0640 -o root -g "$SVC_USER" \
        "$TARGET_HOME/.config/credit-mon/config.json" "$ETC_DIR/config.json"
    say "imported config from $TARGET_USER"
elif [[ ! -f "$ETC_DIR/config.json" ]]; then
    CREDIT_MON_CONFIG_DIR="$ETC_DIR" /usr/bin/python3 - <<'PY'
import sys
sys.path.insert(0, "/usr/local/lib/credit-mon")
from credit_mon import config
print(config.ensure_config_file())
PY
    chgrp "$SVC_USER" "$ETC_DIR/config.json"
    chmod 0640 "$ETC_DIR/config.json"
fi

# ------------------------------------------------------------ credentials ---
install -d -m 0700 -o root -g root "$CRED_DIR"

if command -v systemd-analyze >/dev/null && systemd-analyze has-tpm2 >/dev/null 2>&1; then
    KEY_SPEC="host+tpm2"
    say "sealing keys with host key + TPM2"
else
    KEY_SPEC="host"
    say "sealing keys with root-only host key (no TPM2 available)"
fi

CRED_LINES=""
for provider in deepseek zai; do
    printf '\nPaste the %s API key (hidden input, empty = skip): ' "$provider" >&2
    IFS= read -rs key || true
    printf '\n' >&2
    if [[ -z "${key:-}" ]]; then
        warn "skipping $provider"
        continue
    fi
    tmp="$(mktemp)"
    if ! printf '%s' "$key" | systemd-creds encrypt --name="credit-mon-$provider" \
            --with-key="$KEY_SPEC" - "$tmp" 2>/dev/null; then
        warn "TPM2 sealing failed for $provider, falling back to host key"
        printf '%s' "$key" | systemd-creds encrypt --name="credit-mon-$provider" \
            --with-key=host - "$tmp"
    fi
    install -m 0400 -o root -g root "$tmp" "$CRED_DIR/$provider.cred"
    rm -f "$tmp"
    unset key
    CRED_LINES+="LoadCredentialEncrypted=$provider:$CRED_DIR/$provider.cred"$'\n'
    say "sealed $provider key -> $CRED_DIR/$provider.cred"
done

# ------------------------------------------------------------------ units ---
say "installing system units"
sed "s|^@@CREDENTIAL_LINES@@.*|${CRED_LINES%$'\n'}|" \
    "$REPO_DIR/systemd/credit-mon-system.service" > /etc/systemd/system/credit-mon.service
install -m 0644 "$REPO_DIR/systemd/credit-mon-system.timer" /etc/systemd/system/credit-mon.timer

# The user-level poller would duplicate work and write a second snapshot.
USER_UID="$(id -u "$TARGET_USER")"
if [[ -f "$TARGET_HOME/.config/systemd/user/credit-mon.timer" ]]; then
    say "disabling the per-user timer for $TARGET_USER"
    sudo -u "$TARGET_USER" XDG_RUNTIME_DIR="/run/user/$USER_UID" \
        systemctl --user disable --now credit-mon.timer 2>/dev/null || true
fi

systemctl daemon-reload
systemctl enable --now credit-mon.timer
systemctl start credit-mon.service || true

echo
say "done"
systemctl status credit-mon.service --no-pager 2>&1 | sed -n '1,6p' || true
echo
echo "Key material now lives in $CRED_DIR (0400 root) and is unreadable by '$TARGET_USER'."
echo "Verify with:"
echo "    sudo -u $TARGET_USER cat $CRED_DIR/deepseek.cred   # permission denied"
echo "    cat /run/credit-mon/state.json                     # balances only"
