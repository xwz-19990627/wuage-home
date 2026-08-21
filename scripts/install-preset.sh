#!/usr/bin/env bash
# 安装「家庭管家」preset 到本机 DSH（新会话选择器可见）
# 用法: DSH_HOME=/path/to/.dsh bash scripts/install-preset.sh
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DSH_HOME="${DSH_HOME:-$HOME/.dsh}"
DST="$DSH_HOME/.agent-presets/wuage-family"
mkdir -p "$(dirname "$DST")"
rm -rf "$DST"
cp -r "$REPO/presets/wuage-family" "$DST"
echo "家庭管家 preset 已安装: $DST"
