#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="diskzokej.service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || true)}"
RUN_USER="${RUN_USER:-${SUDO_USER:-$(id -un)}}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Spoustim znovu se sudo, aby slo vytvorit systemd sluzbu..."
  exec sudo RUN_USER="${RUN_USER}" PYTHON_BIN="${PYTHON_BIN}" bash "$0" "$@"
fi

if [[ -z "${PYTHON_BIN}" ]]; then
  echo "Chyba: python3 nebyl nalezen v PATH." >&2
  exit 1
fi

if [[ ! -f "${SCRIPT_DIR}/diskzokej.py" ]]; then
  echo "Chyba: v ${SCRIPT_DIR} neni diskzokej.py." >&2
  exit 1
fi

if [[ ! -f "${SCRIPT_DIR}/token.txt" && ! -f "${SCRIPT_DIR}/.env" ]]; then
  echo "Upozorneni: nenasel jsem token v token.txt ani .env."
  echo "Service se vytvori, ale bot bez tokenu nenastartuje."
fi

cat > "${SERVICE_PATH}" <<EOF
[Unit]
Description=Diskzokej Discord Music Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${SCRIPT_DIR}
ExecStart=${PYTHON_BIN} ${SCRIPT_DIR}/diskzokej.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

echo
echo "Hotovo."
echo "Service: ${SERVICE_NAME}"
echo "Uzivatel: ${RUN_USER}"
echo "Projekt: ${SCRIPT_DIR}"
echo "Python: ${PYTHON_BIN}"
echo
systemctl --no-pager --full status "${SERVICE_NAME}" || true
