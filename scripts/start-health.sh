#!/usr/bin/env bash
# 启动 wuage-home 工具 03「健康助手」（默认仅本机 127.0.0.1:17626）
# 配置环境变量：HEALTH_HOST / HEALTH_PORT / HEALTH_DATA
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
# 2026-09-07 起由 systemd 托管：优先走 systemctl，避免 nohup 进程随会话结束被杀（曾致网关 502）
if systemctl list-unit-files 2>/dev/null | grep -q '^wuage-health.service'; then
  systemctl start wuage-health.service && echo "已通过 systemd 启动 wuage-health.service（127.0.0.1:17626）"
  exit 0
fi
HOST="${HEALTH_HOST:-127.0.0.1}"
PORT="${HEALTH_PORT:-17626}"
DATA_DIR="${HEALTH_DATA:-${WUAGE_DATA:-/root/wuage/data}/health}"
PY=""
for c in python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then echo "需要 python3"; exit 1; fi
mkdir -p "$(dirname "$REPO/data/health.pid")"
if [ -f "$REPO/data/health.pid" ] && kill -0 "$(cat "$REPO/data/health.pid")" 2>/dev/null; then
  echo "已在运行 http://$HOST:$PORT (pid $(cat "$REPO/data/health.pid"))"
  exit 0
fi
mkdir -p "$DATA_DIR"
nohup "$PY" scripts/health.py --host "$HOST" --port "$PORT" > "$REPO/data/health.log" 2>&1 &
echo $! > "$REPO/data/health.pid"
sleep 1
if kill -0 "$(cat "$REPO/data/health.pid")" 2>/dev/null; then
  echo "健康助手已启动：http://$HOST:$PORT（数据：$DATA_DIR）"
else
  echo "启动失败，日志："; tail -5 "$REPO/data/health.log"; exit 1
fi
