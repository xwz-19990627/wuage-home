#!/usr/bin/env bash
# 打包 wuage-home 为可迁移 App（不含 data/，迁移时另拷数据目录）
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
V="${1:-$(git -C "$REPO" describe --tags --abbrev=0 2>/dev/null || echo v0.1.1)}"
STAGE="$(mktemp -d)"
APP="$STAGE/wuage-home"
mkdir -p "$APP"
cp -r "$REPO/scripts" "$REPO/web" "$APP/"
cp "$REPO/README.md" "$REPO/CHANGELOG.md" "$APP/"
cp -r "$REPO/docs" "$APP/docs"
rm -f "$APP/scripts/web.pid"
mkdir -p "$REPO/dist"
tar -czf "$REPO/dist/wuage-home-$V.tar.gz" -C "$STAGE" wuage-home
if command -v zip >/dev/null 2>&1; then
  (cd "$STAGE" && zip -qr "$REPO/dist/wuage-home-$V.zip" wuage-home)
  echo "打包完成：dist/wuage-home-$V.zip + .tar.gz"
else
  echo "打包完成：dist/wuage-home-$V.tar.gz（未装 zip，跳过 zip）"
fi
rm -rf "$STAGE"
ls -lh "$REPO/dist"
