#!/usr/bin/env bash
# install.sh — meet-transcribe 私有化部署脚本（裸机 Linux）
# 兼容：Ubuntu 22.04 / RHEL 8 / Kylin V10
# 不使用 Docker。需要 Python 3.11、PostgreSQL 16+、CUDA 12.x

set -euo pipefail

INSTALL_PREFIX="${INSTALL_PREFIX:-/opt/meet-transcribe}"
SERVICE_USER="${SERVICE_USER:-meet-transcribe}"
ETC_DIR="${ETC_DIR:-/etc/meet-transcribe}"
VAR_DIR="${VAR_DIR:-/var/lib/meet-transcribe}"
LOG_DIR="${LOG_DIR:-/var/log/meet-transcribe}"
WHEELHOUSE="${WHEELHOUSE:-./wheelhouse}"

OFFLINE=0
for arg in "$@"; do
    case "$arg" in
        --offline) OFFLINE=1 ;;
    esac
done

require_root() {
    if [[ $EUID -ne 0 ]]; then
        echo "[ERROR] install.sh must be run as root" >&2
        exit 1
    fi
}

create_user() {
    if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
        useradd --system --home-dir "$INSTALL_PREFIX" --shell /usr/sbin/nologin "$SERVICE_USER"
    fi
}

create_dirs() {
    install -d -m 0755 -o "$SERVICE_USER" -g "$SERVICE_USER" \
        "$INSTALL_PREFIX" "$VAR_DIR" "$LOG_DIR" "$ETC_DIR"
}

setup_venv() {
    if ! command -v python3.11 >/dev/null 2>&1; then
        echo "[ERROR] python3.11 not found in PATH" >&2
        exit 1
    fi
    sudo -u "$SERVICE_USER" python3.11 -m venv "$INSTALL_PREFIX/.venv"
    PIP="$INSTALL_PREFIX/.venv/bin/pip"
    sudo -u "$SERVICE_USER" "$PIP" install --upgrade pip wheel

    if [[ "$OFFLINE" -eq 1 ]]; then
        sudo -u "$SERVICE_USER" "$PIP" install --no-index --find-links="$WHEELHOUSE" \
            -e "$INSTALL_PREFIX"
    else
        sudo -u "$SERVICE_USER" "$PIP" install -e "$INSTALL_PREFIX"
    fi
}

install_systemd_unit() {
    install -m 0644 \
        "$INSTALL_PREFIX/deploy/systemd/meet-transcribe.service" \
        /etc/systemd/system/meet-transcribe.service
    systemctl daemon-reload
}

main() {
    require_root
    echo "[1/4] creating service user $SERVICE_USER"
    create_user
    echo "[2/4] creating directories"
    create_dirs
    echo "[3/4] setting up venv"
    setup_venv
    echo "[4/4] installing systemd unit"
    install_systemd_unit

    echo
    echo "Next steps:"
    echo "  1. Copy your meet-transcribe.yaml to $ETC_DIR/"
    echo "  2. Create $ETC_DIR/env with MT_DB_PASSWORD / MT_SERVER_SECRET / MT_KMS_KEY"
    echo "  3. Initialize PostgreSQL schema:"
    echo "       psql -U postgres -f $INSTALL_PREFIX/deploy/scripts/init_schema.sql"
    echo "  4. Run:  $INSTALL_PREFIX/.venv/bin/meet-transcribe-doctor"
    echo "  5. systemctl enable --now meet-transcribe"
}

main "$@"
