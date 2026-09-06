#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-diskzokej.service}"
REMOTE="${REMOTE:-origin}"
BRANCH="${BRANCH:-main}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_AS_USER="${RUN_AS_USER:-${SUDO_USER:-$(id -un)}}"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || true)}"

run_git() {
  if [[ "${EUID}" -eq 0 ]]; then
    sudo -u "${RUN_AS_USER}" git -C "${SCRIPT_DIR}" "$@"
  else
    git -C "${SCRIPT_DIR}" "$@"
  fi
}

run_as_project_user() {
  if [[ "${EUID}" -eq 0 ]]; then
    sudo -u "${RUN_AS_USER}" "$@"
  else
    "$@"
  fi
}

if [[ ! -d "${SCRIPT_DIR}/.git" ]]; then
  echo "Chyba: ${SCRIPT_DIR} neni git repozitar." >&2
  exit 1
fi

if [[ -z "${PYTHON_BIN}" ]]; then
  echo "Chyba: python3 nebyl nalezen v PATH." >&2
  exit 1
fi

if ! run_git diff --quiet || ! run_git diff --cached --quiet; then
  echo "Repozitar ma lokalni zmeny, automaticky update preskakuju."
  echo "Vyres je rucne pres git status / git diff."
  exit 0
fi

current_commit="$(run_git rev-parse HEAD)"
run_git fetch --quiet "${REMOTE}" "${BRANCH}"
remote_commit="$(run_git rev-parse "${REMOTE}/${BRANCH}")"

if [[ "${current_commit}" == "${remote_commit}" ]]; then
  echo "Zadna nova verze neni k dispozici."
  exit 0
fi

echo "Stahuju novou verzi ${remote_commit}..."
run_git merge --ff-only "${REMOTE}/${BRANCH}"

if [[ -f "${SCRIPT_DIR}/requirements.txt" ]]; then
  echo "Aktualizuju Python zavislosti..."
  run_as_project_user "${PYTHON_BIN}" -m pip install --upgrade -r "${SCRIPT_DIR}/requirements.txt"
fi

echo "Restartuju ${SERVICE_NAME}..."
if [[ "${EUID}" -eq 0 ]]; then
  systemctl restart "${SERVICE_NAME}"
else
  sudo systemctl restart "${SERVICE_NAME}"
fi

echo "Update hotovy."
