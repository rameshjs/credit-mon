#!/usr/bin/env bash
# Remove the system-level poller installed by install-system.sh.
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "run with sudo: sudo bash $0" >&2; exit 1; }

echo "==> stopping and removing system units"
systemctl disable --now credit-mon.timer 2>/dev/null || true
rm -f /etc/systemd/system/credit-mon.service /etc/systemd/system/credit-mon.timer
systemctl daemon-reload

echo "==> removing code and sealed keys"
rm -rf /usr/local/lib/credit-mon /etc/credit-mon
rm -rf /run/credit-mon

if id -u creditmon >/dev/null 2>&1; then
    echo "==> removing system user creditmon (history kept in /var/lib/credit-mon)"
    userdel creditmon 2>/dev/null || true
fi

cat <<'EOF'
done. To go back to the per-user poller:

    cd ~/credit-mon && ./install.sh
    credit-mon keys set deepseek
    credit-mon keys set zai
EOF
