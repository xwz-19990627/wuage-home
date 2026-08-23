# Spec：支出性质统计（固定 / 必要 / 非必要）（v0.2.0-rc.2）

> 复制自 docs/specs/_template.md。本 spec 通过后实现（docs/SPEC.md 第 1 条）。

## 目标

给家庭账本加「支出性质」维度：每个分类挂一个性质标签（固定 / 必要 / 非必要），
记账时按分类自动归类；「财务」Tab 提供月度 / 年度三性质统计，
回答「这个月 / 今年固定支出多少、必要多少、非必要多少」。

## 范围

- 做：
  - categories 表新增 nature 字段（fixed / necessary / discretionary，即 固定 / 必要 / 非必要），种子分类预设默认值，可在分类管理界面修改。
  - 记账自动归类：交易 JOIN 分类取当前 nature（不冗余快照，分类软归档仍可 JOIN）。
  - 统计接口 + 前端「财务」Tab（第五个 Tab）的「支出性质」卡片：本月三性质金额与占比，可切年份看年度。
  - CLI：categories 列表带 nature；set-nature 修改。
- 不做（本期明确排除）：
  - 按性质的预算 / 超支提醒（rc.3 预算模块做）。
  - 二级分类（parent_id 已预留，v0.2 系列后续开）。
  - 性质标签的记忆/学习（无 LLM 依赖，纯规则）。

## 数据契约

- categories 表新增列：`nature TEXT NOT NULL DEFAULT 'necessary'`。
  - 枚举：fixed（固定）、necessary（必要）、discretionary（非必要）；非法值一律回退 necessary。
- 迁移：老库执行 `ALTER TABLE categories ADD COLUMN nature TEXT NOT NULL DEFAULT 'necessary'`；
  新库 SCHEMA 直接含该列。迁移幂等（先查列是否存在）。
- 种子分类默认性质：
  | 分类 | 性质 |
  | --- | --- |
  | 居住 / 通讯 | fixed（固定） |
  | 餐饮 / 医疗 / 教育 / 交通 | necessary（必要） |
  | 购物 / 娱乐 / 人情 / 其他 | discretionary（非必要） |
- transactions 不新增列；统计 SQL 统一 `LEFT JOIN categories c ON c.id=t.category_id` 取 c.nature。

## 接口（API / 命令）

- CLI（ledger.py categories）：
  - 列表输出增加 nature 列（含中文名）。
  - `ledger.py categories set-nature <名称> <固定|必要|非必要>`（也接受 fixed/necessary/discretionary）。
- HTTP：
  - GET /api/categories → 每个分类带 `"nature"`。
  - PATCH /api/categories/<id> body {"nature": "fixed"} → 200；非法枚举 → 400。
  - GET /api/nature?year=2026&month=7 → 响应：
    ```json
    {
      "year": 2026, "month": 7,
      "month_total_cents": 336101,
      "month_items": [{"nature": "fixed", "name": "固定", "total_cents": 0, "count": 0},
                      {"nature": "necessary", "name": "必要", "total_cents": 0, "count": 0},
                      {"nature": "discretionary", "name": "非必要", "total_cents": 0, "count": 0}],
      "year_total_cents": 123456,
      "year_items": [{"nature": "fixed", "name": "固定", "total_cents": 0, "count": 0},
                     {"nature": "necessary", "name": "必要", "total_cents": 0, "count": 0},
                     {"nature": "discretionary", "name": "非必要", "total_cents": 0, "count": 0}],
      "by_category_month": [{"name": "餐饮", "nature": "necessary", "total_cents": 10201}]
    }
    ```
  - month 省略 = 全年（year_items 为主）。
- 前端：第五 Tab「财务」；子卡片「支出性质」：
  - 本年度月份选择器（1~12 月 + 全年）+ 本月三性质大数字（固定/必要/非必要）+ 占比横条，
  - 下方年度合计三性质 + 占比；切 Tab / 切月份即时刷新。

## 依赖 LLM 的点

- 无新增 LLM 调用。AI 解析已返回 category 名（在候选分类内选择），
  统计仅按分类名 JOIN nature —— 全部确定性代码。

## 验收标准（可测试）

- [ ] 新库建表、老库迁移均自动带 nature 列；迁移幂等（跑两次不报错）。
- [ ] 种子 10 分类默认性质符合上表；GET /api/categories 返回 nature。
- [ ] PATCH 修改性质持久化；非法枚举返回 400；篡改数据回退 necessary。
- [ ] /api/nature 月度 / 年度金额与 /api/entries 按分类汇总一致（抽样核对）。
- [ ] 前端财务 Tab：三性质金额 + 占比随月份 / 年份切换刷新；空数据有占位。
- [ ] CLI set-nature 与 HTTP 行为一致（/tmp 独立库测试，不动正式库）。

## 发布与归档标尺

- 版本 v0.2.0-rc.2：CHANGELOG 条目、tag、归档（docs/archive/v0.2.0-rc.2-YYYY-MM-DD.md）就绪。
