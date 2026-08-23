#!/usr/bin/env bash
# wuage-home 每日数据备份：全量拷贝账本 + 完整性校验，保留最近 7 份
# 数据根：$WUAGE_DATA（默认 /root/wuage/data）
set -euo pipefail

DATA_DIR="${WUAGE_DATA:-/root/wuage/data}"
DATA="$DATA_DIR/ledger.db"
BK="$DATA_DIR/backups"
[ -f "$DATA" ] || { echo "no ledger.db, skip"; exit 0; }
mkdir -p "$BK"
STAMP="$(date +%Y%m%d-%H%M%S)"
DEST="$BK/ledger-$STAMP.db"

cp "$DATA" "$DEST"

# 完整性校验：目标库可打开且能查到 transactions 表
if ! python3 -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('SELECT COUNT(*) FROM transactions').fetchone()" "$DEST" >/dev/null 2>&1; then
  echo "[$(date +%T)] backup VERIFY FAILED, removed $DEST"
  rm -f "$DEST"
  exit 1
fi

# 保留最近 7 份
ls -1t "$BK"/ledger-*.db 2>/dev/null | tail -n +8 | xargs -r rm -f
echo "[$(date +%T)] backup ok: $DEST ($(du -h "$DEST" | cut -f1))"
