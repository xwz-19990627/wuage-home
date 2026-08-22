# Changelog

本文件记录 wuage-home 每个版本的更新内容，遵守 `docs/SPEC.md` 的规范。
最新版本在最上面。数据契约详见 `docs/DATA_MODEL.md`。

## [v0.1.4] - 2026-08-22

### Fixed
- 柱状图柱身不可见：.fill 是 inline span，width/height 不生效 → 显式 display:block；
  柱长按占比显示、颜色内联+CSS 兜底（用户验收通过）。
- 旧页面被浏览器缓存导致"改了不生效"：HTML 响应与 <head> 增加 no-store 禁缓存。

### Changed
- 页面版本标记 v0.1.4；- docs/SPEC.md：发布流程加入"用户验收通过后才可提交/打 tag"与"测试数据隔离"守则。
## [v0.1.3] - 2026-08-22

### Added
- 一句话智能记账：面板输入口语文本（如"今天买西瓜花了10块"）→ 大模型自动解析归类入库，
  无需手填金额；解析无金额时返回友好提示（422）。
- 解析实现 scripts/parse.py：走 **OpenCode Go 网关**（https://opencode.ai/zen/go，deepseek-v4-flash），
  凭据链 OPENCODE_GO_API_KEY（环境变量 → $DSH_HOME/.credentials.yaml），不触碰官方 DeepSeek key。
- 解析契约与 wuage-ledger 技能卡一致（口语金额/日期换算/9 类清单/原话入 note）；失败自动重试一次。
- 手动录入折叠保留（"手动录入"按钮展开）。

### Changed
- 面板端口 8000 → **17623**（不常见端口）：systemd 单元、网关 /ledger/ 反代目标、脚本、文档同步。
- 分析图表：柱条长度改为**占区间总金额的百分比**；每项**颜色不同**（类别固定色板、按日趋势循环色板）。

### Fixed
- 之前"柱状图长度都一样"：旧实现按最大值归一，多数记录同比例；已改为占比 + 多色。
## [v0.1.2] - 2026-08-22

### Added
- 远程访问：公网网关新增 `/ledger/` 路由（复用网关登录认证后反代到本机面板 127.0.0.1:8000）。
- 前端 API 全部改相对路径，兼容本地直连与网关子路径两种访问方式。
- 面板托管为 systemd 服务 `wuage-panel.service`（开机自启、失败自动重启）。
- 新增运维文档 docs/OPS.md（网关路由改动、systemd 管理、访问方式）。

### Fixed
- 修复“本机打不开 http://47.99.118.10:8000”问题：面板按设计只绑 127.0.0.1、
  公网 8000 不开放；远程访问一律走认证网关 https://47.99.118.10:8443/ledger/。
- 面板进程从手工 nohup 改为 systemd 托管，避免会话中断导致进程丢失。
## [v0.1.1] - 2026-08-22

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