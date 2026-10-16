# 🧰 工具箱（wuage-home 模块）

> 「蛙哥家庭助手」主页里的工具类能力容器。每个工具一个子页面/入口，
> 数据各自独立目录，迁移服务器 = 拷走对应数据目录。

| # | 工具 | 地址 | 数据目录 | 状态 |
|---|------|------|----------|------|
| 🏠 | 工具入口 Hub | 家人 https://47.99.118.10:8443/home/toolbox/ | -（静态页） | ✅ 2026-09-04 |
| 01 | 📦 文件盒子：跨端文件互传/上传下载 | 本机 http://127.0.0.1:17624 · 家人 https://47.99.118.10:8443/home/files/ | data/files/ | ✅ v0.3.0-rc.1 |
| 02 | 📈 理财助手：基金持仓/自选行情/复盘/回测/收藏/问答笔记 | 本机 http://127.0.0.1:17625 · 家人 https://47.99.118.10:8443/home/finance/ | data/finance/ | 🚧 MVP 待验收 · 网关已接 |
| 03 | 🏥 健康助手：对话式健康问答/体检报告解析入库/日常记录/长期记忆/**饮食运动计划** | 本机 http://127.0.0.1:17626 · 家人 https://47.99.118.10:8443/home/health/ | data/health/ | 🚧 可用 · 网关已接 |

## 工具入口 Hub（web/toolbox.html）

- 从主页「🧰 工具箱」卡进入 `/home/toolbox/`，再选择具体工具；主页另设「📈 理财助手」直达卡。
- 静态页由网关直接读取 `web/toolbox.html`，无需独立服务。
- 路由补丁：`scripts/apply-toolbox-gateway.py`（同时补 工具箱 Hub + 理财助手 两条路由，幂等）。

## 01 · 文件盒子（scripts/filebox.py）

- 纯 Python 标准库，零依赖；页面 web/filebox.html（手机友好）。
- 上传到服务器 / 设备间互传（A 传 B 下载）；页面删除带确认。
- 自动清理：默认保留 30 天，启动清一次 + 每 6 小时清一次；也可点页面「立即清理超期」或 `python3.11 scripts/filebox.py --cleanup-now`（可挂 cron）。
- 配置：FILEBOX_HOST / FILEBOX_PORT / FILEBOX_DATA / FILEBOX_RETENTION_DAYS /
  FILEBOX_MAX_MB（单文件上限，默认 512）/ FILEBOX_QUOTA_MB（总配额，默认 2048）/ FILEBOX_CLEANUP_HOURS。

### 启动 / 停止

```bash
bash scripts/start-filebox.sh        # http://127.0.0.1:17624（仅本机）
bash scripts/stop-filebox.sh
FILEBOX_MAX_MB=1024 bash scripts/start-filebox.sh   # 例：调到 1 GB/文件
```

### 公网接入（网关 /home/files/）

与记账同属家庭入口命名空间；`/files*` 会自动 302 跳到 `/home/files/`（兼容旧书签）。
网关补丁脚本（幂等、自动备份）：

```bash
python3.11 scripts/apply-filebox-gateway.py && sudo systemctl restart dsh-gateway.service
```

启用后家人访问 https://47.99.118.10:8443/home/files/（复用网关账号登录，30 天免登）。

### 数据与迁移

- 所有文件在 **`$WUAGE_DATA/files`**（默认 `/root/wuage/data/files`）平铺存放，`.` 开头为隐藏/临时文件。
- 数据目录解析与其它模块一致：`FILEBOX_DATA` → `WUAGE_DATA/files` → `WUAGE_HOME/data/files` → `/root/wuage/data/files`。
  ⚠️ 2026-10-16 修正：此前默认值是仓库内的 `REPO_ROOT/data/files`，服务器上因此实际写在
  `/root/project/data/files`，脱离数据根（备份/迁移会漏）；已迁移并端到端验证。
- 迁移 = 拷走 `$WUAGE_DATA/files` 目录；恢复时 `FILEBOX_DATA` 指到新位置即可。
- 磁盘小：默认 30 天自动清理 + 2 GB 总配额，均可调。

## 02 · 理财助手（scripts/finance.py）

- 定位：以基金为主的学习型理财工具；spec = `docs/specs/finance-toolbox.md`。
- 纯 Python 标准库，零第三方依赖；页面 `web/finance.html`（手机友好）。
- 功能：
  - 持仓：基金/ETF 买入、卖出、定投流水，自动算成本/市值/收益。
  - 自选/行情：场外基金按最新净值，ETF 按当日行情；免费源 = 天天基金/东方财富/腾讯。
  - 每日复盘：收盘后拉快照到 `daily_snapshots`，可生成人话复盘（LLM 可选）。
  - 回测：定投 DCA / 均线交叉 MA / RSI 区间，输出收益/年化/回撤/夏普/交易明细。
    **价格口径可选**（2026-10-16）：场外基金默认**复权净值**（累计净值口径，分红计入收益；
    单位净值在分红后会低估，实测 163402 十年定投差 35 个百分点），ETF 默认前复权，另可选后复权/不复权。
    回测结果会显示本次用的口径与数据源。
  - 数据源**回退**：基金 pingzhongdata → fundf10 历史净值；ETF/行情 腾讯 → 东财；主源故障自动切备源。
  - 收藏：策略收藏、指标/技术常识收藏。
  - 问答笔记：Web 问答框 → DeepSeek/OpenCode 回答 → 自动写入 notes 库，支持搜索。
- 配置：FINANCE_HOST / FINANCE_PORT / FINANCE_DATA / WUAGE_PARSE_API / WUAGE_PARSE_MODEL。
- 数据源说明：基金用 `fund.eastmoney.com/pingzhongdata` 全量净值；ETF 用腾讯行情/日 K。

### 启动 / 停止

```bash
bash scripts/start-finance.sh        # http://127.0.0.1:17625（仅本机）
bash scripts/stop-finance.sh
FINANCE_DATA=/root/wuage/data/finance bash scripts/start-finance.sh
```

### 公网接入（网关 /home/finance/）

```bash
python3.11 scripts/apply-toolbox-gateway.py && sudo systemctl restart dsh-gateway.service
```

已启用：https://47.99.118.10:8443/home/finance/（复用网关账号登录，30 天免登）。

### 数据与迁移

- SQLite 单库 `data/finance/finance.db`；迁移 = 拷走 `data/finance` 目录即可。
- 参考代码：Vibe-Trading 已放 `/root/wuage/reference/vibe-trading`，本期不运行、不接依赖。
- ⚠️ **家庭真实基金持仓不在这里**：13 只基金在记账库 `ledger.db` 的 `fund_positions` / `fund_trades`，
  由记账面板「理财」tab 维护，并新增「🤖 AI 持仓体检」（`ledger.py funds --analyze`，
  面板 `POST /api/funds/analysis`）读它出人话解读。

## 03 · 健康助手（scripts/health.py）

- 定位：一个"懂这个人的"健康 AI。主页做两件事——聊天、传体检报告；第二页是完整健康档案。
- 纯 Python 标准库，零第三方依赖；页面 `web/health.html`（手机友好）。
- 当前成员：**残雪**（本人）、**蛙哥**。
- 功能：
  - 对话：真 agent 循环（带工具调用），每轮注入该人完整 digest 再回答。
  - 体检报告：上传 PDF / 图片 / 文本 → 视觉模型结构化 → 报告 + 指标入库，自动判异常。
  - 日常记录：聊天里随口说（饮食/运动/睡眠/症状/用药/心情），AI 自动落库。
  - 长期记忆：`memories` 表 + `memory_add` 工具，AI 自己判断该记什么，下次回答前先读。
  - **对话上下文落库**：`conversations` / `messages` 两表，刷新/换设备/隔几天都能接着聊，
    服务端自动带最近 10 条历史；页面顶部「＋️ 新对话」开新会话（旧的保留）。
  - **饮食 / 运动计划落库**：`plans` 表 + `plan_save` / `plan_update` 工具。AI 规划后存下来，
    跨会话可续、可跟踪、可暂停/完成；digest 会把 `active_plans` 一并带给模型，
    所以它下次还记得你们在执行什么计划。
- 模型：与 DSH 同一个 `deepseek-flash`（原生多模态）。
  **凭据已与 DSH 解耦**，解析顺序：`OPENCODE_GO_API_KEY` 环境变量 →
  `$WUAGE_HOME/.credentials.yaml`（wuage 自有）→ `$DSH_HOME/.credentials.yaml`（历史来源）。
  因此 **DSH 升级 / 搬迁 / 移除都不影响本模块**。
- 配置：HEALTH_HOST / HEALTH_PORT / HEALTH_DATA / WUAGE_HEALTH_MODEL / WUAGE_CHAT_MODEL。
- 数据：`data/health/health.db`（结构化）+ `data/health/files/`（报告原件）；
  迁移 = 拷走 `data/health` 目录。

### 启动 / 停止

```bash
bash scripts/start-health.sh         # http://127.0.0.1:17626（仅本机）
bash scripts/stop-health.sh
HEALTH_DATA=/root/wuage/data/health bash scripts/start-health.sh
```

### 公网接入（网关 /home/health/）

```bash
python3.11 scripts/apply-health-gateway.py && sudo systemctl restart dsh-gateway.service
```

已启用：https://47.99.118.10:8443/home/health/（复用网关账号登录，30 天免登）。

### 常用 CLI

```bash
python3.11 scripts/health.py digest --name 残雪          # 全档案摘要（回答前必读）
python3.11 scripts/health.py parse-report --name 残雪 --file 报告.pdf
python3.11 scripts/health.py record --name 残雪 --category diet --content "早餐燕麦+鸡蛋"
python3.11 scripts/health.py plan --name 残雪 --kind diet --content "…"   # 存计划
python3.11 scripts/health.py plan --name 残雪 --status active            # 看在执行中的
```
