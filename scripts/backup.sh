#!/usr/bin/env bash
# wuage-home 每日数据备份（2026-10-16 起覆盖整个数据根，不再只备 ledger.db）
#
# 覆盖范围：$WUAGE_DATA 下除 backups/ 与 logs/ 外的全部内容 ——
#   ledger.db（账本）· finance/finance.db（理财）· health/health.db + health/files（健康档案与报告原件）
#   · files/（文件盒子）· import_detail_archive.json 等
# 做法：
#   1) 所有 sqlite 库用 sqlite3 backup API 取**一致性快照**（避免边写边拷出半个库）；
#   2) 其余文件原样拷贝；两者合成一份 tar.gz；
#   3) 另存一份 ledger.db 单文件快照（历史习惯，恢复单表最方便）；
#   4) tar 可读性 + 关键条目校验，失败就删掉这份备份并退非零（不让坏备份占位）。
# 保留：整包最近 WUAGE_BACKUP_KEEP 份（默认 7），ledger-*.db 最近 7 份。
#
# 用法：
#   bash scripts/backup.sh                 # 正常备份（cron 用）
#   bash scripts/backup.sh --verify        # 校验最近一份整包（解开到临时目录跑 integrity_check）
#   bash scripts/backup.sh --verify <file> # 校验指定整包
set -euo pipefail

DATA_DIR="${WUAGE_DATA:-/root/wuage/data}"
BK="$DATA_DIR/backups"
LOG_DIR="$DATA_DIR/logs"
KEEP="${WUAGE_BACKUP_KEEP:-7}"
STAMP="$(date +%Y%m%d-%H%M%S)"

log() { echo "[$(date +%T)] $*"; }

# python 解释器：优先 3.11（其余模块也用这个），退到系统 python3。
# ⚠️ 服务器自带的 /usr/bin/python3 是 3.6，**没有 sqlite3.Connection.backup**，
#    所以这里必须挑到 3.7+，否则快照会退化（脚本内有 VACUUM INTO / 文件拷贝兜底）。
PY=""
for c in python3.11 python3.10 python3.9 python3; do
  if command -v "$c" >/dev/null 2>&1; then
    if "$c" -c 'import sqlite3,sys; sys.exit(0 if hasattr(sqlite3.Connection,"backup") else 1)' 2>/dev/null; then
      PY="$c"; break
    fi
    [ -n "$PY" ] || PY="$c"   # 都不支持 backup 时至少留一个可用解释器
  fi
done
[ -n "$PY" ] || { echo "找不到可用的 python3"; exit 1; }

# ---------- 校验模式 ----------
if [ "${1:-}" = "--verify" ]; then
  ARCHIVE="${2:-}"
  if [ -z "$ARCHIVE" ]; then
    ARCHIVE="$(ls -1t "$BK"/wuage-data-*.tar.gz 2>/dev/null | head -1 || true)"
  fi
  [ -n "$ARCHIVE" ] && [ -f "$ARCHIVE" ] || { echo "找不到整包备份可校验"; exit 1; }
  TMP="$(mktemp -d /tmp/wuage-verify-XXXXXX)"
  trap 'rm -rf "$TMP"' EXIT
  tar -xzf "$ARCHIVE" -C "$TMP"
  "$PY" - "$TMP" <<'PY'
import os, sqlite3, sys
root = sys.argv[1]
bad = 0
dbs = []
for dirpath, dirnames, filenames in os.walk(root):
    for fn in filenames:
        if fn.endswith((".db", ".sqlite", ".sqlite3")):
            dbs.append(os.path.join(dirpath, fn))
for db in sorted(dbs):
    rel = os.path.relpath(db, root)
    try:
        con = sqlite3.connect(db)
        res = con.execute("PRAGMA integrity_check").fetchone()[0]
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        con.close()
    except Exception as exc:                       # noqa: BLE001
        print(f"  ✗ {rel}: 打不开（{exc}）")
        bad += 1
        continue
    if res != "ok":
        print(f"  ✗ {rel}: integrity_check={res}")
        bad += 1
    else:
        print(f"  ✓ {rel}: ok（{len(tables)} 张表）")
if not dbs:
    print("  ✗ 包里一个数据库都没有")
    bad += 1
sys.exit(1 if bad else 0)
PY
  echo "校验通过：$ARCHIVE"
  exit 0
fi

# ---------- 备份模式 ----------
if [ ! -d "$DATA_DIR" ]; then log "数据根不存在：$DATA_DIR，跳过"; exit 0; fi
mkdir -p "$BK" "$LOG_DIR"

STAGE="$(mktemp -d /tmp/wuage-backup-XXXXXX)"
MAN_STAGE="$(mktemp /tmp/wuage-man-stage-XXXXXX)"
MAN_TAR="$(mktemp /tmp/wuage-man-tar-XXXXXX)"
cleanup() { rm -rf "$STAGE"; rm -f "$MAN_STAGE" "$MAN_TAR"; }
trap cleanup EXIT

# 1) sqlite 一致性快照（在写库时也安全）
SNAP_LIST="$("$PY" - "$DATA_DIR" "$STAGE" <<'PY'
import os, shutil, sqlite3, sys
root, stage = sys.argv[1], sys.argv[2]
skip = {"backups", "logs"}


def snapshot(src, dst):
    """一致性快照：优先 sqlite backup API（3.7+），退 VACUUM INTO（sqlite 3.27+），最后兜底文件拷贝。"""
    con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        if hasattr(con, "backup"):
            out = sqlite3.connect(dst)
            try:
                with out:
                    con.backup(out)
            finally:
                out.close()
            return "backup"
        try:
            con.execute("VACUUM INTO ?", (dst,))
            return "vacuum"
        except Exception:                              # noqa: BLE001
            pass
    finally:
        con.close()
    shutil.copy2(src, dst)
    return "copy"


done = []
for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = [d for d in dirnames if d not in skip]
    for fn in filenames:
        if not fn.endswith((".db", ".sqlite", ".sqlite3")):
            continue
        src = os.path.join(dirpath, fn)
        rel = os.path.relpath(src, root)
        dst = os.path.join(stage, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        how = snapshot(src, dst)
        done.append(rel if how == "backup" else f"{rel}({how})")
print(" ".join(done))
PY
)"
[ -n "$SNAP_LIST" ] || { log "没找到任何数据库，跳过"; exit 0; }
log "sqlite 快照：$SNAP_LIST"

# 2) 非数据库文件（文件盒子 / 报告原件 / 归档 json …）原样拷进 stage
tar -cf - --exclude=backups --exclude=logs --exclude='*.db' --exclude='*.sqlite3' \
    -C "$DATA_DIR" . | tar -xf - -C "$STAGE"

# 3) 打包
DEST="$BK/wuage-data-$STAMP.tar.gz"
tar -czf "$DEST" -C "$STAGE" .

# 4) 校验：包可读 + 内容与阶段的文件清单**逐条一致**（不写死模块名单，
#    这样以后加模块自动纳入校验，也不会因为某个模块还没建库就误判失败）
if ! tar -tzf "$DEST" >/dev/null 2>&1; then
  log "VERIFY FAILED（tar 损坏），已删除 $DEST"; rm -f "$DEST"; exit 1
fi
( cd "$STAGE" && find . -type f | sort ) > "$MAN_STAGE"
tar -tzf "$DEST" | grep -v '/$' | sort > "$MAN_TAR"
if ! diff -q "$MAN_STAGE" "$MAN_TAR" >/dev/null; then
  log "VERIFY FAILED（包内容与快照不一致）："
  diff "$MAN_STAGE" "$MAN_TAR" | head -10
  rm -f "$DEST"; exit 1
fi
FILE_COUNT="$(wc -l < "$MAN_STAGE")"
log "备份完成：${DEST}（$(du -h "$DEST" | cut -f1) · $FILE_COUNT 个文件）"

# 5) 兼容历史的 ledger 单文件快照（含完整性校验）
if [ -f "$STAGE/ledger.db" ]; then
  LEDGER="$BK/ledger-$STAMP.db"
  cp "$STAGE/ledger.db" "$LEDGER"
  if ! "$PY" -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('SELECT COUNT(*) FROM transactions').fetchone()" "$LEDGER" >/dev/null 2>&1; then
    log "ledger 单文件校验失败，已删除 $LEDGER"; rm -f "$LEDGER"
  else
    log "账本单文件快照：$LEDGER"
  fi
  ls -1t "$BK"/ledger-*.db 2>/dev/null | tail -n +8 | xargs -r rm -f
fi

# 6) 整包保留最近 $KEEP 份
ls -1t "$BK"/wuage-data-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f
log "保留整包：$(ls -1 "$BK"/wuage-data-*.tar.gz 2>/dev/null | wc -l) 份 / 上限 $KEEP"
