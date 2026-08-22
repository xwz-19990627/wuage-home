#!/usr/bin/env bash
# wuage-home 发布助手（docs/SPEC.md 第 5 条）
# 用法: bash scripts/release.sh v0.2.0
set -euo pipefail

V="${1:-}"
if [[ -z "$V" ]]; then echo "用法: bash scripts/release.sh v0.x.y"; exit 1; fi
if [[ ! "$V" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then echo "版本号格式应为 vX.Y.Z"; exit 1; fi

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

if ! grep -q "## [$V]" CHANGELOG.md; then
  echo "CHANGELOG.md 缺少 ## [$V] 条目，先补全再发布"
  exit 1
fi

git add -A
git commit -m "release: $V" || true
git tag -a "$V" -m "wuage-home $V ($(date +%F))"
git push
git push --tags
echo "完成：$V 已提交、打 tag 并推送（归档请补 docs/archive/$V-$(date +%F).md）"
