#!/usr/bin/env bash
set -euo pipefail

[ "${EUID}" -eq 0 ] || { echo "请使用 sudo 运行更新脚本。" >&2; exit 1; }

if [ -z "${INSTALL_DIR:-}" ]; then
  if [ -d /opt/sgcc-data-collection-platform/.git ]; then
    INSTALL_DIR=/opt/sgcc-data-collection-platform
  elif [ -d /opt/tender-collection-platform/.git ]; then
    INSTALL_DIR=/opt/tender-collection-platform
  else
    echo "未找到平台安装目录；可使用 INSTALL_DIR=/实际路径 重新运行。" >&2
    exit 1
  fi
fi

if [ -z "${SERVICE_NAME:-}" ]; then
  if systemctl cat sgcc-platform.service >/dev/null 2>&1; then SERVICE_NAME=sgcc-platform
  else SERVICE_NAME=tender-platform
  fi
fi
SERVICE_USER="${SERVICE_USER:-tenderplatform}"
if [ -z "${BACKEND_PORT:-}" ]; then
  service_command="$(systemctl show "$SERVICE_NAME" -p ExecStart --value 2>/dev/null || true)"
  BACKEND_PORT="$(printf '%s' "$service_command" | sed -nE 's/.*--port[[:space:]]+([0-9]+).*/\1/p')"
  BACKEND_PORT="${BACKEND_PORT:-8000}"
fi
GIT=(git -c "safe.directory=$INSTALL_DIR" -C "$INSTALL_DIR")

[ -f "$INSTALL_DIR/.env" ] || { echo "缺少 $INSTALL_DIR/.env，无法安全更新。" >&2; exit 1; }
[ -x "$INSTALL_DIR/.venv/bin/python" ] || { echo "Python 虚拟环境不存在，请重新执行一键安装。" >&2; exit 1; }

if [ -n "$("${GIT[@]}" status --porcelain --untracked-files=no)" ]; then
  echo "检测到程序目录存在本地代码修改，已停止更新，避免覆盖定制功能。" >&2
  echo "请先提交/备份这些修改，或从网页导出完整迁移包后重新安装。" >&2
  exit 1
fi

current_branch="$("${GIT[@]}" branch --show-current)"
[ "$current_branch" = "main" ] || { echo "当前分支为 $current_branch，请切换到 main 后更新。" >&2; exit 1; }
old_commit="$("${GIT[@]}" rev-parse HEAD)"
update_started=0

rollback() {
  code=$?
  if [ "$update_started" -eq 1 ]; then
    echo "更新失败，正在恢复更新前程序版本……" >&2
    "${GIT[@]}" reset --hard "$old_commit" >/dev/null
    "$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt" >/dev/null 2>&1 || true
    systemctl restart "$SERVICE_NAME" >/dev/null 2>&1 || true
  fi
  exit "$code"
}
trap rollback ERR

echo "正在创建更新前数据库备份……"
su -s /bin/bash "$SERVICE_USER" -c "set -a; source '$INSTALL_DIR/.env'; set +a; cd '$INSTALL_DIR'; .venv/bin/python - <<'PY'
import os, sqlite3
from datetime import datetime
from pathlib import Path
source_path = Path(os.environ['DATABASE_PATH'])
backup_dir = Path(os.environ.get('BACKUP_DIR', source_path.parent / 'backups'))
backup_dir.mkdir(parents=True, exist_ok=True)
target = backup_dir / f'pre-update-{datetime.now().astimezone():%Y%m%d-%H%M%S}.sqlite3'
source, destination = sqlite3.connect(source_path), sqlite3.connect(target)
try: source.backup(destination)
finally: destination.close(); source.close()
print(target)
PY"

echo "正在拉取 main 分支更新……"
update_started=1
"${GIT[@]}" pull --ff-only origin main

echo "正在更新 Python 依赖和数据库结构……"
if command -v apt-get >/dev/null 2>&1; then
  conversion_packages=()
  command -v libreoffice >/dev/null 2>&1 || conversion_packages+=(libreoffice-core libreoffice-writer libreoffice-calc)
  { command -v 7zz >/dev/null 2>&1 || command -v 7z >/dev/null 2>&1; } || conversion_packages+=(7zip)
  command -v unar >/dev/null 2>&1 || conversion_packages+=(unar)
  if [ "${#conversion_packages[@]}" -gt 0 ]; then
    echo "正在补充附件转换工具：${conversion_packages[*]}"
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y "${conversion_packages[@]}"
  fi
fi
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip wheel
"$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
bash "$INSTALL_DIR/scripts/install-local-model.sh" || echo "警告：本地模型更新失败，平台将继续使用规则/OCR模式。"
su -s /bin/bash "$SERVICE_USER" -c "set -a; source '$INSTALL_DIR/.env'; set +a; cd '$INSTALL_DIR'; .venv/bin/python -c 'from app.database import init_db; init_db()'"

for nginx_file in /etc/nginx/sites-available/tender-platform /etc/nginx/conf.d/tender-platform.conf /etc/nginx/conf.d/sgcc-platform.conf; do
  if [ -f "$nginx_file" ]; then sed -i -E 's/client_max_body_size[[:space:]]+[0-9]+[mM];/client_max_body_size 110m;/' "$nginx_file"; fi
done
nginx -t
systemctl reload nginx
systemctl restart "$SERVICE_NAME"

echo "正在等待更新后的平台服务启动……"
health_ok=0
for attempt in $(seq 1 30); do
  if curl -fs "http://127.0.0.1:$BACKEND_PORT/healthz" >/dev/null 2>&1; then health_ok=1; break; fi
  if systemctl is-failed --quiet "$SERVICE_NAME"; then break; fi
  sleep 2
done
if [ "$health_ok" -ne 1 ]; then
  systemctl status "$SERVICE_NAME" --no-pager >&2 || true
  journalctl -u "$SERVICE_NAME" -n 40 --no-pager >&2 || true
  echo "更新后平台在 60 秒内未通过健康检查。" >&2
  exit 1
fi
echo "平台健康检查通过。"

new_commit="$("${GIT[@]}" rev-parse --short HEAD)"
update_started=0
trap - ERR
echo "更新完成，当前版本：$new_commit（服务：$SERVICE_NAME，后端端口：$BACKEND_PORT）"
