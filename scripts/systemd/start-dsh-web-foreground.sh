#!/usr/bin/env bash
# dsh web 本体（127.0.0.1:3081）独立托管脚本 —— systemd: dsh-web.service
# 由 scripts/systemd/install.sh 从仓库安装到 /root/dsh_rsc/，与旧 start-public-dsh-foreground.sh 拆分。
# 注意：本脚本与「公网网关」已解耦；重启网关不再影响 dsh web。
set -euo pipefail
cd /root/dsh_rsc
if [ -f /etc/dsh.env ]; then set -a; . /etc/dsh.env; set +a; fi
export DSH_HOME=/root/dsh_rsc/.dsh
export PATH=/root/dsh_rsc/pnpm/node_modules/.bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin
export PNPM_HOME=/root/dsh_rsc/.pnpm-home
export XDG_DATA_HOME=/root/dsh_rsc/.local/share
export PYTHON=/usr/bin/python3.11
export npm_config_python=/usr/bin/python3.11

# --- auto-patch: 确保插件注入声明含 slots (start)（与旧脚本保持一致） ---
PKG=/root/dsh_rsc/.dsh/profiles/web/node_modules/@linxin666/dsh-web-ui-all/package.json
BUNDLE=/root/dsh_rsc/.dsh/profiles/web/node_modules/@linxin666/dsh-web-ui-all/lib/client.js
if [ -f "$PKG" ]; then
  if ! grep -q '"slots"' "$PKG"; then
    python3.11 -c "
import json,sys
p='$PKG'
d=json.load(open(p))
inj=d.get('dsh',{}).get('client',{}).get('inject',[])
if 'slots' not in inj: inj.append('slots')
d.setdefault('dsh',{}).setdefault('client',{})['inject']=inj
json.dump(d,open(p,'w'),indent=2,ensure_ascii=False)
" && echo "[auto-patch] package.json inject 已补 slots"
  fi
fi
if [ -f "$BUNDLE" ]; then
  if ! grep -Fq 'const inject = ["slots"]' "$BUNDLE"; then
    python3.11 - "$BUNDLE" <<'PYEOF'
import sys
p = sys.argv[1]
src = open(p, encoding='utf8').read()
old = 'const inject = [];'
if src.count(old) == 1:
    src = src.replace(old, 'const inject = ["slots"];')
    open(p, 'w', encoding='utf8').write(src)
    print('[auto-patch] bundle inject 已补 slots')
    sys.exit(0)
print('[auto-patch] bundle inject 形态未知(count=%d)，跳过（请人工检查）' % src.count(old))
sys.exit(1)
PYEOF
  fi
fi
# --- auto-patch (end) ---

exec node /root/dsh_rsc/dsh-copy/lib/bin.js web --port 3081 --trusted-host 47.99.118.10:8443
