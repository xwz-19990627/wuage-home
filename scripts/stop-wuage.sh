#!/usr/bin/env bash
# 停止 wuage-home Web 面板
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PIDFILE="$REPO/data/web.pid"
if [ ! -f "$PIDFILE" ]; then echo "未在运行"; exit 0; fi
PID="$(cat "$PIDFILE")"
if kill -0 "$PID" 2>/dev/null; then kill "$PID" && echo "已停止 (pid $PID)"; else echo "进程已不存在"; fi
rm -f "$PIDFILE"
