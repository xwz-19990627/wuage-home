# wuage-home · 运维手册（OPS）

> 记录服务器侧改动（不入 wuage 仓库代码，但仓库是本记忆）。迁移/重装时按此重做。

## 服务拓扑（本机）

| 服务 | 端口 | 说明 | 托管 |
| --- | --- | --- | --- |
| dsh web | 127.0.0.1:3081 | DSH 中枢 Web GUI | systemd: dsh-public.service |
| 公网网关 | 0.0.0.0:8443 | 登录认证 + 反代（/ 到 3081，/ledger/ 到 17623）| systemd: dsh-public.service |
| wuage 面板 | 127.0.0.1:17623 | 家庭账本面板（读写 $WUAGE_DATA=/root/wuage/data/ledger.db）| systemd: wuage-panel.service |

## wuage-panel.service

```bash
systemctl status wuage-panel      # 状态
systemctl restart wuage-panel     # 重启
systemctl enable wuage-panel      # 开机自启（已启用）
journalctl -u wuage-panel -f      # 日志
```

## 网关 /ledger/ 路由

- 文件：/root/dsh_rsc/public-gateway/server.js（已备份 server.js.bak-20260822-wuage）。
- 行为：未登录 → 跳 /login；已登录 → /ledger/* 反代到 http://127.0.0.1:17623（去掉前缀）。
- 重启网关：systemctl restart dsh-public（会同时重启 dsh web 与网关）。
- 网关配置：/root/dsh_rsc/public-gateway/gateway.env（AUTH_USER/AUTH_PASS/ALLOWED_IPS）。

## 访问方式

| 场景 | 地址 | 说明 |
| --- | --- | --- |
| 服务器本机 | http://127.0.0.1:17623 | 无需登录 |
| 远程（电脑/手机） | https://47.99.118.10:8443/ledger/ | 需网关账号密码（30 天免登录可勾选） |
| DSH 中枢对话 | https://47.99.118.10:8443/ | 同账号体系，家庭管家新会话 |

## 迁移到 NAS（按快照思路）

1. 装同版本 DSH（dsh-public.service 对应配置）与 python3.11。
2. 部署 wuage 仓库同 commit，拷入数据根 `/root/wuage`（data/ledger.db + backups/）。
3. 建 wuage-panel.service，重做网关 /ledger/ 路由（本文件逻辑）。
4. 冒烟：bash scripts/selftest.sh。