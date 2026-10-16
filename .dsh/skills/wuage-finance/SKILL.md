---
name: wuage-finance
description: 理财/投资助手：先读该家理财长时记忆与笔记（偏好/目标/持仓/决策），再基于事实回答基金、资产配置、复盘、回测问题；也可录入理财记忆。当用户问投资/理财/基金/持仓/资产配置/回测/收益相关问题时使用本技能。
---

# wuage-finance · 理财技能卡

## 定位

wuage-home 的理财模块。数据在 `$WUAGE_DATA/finance/finance.db`（当前 /root/wuage/data/finance，环境变量 `WUAGE_DATA` / `FINANCE_DATA` 可覆盖）。
脚本：`scripts/finance.py`（纯 Python3 标准库，零第三方依赖）。数据与记账**隔离**，但可**只读**引用记账（如净资产/活钱）。

若不确定当前目录，先 `git rev-parse --show-toplevel` 确认仓库根，所有命令以仓库根为 $REPO 执行。
命令统一为：`export WUAGE_DATA="${WUAGE_DATA:-/root/wuage/data}" && python3 $REPO/scripts/finance.py ...`

## 核心原则

**回答任何投资/理财/基金问题前，先读该家的理财记忆与持仓摘要**，不要凭空给建议；不构成投资建议，涉及具体买卖的给出“研究/咨询专业意见”的提醒。

读记忆：
```bash
python3 $REPO/scripts/finance.py --memo-list
```
读持仓：
```bash
python3 $REPO/scripts/finance.py --init >/dev/null 2>&1; curl -s http://127.0.0.1:17625/api/positions
```
（也可用服务 `/api/positions`、`/api/notes`、`/api/memories`。）

## 长时记忆（自动记 · 由模型自行判断）

出现**值得长期记住的理财信息**时，写入记忆：偏好（稳健/激进）、目标（收益/回撤）、已做的决定（某类仓位/定投计划）、踩坑/教训。

写：
```bash
python3 $REPO/scripts/finance.py --memo-add --memo-kind preference --memo-topic 风险 --memo-content "偏好稳健，以基金为主" --memo-importance high
python3 $REPO/scripts/finance.py --memo-add --memo-kind digest --memo-topic 决策 --memo-content "2026-09 决定给雪球基金加仓，不做短线" --memo-importance medium
```
- `--memo-kind`：`fact` / `preference` / `digest`。
- `--memo-importance`：`high`（长期目标/投资纪律）/ `medium`（一般偏好）/ `low`（临时）。
- **偏好/目标不需要用户手工设置**，由你（模型）在对话中自然判断值得记的再记；用户没提就别硬造。

## 常见操作
- 持仓/买入卖出/收益率：页面 http://127.0.0.1:17625 或 `/api/positions`、`/api/transactions`。
- 每日复盘/行情：`POST /api/refresh`、`GET /api/review?date=YYYY-MM-DD`。
- 回测：`POST /api/backtest`（自研、确定性、真实净值数据，不做 LLM 计算）。
- 学习问答：`POST /api/ask`（自动记进 notes）。

## 回答风格
- 开头点明“参考了你们的理财记录（偏好稳健、以基金为主、当前持仓 XX）”。
- 结合持仓/记忆给分析；涉及买卖给出市场信息与风险，不直接下“买卖指令”。
- 明确“不构成投资建议”。