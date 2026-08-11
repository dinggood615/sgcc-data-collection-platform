#!/usr/bin/env bash
set -euo pipefail

# Keep template and source decoding deterministic on minimal Linux images.
export LANG="${LANG:-C.UTF-8}"
export LC_ALL="${LC_ALL:-C.UTF-8}"

# One-command native installer for systemd Linux distributions.
REPOSITORY_URL="${1:-https://github.com/dinggood615/sgcc-data-collection-platform.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/sgcc-data-collection-platform}"
SERVICE_USER="tenderplatform"
PUBLIC_PORT="${PORT:-5555}"
BACKEND_PORT="${BACKEND_PORT:-8001}"
SERVICE_NAME="sgcc-platform"
TLS_DIR=/etc/sgcc-platform/tls
DOMAIN="${DOMAIN:-}"
LETSENCRYPT_EMAIL="${LETSENCRYPT_EMAIL:-}"

prompt_domain() {
  [ -n "$DOMAIN" ] && return
  [ -r /dev/tty ] || return
  printf '请输入平台域名（例如 sgcc.example.com；直接回车则使用自签名证书）: ' >/dev/tty
  read -r DOMAIN </dev/tty
  if [ -n "$DOMAIN" ] && [ -z "$LETSENCRYPT_EMAIL" ]; then
    printf '请输入证书通知邮箱（可直接回车跳过）: ' >/dev/tty
    read -r LETSENCRYPT_EMAIL </dev/tty
  fi
}

die() { echo "错误：$*" >&2; exit 1; }
[ "${EUID}" -eq 0 ] || die "请使用 sudo 运行"
[ -d /run/systemd/system ] || die "原生安装需要 systemd；容器环境请使用 Docker 安装。"

wait_for_platform() {
  local attempt
  echo "正在等待数据采集管理平台启动……"
  for attempt in $(seq 1 30); do
    if curl -fs "http://127.0.0.1:$BACKEND_PORT/healthz" >/dev/null 2>&1; then
      echo "平台后端健康检查通过。"
      return 0
    fi
    if systemctl is-failed --quiet "$SERVICE_NAME.service"; then
      journalctl -u "$SERVICE_NAME.service" -n 40 --no-pager >&2 || true
      die "平台服务启动失败，以上是最近的服务日志。"
    fi
    sleep 2
  done
  systemctl status "$SERVICE_NAME.service" --no-pager >&2 || true
  journalctl -u "$SERVICE_NAME.service" -n 40 --no-pager >&2 || true
  die "平台在 60 秒内未通过健康检查，以上是服务状态和最近日志。"
}

verify_https_entry() {
  local attempt
  for attempt in $(seq 1 15); do
    if curl -kfs "https://127.0.0.1:$PUBLIC_PORT/healthz" >/dev/null 2>&1; then
      echo "HTTPS 访问入口检查通过。"
      return 0
    fi
    sleep 1
  done
  nginx -t >&2 || true
  die "平台后端正常，但 HTTPS 入口检查失败；请检查 Nginx 服务和端口 $PUBLIC_PORT。"
}

install_packages() {
  if command -v apt-get >/dev/null; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates git python3 python3-venv python3-pip build-essential openssl curl nginx libreoffice-core libreoffice-writer libreoffice-calc poppler-utils 7zip unar tesseract-ocr tesseract-ocr-chi-sim
  elif command -v dnf >/dev/null; then
    dnf install -y ca-certificates git python3 python3-pip gcc gcc-c++ make openssl curl nginx
    dnf install -y libreoffice-headless libreoffice-writer libreoffice-calc poppler-utils p7zip p7zip-plugins tesseract || echo "警告：部分附件转换工具未安装，请按发行版仓库补充。"
  elif command -v yum >/dev/null; then
    yum install -y ca-certificates git python3 python3-pip gcc gcc-c++ make openssl curl nginx
    yum install -y libreoffice-headless libreoffice-writer libreoffice-calc poppler-utils p7zip p7zip-plugins tesseract || echo "警告：部分附件转换工具未安装，请按发行版仓库补充。"
  elif command -v zypper >/dev/null; then
    zypper --non-interactive install ca-certificates git python3 python3-pip gcc gcc-c++ make openssl curl nginx
    zypper --non-interactive install libreoffice poppler-tools p7zip tesseract-ocr || echo "警告：部分附件转换工具未安装，请按发行版仓库补充。"
  elif command -v pacman >/dev/null; then
    pacman -Sy --noconfirm ca-certificates git python python-pip base-devel openssl curl nginx
    pacman -Sy --noconfirm libreoffice-fresh poppler p7zip tesseract tesseract-data-chi_sim || echo "警告：部分附件转换工具未安装，请按发行版仓库补充。"
  else
    die "未识别的软件包管理器。支持 apt、dnf、yum、zypper、pacman。"
  fi
}

install_certbot() {
  if command -v certbot >/dev/null; then return; fi
  if command -v apt-get >/dev/null; then DEBIAN_FRONTEND=noninteractive apt-get install -y certbot
  elif command -v dnf >/dev/null; then dnf install -y certbot
  elif command -v yum >/dev/null; then yum install -y certbot
  elif command -v zypper >/dev/null; then zypper --non-interactive install certbot
  elif command -v pacman >/dev/null; then pacman -Sy --noconfirm certbot
  else die "无法安装 Certbot；请手动安装后重新执行。"
  fi
}

valid_domain() {
  [ -z "$DOMAIN" ] || printf '%s' "$DOMAIN" | grep -Eq '^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$'
}

open_tls_firewall_ports() {
  # Ubuntu/Debian deployments commonly use UFW.  Opening these ports here
  # prevents a successful DNS update from still failing the ACME HTTP check.
  if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q 'Status: active'; then
    ufw allow 80/tcp
    ufw allow 443/tcp
  fi
}

git_repo() {
  if [ -n "${GITHUB_TOKEN:-}" ]; then
    git -c http.extraHeader="Authorization: Bearer ${GITHUB_TOKEN}" "$@"
  else
    git "$@"
  fi
}

prompt_domain
valid_domain || die "DOMAIN 格式不正确；请只填写域名，例如 tender.example.com。"
install_packages
id "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
if [ -d "$INSTALL_DIR/.git" ]; then git_repo -C "$INSTALL_DIR" pull --ff-only; else git_repo clone "$REPOSITORY_URL" "$INSTALL_DIR"; fi
find "$INSTALL_DIR/app" -type f \( -name '*.py' -o -name '*.html' -o -name '*.css' \) -print0 |
  xargs -0 -r -n1 iconv -f UTF-8 -t UTF-8 >/dev/null || die "应用文件不是 UTF-8 编码，请重新下载项目后再安装。"
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip wheel
"$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"
if [ ! -f "$INSTALL_DIR/.env" ]; then
  cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
  sed -i "s|APP_SECRET=.*|APP_SECRET=$(openssl rand -hex 32)|;s|ADMIN_USERNAME=.*|ADMIN_USERNAME=admin|;s|ADMIN_PASSWORD=.*|ADMIN_PASSWORD=admin|;s|DATABASE_PATH=.*|DATABASE_PATH=$INSTALL_DIR/data/platform.sqlite3|;s|SCRAPLING_STORAGE_PATH=.*|SCRAPLING_STORAGE_PATH=$INSTALL_DIR/data/scrapling-selectors.sqlite3|;s|CHROME_CDP_URL=.*|CHROME_CDP_URL=http://127.0.0.1:9222|" "$INSTALL_DIR/.env"
  chmod 600 "$INSTALL_DIR/.env"
fi
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$INSTALL_DIR/data"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
# Initialize SQLite before the authenticated web endpoint is exposed.  This
# prevents a first-request race from creating an empty database file.
su -s /bin/bash "$SERVICE_USER" -c "set -a; source '$INSTALL_DIR/.env'; set +a; cd '$INSTALL_DIR'; .venv/bin/python -c 'from app.database import init_db; init_db()'"
# Invoke explicitly with bash: Git mirrors may not preserve executable bits.
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
ExecStart=$INSTALL_DIR/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port $BACKEND_PORT
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

install -d -m 700 "$TLS_DIR"
if [ ! -f "$TLS_DIR/cert.pem" ] || [ ! -f "$TLS_DIR/key.pem" ]; then
  openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 3650 -subj "/CN=$(hostname -f 2>/dev/null || hostname)" -keyout "$TLS_DIR/key.pem" -out "$TLS_DIR/cert.pem"
  chmod 600 "$TLS_DIR/key.pem"
fi
if [ -d /etc/nginx/sites-available ]; then
  NGINX_SITE=/etc/nginx/sites-available/sgcc-platform
  NGINX_ENABLED=/etc/nginx/sites-enabled/sgcc-platform
  install -m 644 "$INSTALL_DIR/nginx/tender-platform.conf" "$NGINX_SITE"
  ln -sfn "$NGINX_SITE" "$NGINX_ENABLED"
  rm -f /etc/nginx/sites-enabled/default
else
  NGINX_SITE=/etc/nginx/conf.d/sgcc-platform.conf
  install -m 644 "$INSTALL_DIR/nginx/tender-platform.conf" "$NGINX_SITE"
fi
sed -i "s|/etc/tender-platform/tls|$TLS_DIR|g;s|127.0.0.1:8000|127.0.0.1:$BACKEND_PORT|g" "$NGINX_SITE"
# Keep standard HTTPS 443 for Enterprise WeChat.  Avoid duplicate listen
# directives when the dashboard port itself is configured as 443.
if [ "$PUBLIC_PORT" != "443" ]; then
  sed -i "s/listen 5555 ssl;/listen $PUBLIC_PORT ssl;/" "$NGINX_SITE"
else
  sed -i "s/listen 5555 ssl;/listen 443 ssl;/" "$NGINX_SITE"
fi
if [ -n "$DOMAIN" ]; then
  sed -i "s/server_name _;/server_name $DOMAIN;/" "$NGINX_SITE"
  sed -i "/server_name $DOMAIN;/a\\    add_header Strict-Transport-Security \"max-age=31536000\" always;" "$NGINX_SITE"
  if [ "$PUBLIC_PORT" != "443" ]; then
    sed -i "/listen $PUBLIC_PORT ssl;/a\\    listen 443 ssl;" "$NGINX_SITE"
  fi
fi
nginx -t
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME.service"
systemctl enable nginx.service
systemctl restart nginx.service
wait_for_platform
if [ -n "$DOMAIN" ]; then
  echo "正在为 $DOMAIN 申请 Let's Encrypt 证书；请确认 DNS 已解析到本服务器且已放行 80、443。"
  open_tls_firewall_ports
  install_certbot
  systemctl stop nginx.service
  CERTBOT_ARGS=(certonly --standalone --non-interactive --agree-tos --keep-until-expiring -d "$DOMAIN")
  if [ -n "$LETSENCRYPT_EMAIL" ]; then CERTBOT_ARGS+=(--email "$LETSENCRYPT_EMAIL"); else CERTBOT_ARGS+=(--register-unsafely-without-email); fi
  if ! certbot "${CERTBOT_ARGS[@]}"; then
    systemctl start nginx.service
    die "证书申请失败。请检查域名解析和 80/443 入站规则后重试。"
  fi
  ln -sfn "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" "$TLS_DIR/cert.pem"
  ln -sfn "/etc/letsencrypt/live/$DOMAIN/privkey.pem" "$TLS_DIR/key.pem"
  install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
  cat > /etc/letsencrypt/renewal-hooks/deploy/sgcc-platform-nginx <<'EOF'
#!/usr/bin/env sh
set -eu
nginx -t
systemctl reload nginx.service
EOF
  chmod 755 /etc/letsencrypt/renewal-hooks/deploy/sgcc-platform-nginx
  cat >>"$NGINX_SITE" <<EOF

server {
    listen 80;
    server_name $DOMAIN;
    return 301 https://\$host\$request_uri;
}
EOF
  nginx -t
  systemctl start nginx.service
  systemctl reload nginx.service
  echo "企业微信回调地址：https://$DOMAIN/wecom/callback"
else
  echo "提示：当前使用自签名证书；企业微信聊天助手需要有效域名 HTTPS 证书。"
fi
verify_https_entry
echo "初始账户：admin / admin（请在首次登录后修改）"
if [ -n "$DOMAIN" ]; then echo "完成：访问 https://$DOMAIN。"; else echo "完成：访问 https://服务器IP:$PUBLIC_PORT。"; fi
echo "国网固定站点已自动适配，无需添加站点或人工验证。"
