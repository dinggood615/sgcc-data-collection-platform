#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/sgcc-data-collection-platform}"
REPOSITORY_URL="${REPOSITORY_URL:-https://github.com/dinggood615/sgcc-data-collection-platform.git}"

if [ "${EUID}" -ne 0 ]; then echo "请使用 sudo 运行"; exit 1; fi
echo "1) 原生 Linux 安装  2) Docker 安装  3) 原生 Linux 更新  4) Docker 更新  5) 卸载"
read -r -p "请选择 [1-5]: " choice
case "$choice" in
  1) bash install-linux.sh "$REPOSITORY_URL" ;;
  2) sh install-docker.sh install ;;
  3) INSTALL_DIR="$INSTALL_DIR" bash update-linux.sh ;;
  4) INSTALL_DIR="$INSTALL_DIR" sh install-docker.sh update ;;
  5)
    read -r -p "确认删除 $INSTALL_DIR 及其采集数据？输入 DELETE 确认: " confirm
    [ "$confirm" = "DELETE" ] || { echo "已取消"; exit 0; }
    INSTALL_DIR="$INSTALL_DIR" bash uninstall-linux.sh --yes
    ;;
  *) echo "无效选择"; exit 1 ;;
esac
