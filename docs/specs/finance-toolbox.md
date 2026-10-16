# Spec：理财助手（工具箱 #02）（v0.3.x）

> 工具箱「蛙哥家庭助手」里的工具类能力容器新增第二个工具：**理财助手**。
> 定位是「以基金为主的学习型理财工具」：记录持仓、看每日净值/行情、做简单回测、
> 收藏策略/指标、通过问答沉淀技术常识笔记。
> 版本号占位 `v0.3.x`，待与现有 v0.3.0-rc.1 工具箱模块规划对齐后定最终版本号。

## 目标

一句话：让用户（本人）在一个手机友好的 Web 工具里完成「基金持仓记录 → 每日复盘 →
策略/指标收藏 → 问答学习自动记笔记 → 自研简单回测」的理财学习闭环。

本期结论已和用户确认：

- 市场范围：**以基金为主**（场外基金 + ETF；个股暂不优先）
- 持仓方式：**手动记录**买入/卖出/定投
- 盘面需求：**每日盘后复盘 + 当日净值/行情**，不做盘中实时盯盘
- 集成策略：**不接 Vibe-Trading**；先自研，后续如需要复杂量化再做 sidecar 评估

## 范围

- 做：
  - 独立 Web 服务 `scripts/finance.py` + 页面 `web/finance.html`，零第三方依赖优先。
  - 持仓管理：基金/ETF 的买入、卖出、定投记录；自动计算当前持仓、成本、市值、收益。
  - 自选列表：场外基金按最新净值、ETF 按当日行情；支持手动添加/删除。
  - 每日快照：收盘后拉取自选/持仓的最新净值与行情，存 `daily_snapshots`，可做历史复盘。
  - 每日复盘：基于当日快照，生成持仓市值变化、涨跌摘要；可一键让 LLM 生成人话解读。
  - 自研回测：面向基金的定投回测、均线/RSI 类简单策略回测，输出累计收益、年化、最大回撤、
    夏普、胜率、交易明细。
  - 策略收藏、指标收藏（缩量放量/均线等技术常识）。
  - 学习问答：Web 页面问答框 → LLM 回答 → 自动落库为笔记，支持标签和全文搜索。
  - 数据落 `/root/wuage/data/finance/`，迁移 = 拷目录。
  - 网关接入 `/home/finance/`，复用家庭网关账号和 HTTPS。
- 不做（本期明确排除）：
  - 不接 Vibe-Trading，不做多因子/alpha 研究、跨市场组合优化。
  - 不接券商/基金账户自动同步，只做手动记录。
  - 不做盘中实时盯盘/推送。
  - 不做个股深度行情（如 K 线工具、龙虎榜、资金流）。
  - 不做自动买卖信号执行。
  - 不与 ledger.db 自动对账（收益/持仓不自动写入账本；后续可做可选联动）。
  - 不构成投资建议；页面不展示“买卖建议”，只做记录、回测与知识学习。

## 数据契约

- 数据根：`FINANCE_DATA`，默认 `$WUAGE_DATA/finance`（当前 `/root/wuage/data/finance`）。
- 数据库：`finance.db`（SQLite，schema 以 `scripts/finance.py` 为准）。

### 表结构草案

- **transactions** 交易流水
  - `id, symbol, name, asset_type(fund/etf), direction(buy/sell)`
  - `trade_date, shares, price, amount_cents, fee_cents`
  - `source(manual/import), note, created_at`
- **watchlist** 自选列表
  - `id, symbol, name, asset_type, category, sort_order, created_at`
- **daily_snapshots** 每日净值/行情快照
  - `id, symbol, asset_type, trade_date, open, high, low, close, pre_close, change_pct`
  - `volume, amount, nav, source, fetched_at`
  - 唯一约束：`(symbol, trade_date)`
- **positions** 持仓视图（可由 transactions 实时计算；如后续需要可落快照表）
- **strategies** 收藏的策略
  - `id, name, description, rules_json, tags, created_at, updated_at`
- **indicators** 收藏的指标/技术常识
  - `id, name, category, summary, detail, formula, tags, created_at`
- **notes** 学习笔记/问答记录
  - `id, question, answer, tags, source(ask/manual), created_at, updated_at`
- **meta** 元信息
  - `key, value`（schema_version 等）

金额统一用 `amount_cents`/`fee_cents` 整数存储（元 × 100），与 ledger 风格一致；
份额 `shares` 用 REAL 存小数；价格 `price` 用 REAL（场外基金净值保留 4 位）。

## 接口（API / 命令 / 技能）

- CLI：`python3.11 scripts/finance.py`（启动服务）、`--init`（建库）、`--refresh`（手动拉一次快照）、
  `--backtest-demo`（跑一次示例回测）。
- HTTP（相对路径，经网关 `/home/finance/` 前缀转发后语义不变）：
  - `GET  /` → 页面 `web/finance.html`
  - `GET  /api/health` → 健康检查
  - `GET  /api/positions` → 当前持仓（成本/市值/收益）
  - `POST /api/transactions` → 记一笔买入/卖出/定投
  - `PATCH/DELETE /api/transactions/<id>` → 改/删流水
  - `GET/POST /api/watchlist` → 自选列表/添加
  - `DELETE /api/watchlist/<id>` → 删除自选
  - `POST /api/refresh` → 拉取今日自选/持仓净值与行情快照
  - `GET  /api/review?date=YYYY-MM-DD` → 当日复盘摘要
  - `GET/POST/PATCH/DELETE /api/strategies` → 策略收藏
  - `GET/POST/PATCH/DELETE /api/indicators` → 指标收藏
  - `GET  /api/notes?q=&tag=` → 笔记检索
  - `POST /api/ask` → LLM 问答（body: question），回答后自动写入 notes
  - `DELETE /api/notes/<id>` → 删除笔记
  - `POST /api/backtest` → 运行自研回测（body: symbol/strategy/params/period）
- 入口/网关：`https://47.99.118.10:8443/home/finance/`；
  `/finance*` 兼容 302 到 `/home/finance/`（与文件盒子一致）。
- 数据源：免费公开接口，优先腾讯/东方财富/天天基金；用 Python 标准库 `urllib` 请求。
  不强制引入 akshare/tushare，保持零第三方依赖；如后续数据源不足再评估。

## 依赖 LLM 的点

- 本次为「学习问答 + 复盘解读」提供自然语言能力：
  - `/api/ask`：由 DeepSeek 负责回答问题，并把回答整理成可检索笔记。
  - `/api/review`：可让 LLM 把数字快照转成“人话复盘”（如涨跌主因、持仓变化）。
- 其余（持仓计算、行情拉取、快照存储、回测指标、搜索）全部是**确定性代码**，不依赖 LLM。
- LLM 输出不自动成为“买卖建议”，只作为学习/复盘辅助。

## 验收标准（可测试）

- [ ] `finance.py --init` 在 `FINANCE_DATA=/tmp/wuage-test/finance` 下建库成功，无第三方依赖。
- [ ] 手动录入一笔基金买入/卖出后，`/api/positions` 的成本/份额/市值/收益计算正确（隔离库内验证）。
- [ ] 自选添加场外基金和 ETF 后，`/api/refresh` 能拉到真实净值和当日行情并落 daily_snapshots；
      如果某个源失败，有明确错误且不影响其他标的（fallback 不静默用旧数据）。
- [ ] `POST /api/backtest` 跑定投/均线/RSI 三套示例，输出累计收益、年化、最大回撤、夏普、胜率、交易明细。
- [ ] `/api/ask` 提问后，回答写入 notes；`/api/notes?q=` 能检索到该笔记。
- [ ] Web 页面在 127.0.0.1:17625 可完成：持仓登记 → 自选刷新 → 查看复盘 → 收藏策略/指标 → 问答记笔记。
- [ ] 网关补丁后 `https://…:8443/home/finance/` 登录可达；`/finance` 302 到 `/home/finance/`。
- [ ] 数据迁移：拷走 `/tmp/.../finance` 后，换新路径重启仍能读到持仓/笔记/快照。
- [ ] 页面不出现“买卖建议”式表述，明确风险提示（学习工具，不构成投资建议）。

## 发布与归档标尺

- 本版本 CHANGELOG 条目、tag、归档文件（docs/archive/v0.3.x-YYYY-MM-DD.md）就绪时即完成。
- 与 Vibe-Trading 的关系记录进 DECISIONS/TODO：本期自研，不引入 Vibe；
  未来如需要复杂回测/研究，再评估 `data/finance/vibe/` sidecar，不改变 finance.db 契约。