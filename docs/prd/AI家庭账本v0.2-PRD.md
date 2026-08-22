# AI家庭账本 v0.2 产品需求文档（架构修订版）

> 来源：用户家电脑 PRD 原文（2026-08-22），原样归档。
> 版本说明：本版基于原 PRD 和架构评审建议重构，聚焦核心闭环。

## 核心理念

以"财务事件（Transaction）"为中心，AI 为入口，为后续智能分析打下坚实的数据地基。

## 1. 产品定位

**WuAge AI 家庭财务助手**：AI Native 的家庭财务管理工具。用户只需自然语言描述消费（例如"今天买菜花了86元"），系统自动完成：金额、日期、分类、成员、商户识别；账单生成与存储；实时消费汇总与简单可视化。

> 核心理念：不只是记录钱，而是帮助家庭理解钱怎么花。

## 2. 系统整体结构

底部导航（共 4 项）：**首页 ｜ AI记账 ｜ 账单 ｜ 家庭**

- 首页：月度消费概览 + 分类占比 + 趋势简图
- AI记账：对话式记账，完整状态机与草稿机制
- 账单：所有流水列表，支持筛选与删除
- 家庭：家庭成员管理（姓名、关系）

砍掉（v1.0 不做）：预算管理、资产总览、财务健康评分、复杂分析图表（v0.2 系列再生长）。

## 3. 首页 Dashboard

- 本月总支出（较上月环比）
- 分类占比饼图（前 5 + 其他，可下钻到账单页）
- 近 7 日每日支出柱状图
- 日期选择：默认本月，可切历史月份

## 4. AI 记账页面

状态机：WAIT_INPUT → AI_ANALYZE → AI_RESULT → USER_CONFIRM → SAVE_SUCCESS（失败 → FAILED → 重新输入；修改 → EDIT_MODE）。

草稿机制：AI 返回结果但未确认/取消直接退出 → 自动入草稿箱；下次进入提示恢复；草稿保留 24 小时。

解析过程展示：正在分析… → ✅ 金额 / 日期 / 分类 / 成员 / 商户。

结果确认卡片：金额、日期、分类、成员、商户、备注 + 确认记账 / 修改 / 取消。

AI 识别要求：必须识别成员（家庭列表，未识别默认"本人"）；必须识别分类（动态分类表，未识别归"其他"）；记录 raw_text 与 confidence。

## 5. 账单页面

- 筛选：时间（全部/今天/近7天/本月/自定义）、分类（动态列表）、成员
- 流水：日期 分类 金额 备注 成员；支出红色、收入绿色；编辑/删除按钮；删除需确认
- 空状态："暂无账单，试试用 AI 记一笔吧"

## 6. 家庭页面

- 成员列表（姓名、关系）+ 当月消费总额
- 新增成员（姓名必填、关系选填、头像 v1.0 默认图标）
- 删除成员：有账单则禁止删除

## 7. 数据模型（核心）

> 原 PRD 要求：所有数据存储在浏览器本地（IndexedDB），为未来云端同步预留扩展。
> **（本仓库评审见 docs/prd/PRD-REVIEW.md：建议改为服务器 SQLite 权威 + 浏览器只作视图）**

- Family：id / name / created_at
- Member：id / family_id / name / relation / avatar / is_active
- Account（预留，v1.0 默认"现金"）：id / family_id / name / type / balance
- Category（动态可扩展，支持 parent_id 二级，is_system 种子）：餐饮、交通、医疗、购物、教育、娱乐、居住、通讯、人情、其他
- Transaction（核心）：id / family_id / account_id / member_id / category_id / amount（正=收入，负=支出）/ date / created_at / remark / merchant / source(manual|ai_parsed|ai_corrected) / raw_text / ai_confidence / corrected_from
- Draft：id / family_id / data(Partial<Transaction>) / created_at / expired_at(+24h)

## 8. 核心状态机（AI 记账）

WAIT_INPUT → AI_ANALYZE → AI_RESULT → 确认→SAVE→SUCCESS→回首页；修改→EDIT_MODE→保存→AI_RESULT；取消→WAIT_INPUT；失败→FAILED→WAIT_INPUT；离开页面→DRAFT→下次恢复。

## 9. 前端开发要求

移动端优先响应式；按钮交互反馈（点击态/加载态）；异步操作 Loading；成功/失败 Toast；空数据占位；数据持久化 IndexedDB（推荐 idb 库）。

## 10. UI 设计规范

背景 #FAF8F3，主色 #D99025，卡片白色圆角 20px 弱阴影，系统字体+数字加粗，简洁线条图标；参考 Apple Health + 支付宝账单风格。

## 11. 开发阶段划分

第一阶段（v1.0 核心交付）：Dashboard、AI 记账全流程（含草稿/修改/取消）、账单列表、家庭成员管理、数据持久化、交互反馈。
第二阶段（v0.2 系列）：预算、家庭资产负债表、支出性质统计（固定/必要/非必要）、理财、健康评分、多账户、高级分析。

## 12. 落地提示

先建数据层（IndexedDB CRUD 工具类）→ Mock AI 跑通 UI 再接入大模型 → 草稿实时保存/确认后清除 → 分类与成员动态进 Prompt → 测试：AI 超时、强刷草稿恢复、旧数据迁移。

## 附录：变更记录

- v0.1 2026-08-01 初始 Demo（简单记账 + LLM 一句话）
- v1.0 2026-08-22 重构数据模型，增加账户/成员/草稿，砍掉预算/资产，聚焦核心闭环
