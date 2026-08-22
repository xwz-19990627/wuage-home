# wuage-home · 项目开发规范（SPEC）

> 本文件是项目的"本地 spec 文档"，常驻仓库，所有版本/迭代工作照此执行。
> 配套文件：`CHANGELOG.md`（更新记录）、`docs/VERSION_PLAN.md`（版本规划）、
> `docs/specs/`（功能设计）、`docs/archive/`（版本归档）、`docs/daily/`（每日流水，可选）。

## 1. 总则：spec-first

- 每个**新版本**或**新功能模块**开工前，先在 `docs/specs/` 写一份功能 spec
  （复制 `docs/specs/_template.md`），写清目标、范围、数据契约、接口、验收标准。
- spec 通过（本会话或与你讨论确认）后才动手实现。
- 小修补（< 30 分钟、不影响数据契约）可跳过独立 spec，但必须在 CHANGELOG 记一笔。

## 2. 版本号规则

- 格式 `vMAJOR.MINOR.PATCH`：`v0.x.y`。
  - `x`（MINOR）= 新功能/新模块；
  - `y`（PATCH）= 修复与打磨，不改变数据契约与接口；
  - `MAJOR` 留到公开稳定（对外使用）时升 1。
- 每个发布版本打 **git tag**（`git tag -a v0.x.y`），tag 即归档点。

## 3. 迭代节奏：按版本为主、按日期为辅

- **默认按版本**：一个功能版本做完 → CHANGELOG 记录 → tag → 归档。
- **可选按日期**：工作日如有零散改动，在 `docs/daily/YYYY-MM-DD.md` 记流水
  （改了什么、为什么），版本发布时并入该版本 CHANGELOG 条目。
- **每天或每个版本必须记录更新了什么**——这是硬规则，禁止"改了不记"。

## 4. CHANGELOG 格式（`CHANGELOG.md`）

```markdown
## [v0.x.y] - YYYY-MM-DD

### Added    新增
- ...

### Changed  变更（行为/接口/契约变化）
- ...

### Fixed    修复
- ...

### Removed  移除
- ...
```

- 最新版本在最上面（倒序）。
- 数据契约（`docs/DATA_MODEL.md`）与接口有变化时，必须在 Changed 里明确写出。

## 5. 版本发布流程（`bash scripts/release.sh v0.x.y`）

1. 功能 spec 已完成、测试通过（本地跑一遍 ledger.py / API 冒烟）。
2. `CHANGELOG.md` 写好本版条目。
3. 执行 `bash scripts/release.sh v0.x.y`：
   自动 `git add -A` → commit `release: v0.x.y` → 打 tag → push（含 tag）。
   （release.sh 会校验版本号格式和 CHANGELOG 是否已有该版本条目）

## 6. 归档规则

- 版本完成发布后，把该版本的 **spec 与完成记录**归档到 `docs/archive/v0.x.y-YYYY-MM-DD.md`：
  一句话总结、新增了什么、关键决策（引用 DECISIONS/VISION）、遗留事项。
- 归档后，`docs/specs/` 里该功能的草稿可删（或移入归档文件附录），保持 specs 目录只放"未完成/进行中"的 spec。
- CHANGELOG 保留摘要，不删。

## 7. 会话模式约定

- 版本规划 / 新功能设计 → 切 **spec 模式**（计划先行，写 spec）。
- 实现与调试 → 可留在当前模式（react/自动），但不得跳过本规范。

## 8. 完成定义（Definition of Done）

- [ ] 代码实现并本地验证（脚本冒烟 / API 测试）
- [ ] `docs/specs/` 有对应 spec（小修补除外）
- [ ] `CHANGELOG.md` 记了本版条目
- [ ] 数据契约变化同步更新 `docs/DATA_MODEL.md`
- [ ] release.sh 发布（tag + push）
