#!/usr/bin/env bash
# Remove the extension, units and CLI shim. Keys/config/state are kept unless
# you pass --purge.
set -euo pipefail

UUID="credit-mon@local"
EXT_DIR="$HOME/.local/share/gnome-shell/extensions/$UUID"
UNIT_DIR="$HOME/.config/systemd/user"
PURGE=0
[[ "${1:-}" == "--purge" ]] && PURGE=1

echo "==> disabling extension"
gnome-extensions disable "$UUID" 2>/dev/null || true
rm -rf "$EXT_DIR"

echo "==> removing units"
systemctl --user disable --now credit-mon.timer 2>/dev/null || true
rm -f "$UNIT_DIR/credit-mon.service" "$UNIT_DIR/credit-mon.timer"
systemctl --user daemon-reload

echo "==> removing CLI shim"
rm -f "$HOME/.local/bin/credit-mon"

if [[ $PURGE -eq 1 ]]; then
    echo "==> purging keys, config and history"
    for p in deepseek moonshot zai; do
        /usr/bin/python3 -c "
import sys; sys.path.insert(0, '$PWD')
from credit_mon import secrets
print('  ', '$p', secrets.delete_key('$p'))
" 2>/dev/null || true
    done
    rm -rf "$HOME/.config/credit-mon" "$HOME/.local/state/credit-mon"
    rm -rf "${XDG_RUNTIME_DIR:-/tmp}/credit-mon"
fi

echo "done. Log out and back in if the panel item lingers."
