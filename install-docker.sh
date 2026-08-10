#!/bin/sh
set -eu

# Portable Docker lifecycle manager for Linux, Synology DSM, fnOS and OpenWrt.
ACTION="${1:-install}"
case "$ACTION" in
  http://github.com/*|https://github.com/*)
    legacy_url="$ACTION"
    REPOSITORY_SLUG="$(printf '%s' "$legacy_url" | sed -E 's#https?://github.com/##;s#\.git$##')"
    ACTION=install
    ;;
esac
if [ "$ACTION" = "--help" ] || [ "$ACTION" = "help" ]; then
  echo "用法: install-docker.sh {install|update|uninstall|status} [--yes]"
  echo "环境变量: INSTALL_DIR DATA_DIR PLATFORM_PORT TZ BRANCH DELETE_DATA GITHUB_TOKEN"
  exit 0
fi
AUTO_CONFIRM="${2:-}"
REPOSITORY_SLUG="${REPOSITORY_SLUG:-dinggood615/sgcc-data-collection-platform}"
BRANCH="${BRANCH:-main}"
PLATFORM_PORT="${PLATFORM_PORT:-8000}"
TZ="${TZ:-Asia/Shanghai}"
PROJECT_NAME="${COMPOSE_PROJECT_NAME:-sgcc-data-collection-platform}"

detect_platform() {
  if [ -f /etc/synoinfo.conf ]; then echo synology
  elif [ -f /etc/openwrt_release ]; then echo openwrt
  elif grep -Eiq 'fnos|trim|飞牛' /etc/os-release 2>/dev/null; then echo fnos
  else echo linux
  fi
}

PLATFORM="$(detect_platform)"
if [ -z "${INSTALL_DIR:-}" ]; then
  case "$PLATFORM" in
    synology) INSTALL_DIR="/volume1/docker/sgcc-data-collection-platform" ;;
    openwrt) INSTALL_DIR="/opt/docker/sgcc-data-collection-platform" ;;
    *) INSTALL_DIR="/opt/sgcc-data-collection-platform" ;;
  esac
fi
DATA_DIR="${DATA_DIR:-$INSTALL_DIR/data}"

die() { echo "错误：$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "缺少命令：$1"; }
safe_target() {
  case "$1" in
    *'/../'*|*/..|*'/./'*|*/.) return 1 ;;
    ""|/|/opt|/mnt|/volume1|/volume2|/home|/root|/usr|/var) return 1 ;;
    /*) return 0 ;;
    *) return 1 ;;
  esac
}
safe_target "$INSTALL_DIR" || die "INSTALL_DIR 必须是明确的绝对子目录，不能使用系统根目录或磁盘根目录。"
safe_target "$DATA_DIR" || die "DATA_DIR 必须是明确的绝对子目录，不能使用系统根目录或磁盘根目录。"
case "$PLATFORM_PORT" in *[!0-9]*|"") die "PLATFORM_PORT 必须是 1-65535 的数字" ;; esac
[ "$PLATFORM_PORT" -ge 1 ] && [ "$PLATFORM_PORT" -le 65535 ] || die "PLATFORM_PORT 必须是 1-65535 的数字"

need docker
docker info >/dev/null 2>&1 || die "Docker 服务未运行或当前用户无权访问 Docker。"
if docker compose version >/dev/null 2>&1; then COMPOSE_MODE=v2
elif command -v docker-compose >/dev/null 2>&1 && docker-compose version >/dev/null 2>&1; then COMPOSE_MODE=v1
else die "未找到 Docker Compose；请安装 Compose v2 插件或 docker-compose。"
fi
compose() {
  if [ "$COMPOSE_MODE" = v2 ]; then (cd "$INSTALL_DIR" && docker compose -p "$PROJECT_NAME" "$@")
  else (cd "$INSTALL_DIR" && docker-compose -p "$PROJECT_NAME" "$@")
  fi
}

random_hex() {
  if command -v openssl >/dev/null 2>&1; then openssl rand -hex 32
  else need od; od -An -N32 -tx1 /dev/urandom | tr -d ' \n'
  fi
}

download_source() {
  need curl; need tar; need mktemp
  temp_dir="$(mktemp -d)"
  trap 'rm -rf "$temp_dir"' EXIT HUP INT TERM
  archive="$temp_dir/source.tar.gz"
  auth_header=""
  [ -z "${GITHUB_TOKEN:-}" ] || auth_header="Authorization: Bearer $GITHUB_TOKEN"
  if [ -n "$auth_header" ]; then
    curl -fL --retry 3 --connect-timeout 15 -H "$auth_header" "https://github.com/$REPOSITORY_SLUG/archive/refs/heads/$BRANCH.tar.gz" -o "$archive"
  else
    curl -fL --retry 3 --connect-timeout 15 "https://github.com/$REPOSITORY_SLUG/archive/refs/heads/$BRANCH.tar.gz" -o "$archive"
  fi
  tar -xzf "$archive" -C "$temp_dir"
  source_dir="$(find "$temp_dir" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
  [ -n "$source_dir" ] || die "无法识别下载的项目源码。"
  mkdir -p "$INSTALL_DIR"
  cp -R "$source_dir"/. "$INSTALL_DIR"/
}

write_env_value() {
  key="$1"; value="$2"; file="$INSTALL_DIR/.env"
  if grep -q "^${key}=" "$file"; then
    escaped="$(printf '%s' "$value" | sed 's/[|&]/\\&/g')"
    sed -i "s|^${key}=.*|${key}=${escaped}|" "$file"
  else
    printf '%s=%s\n' "$key" "$value" >>"$file"
  fi
}

prepare_environment() {
  mkdir -p "$DATA_DIR"
  if [ ! -f "$INSTALL_DIR/.env" ]; then
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    write_env_value APP_SECRET "$(random_hex)"
    write_env_value ADMIN_USERNAME admin
    write_env_value ADMIN_PASSWORD admin
  fi
  write_env_value DATABASE_PATH /data/platform.sqlite3
  write_env_value SCRAPLING_STORAGE_PATH /data/scrapling-selectors.sqlite3
  write_env_value DATA_DIR "$DATA_DIR"
  write_env_value PLATFORM_PORT "$PLATFORM_PORT"
  write_env_value TZ "$TZ"
  write_env_value CHROME_CDP_URL ""
  chmod 600 "$INSTALL_DIR/.env" 2>/dev/null || true
}

wait_healthy() {
  echo "正在等待容器健康检查……"
  attempt=1
  while [ "$attempt" -le 60 ]; do
    if compose exec -T tender-platform python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=3)" >/dev/null 2>&1; then
      echo "容器健康检查通过。"
      return 0
    fi
    sleep 2; attempt=$((attempt + 1))
  done
  compose ps >&2 || true
  compose logs --tail=80 tender-platform >&2 || true
  die "容器在 120 秒内未通过健康检查。"
}

backup_before_update() {
  container_id="$(compose ps -q tender-platform 2>/dev/null || true)"
  if [ -n "$container_id" ] && [ "$(docker inspect -f '{{.State.Running}}' "$container_id" 2>/dev/null || true)" = true ]; then
    compose exec -T tender-platform python -c "from app.database import backup_database; print(backup_database(14))" >/dev/null 2>&1 || true
  fi
}

install_or_update() {
  if [ "$ACTION" = update ] && [ ! -f "$INSTALL_DIR/docker-compose.yml" ]; then
    die "未找到现有 Docker 安装：$INSTALL_DIR"
  fi
  [ "$ACTION" != update ] || backup_before_update
  download_source
  prepare_environment
  cd "$INSTALL_DIR"
  compose up -d --build --remove-orphans
  wait_healthy
  echo "完成：平台=$PLATFORM，架构=$(uname -m)，Compose=$COMPOSE_MODE"
  echo "访问：http://设备IP:$PLATFORM_PORT"
  echo "数据目录：$DATA_DIR"
  echo "初始账户：admin / admin（首次登录后请立即修改）"
}

uninstall_platform() {
  [ "$AUTO_CONFIRM" = "--yes" ] || {
    printf '将停止容器并删除程序目录 %s。输入 DELETE 确认: ' "$INSTALL_DIR"
    if [ -r /dev/tty ]; then read -r answer </dev/tty
    else die "当前没有交互终端；无人值守卸载请使用 uninstall --yes。"
    fi
    [ "$answer" = DELETE ] || { echo "已取消。"; exit 0; }
  }
  if [ -f "$INSTALL_DIR/docker-compose.yml" ]; then
    cd "$INSTALL_DIR"; compose down --remove-orphans || true
  fi
  if [ "${DELETE_DATA:-0}" = 1 ] && [ "$DATA_DIR" != "$INSTALL_DIR/data" ]; then
    safe_target "$DATA_DIR" || die "拒绝删除不安全的数据目录。"
    rm -rf "$DATA_DIR"
    echo "已删除外部数据目录：$DATA_DIR"
  fi
  rm -rf "$INSTALL_DIR"
  echo "已卸载平台。Docker 本身未卸载。"
  case "$DATA_DIR" in
    "$INSTALL_DIR"/*) echo "安装目录内的数据已一并删除。" ;;
    *) [ "${DELETE_DATA:-0}" = 1 ] || echo "外部数据目录已保留：$DATA_DIR" ;;
  esac
}

echo "检测到平台：$PLATFORM；安装目录：$INSTALL_DIR；数据目录：$DATA_DIR"
case "$ACTION" in
  install|update) install_or_update ;;
  uninstall) uninstall_platform ;;
  status) cd "$INSTALL_DIR" && compose ps ;;
  *) die "未知操作：$ACTION；支持 install、update、uninstall、status" ;;
esac
