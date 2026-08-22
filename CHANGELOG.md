# Changelog

本文件记录 wuage-home 每个版本的更新内容，遵守 `docs/SPEC.md` 的规范。
最新版本在最上面。数据契约详见 `docs/DATA_MODEL.md`。

## [v0.1.1] - 2026-08-21

### Added
- 分析卡片：本周/近7天/近30天/自定义区间切换；总支出/笔数/日均指标；
  按类别占比视图（金额、笔数、百分比柱条）与按日趋势视图。
- 完整行内编辑：金额/日期/类别/备注全字段（PATCH）。
- 流水表筛选合计行；页面版本号标注 v0.1.1。
- 验收工具：scripts/selftest.sh（API 回环冒烟：health/categories/增改删/weekly）。
- 一键启停：scripts/start-wuage.sh（自动开浏览器）/ scripts/stop-wuage.sh。
- 打包：scripts/package.sh → dist/wuage-home-v0.1.1.{zip,tar.gz}（不含 data/）。

### Changed
- v0.1.1 spec 确认范围：数据可视化 + 编辑；**不做账单导入**（用户拍板，移至后续版本）。

### Fixed
- （无）
## [v0.1.0] - 2026-08-21

### Added
- 项目初始化：wuage-home 仓库（git main 分支 + 远端 GitHub wuage-home）。
- 规范体系：`docs/SPEC.md`（开发规范）、`docs/VERSION_PLAN.md`（版本规划）、
  `CHANGELOG.md`（本文件）、`docs/specs/` 功能 spec 模板、`docs/archive/` 归档机制。
- 架构文档：`docs/DECISIONS.md`（运行环境/交互/存储/DSH 壳子/知识库工作流决策）、
  `docs/VISION.md`（最终形态愿景）、`docs/DATA_MODEL.md`（数据模型 v1）、
  `docs/ROADMAP.md`（候选模块路线）。
- 数据层：`scripts/ledger.py`（纯标准库 CLI：init/add/update/delete/list/categories/summary/weekly，
  金额以分存储，9 项固定大类校验，WUAGE_DATA 可迁移）。
- 技能卡：`.dsh/skills/wuage-ledger/SKILL.md`（一句话记账解析契约，DSH 实时发现）。
- 预设：「家庭管家」`presets/wuage-family/` + `scripts/install-preset.sh`。
- Web 面板：`scripts/web.py`（零依赖 HTTP API：entries/categories/summary/weekly/health）+ `web/index.html`
  （单页前端：记一笔、流水筛选、行内改账、删除、本周汇总柱状图）。

### Changed
- 分类体系最终拍板：只有 9 项一级大类，不再做二级小类；原话保留在 note 作为未来词表种子源。

### Fixed
- （首版基线，无修复项）

### 备注
- 该版本为自建基线，汇总了此前所有开发提交。
- 遗留事项（不在本版范围）：tags/member 字段预留未启用、微信账单导入、周报 LLM 叙述、主动提醒定时。