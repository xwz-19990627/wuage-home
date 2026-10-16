#!/usr/bin/env bash
# 公网 HTTPS 网关（0.0.0.0:8443）独立托管脚本 —— systemd: dsh-gateway.service
# 由 scripts/systemd/install.sh 从仓库安装到 /root/dsh_rsc/public-gateway/。
# 与 dsh web(3081) 解耦：重启本服务不影响 dsh web 与文件盒子/记账面板。
set -euo pipefail
cd /root/dsh_rsc/public-gateway
set -a; . ./gateway.env; set +a
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin
exec node server.js
