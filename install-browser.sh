#!/usr/bin/env bash
set -euo pipefail
INSTALL_DIR="${1:-/opt/sgcc-data-collection-platform}"
SERVICE_USER="${2:-tenderplatform}"

[ "${EUID}" -eq 0 ] || { echo "请使用 sudo 运行" >&2; exit 1; }
if command -v apt-get >/dev/null; then
  apt-get update; DEBIAN_FRONTEND=noninteractive apt-get install -y xvfb x11vnc novnc websockify wget ca-certificates
  if ! command -v google-chrome >/dev/null && ! command -v chromium >/dev/null; then
    package_file="$(mktemp /tmp/google-chrome.XXXXXX.deb)"; wget -q -O "$package_file" https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb; apt-get install -y "$package_file"; rm -f "$package_file"
  fi
elif command -v dnf >/dev/null; then
  dnf install -y xorg-x11-server-Xvfb x11vnc novnc python3-websockify chromium ca-certificates
elif command -v yum >/dev/null; then
  yum install -y xorg-x11-server-Xvfb x11vnc novnc python3-websockify chromium ca-certificates
elif command -v zypper >/dev/null; then
  zypper --non-interactive install xorg-x11-server x11vnc novnc python3-websockify chromium
elif command -v pacman >/dev/null; then
  pacman -Sy --noconfirm xorg-server-xvfb x11vnc novnc websockify chromium
else
  echo "当前发行版无法自动安装可视 Chrome；可继续使用静态采集。" >&2; exit 1
fi
BROWSER_BIN="$(command -v google-chrome || command -v google-chrome-stable || command -v chromium || command -v chromium-browser || true)"
[ -n "$BROWSER_BIN" ] || { echo "未找到 Chrome/Chromium" >&2; exit 1; }
for required_bin in Xvfb x11vnc websockify; do
  command -v "$required_bin" >/dev/null || { echo "缺少可视浏览器依赖：$required_bin" >&2; exit 1; }
done
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 700 "$INSTALL_DIR/browser-profile"
NOVNC_WEB="/usr/share/novnc"; [ -d "$NOVNC_WEB" ] || NOVNC_WEB="/usr/share/webapps/novnc"
printf '%s\n' '#!/usr/bin/env bash' 'set -euo pipefail' 'export DISPLAY=:99' 'Xvfb :99 -screen 0 1366x768x24 -nolisten tcp &' 'x11vnc -display :99 -forever -shared -nopw -localhost -rfbport 5900 &' "websockify --web=$NOVNC_WEB 127.0.0.1:6080 127.0.0.1:5900 &" "exec $BROWSER_BIN --no-first-run --no-default-browser-check --disable-gpu --disable-dev-shm-usage --password-store=basic --user-data-dir=$INSTALL_DIR/browser-profile --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --remote-allow-origins=* about:blank" >"$INSTALL_DIR/manual-browser.sh"
chown "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR/manual-browser.sh"; chmod 750 "$INSTALL_DIR/manual-browser.sh"
printf '%s\n' '[Unit]' 'Description=Persistent Chrome for tender site manual verification' 'After=network-online.target' '' '[Service]' "User=$SERVICE_USER" "Group=$SERVICE_USER" "WorkingDirectory=$INSTALL_DIR" "ExecStart=$INSTALL_DIR/manual-browser.sh" 'Restart=always' '' '[Install]' 'WantedBy=multi-user.target' >/etc/systemd/system/tender-manual-browser.service
systemctl daemon-reload; systemctl enable --now tender-manual-browser.service
