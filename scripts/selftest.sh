#!/usr/bin/env bash
# wuage-home self-test：验收/升级冒烟用的 API 回环检查
# 用法: bash scripts/selftest.sh [BASE_URL]
set -euo pipefail
BASE="${1:-http://127.0.0.1:17623}"
echo "== health =="
curl -sf "$BASE/api/health" | head -c 120; echo
echo "== categories =="
curl -sf "$BASE/api/categories" | grep -o 餐饮 | head -1
echo "== add -> patch -> delete 回环 =="
ID="$(curl -sf -X POST "$BASE/api/entries" -H 'Content-Type: application/json' -d '{"amount_cents":1234,"category":"餐饮","note":"selftest"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")"
echo "created id=$ID"
curl -sf -X PATCH "$BASE/api/entries/$ID" -H 'Content-Type: application/json' -d '{"category":"购物","amount_cents":2345}' | grep -o 购物 | head -1
curl -sf -X DELETE "$BASE/api/entries/$ID" >/dev/null && echo "deleted ok"
echo "== weekly =="
curl -sf "$BASE/api/weekly" | head -c 120; echo
echo "SELFTEST OK"