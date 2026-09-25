#!/usr/bin/env bash
set -euo pipefail

# Direct HTTP installer. It does not install or configure a web server, TLS
# certificate, reverse proxy, or public port 80/443.
export LANG="${LANG:-C.UTF-8}"
export LC_ALL="${LC_ALL:-C.UTF-8}"
REPOSITORY_URL="${1:-https://github.com/dinggood615/sgcc-data-collection-platform.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/sgcc-data-collection-platform}"
SERVICE_USER="tenderplatform"
PORT="${PORT:-5555}"
SERVICE_NAME="sgcc-platform"

die() { echo "错误：$*" >&2; exit 1; }
[ "${EUID}" -eq 0 ] || die "请使用 sudo 运行"
[ -d /run/systemd/system ] || die "原生安装需要 systemd；容器环境请使用 Docker 安装。"
case "$PORT" in *[!0-9]*|"") die "PORT 必须是 1-65535 的数字" ;; esac
[ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || die "PORT 必须是 1-65535 的数字"

install_packages() {
  if command -v apt-get >/dev/null; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates git python3 python3-venv python3-pip build-essential openssl curl sudo libreoffice-core libreoffice-writer libreoffice-calc poppler-utils 7zip unar tesseract-ocr tesseract-ocr-chi-sim
  elif command -v dnf >/dev/null; then
    dnf install -y ca-certificates git python3 python3-pip gcc gcc-c++ make openssl curl sudo
    dnf install -y libreoffice-headless libreoffice-writer libreoffice-calc poppler-utils p7zip p7zip-plugins tesseract || echo "警告：部分附件转换工具未安装，请按发行版仓库补充。"
  elif command -v yum >/dev/null; then
    yum install -y ca-certificates git python3 python3-pip gcc gcc-c++ make openssl curl sudo
    yum install -y libreoffice-headless libreoffice-writer libreoffice-calc poppler-utils p7zip p7zip-plugins tesseract || echo "警告：部分附件转换工具未安装，请按发行版仓库补充。"
  elif command -v zypper >/dev/null; then
    zypper --non-interactive install ca-certificates git python3 python3-pip gcc gcc-c++ make openssl curl sudo
    zypper --non-interactive install libreoffice poppler-tools p7zip tesseract-ocr || echo "警告：部分附件转换工具未安装，请按发行版仓库补充。"
  elif command -v pacman >/dev/null; then
    pacman -Sy --noconfirm ca-certificates git python python-pip base-devel openssl curl sudo
    pacman -Sy --noconfirm libreoffice-fresh poppler p7zip tesseract tesseract-data-chi_sim || echo "警告：部分附件转换工具未安装，请按发行版仓库补充。"
  else
    die "未识别的软件包管理器。支持 apt、dnf、yum、zypper、pacman。"
  fi
}

git_repo() {
  if [ -n "${GITHUB_TOKEN:-}" ]; then git -c http.extraHeader="Authorization: Bearer ${GITHUB_TOKEN}" "$@"; else git "$@"; fi
}

remove_legacy_proxy_config() {
  # These are only paths created by earlier versions of this project. Do not
  # inspect or alter any other virtual host, certificate, or proxy service.
  rm -f /etc/nginx/sites-enabled/sgcc-platform /etc/nginx/sites-enabled/tender-platform
  rm -f /etc/nginx/sites-available/sgcc-platform /etc/nginx/sites-available/tender-platform
  rm -f /etc/nginx/conf.d/sgcc-platform.conf /etc/nginx/conf.d/tender-platform.conf
  if command -v nginx >/dev/null 2>&1 && systemctl is-active --quiet nginx; then
    nginx -t && systemctl reload nginx
  fi
}

wait_for_platform() {
  local attempt
  echo "正在等待数据采集管理平台启动……"
  for attempt in $(seq 1 30); do
    if curl -fs "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then echo "平台健康检查通过。"; return 0; fi
    if systemctl is-failed --quiet "$SERVICE_NAME.service"; then journalctl -u "$SERVICE_NAME.service" -n 40 --no-pager >&2 || true; die "平台服务启动失败。"; fi
    sleep 2
  done
  systemctl status "$SERVICE_NAME.service" --no-pager >&2 || true
  die "平台在 60 秒内未通过健康检查。"
}

install_packages
id "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
if [ -d "$INSTALL_DIR/.git" ]; then git_repo -C "$INSTALL_DIR" pull --ff-only; else git_repo clone "$REPOSITORY_URL" "$INSTALL_DIR"; fi
remove_legacy_proxy_config
find "$INSTALL_DIR/app" -type f \( -name '*.py' -o -name '*.html' -o -name '*.css' \) -print0 | xargs -0 -r -n1 iconv -f UTF-8 -t UTF-8 >/dev/null || die "应用文件不是 UTF-8 编码，请重新下载项目后再安装。"
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip wheel
"$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"
if [ ! -f "$INSTALL_DIR/.env" ]; then
  cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
  sed -i "s|APP_SECRET=.*|APP_SECRET=$(openssl rand -hex 32)|;s|DATABASE_PATH=.*|DATABASE_PATH=$INSTALL_DIR/data/platform.sqlite3|;s|SCRAPLING_STORAGE_PATH=.*|SCRAPLING_STORAGE_PATH=$INSTALL_DIR/cache/scrapling-selectors.sqlite3|;s|CHROME_CDP_URL=.*|CHROME_CDP_URL=http://127.0.0.1:9222|" "$INSTALL_DIR/.env"
  chmod 600 "$INSTALL_DIR/.env"
fi
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$INSTALL_DIR/data" "$INSTALL_DIR/cache"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
bash "$INSTALL_DIR/scripts/install-local-model.sh" || echo "警告：本地模型部署失败，平台将自动使用规则/OCR模式。"
su -s /bin/bash "$SERVICE_USER" -c "set -a; source '$INSTALL_DIR/.env'; set +a; cd '$INSTALL_DIR'; .venv/bin/python -c 'from app.database import init_db; init_db()'"
cat >"/etc/systemd/system/$SERVICE_NAME.service" <<EOF
[Unit]
Description=招标采集管理平台
After=network-online.target
Wants=network-online.target

[Service]
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$INSTALL_DIR/.env
Environment=CACHE_DIR=$INSTALL_DIR/cache
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=$INSTALL_DIR/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port $PORT
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME.service"
wait_for_platform
echo "完成：访问 http://服务器IP:$PORT。"
echo "提示：本安装仅提供 HTTP 直连，不会配置 HTTPS、证书、Nginx、Caddy 或反向代理。"
