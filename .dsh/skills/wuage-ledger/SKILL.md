---
name: wuage-ledger
description: 一句话记账与查账：把口语消费转成结构化账本、改账、按类别汇总、生成人话周报。当用户说"今天买了XX花了XX"之类的记账、查账、改账指令时使用本技能。
---

# wuage-ledger · 记账技能卡

## 定位

wuage-home 的记账模块。数据在项目根的 `data/ledger.db`（环境变量 `WUAGE_DATA` 可覆盖，迁移=拷该目录）。
脚本：`scripts/ledger.py`（纯 Python3 标准库，无第三方依赖）。

若不确定当前目录，先 `git rev-parse --show-toplevel` 确认仓库根，所有命令以仓库根为 $REPO 执行。
命令统一为：`python3 $REPO/scripts/ledger.py ...`

## 一句话记账流程

1. 把用户口语解析为 JSON（契约见下）。
2. 执行 `python3 $REPO/scripts/ledger.py add --json '<json>'`，脚本会回显入库记录。
3. 回执（人话，不贴 JSON）：如 `已记：餐饮 10.00 元（2026-08-21），第 5 笔。说"改成XX"可改类别。`

## 解析契约（必须严格遵守）

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

- `amount_cents`：整数，单位**分**。"10块"→1000，"23块5"→2350，"十块"也要认。
- `date`：YYYY-MM-DD，**默认今天**；"昨天/前天/上周三"换算成本地日期。
- `kind`：默认 expense；只有明确说收入/转账才用 income/transfer。
- `category`：**必须**取自九大清单之一：
  餐饮 / 交通 / 居住 / 购物 / 娱乐 / 育儿教育 / 医疗健康 / 人情往来 / 其他
- `tags`：恒为 []（MVP 不用小类）。
- `note`：**用户原话原样保留**，不缩写、不改写、不翻译。
- `source`：manual。`member`：不填。

## 分类决策规则

- 分不准时：`category="其他"`，note 保留原话，回执中提示"已归入待整理，说『改成XX』可修正"。
- 金额 > 200 元或明显歧义（如还款、转账、多人消费）：先**问一句确认**再入库。
- 小额（≤200）直接记，不打断流畅感。

## 纠错（用户说"改"时）

- "改成下馆子" → `update --id N --category 餐饮 --note '原话'`（类别必须来自清单）。
- 只改类别不影响金额与日期；"顺便改金额"才更新 amount_cents（update 无该参数时用 `add`+delete 或提示）。
- "删掉第 N 笔" → `delete --id N`。

## 查账

- `list [--from YYYY-MM-DD] [--to ...] [--category X] [--json]`
- `summary --from ... --to ... [--json]`（区间按类别汇总）
- `weekly --json`（本周汇总）

## 周报流程

1. `python3 $REPO/scripts/ledger.py weekly --json`
2. 用 3~6 条**人话**叙述，不贴 JSON：
   - 本周总支出、笔数；
   - 金额前 2 的类别及占比；
   - 异常信号：某类别笔数/金额激增、出现大额、"其他/待整理"条目；
   - 收尾一句建议或可做的事。
3. 若本周无记录，直接说"这周还没有记账，来一句试试"。
