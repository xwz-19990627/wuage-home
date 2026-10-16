#!/usr/bin/env bash
# 停止 wuage-home 工具 03「健康助手」
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
if systemctl list-unit-files 2>/dev/null | grep -q '^wuage-health.service'; then
  systemctl stop wuage-health.service && echo "已停止 wuage-health.service"
  exit 0
fi
PIDFILE="$REPO/data/health.pid"
if [ ! -f "$PIDFILE" ]; then echo "未在运行"; exit 0; fi
PID="$(cat "$PIDFILE")"
if kill -0 "$PID" 2>/dev/null; then kill "$PID" && echo "已停止 (pid $PID)"; else echo "进程已不存在"; fi
rm -f "$PIDFILE"
