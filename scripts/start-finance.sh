#!/usr/bin/env bash
# 启动 wuage-home 工具 02「理财助手」（默认仅本机 127.0.0.1:17625）
# 配置环境变量：FINANCE_HOST / FINANCE_PORT / FINANCE_DATA
#               WUAGE_PARSE_API / WUAGE_PARSE_MODEL（问答/复盘用）

set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
# 2026-09-07 起由 systemd 托管：优先走 systemctl，避免 nohup 进程随会话结束被杀（曾致网关 502）
if systemctl list-unit-files 2>/dev/null | grep -q '^wuage-finance.service'; then
  systemctl start wuage-finance.service && echo "已通过 systemd 启动 wuage-finance.service（127.0.0.1:17625）"
  exit 0
fi
HOST="${FINANCE_HOST:-127.0.0.1}"
PORT="${FINANCE_PORT:-17625}"
DATA_DIR="${FINANCE_DATA:-${WUAGE_DATA:-/root/wuage/data}/finance}"
PY=""
for c in python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then echo "需要 python3"; exit 1; fi
mkdir -p "$(dirname "$REPO/data/finance.pid")"
if [ -f "$REPO/data/finance.pid" ] && kill -0 "$(cat "$REPO/data/finance.pid")" 2>/dev/null; then
  echo "已在运行 http://$HOST:$PORT (pid $(cat "$REPO/data/finance.pid"))"
  exit 0
fi
mkdir -p "$DATA_DIR"
nohup "$PY" scripts/finance.py --host "$HOST" --port "$PORT" > "$REPO/data/finance.log" 2>&1 &
echo $! > "$REPO/data/finance.pid"
sleep 1
if kill -0 "$(cat "$REPO/data/finance.pid")" 2>/dev/null; then
  echo "理财助手已启动：http://$HOST:$PORT（数据：$DATA_DIR）"
else
  echo "启动失败，日志："; tail -5 "$REPO/data/finance.log"; exit 1
fi