#!/usr/bin/env bash
# 启动 wuage-home 工具 01「文件盒子」（默认仅本机 127.0.0.1:17624）
# 复用记账面板的启动风格：nohup + pid 文件 + 日志。
# 配置环境变量：FILEBOX_HOST / FILEBOX_PORT / FILEBOX_DATA / FILEBOX_RETENTION_DAYS
#               FILEBOX_MAX_MB / FILEBOX_QUOTA_MB / FILEBOX_CLEANUP_HOURS

set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
HOST="${FILEBOX_HOST:-127.0.0.1}"
PORT="${FILEBOX_PORT:-17624}"
DATA_ROOT="${WUAGE_DATA:-/root/wuage/data}"
DATA_DIR="${FILEBOX_DATA:-$DATA_ROOT/files}"
PY=""
for c in python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then echo "需要 python3"; exit 1; fi
mkdir -p "$DATA_ROOT"
if [ -f "$DATA_ROOT/filebox.pid" ] && kill -0 "$(cat "$DATA_ROOT/filebox.pid")" 2>/dev/null; then
  echo "已在运行 http://$HOST:$PORT (pid $(cat "$DATA_ROOT/filebox.pid"))"
  exit 0
fi
mkdir -p "$DATA_DIR"
nohup "$PY" scripts/filebox.py --host "$HOST" --port "$PORT" > "$DATA_ROOT/filebox.log" 2>&1 &
echo $! > "$DATA_ROOT/filebox.pid"
sleep 1
if kill -0 "$(cat "$DATA_ROOT/filebox.pid")" 2>/dev/null; then
  echo "文件盒子已启动：http://$HOST:$PORT（数据：$DATA_DIR）"
else
  echo "启动失败，日志："; tail -5 "$DATA_ROOT/filebox.log"; exit 1
fi
