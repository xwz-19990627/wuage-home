#!/usr/bin/env bash
# 启动 wuage-home 家庭账本 App（默认仅本机 127.0.0.1:17623）
# 手机访问：WUAGE_WEB_HOST=0.0.0.0 bash scripts/start-wuage.sh
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
HOST="${WUAGE_WEB_HOST:-127.0.0.1}"
PORT="${WUAGE_WEB_PORT:-17623}"
PY=""
for c in python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then echo "需要 python3"; exit 1; fi
if [ -f data/web.pid ] && kill -0 "$(cat data/web.pid)" 2>/dev/null; then
  echo "已在运行 http://$HOST:$PORT (pid $(cat data/web.pid))"
  exit 0
fi
nohup "$PY" scripts/web.py --host "$HOST" --port "$PORT" > data/web.log 2>&1 &
echo $! > data/web.pid
sleep 1
if kill -0 "$(cat data/web.pid)" 2>/dev/null; then
  echo "已启动：http://$HOST:$PORT （数据：$PWD/data/ledger.db）"
  if command -v xdg-open >/dev/null 2>&1; then
    (xdg-open "http://$HOST:$PORT" >/dev/null 2>&1 &) || true
  fi
else
  echo "启动失败，日志："; tail -5 data/web.log; exit 1
fi