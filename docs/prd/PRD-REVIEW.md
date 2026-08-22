# PRD 评审 · WuAge AI 家庭财务助手（v0.2 对齐分析）

> 日期 2026-08-22。对照 wuage-home 现状（DSH 中枢 + 服务器 SQLite + Web 面板）评审 PRD。

## 一、关键冲突：数据存储（必须先在讨论中定）

PRD：全部数据在**浏览器 IndexedDB**（本地 + 预留云同步）。
现状：权威数据在**服务器 SQLite**（WUAGE_DATA/ledger.db，可迁移、多端、DSH 中枢、未来 NAS/QQ bot）。

| 维度 | IndexedDB（PRD） | 服务器 SQLite（现状，建议保留） |
| --- | --- | --- |
| 多设备/手机+电脑 | 各存各的，不同步 | 一处存、处处看（现网关已验证） |
| 未来 QQ bot / 多入口 | 无法读写浏览器库 | API 一接即用 |
| NAS 迁移 / 数据主权 | 数据散在各设备 | data/ 目录拷走即迁移 |
| 已建资产（v0.1.x 记账/解析/面板） | 全部作废重写 | 平滑升级 |
| 离线可用 | 优势 | 劣势（可后续用缓存补） |

**建议：服务器 SQLite 为权威存储，浏览器 IndexedDB 顶多作离线缓存（或 v1.0 不做）。**
若你其实想要"纯本地 App（不联网、数据不出门）"的产品形态，那 IndexedDB 成立——但这与 DSH 中枢/远程访问/NAS 不是同一路线，请二选一。

## 二、数据模型升级映射（保留服务器方案时）

| PRD 模型 | 落地 | 与现状关系 |
| --- | --- | --- |
| Transaction | ledger.db 升级：新增 member_id/merchant/raw_text/ai_confidence/source/corrected_from；amount 改带符号 | 现有 ledger 表迁移列 |
| Member | 新表 members（默认"本人"），member 字段从预留变为启用 | 现状 member 列即 id |
| Category | 新表 categories（is_system 种子 10 项，支持 parent_id） | 现固定 9 类 → 种子 10 类（医疗健康→医疗、加通讯、育儿教育⊂教育），旧数据映射 |
| Account | 新表 accounts，默认"现金"一张 | 纯新增，v1.0 不展示 |
| Draft | 新表 drafts（24h 过期） | 纯新增 |
| Family | 新表 families，默认"我的家庭" | 纯新增，单家庭 |

分类策略延续既有规矩：LLM 只能从分类表选；新类别需人工确认入库（防爆炸）。

## 三、AI 解析契约升级（scripts/parse.py）

- 新增：`member`（候选=members 表，未识别默认"本人"）、`merchant`、`ai_confidence`、`raw_text`
- 分类候选改读 categories 表（而非硬编码 9 类）
- 结果仍严格限制字段集合；失败自动重试

## 四、AI 记账 UX 升级（面板/AI 记账页）

- 现状：一句话→解析→**直接入库**
- PRD：解析→**结果卡（金额/日期/分类/成员/商户/备注）→ 确认/修改/取消**；离开未确认→草稿；草稿 24h
- 建议：按 PRD 上确认+草稿（正确率更有保障），改动集中在面板前端 + drafts API

## 五、页面结构（Web 面板改造）

4 Tab 移动优先：首页 Dashboard（本月总额+环比+前5饼图+7日柱图+月度切换）/ AI记账 / 账单（筛选/删除/空态）/ 家庭（成员增删+当月消费）。
主题：#FAF8F3 + #D99025 + 圆角 20px。（现面板为桌面单页，将重构为移动优先。）

## 六、小版本拆分建议（延续 0.1.x 滚动）

| 小版本 | 内容 |
| --- | --- |
| v0.1.4 | 收尾现有验收（柱子 display:block 修复已完成待验收） |
| v0.1.5 | 数据层升级：members/categories/accounts/drafts 表 + ledger 迁移 + 种子分类 |
| v0.1.6 | AI 契约升级（member/merchant/confidence）+ 结果卡确认/修改/取消 + 草稿 API |
| v0.1.7 | 面板重构：4 Tab 移动优先 + 账单页 + 家庭页 |
| v0.1.8 | 首页 Dashboard（月度+环比+饼图+7日）+ 新主题 |
| v0.2.0 | 里程碑发布（PRD 阶段一完成） |

## 七、待确认问题

1. **存储权威**：服务器 SQLite（推荐）还是 IndexedDB 纯本地？
2. 现 v0.1.4-rc2 柱子修复（display:block）是否已验收？验收通过后先发布它。
3. 分类种子按 PRD 10 项迁移？（医疗健康→医疗、加通讯、育儿教育并入教育）
4. 默认成员"本人"：家庭页首次进入自动创建，还是仅默认值？
