#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="diskzokej-update.service"
TIMER_NAME="diskzokej-update.timer"
BOT_SERVICE_NAME="${BOT_SERVICE_NAME:-diskzokej.service}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER="${RUN_USER:-${SUDO_USER:-$(id -un)}}"
INTERVAL="${INTERVAL:-5min}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Spoustim znovu se sudo, aby slo vytvorit systemd timer..."
  exec sudo RUN_USER="${RUN_USER}" BOT_SERVICE_NAME="${BOT_SERVICE_NAME}" INTERVAL="${INTERVAL}" bash "$0" "$@"
fi

if [[ ! -f "${SCRIPT_DIR}/auto_update_from_git.sh" ]]; then
  echo "Chyba: v ${SCRIPT_DIR} neni auto_update_from_git.sh." >&2
  exit 1
fi

chmod +x "${SCRIPT_DIR}/auto_update_from_git.sh"

cat > "/etc/systemd/system/${SERVICE_NAME}" <<EOF
[Unit]
Description=Auto update Diskzokej from Git
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=${SCRIPT_DIR}
Environment=RUN_AS_USER=${RUN_USER}
Environment=SERVICE_NAME=${BOT_SERVICE_NAME}
ExecStart=/bin/bash ${SCRIPT_DIR}/auto_update_from_git.sh
EOF

cat > "/etc/systemd/system/${TIMER_NAME}" <<EOF
[Unit]
Description=Run Diskzokej Git auto update periodically

[Timer]
OnBootSec=2min
OnUnitActiveSec=${INTERVAL}
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now "${TIMER_NAME}"

echo
echo "Hotovo."
echo "Timer: ${TIMER_NAME}"
echo "Interval: ${INTERVAL}"
echo "Projekt: ${SCRIPT_DIR}"
echo "Git uzivatel: ${RUN_USER}"
echo "Restartovana sluzba: ${BOT_SERVICE_NAME}"
echo
systemctl --no-pager --full status "${TIMER_NAME}" || true
