#!/usr/bin/env bash
set -euo pipefail

SERVICE_USER="${SERVICE_USER:-tenderplatform}"
LLAMA_DIR="${LLAMA_DIR:-/opt/llama.cpp}"
MODEL_DIR="${MODEL_DIR:-/opt/local-llm/models}"
INSTALL_LOCAL_MODELS="${INSTALL_LOCAL_MODELS:-1}"
[ "$INSTALL_LOCAL_MODELS" != "0" ] || { echo "已按配置跳过本地模型部署。"; exit 0; }

arch="$(uname -m)"
case "$arch" in
  x86_64) asset_pattern='bin-ubuntu-x64.tar.gz' ;;
  aarch64|arm64) asset_pattern='bin-ubuntu-arm64.tar.gz' ;;
  *) echo "当前架构 $arch 暂无预编译 llama.cpp，本地模型已跳过。"; exit 0 ;;
esac

install -d -m 755 "$LLAMA_DIR" "$MODEL_DIR" /opt/local-llm/logs
if [ -x "$LLAMA_DIR/llama-b10327/llama-server" ] && [ ! -x "$LLAMA_DIR/llama-server" ]; then
  ln -s "$LLAMA_DIR/llama-b10327/llama-server" "$LLAMA_DIR/llama-server"
fi
if [ ! -x "$LLAMA_DIR/llama-server" ]; then
  echo "正在安装 llama.cpp CPU 推理服务……"
  release_json="$(curl -fsSL --retry 3 https://api.github.com/repos/ggml-org/llama.cpp/releases/latest)"
  asset_url="$(printf '%s' "$release_json" | python3 -c "import json,sys; p='$asset_pattern'; print(next((x['browser_download_url'] for x in json.load(sys.stdin)['assets'] if x['name'].endswith(p)), ''))")"
  [ -n "$asset_url" ] || { echo "未找到适用的 llama.cpp Linux 预编译包，本地模型已跳过。"; exit 0; }
  temporary="$(mktemp -d)"
  trap 'rm -rf "$temporary"' EXIT
  curl -fL --retry 3 "$asset_url" -o "$temporary/llama.tar.gz"
  tar -xzf "$temporary/llama.tar.gz" -C "$temporary"
  binary="$(find "$temporary" -type f -name llama-server -perm /111 | head -n 1)"
  [ -n "$binary" ] || { echo "llama.cpp 安装包不含 llama-server，本地模型已跳过。"; exit 0; }
  binary_dir="$(dirname "$binary")"
  cp -a "$binary_dir"/. "$LLAMA_DIR/"
fi

download_model() {
  local target="$1" url="$2" minimum="$3"
  if [ -f "$target" ] && [ "$(stat -c %s "$target")" -ge "$minimum" ]; then return; fi
  echo "正在下载 $(basename "$target")……"
  curl -fL --retry 5 --continue-at - "$url" -o "$target.part"
  mv "$target.part" "$target"
}

download_model "$MODEL_DIR/qwen3-0.6b-q8_0.gguf" \
  "https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/resolve/main/Qwen3-0.6B-Q8_0.gguf?download=true" 500000000
download_model "$MODEL_DIR/qwen3-1.7b-q4_k_m.gguf" \
  "https://huggingface.co/ggml-org/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q4_K_M.gguf?download=true" 1000000000

chown -R "$SERVICE_USER:$SERVICE_USER" /opt/local-llm
cat >/etc/systemd/system/local-llm-quick.service <<EOF
[Unit]
Description=SGCC local Qwen3 0.6B quick review (on demand)
After=network-online.target
Conflicts=local-llm-summary.service

[Service]
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=/opt/local-llm
ExecStart=$LLAMA_DIR/llama-server -m $MODEL_DIR/qwen3-0.6b-q8_0.gguf --host 127.0.0.1 --port 8081 --ctx-size 2048 --threads 3 --threads-batch 3 --parallel 1 --cache-type-k q8_0 --cache-type-v q8_0 --jinja --reasoning off --no-webui
Restart=no
RuntimeMaxSec=300
MemoryHigh=1100M
MemoryMax=1400M
CPUQuota=250%
NoNewPrivileges=true
PrivateTmp=true
EOF
cat >/etc/systemd/system/local-llm-summary.service <<EOF
[Unit]
Description=SGCC local Qwen3 1.7B detailed review (on demand)
After=network-online.target
Conflicts=local-llm-quick.service tender-manual-browser.service

[Service]
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=/opt/local-llm
ExecStart=$LLAMA_DIR/llama-server -m $MODEL_DIR/qwen3-1.7b-q4_k_m.gguf --host 127.0.0.1 --port 8082 --ctx-size 2048 --threads 3 --threads-batch 3 --parallel 1 --cache-type-k q8_0 --cache-type-v q8_0 --jinja --reasoning off --no-webui
Restart=no
RuntimeMaxSec=600
MemoryHigh=1800M
MemoryMax=2200M
CPUQuota=250%
NoNewPrivileges=true
PrivateTmp=true
EOF
systemctl daemon-reload
systemctl disable local-llm-quick.service local-llm-summary.service >/dev/null 2>&1 || true
install -d -m 755 /etc/sudoers.d
cat >/etc/sudoers.d/sgcc-local-model <<EOF
$SERVICE_USER ALL=(root) NOPASSWD: /bin/systemctl start --no-block local-llm-quick.service, /bin/systemctl start --no-block local-llm-summary.service, /usr/bin/systemctl start --no-block local-llm-quick.service, /usr/bin/systemctl start --no-block local-llm-summary.service
EOF
chmod 440 /etc/sudoers.d/sgcc-local-model
install -m 755 "$(dirname "$0")/local-model-dispatcher.py" /opt/local-llm/dispatcher.py
cat >/etc/systemd/system/local-model-dispatcher.service <<EOF
[Unit]
Description=Shared local model dispatcher for data collection platforms
After=network-online.target
Conflicts=local-llm-quick.service local-llm-summary.service

[Service]
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=/opt/local-llm
ExecStart=/usr/bin/python3 /opt/local-llm/dispatcher.py
Restart=on-failure
RestartSec=5
MemoryHigh=1900M
MemoryMax=2300M
CPUQuota=250%
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl disable --now local-llm-quick.service local-llm-summary.service >/dev/null 2>&1 || true
systemctl enable --now local-model-dispatcher.service
echo "本地 Qwen3 混合分析模型已部署为按需服务。"
