# wuage-home · 架构（依赖现状 & 演进方向）

> 回答“我们依赖 DSH 服务吗”：**核心不依赖，解析环节当前依赖**，下一步把 DSH 降级为可选入口。

## 现状依赖图

```
[对话入口] DSH 会话（家庭管家 preset + wuage-ledger 技能卡）
   │   ← 唯一依赖 DSH 的环节：LLM 把一句话解析成结构化 JSON
   ▼
[数据层] scripts/ledger.py（纯 Python，零 DSH 依赖）──▶ data/ledger.db
   ▲
[展示层] scripts/web.py（纯 Python http 服务，零 DSH 依赖）──▶ 浏览器（127.0.0.1:8000）
```

- **不依赖 DSH 的部分**：数据层、Web 面板、所有数据文件。
  把它们拷到任意有 python3 的机器，CLI 和面板照常运行。
- **依赖 DSH 的部分**：“说一句话自动记账”的解析环节（agent 按技能卡理解）+ 预设/技能装载。
  机器上没有 DSH 时，自动分类这步缺失（手动填 Web 表单仍可用）。
- **未来注意**：定时任务若走 DSH 任务看板则依赖 DSH；也可用系统 cron / systemd 独立实现（0.1.x 不涉及）。

## 演进方向：DSH 从“必需”降为“可选入口”

1. 新增 `scripts/parse.py`（约 50 行）：直调 DeepSeek API（OpenAI 兼容，V4 Flash），
   一句话 → JSON，契约与 wuage-ledger 技能卡完全一致。依赖：环境变量 `DEEPSEEK_API_KEY`。
2. web.py 增加 `POST /api/parse`，前端加“一句话输入框”：浏览器里直接说完即记，完全不经过 DSH。
3. 入口并列：DSH 会话 / Web 输入框 / 未来 QQ bot —— 三个适配器，核心不变。

### 结果

核心（ledger + web + parse）零 DSH 依赖；DSH 仍是体验最好的高级对话壳（可执行任意复杂指令、
调用未来模块），但不是必需品。迁移 = python3 + 拷 data/ + 配 `DEEPSEEK_API_KEY`。

## 权衡

| 方案 | 成本 | 收益 |
| --- | --- | --- |
| 维持现状（解析走 DSH） | 零成本 | 解析能力免费；但没有 DSH 的机器上“一句话记账”不可用 |
| 去 DSH 化（parse.py + 输入框） | 约 1 个小版本（v0.1.1） | 核心完全自足，兑现“可迁移”承诺；DSH 变可选 |
| 全盘独立（连 DSH 会话也不要） | 放弃现有对话壳 | 不做推荐——DSH 已经是好用的高级入口 |

推荐：**核心自足 + DSH 作高级入口**（中间方案），即 v0.1.1 计划。
