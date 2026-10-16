# wuage-home · 运维手册（OPS）

> 记录服务器侧改动（不入 wuage 仓库代码，但仓库是本记忆）。迁移/重装时按此重做。

## 服务拓扑（本机）

> ⚠️ 2026-10-16 实测：**当前跑的是 `dsh-public.service`（web 3081 + 网关 8443 合体）**，
> `dsh-web.service` / `dsh-gateway.service` 处于 inactive；两者是 2026-08-23 的拆分方案，
> 保留但不启用（详见下方「dsh web 与网关拆分」）。改路由/重启网关时先 `systemctl is-active`
> 确认到底动的是哪一个，别改错了单元。

| 服务 | 端口 | 说明 | 托管 |
| --- | --- | --- | --- |
| dsh web | 127.0.0.1:3081 | DSH 中枢 Web GUI | 现由 dsh-public.service 拉起（dsh-web.service 已停用） |
| 公网网关 | 0.0.0.0:8443 | 登录认证 + 反代（/ → 3081，/home/ → 17623，/home/files/ → 17624，/home/finance/ → 17625，/home/health/ → 17626）| 现由 dsh-public.service 拉起（dsh-gateway.service 已停用） |
| wuage 面板 | 127.0.0.1:17623 | 家庭账本面板（读写 $WUAGE_DATA=/root/wuage/data/ledger.db）| systemd: wuage-panel.service |
| 文件盒子 | 127.0.0.1:17624 | 工具箱 01：跨端文件互传（scripts/filebox.py）| systemd: dsh-filebox.service |
| 理财助手 | 127.0.0.1:17625 | 工具箱 02：持仓/行情/复盘/回测（scripts/finance.py，WUAGE_DATA/finance）| systemd: wuage-finance.service |
| 健康助手 | 127.0.0.1:17626 | 工具 03：AI 问答 + 体检报告解析 + 健康档案/指标/记录/记忆（scripts/health.py，WUAGE_DATA/health）| systemd: wuage-health.service |

## 数据根布局与备份（2026-10-16）

数据根 `/root/wuage/data/`（**仓库 `/root/project` 内不留任何运行时数据**）：

```
data/
├── ledger.db                 账本（wuage-panel）
├── finance/finance.db        理财（wuage-finance）
├── health/health.db + files/ + trash/   健康（wuage-health，files 是体检报告原件）
├── files/                    文件盒子（dsh-filebox）
├── import_detail_archive.json 历史明细存档
├── logs/                     服务日志 / backup.log；logs/legacy/ 是归档的旧手工启动日志
└── backups/                  每日备份（见下）
```

- 每日 04:00 由 cron 跑 `bash /root/project/scripts/backup.sh`
  （日志 `$WUAGE_DATA/logs/backup.log`，2026-10-16 前误写在 `/root/project/backups/`）。
- 产出一份整包 `backups/wuage-data-<时间戳>.tar.gz`（覆盖上表**全部**内容），保留最近 7 份
  （`WUAGE_BACKUP_KEEP` 可调）；另存 `backups/ledger-<时间戳>.db` 单文件快照 7 份。
- sqlite 走 backup API 一致性快照 → 边写边备也安全；打包后逐条比对清单，不一致即作废。
- **校验**：`bash /root/project/scripts/backup.sh --verify`（默认校验最近一份整包，逐个库 `integrity_check`）。
- **恢复**：`tar -xzf backups/wuage-data-<stamp>.tar.gz -C /tmp/restore`，确认后覆盖回数据根并
  `systemctl restart wuage-panel wuage-finance wuage-health dsh-filebox`。
- ⚠️ 备份**不含** `/root/wuage/.credentials.yaml`（模型密钥，故意不备）。
- 教训：模块数据目录必须落在数据根内（2026-10-16 修掉文件盒子写进 `/root/project/data/files` 的老问题），
  否则备份/迁移静默漏数据。

## wuage-panel.service / wuage-finance.service / wuage-health.service

```bash
systemctl status wuage-panel      # 状态
systemctl restart wuage-panel     # 重启
systemctl enable wuage-panel      # 开机自启（已启用）
journalctl -u wuage-panel -f      # 日志
```

- 三个工具服务同一套路：`wuage-panel`(17623)、`wuage-finance`(17625)、`wuage-health`(17626)，
  单元文件在仓库 `scripts/systemd/`，已 `enable` 开机自启、`Restart=on-failure`。
- ⚠️ 2026-09-07 起，理财/健康从「nohup 手工拉起」改为 systemd 托管：
  **不要再跑 `scripts/start-finance.sh` / `start-health.sh`**（会与 systemd 抢端口）；
  启停一律用 `systemctl {start,stop,restart} wuage-finance|wuage-health`。
- Bad Gateway(502) 排查：先 `curl -s 127.0.0.1:17625/api/health`（17626 同理）确认上游活着；
  上游死了就 `systemctl restart wuage-*`，网关本身一般没问题。

### 健康助手 v2（2026-09-10 重做）

- **模型**：与 DSH 同一个 `deepseek-flash`（原生多模态）。凭据走 `DSH_HOME=/root/dsh_rsc/.dsh`
  下的 `.credentials.yaml`（`OPENCODE_GO_API_KEY`）——**单元文件里必须有 `DSH_HOME`**，否则服务读不到密钥，
  页面能开但一问就报"模型密钥未配置"。自检：`curl -s 127.0.0.1:17626/api/meta` 看 `tools.api_key` 是否 `true`。
- **外部依赖**（报告解析用，缺了会降级但不崩）：
  - `poppler-utils` → `pdftoppm`/`pdfinfo`：PDF 报告渲染成图（`dnf install -y poppler-utils`）
  - `zbar` → `zbarimg`：解报告里的查验二维码（`dnf install -y zbar`）
  - 用 `/api/meta` 的 `tools` 字段自检这三个依赖 + 密钥。
- **数据**：`$WUAGE_DATA/health/health.db` + `files/`（上传原件，sha256 命名）+ `trash/`（删人时原件转移不真删）。
- **改模型**：`WUAGE_HEALTH_MODEL`（或分别设 `WUAGE_VISION_MODEL` / `WUAGE_CHAT_MODEL`）。
- **注意**：视觉请求单次 ≤ 相关页数限制，长报告自动逐页解析后合并（`MAX_PDF_PAGES=20`）。

## 网关路由（/home/files/ 与 /ledger/）

- 文件：/root/dsh_rsc/public-gateway/server.js（备份 server.js.bak-*；/home/files 补丁由 scripts/apply-filebox-gateway.py 管理，幂等+自动备份）。
- 行为：未登录 → 跳 /login；已登录 → /home/* 反代 17623、/home/files/* 反代 17624（去前缀）；/files* → 302 兼容跳 /home/files/。
- 重启网关：systemctl restart dsh-gateway.service（**只重启网关，不再碰 dsh web**）。
- 网关配置：/root/dsh_rsc/public-gateway/gateway.env（AUTH_USER/AUTH_PASS/ALLOWED_IPS）。

## dsh web 与网关拆分（2026-08-23）

> 📌 **当前实际状态（2026-10-16 实测）= 已回滚到 `dsh-public.service`**：
> `dsh-public` active，`dsh-web` / `dsh-gateway` inactive。也就是说下面这套拆分**目前没有启用**，
> 只是保留着随时可切。看网关日志、改 server.js 之后重启，都要对 `dsh-public.service` 操作。

- 原 dsh-public.service（web+网关共管）已停用并备份为 /etc/systemd/system/dsh-public.service.bak-*；
  新单元：dsh-web.service（3081）与 dsh-gateway.service（8443），互不牵连。
- 前台脚本：/root/dsh_rsc/start-dsh-web-foreground.sh、/root/dsh_rsc/public-gateway/start-gateway-foreground.sh。
- 安装/重跑：sudo bash scripts/systemd/install.sh（幂等；第 4 步切换会中断当前 dsh 会话）。
- 回滚：sudo systemctl stop dsh-web.service dsh-gateway.service && sudo systemctl disable dsh-web.service dsh-gateway.service && sudo systemctl enable --now dsh-public.service
- 手动静默备份脚本 start-public-dsh.sh / stop-public-dsh.sh 保留，仍可整套拉起/停掉。

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