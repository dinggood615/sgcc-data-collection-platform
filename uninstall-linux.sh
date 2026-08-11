#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/sgcc-data-collection-platform}"
AUTO_CONFIRM=0
for argument in "$@"; do
  case "$argument" in
    --yes) AUTO_CONFIRM=1 ;;
    *) echo "未知参数：$argument" >&2; exit 2 ;;
  esac
done

if [ "${EUID}" -ne 0 ]; then
  echo "请使用 sudo 运行。" >&2
  exit 1
fi

if [ "$AUTO_CONFIRM" -ne 1 ]; then
  if [ ! -t 0 ]; then
    echo "当前通过管道执行，无法读取交互确认。" >&2
    echo "确认删除全部平台数据时，请使用：" >&2
    echo "curl -fsSL https://raw.githubusercontent.com/dinggood615/sgcc-data-collection-platform/main/uninstall-linux.sh | sudo bash -s -- --yes" >&2
    exit 2
  fi
  read -r -p "将删除数据采集平台及其本地数据。输入 DELETE 确认: " confirm
  [ "$confirm" = "DELETE" ] || { echo "已取消。"; exit 0; }
fi

SERVICE_NAME="${SERVICE_NAME:-sgcc-platform}"
if ! systemctl cat "$SERVICE_NAME.service" >/dev/null 2>&1 && systemctl cat tender-platform.service >/dev/null 2>&1; then
  legacy_workdir="$(systemctl show tender-platform.service -p WorkingDirectory --value 2>/dev/null || true)"
  [ "$legacy_workdir" != "$INSTALL_DIR" ] || SERVICE_NAME=tender-platform
fi

if [ -f "$INSTALL_DIR/docker-compose.yml" ] && command -v docker >/dev/null 2>&1; then
  if docker compose version >/dev/null 2>&1; then
    docker compose -p data-collection-platform -f "$INSTALL_DIR/docker-compose.yml" down --remove-orphans 2>/dev/null || true
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose -p data-collection-platform -f "$INSTALL_DIR/docker-compose.yml" down --remove-orphans 2>/dev/null || true
  fi
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl disable --now "$SERVICE_NAME.service" sgcc-manual-browser.service 2>/dev/null || true
fi
rm -f "/etc/systemd/system/$SERVICE_NAME.service" /etc/systemd/system/sgcc-manual-browser.service
rm -f /etc/nginx/sites-enabled/sgcc-platform /etc/nginx/sites-available/sgcc-platform /etc/nginx/conf.d/sgcc-platform.conf
rm -rf /etc/sgcc-platform "$INSTALL_DIR"
if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload
  systemctl reset-failed
fi

if command -v nginx >/dev/null 2>&1 && command -v systemctl >/dev/null 2>&1; then
  nginx -t && systemctl reload nginx || true
fi
echo "国网数据采集管理平台已卸载。Nginx、Docker 和其他系统服务未删除。"
