# wuage-home · 数据模型 v1（记账模块）

> 目标：你说一句，机器自动分类入库；固定字段自动填。
> 本文件是存储层契约，迁移 = 带走 `WUAGE_DATA` 即可。

## 字段（已定）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| amount_cents | INTEGER | 金额，单位**分**（避免浮点误差） |
| date | TEXT | 消费日期 YYYY-MM-DD，默认今天；"昨天/上周三"由模型换算 |
| kind | TEXT | expense / income / transfer，默认 expense（给未来的资产分析留口） |
| category | TEXT | 一级类别，**必须来自受控清单**（见下） |
| tags | JSON | **MVP 不用**，默认 []，为未来细粒度预留 |
| note | TEXT | 用户**原话**/备注 —— 未来"个人词表"的种子源，务必保留原文 |
| source | TEXT | manual / voice / wechat / import，默认 manual |
| member | TEXT NULL | 家庭成员维度——本期不启用，字段预留（后期按设备区分） |
| created_at | TEXT | 入库时间，ISO 8601 |

## 分类：MVP 只做一级大类（已拍板）

**9 项固定清单（LLM 只能从中选，周报按此聚合）：**

餐饮 / 交通 / 居住 / 购物 / 娱乐 / 育儿教育 / 医疗健康 / 人情往来 / 其他

- **不做二级小类/标签**。理由：分类更准、无需维护词表、周报粒度足够。
- 保留 `tags` 列但恒为空——以后想加细粒度不用改表结构。

## 未来小类策略（先立规矩，防标签爆炸）

当某天需要细粒度分析时（不是现在）：

1. **从源生成**：让模型对历史 `note`（原话）聚类，产出候选列表（如 买菜/水果/饮料）。
2. **人工确认后入库**：确认过的词才进入固定可选库。
3. **只许选库，禁自由新增**：模型分类时只能从库中选；新词必须走"确认入库"流程，
   从源头杜绝"外卖/点外卖/叫外卖"式标签堆积。
4. **定期相似合并**：同义词归并（半年/一年一次），只影响展示与统计，不改原始记录。

## 自动分类契约

输入：`今天买西瓜花了10块` → 输出：

```json
{
  "amount_cents": 1000,
  "date": "2026-08-21",
  "kind": "expense",
  "category": "餐饮",
  "tags": [],
  "note": "今天买西瓜花了10块",
  "source": "manual"
}
```

规则：
- 金额：口语数字+单位都要认（"十块"→1000，"23块5"→2350）。
- 日期：默认今天；昨天/前天/上周X 换算为本地日期。
- **错误率策略**：9 项短清单天然低错误率；分不准 → category="其他"，note 保留原话；
  金额较大（>200）或明显歧义时**追问一次确认**，小额直接记；**随时可改**（"改成下馆子"）。
- 录入后修改只改 category/tags，不动金额日期和原话。

## SQLite 表 v1

```sql
CREATE TABLE ledger (
  id           INTEGER PRIMARY KEY,
  amount_cents INTEGER NOT NULL,
  date         TEXT NOT NULL,            -- YYYY-MM-DD
  kind         TEXT NOT NULL DEFAULT 'expense',
  category     TEXT NOT NULL,
  tags         TEXT NOT NULL DEFAULT '[]', -- JSON array（MVP 恒空，预留）
  note         TEXT NOT NULL DEFAULT '',
  source       TEXT NOT NULL DEFAULT 'manual',
  member       TEXT,                     -- 预留：后期按设备区分
  created_at   TEXT NOT NULL
);
CREATE INDEX idx_ledger_date ON ledger(date);
CREATE INDEX idx_ledger_category ON ledger(category);
```
