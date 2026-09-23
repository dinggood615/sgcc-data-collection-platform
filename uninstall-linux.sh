#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/sgcc-data-collection-platform}"
DATA_DIR="$INSTALL_DIR/data"
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

safe_target() {
  case "$1" in
    *'/../'*|*/..|*'/./'*|*/.) return 1 ;;
    ""|/|/opt|/mnt|/volume1|/volume2|/home|/root|/usr|/var) return 1 ;;
    /*) return 0 ;;
    *) return 1 ;;
  esac
}
safe_target "$INSTALL_DIR" || { echo "INSTALL_DIR 不是可安全删除的项目目录。" >&2; exit 2; }
if [ -f "$INSTALL_DIR/.env" ]; then
  configured_data_dir="$(sed -n 's/^DATA_DIR=//p' "$INSTALL_DIR/.env" | tail -n 1)"
  [ -z "$configured_data_dir" ] || DATA_DIR="$configured_data_dir"
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
  compose_files=(-f "$INSTALL_DIR/docker-compose.yml")
  [ ! -f "$INSTALL_DIR/docker-compose.tls.yml" ] || compose_files+=(-f "$INSTALL_DIR/docker-compose.tls.yml")
  for project_name in sgcc-data-collection-platform data-collection-platform; do
    if docker compose version >/dev/null 2>&1; then
      docker compose -p "$project_name" "${compose_files[@]}" down --remove-orphans -v 2>/dev/null || true
    elif command -v docker-compose >/dev/null 2>&1; then
      docker-compose -p "$project_name" "${compose_files[@]}" down --remove-orphans -v 2>/dev/null || true
    fi
  done
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl disable --now "$SERVICE_NAME.service" tender-platform.service sgcc-manual-browser.service 2>/dev/null || true
fi
rm -f "/etc/systemd/system/$SERVICE_NAME.service" /etc/systemd/system/tender-platform.service /etc/systemd/system/sgcc-manual-browser.service
rm -f /etc/nginx/sites-enabled/sgcc-platform /etc/nginx/sites-enabled/tender-platform
rm -f /etc/nginx/sites-available/sgcc-platform /etc/nginx/sites-available/tender-platform
rm -f /etc/nginx/conf.d/sgcc-platform.conf /etc/nginx/conf.d/tender-platform.conf
rm -rf /etc/sgcc-platform "$INSTALL_DIR"
if [ "$DATA_DIR" != "$INSTALL_DIR/data" ] && [ -e "$DATA_DIR" ]; then
  safe_target "$DATA_DIR" || { echo "外部数据目录未删除（路径不安全）：$DATA_DIR" >&2; exit 2; }
  rm -rf "$DATA_DIR"
fi
if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload
  systemctl reset-failed
fi

if command -v nginx >/dev/null 2>&1 && command -v systemctl >/dev/null 2>&1; then
  nginx -t && systemctl reload nginx || true
fi
echo "国网数据采集管理平台及其程序、数据库、容器卷和旧代理配置已删除。Nginx、Docker 和其他网站/证书未删除。"
