#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${PHLIST_SERVICE_NAME:-phlist-server}"
APP_DIR="${PHLIST_APP_DIR:-/opt/phlist-server}"
CONFIG_FILE="${PHLIST_CONFIG_FILE:-/etc/phlist-server/.env}"
LIST_DIR="${PHLIST_LIST_DIR:-/var/lib/phlist/lists}"
BACKUP_ROOT="${PHLIST_BACKUP_ROOT:-/opt/phlist-server.backups}"

die() {
    echo "error: $*" >&2
    exit 1
}

info() {
    echo "==> $*"
}

if [[ "${EUID}" -ne 0 ]]; then
    die "run with sudo: sudo scripts/update-deployed.sh"
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_DIR}"
REPO_REAL="$(realpath "${REPO_DIR}")"
APP_REAL="$(realpath "${APP_DIR}")"

[[ -f phlist_server.py ]] || die "phlist_server.py not found; run from a phlist-server checkout"
[[ -d templates ]] || die "templates/ not found"
[[ -d static ]] || die "static/ not found"
[[ -f requirements.txt ]] || die "requirements.txt not found"
[[ -f systemd/phlist-server.service ]] || die "systemd/phlist-server.service not found"
[[ -d "${APP_DIR}" ]] || die "${APP_DIR} does not exist; run the README deployment steps first"
[[ -f "${CONFIG_FILE}" ]] || die "${CONFIG_FILE} does not exist; refusing to update without deployed config"
[[ -d "${LIST_DIR}" ]] || die "${LIST_DIR} does not exist; refusing to update without list storage"

command -v python3 >/dev/null || die "python3 not found"
command -v systemctl >/dev/null || die "systemctl not found"
command -v curl >/dev/null || die "curl not found"

timestamp="$(date +%Y%m%d-%H%M%S)"
backup_dir="${BACKUP_ROOT}/${timestamp}"

info "Backing up current app files to ${backup_dir}"
install -d -m 0755 "${backup_dir}"
for path in phlist_server.py templates static; do
    if [[ -e "${APP_DIR}/${path}" ]]; then
        cp -a "${APP_DIR}/${path}" "${backup_dir}/"
    fi
done

if [[ "${REPO_REAL}" == "${APP_REAL}" ]]; then
    info "Source checkout is already ${APP_DIR}; leaving app files in place"
else
    info "Updating app files in ${APP_DIR}"
    install -d -m 0755 "${APP_DIR}"
    install -m 0644 phlist_server.py "${APP_DIR}/phlist_server.py"
    rm -rf "${APP_DIR}/templates" "${APP_DIR}/static"
    cp -a templates "${APP_DIR}/templates"
    cp -a static "${APP_DIR}/static"
    chown -R root:root "${APP_DIR}/phlist_server.py" "${APP_DIR}/templates" "${APP_DIR}/static"
fi

if [[ ! -x "${APP_DIR}/venv/bin/pip" ]]; then
    info "Creating virtual environment at ${APP_DIR}/venv"
    python3 -m venv "${APP_DIR}/venv"
fi

info "Updating Python dependencies"
"${APP_DIR}/venv/bin/pip" install -r requirements.txt

info "Updating systemd unit"
install -m 0644 systemd/phlist-server.service "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload

info "Restarting ${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

info "Checking service status"
systemctl --no-pager --full status "${SERVICE_NAME}"

info "Running authenticated health check"
set -a
# shellcheck disable=SC1090
source "${CONFIG_FILE}"
set +a

host="${PHLIST_HOST:-0.0.0.0}"
port="${PHLIST_PORT:-8765}"
api_key="${PHLIST_API_KEY:-}"
[[ -n "${api_key}" ]] || die "PHLIST_API_KEY is empty in ${CONFIG_FILE}"

if [[ "${host}" == "0.0.0.0" || "${host}" == "::" ]]; then
    host="127.0.0.1"
fi

health_url="http://${host}:${port}/health"
for attempt in {1..10}; do
    if curl -fsS -H "Authorization: Bearer ${api_key}" "${health_url}" >/dev/null; then
        break
    fi

    if [[ "${attempt}" -eq 10 ]]; then
        die "health check failed after ${attempt} attempts: ${health_url}"
    fi

    sleep 1
done

info "Update complete"
echo "Backup: ${backup_dir}"
