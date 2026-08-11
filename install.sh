#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_URL="${1:-https://github.com/dinggood615/sgcc-data-collection-platform.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/sgcc-data-collection-platform}"

if [ "${EUID}" -ne 0 ]; then
  echo "请使用 sudo 运行：sudo bash install.sh"
  exit 1
fi

apt-get update
apt-get install -y ca-certificates curl git openssl
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi

if [ -d "$INSTALL_DIR/.git" ]; then
  git -C "$INSTALL_DIR" pull --ff-only
else
  git clone "$REPOSITORY_URL" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"
if [ ! -f .env ]; then
  cp .env.example .env
  SECRET="$(openssl rand -hex 32)"
  sed -i "s|APP_SECRET=.*|APP_SECRET=$SECRET|;s|ADMIN_PASSWORD=.*|ADMIN_PASSWORD=admin|" .env
  chmod 600 .env
  echo "初始账户：admin / admin（请在首次登录后修改）"
  echo "请立即编辑 $INSTALL_DIR/.env 填入 SMTP 参数后，再访问 http://服务器IP:8000"
fi
docker compose up -d --build
docker compose ps
