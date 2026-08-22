# wuage-home · 版本规划（VERSION_PLAN）

> 与 docs/ROADMAP.md 配合：这里按版本排时间线，ROADMAP 列候选模块内容。
> 每个版本开工前先写 `docs/specs/<功能>.md`（见 _template.md）。规划随讨论调整。

| 版本 | 主题 | 内容 | 状态 |
| --- | --- | --- | --- |
| v0.1.0 | 记账 MVP 基线 | 数据层 CLI + 技能卡 + 家庭管家 preset + Web 面板 v1 | ✅ 2026-08-21 发布 |
| v0.2.x | 面板打磨 + 导入 | Web 面板 UX（图表细化/导出 CSV/编辑体验）、微信/支付宝账单导入入口 | 规划中 |
| v0.3.x | 新闻简报模块 | RSS 订阅 → Flash 去重过滤 → 每日 3~5 条简报（邮件/Web） | 规划中 |
| v0.4.x | 家庭知识问答 | 文档层记忆（FTS5）+ 保修卡/说明书画册式问答 | 规划中 |
| v0.5.x | 主动提醒 | 到期守望（账单/保修/生日）、周报定时任务（DSH 任务看板 cron） | 规划中 |
| v0.6.x | 多入口适配 | QQ bot / 邮件推送（复用现有 API 层） | 规划中 |

## 迭代纪律（简版）

- 每个版本/每天必须记录：CHANGELOG.md（版本）或 docs/daily/YYYY-MM-DD.md（零散改动）。
- 完成即发布：release.sh → tag → 归档 docs/archive/。
- 具体规则见 docs/SPEC.md。
