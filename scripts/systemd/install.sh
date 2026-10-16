#!/usr/bin/env bash
# 拆分 dsh web(3081) 与公网网关(8443) 为两个 systemd 单元，并把文件盒子(17624)接入开机自启（幂等）。
#
# ⚠️ 警告：第 4 步会停止旧单元并启动两个新单元——当前所有 dsh 会话
#   （包括正在执行本脚本的会话）都会被中断，请安排好时机（如夜里没人用时）。
#
# 用法：sudo bash scripts/systemd/install.sh
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "请用 sudo 运行：sudo bash $0"; exit 1; }

SRC="$(cd "$(dirname "$0")" && pwd)"
TS="$(date +%Y%m%d-%H%M%S)"

if systemctl is-active --quiet dsh-web.service 2>/dev/null && systemctl is-active --quiet dsh-gateway.service 2>/dev/null; then
  echo "拆分已生效（dsh-web.service / dsh-gateway.service 均在运行），无需重复安装。"
  systemctl --no-pager --lines=3 status dsh-web.service dsh-gateway.service 2>/dev/null | head -24
  exit 0
fi

echo "== 1/5 备份现有文件 =="
[ -f /etc/systemd/system/dsh-public.service ] && cp -a /etc/systemd/system/dsh-public.service "/etc/systemd/system/dsh-public.service.bak-$TS" && echo "  备份 unit -> dsh-public.service.bak-$TS"
[ -f /root/dsh_rsc/start-public-dsh-foreground.sh ] && cp -a /root/dsh_rsc/start-public-dsh-foreground.sh "/root/dsh_rsc/start-public-dsh-foreground.sh.bak-$TS" && echo "  备份脚本 -> start-public-dsh-foreground.sh.bak-$TS"

echo "== 2/5 安装单元与前台脚本 =="
install -m 0644 "$SRC/dsh-web.service" /etc/systemd/system/dsh-web.service
install -m 0644 "$SRC/dsh-gateway.service" /etc/systemd/system/dsh-gateway.service
install -m 0644 "$SRC/dsh-filebox.service" /etc/systemd/system/dsh-filebox.service
install -m 0755 "$SRC/start-dsh-web-foreground.sh" /root/dsh_rsc/start-dsh-web-foreground.sh
install -m 0755 "$SRC/start-gateway-foreground.sh" /root/dsh_rsc/public-gateway/start-gateway-foreground.sh
systemctl daemon-reload

echo "== 3/5 校验配置 =="
systemd-analyze verify /etc/systemd/system/dsh-web.service /etc/systemd/system/dsh-gateway.service /etc/systemd/system/dsh-filebox.service || { echo "单元校验失败，已中止（未切换）"; exit 1; }

echo "== 4/5 切换（会中断当前 dsh 会话！）=="
systemctl disable --now dsh-public.service 2>/dev/null || true
systemctl enable --now dsh-web.service dsh-gateway.service

echo "== 5/5 冒烟 =="
sleep 6
echo -n "dsh web(3081):       "; curl -s -o /dev/null -m 5 -w "%{http_code}\n" http://127.0.0.1:3081/ || echo FAIL
echo -n "gateway(8443 /login): "; curl -sk -o /dev/null -m 5 -w "%{http_code}\n" https://127.0.0.1:8443/login || echo FAIL
echo -n "filebox(17624):      "; curl -s -o /dev/null -m 5 -w "%{http_code}\n" http://127.0.0.1:17624/ || echo FAIL

echo "== 6 文件盒子开机自启 =="
if systemctl is-active --quiet dsh-filebox.service 2>/dev/null; then
  echo "dsh-filebox.service 已在运行"
else
  systemctl enable dsh-filebox.service 2>/dev/null && systemctl start dsh-filebox.service || echo "警告：文件盒子服务启动失败，请看 journalctl -u dsh-filebox.service"
fi

echo; echo "完成！之后：改网关/路由 → sudo systemctl restart dsh-gateway.service（不再碰 dsh web）"
echo "文件盒子随系统自启；手动起停仍可用 bash scripts/start-filebox.sh / stop-filebox.sh"
echo "回滚：sudo systemctl stop dsh-web.service dsh-gateway.service; sudo systemctl disable dsh-web.service dsh-gateway.service; sudo systemctl enable --now dsh-public.service"
