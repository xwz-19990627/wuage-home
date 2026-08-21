# wuage-home · 数据模型 v1（记账模块）

> 目标：你说一句，机器自动分类入库；固定字段自动填，只有"标签"需要人定。
> 本文件是存储层契约，迁移 = 带走 `WUAGE_DATA` 即可。

## 字段（已定）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| amount_cents | INTEGER | 金额，单位**分**（避免浮点误差） |
| date | TEXT | 消费日期 YYYY-MM-DD，默认今天；"昨天/上周三"由模型换算 |
| kind | TEXT | expense / income / transfer，默认 expense（给未来的资产分析留口） |
| category | TEXT | 一级类别，**必须来自受控清单**（见下） |
| tags | JSON | 二级标签数组，可自由生长 |
| note | TEXT | 用户原话/备注（保留原始描述，便于回溯和纠错） |
| source | TEXT | manual / voice / wechat / import，默认 manual |
| member | TEXT NULL | 家庭成员维度——本期不启用，**字段预留**（后期按设备区分） |
| created_at | TEXT | 入库时间，ISO 8601 |

## 分类体系：一级固定 + 二级自由

**一级类别（受控清单，LLM 只能从中选，用于周报聚合）：**

| 类别 | 典型二级标签（建议但不限制） |
| --- | --- |
| 餐饮 | 外卖 / 买菜 / 下馆子 / 零食饮料 |
| 交通 | 打车 / 地铁公交 / 加油 / 停车 / 机票 |
| 居住 | 房租 / 水电燃气 / 网络话费 / 维修 / 家居 |
| 购物 | 服装 / 数码 / 日用 / 美妆 / 宠物 |
| 娱乐 | 游戏 / 影视 / 健身 / 旅行 / 门票 |
| 育儿教育 | 学费 / 培训 / 玩具 / 图书 |
| 医疗健康 | 挂号 / 药品 / 体检 / 保险 |
| 人情往来 | 红包 / 礼品 / 请客 |
| 其他 | 无法归类时兜底，周报单列"待整理" |

**规则**：一级必须是清单内（模型输出非法则回退"其他"并提示）；二级标签自由，模型从建议集选，
必要时可新增（新增即入库，自然生长成你的个人词表）。

## 自动分类契约（LLM 输出 JSON）

输入：`今天买西瓜花了10块` → 输出：

```json
{
  "amount_cents": 1000,
  "date": "2026-08-21",
  "kind": "expense",
  "category": "餐饮",
  "tags": ["水果"],
  "note": "今天买西瓜花了10块",
  "source": "manual"
}
```

规则：
- 金额：口语数字+单位都要认（"十块"→1000，"23块5"→2350）。
- 日期：默认今天；昨天/前天/上周X 换算为本地日期。
- 分类不确定 → category="其他"，note 保留原话，UI 提示可改。
- 录入后随时可改（`改成下馆子` → 仅改 category/tags，不动金额日期）。

## SQLite 表 v1

```sql
CREATE TABLE ledger (
  id           INTEGER PRIMARY KEY,
  amount_cents INTEGER NOT NULL,
  date         TEXT NOT NULL,            -- YYYY-MM-DD
  kind         TEXT NOT NULL DEFAULT 'expense',
  category     TEXT NOT NULL,
  tags         TEXT NOT NULL DEFAULT '[]', -- JSON array
  note         TEXT NOT NULL DEFAULT '',
  source       TEXT NOT NULL DEFAULT 'manual',
  member       TEXT,                     -- 预留：后期按设备区分
  created_at   TEXT NOT NULL
);
CREATE INDEX idx_ledger_date ON ledger(date);
CREATE INDEX idx_ledger_category ON ledger(category);
```
