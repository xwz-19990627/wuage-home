# wuage-home · 家用智能体

> 个人家庭场景的智能体项目（起步阶段），目标是长期可扩展的「家庭数字助手」。

## 愿景

一个跑在家里、以本人和家人为中心的智能体，未来按需挂载各种能力模块：

- 💰 记账 + 资产分析
- 📰 新闻/信息推送
- 📔 家庭日志 / 备忘
- …（规划中，持续补充）

## 快速开始（本机）

```bash
# 1. 安装「家庭管家」preset（新会话选择器里可选）
DSH_HOME=/root/dsh_rsc/.dsh bash scripts/install-preset.sh

# 2. 在 Web GUI 新建会话，预设选「家庭管家」，然后直接说：
#    "今天买西瓜花了10块"
#    "昨天打车23"
#    "这周花了多少？"
#    或命令行直连：
python3 scripts/ledger.py add --json
python3 scripts/ledger.py categories --add 宠物   # 手动新增分类（LLM 不自动建）
python3 scripts/ledger.py members --add 妈妈 --relation 老婆 '{"amount_cents":1000,"category":"餐饮","note":"今天买西瓜花了10块"}'
python3 scripts/ledger.py weekly

# 3. 起 Web 面板（查账/图表/按钮改账，零依赖）
python3.11 scripts/web.py --port 17623            # 打开 http://127.0.0.1:17623
#    手机访问（局域网内可见数据）：
#    python3.11 scripts/web.py --port 17623 --host 0.0.0.0
```

数据统一在 `data/`（`WUAGE_DATA` 环境变量可改），迁移 = 拷走该目录。

## 规范与合作方式

- 项目规范（版本/迭代/记录/归档）：[docs/SPEC.md](docs/SPEC.md)
- 更新记录：[CHANGELOG.md](CHANGELOG.md) ｜ 版本规划：[docs/VERSION_PLAN.md](docs/VERSION_PLAN.md)
- 功能设计：[docs/specs/](docs/specs/) ｜ 完成归档：[docs/archive/](docs/archive/)


## 打包与启动（App）

```bash
bash scripts/start-wuage.sh    # 启动面板并自动打开浏览器（http://127.0.0.1:17623）
bash scripts/stop-wuage.sh     # 停止
bash scripts/selftest.sh       # 验收/冒烟：API 回环检查
bash scripts/package.sh        # 打包 → dist/wuage-home-v0.1.1.{zip,tar.gz}（不含 data/）

# 手机访问：WUAGE_WEB_HOST=0.0.0.0 bash scripts/start-wuage.sh（局域网内可见）
```

## 现状

- [x] 项目脚手架初始化（git）
- [ ] 与 GitHub 远程仓库对接推送
- [ ] 架构与模块规划（见 docs/ROADMAP.md）

## 开发

```bash
git status          # 当前变更
git log --oneline   # 提交历史
```

## 仓库

- 作者：https://github.com/xwz-19990627