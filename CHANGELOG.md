# Changelog

本文件记录 wuage-home 每个版本的更新内容，遵守 `docs/SPEC.md` 的规范。
最新版本在最上面。数据契约详见 `docs/DATA_MODEL.md`。

## [Unreleased]

### Changed（2026-10-16 · 布局升级 v0.2.0-rc.3）
- **底部中央「＋」快速记账**：Tab 栏改为 **[概览][账单][＋][AI记账][理财]**（拍板方案 A）。
  中央「＋」是凸起的圆形按钮，点开**快速记账面板**：上半部「🤖 一句话」、下半部「✍️ 手动」两个页签可互切；
  一句话解析完成后自动把金额/日期/分类/成员灌进手动页，核对后保存（解析失败/无金额会给出人话提示）。
  面板里还有「完整页」按钮跳回原 AI 记账页。`#tab-plus` 与概览里的「✍️ 记一笔」都会打开它。
- **财务页并入概览**：净资产大卡、资产明细（活钱/基金/存款/黄金）、AI 记账入口、更新账户余额表单
  全部上移到概览页；原「财务」tab 去掉，旧书签 `#/ledger/finance` 仍能落到概览。
  同时删掉与新三卡重复的「本月结余」卡（数字一模一样，避免两处显示同一件事）。
- **概览三卡**：本月**支出 / 收入 / 结余**三张卡（结余按正负显示绿/红与「结余为正 / 入不敷出」），
  下方保留日均 + 环比 + 同比 Pill；新增**分类占比环**（显示占比最高分类），并写明
  「预算模块上线后这个环会变成预算进度」——按 spec 预留预算进度环位。
  `/api/dashboard` 相应新增 `month_income_cents` / `month_balance_cents` / `prev_income_cents` /
  `income_change_pct` / `income_count` / `expense_count`（原有字段不变）。
- **账单页时间流**：默认「时间流」视图——按日期分组（`10月16日 周五 · 今天`）、每组一行收支小计、
  支出/收入金额分色、分类显示图标；右上角可切「表格」视图（桌面友好，保留原来的整表）。
  底部合计改为「支出 X · 收入 Y · 共 N 笔」。
- **分类图标与排序**：种子 16 分类补默认 emoji + 配色（🍚🚌💊🛍️📚🎮🏠📶🧧👕💄🐾💰🛡️💼❓），
  老库自动回填（只填空值，不覆盖用户已设的）；`list_categories` 排序改为 **sort_order 优先**，
  分类管理里新增 **↑↓** 按钮与相邻分类换位（`PATCH /api/categories/<id> {"move":"up"|"down"}`）；
  快速记账面板的分类网格按**使用次数**排序（常用靠前，前 8 个 + 「更多」网格弹层）。
  CLI：`ledger.py categories --set-style 名称 --icon 🧺 --color '#123456' --order 0`，或 `--move up|down`。
- **重复交易检测**：新增 `GET /api/entries/check-dup?date=&amount_cents=&category=&kind=&member=&days=&exclude_id=`，
  按「同日期 + 同金额（近 30 天）」判定，分类也相同的排在前面并标 ⚠️。
  前端在**快速记账、手动录入、AI 确认、账单改账**四处提交前都会先查一次，
  命中弹「🤔 疑似重复记账」列出命中记录，可选「取消」或「仍然记账」；不同金额一律不报。
  改账时带 `exclude_id` 排除自己这条，避免"编辑一笔就被自己拦住"。
- **手势**：账单时间流的行支持**左滑露出「删除 / 改」**、**长按或轻点直接编辑**（点开着的行只是收起）；
  桌面（有鼠标）下操作按钮常驻可点，不依赖手势。
- **账单行内编辑改为独立卡片**：原来的"把表格单元格换成输入框"改成固定的「✏️ 修改这笔」卡片
  （日期/金额/分类/备注），时间流和表格两个视图共用。

### Fixed（2026-10-16 · 工程债：数据归位 + 全量备份）
- **文件盒子数据写在仓库里**（违反"模块数据落 `$WUAGE_DATA/<模块>`"的纪律）：
  `filebox.py` 的默认数据目录是 `REPO_ROOT/data/files`，服务器上因此实际落在
  `/root/project/data/files` —— 脱离数据根，**备份和迁移都会漏**（迁移只拷 `/root/wuage` 就丢文件）。
  修法：`filebox.py` 与 `ledger.py` / `finance.py` / `health.py` 统一三级解析
  （`WUAGE_DATA` → `WUAGE_HOME/data` → `/root/wuage/data`），默认 `$WUAGE_DATA/files`；
  `scripts/start-filebox.sh` / `stop-filebox.sh` 的 pid/日志也一并挪出仓库；
  `scripts/systemd/dsh-filebox.service` 补 `WUAGE_DATA` 并把 `FILEBOX_DATA` 指到 `/root/wuage/data/files`。
  服务器已迁移（sha256 校验一致）并端到端验证：上传 → 落盘 → 下载 sha256 一致 → 删除；
  `/root/project/data` 与 `/root/project/backups` 已清空（旧日志归档到 `$WUAGE_DATA/logs/legacy/`）。
- **每日备份只备份了 `ledger.db`**：理财、健康（含体检报告原件）、文件盒子的数据
  **完全没有备份**，cron 里那句 `backup.sh` 只 `cp ledger.db`。
  重写 `scripts/backup.sh`：整个数据根（除 `backups/`、`logs/`）打一份
  `wuage-data-<时间戳>.tar.gz`，其中所有 sqlite 用 **backup API 取一致性快照**（边写边备也安全，
  python<3.7 退 `VACUUM INTO`，再退文件拷贝），其余文件原样拷贝；
  打包后逐条比对"包内清单 == 快照清单"，不一致就删掉这份并退非零（不让坏备份占位）；
  保留最近 7 份整包（`WUAGE_BACKUP_KEEP` 可调）+ 保留历史习惯的 `ledger-*.db` 单文件快照 7 份。
  另加 `bash scripts/backup.sh --verify [整包]`：解开到临时目录对每个库跑 `PRAGMA integrity_check`。
  cron 日志路径从仓库内的 `/root/project/backups/backup.log` 改到 `$WUAGE_DATA/logs/backup.log`。
  实测：真实数据整包 1.1 MB / 8 个文件，`--verify` 三个库全部 `ok`。
- **服务器 python 版本坑**：`/usr/bin/python3` 是 3.6（无 `sqlite3.Connection.backup`），
  `backup.sh` 现在显式挑选 3.7+ 解释器（优先 `python3.11`，其余模块同款）。

### Changed（2026-10-11 · 与 DSH 解耦）
- **模型凭据不再依赖 DSH**：`health.py` / `parse.py` / `finance.py` 的凭据函数
  （三个模块各有独立实现，**容易漏改**，已用回归测试锁住行为一致）统一改为三级解析：
  `OPENCODE_GO_API_KEY` 环境变量 → **`$WUAGE_HOME/.credentials.yaml`（wuage 自有，新增）**
  → `$DSH_HOME/.credentials.yaml`（历史来源，保留兼容）。
  背景：健康助手的 agent 循环（`_chat_llm` + `chat()` + `_apply_tool`）本来就是**自研**的，
  DSH 不在那条链路上，唯一接触点就是读一个 API key 文件——现在这个接触点也解耦了。
  实测：`DSH_HOME` 指向不存在路径时凭据仍可用；三级都缺失时优雅返回 `None` 并给出明确报错。
- 服务器已建立 `/root/wuage/.credentials.yaml`（`chmod 600`，root:root）；
  `wuage-health.service` / `wuage-finance.service` 的凭据注释同步更新
  （`DSH_HOME` 降级为第三级兜底）。

### Changed（2026-10-11 · 健康助手：持久对话 + 可跟踪计划）
- **对话上下文落库（修掉"一次对话就结束"）**：新增 `conversations` / `messages` 两张表，聊天记录真正持久化。
  服务端每轮自动带最近 10 条历史，**不再依赖前端传 `history`**；页面刷新 / 换设备 / 隔几天再来上下文都在。
  每轮的工具执行记录一并入库，历史消息重新渲染时能看到"当时记了什么"。
  页面顶部新增「＋ 新对话」；新增 `GET /api/persons/<id>/conversations`、`GET /api/conversations/<cid>/messages`、
  `POST /api/conversations/new|delete`。模型调用失败时回收本次新建的空会话，不留垃圾。
- **饮食/运动计划落库（可延续、可跟踪）**：新增 `plans` 表 + `plan_save` / `plan_update` 两个对话工具。
  规划后必须存进计划库（已写进 system prompt），`digest` 把 `active_plans` 一并喂给模型 →
  跨会话记得在执行什么计划；档案里已有 active 计划时要求"接着更新"而不是每次重造。
  新增 `plan` CLI 子命令（存/查/改/删，支持 `--status` 过滤）与 `/api/persons/<id>/plans`、`/api/plans` 接口；
  档案页新增「📋️ 计划」卡片（状态 / 周期 / 正文 / 删除）。
- **DSH persona 补全**：「家庭管家」原先只写了记账职责，现补齐 wuage-health / wuage-finance 两张技能卡的分工；
  健康部分明确"回答前必须先 digest 读档案""规划要落库"。`preset.yml` 描述同步更新。
- **入口补全**：主页新增「🏥 健康助手」直达卡，工具箱卡描述改为"文件/理财/健康"；Hub 卡片描述补上饮食运动规划。
- **文档**：`docs/TOOLBOX.md` 登记工具 #03（接口 / CLI / 数据迁移）；技能卡 `wuage-health` 新增
  「饮食 / 运动计划」「对话上下文」两节。
- **验证方式**：新增 Playwright 真实浏览器测试，覆盖页面加载 / 发消息 / **刷新后上下文恢复** /
  切换成员上下文隔离 / 新对话 / 档案页计划卡片 / **档案表单填写→保存→刷新仍在→digest 生效→清空字段** /
  无 JS 报错；报告解析用仿真图实测——
  单页图 16 项、两页 PDF 19 项（跨页合并）、**手机拍摄变体（旋转 90°/倾斜 6°/光线暗/轻微模糊）
  均为 16 项指标 + 8 项异常 + 姓名识别正确 + 关键指标 4/4**、25 页 PDF 触发截断提示；
  另外验证了**公网网关认证路径**（登录 → 页面 → 经网关对话 → 会话回读）与**并发写入**
  （90 并发写 + 40 并发读，无 database is locked）。

### Fixed（2026-10-11）
- **超长体检报告被静默截断**：`MAX_PDF_PAGES=20`（每页一次视觉调用，太多会拖很久），
  25 页的报告只解析前 20 页，**第 21-25 页无声丢弃**，档案里却是一份"看起来完整"的报告
  （实测 25 页 → 20 项指标，无任何提示）。
  修法：`ingest_report()` 记录 `pages_total` / `pages_parsed` / `truncated`，
  透出到 `/api/upload`、`/api/chat` 与页面；页面显示
  "⚠️ 报告太长被截断：共 N 页，只解析了前 20 页 —— 后面几页没有入库，建议拆成两份分别上传"；
  对话流还会把这件事作为系统提示交给模型，要求它**如实说明后半部分没入库**、别假装看到了。
- **传错文件会污染健康档案**：上传非体检报告（购物小票/发票/说明书/截图）时，视觉模型会把
  "合计 87.50 元"这类单据数字当成检查指标写进档案，还生成一份 category=「其他」的报告。
  修法：解析提示词新增第 1 条硬规则——**先判断这是不是体检/化验报告**，不是就让
  `indicators` 返回空数组、`category` 填「非报告」；`ingest_report()` 加守卫：
  非报告（或零指标）**只留档文件、不建报告、不写指标、不回填档案**，并返回 `not_a_report`；
  `/api/upload`、`/api/chat`、页面提示同步透出（"看起来不是体检报告…换一份再传吧"），
  对话流还会把这件事作为系统提示交给模型，避免它硬解读或编造指标。
- **损坏/空文件只回一句看不懂的报错**：上传截断的、改名的、空的文件时，服务把请求原样丢给视觉模型，
  用户看到的是 `模型服务 400：{"model":"deepseek-flash"}`。
  修法：`ingest_report()` 先做**文件有效性预检**（空文件 / PNG·JPEG·GIF·BMP·TIFF·WEBP 文件头 /
  `%PDF` 头 / 空文本），不合格的直接给出人话原因，不再白跑一次模型。
- **口头报的数值被当成「体检报告」**：`indicator_add`（聊天里说"我血压 145"）原先每次都调
  `add_report(category="自测")`，于是每报一个数值就多一份"报告"——每天报一次血压，档案页的
  「体检报告」就会堆出一长串「自测」条目，概览的"报告 N 份"也失真。
  修法：新增 `add_indicators()`（`report_id=NULL`，只写指标不建报告），`indicator_add` 改走它；
  `list_indicators(standalone=True)` 只取这类指标；档案页新增独立的
  **「📏 自测指标」卡片**（项目/结果/参考/日期），概览行改为
  "体检报告 N 份 · 指标 N 项（异常 N）· 自测 N 条 · 日常记录 N 条 · 计划 N 份"。
  上传的体检报告仍走 `add_report()`，行为不变；删报告也不会误删自测指标。
- **记账「一句话记账」与理财 AI 调用全部 400（OpenCode Go 网关要求会话头）**：
  `scripts/parse.py`（记账解析，`web.py` 也走它）与 `scripts/finance.py`（理财问答/复盘）
  请求时**没有带 `x-opencode-session`**，网关返回
  `400 {"type":"MissingSessionID","message":"Request is missing x-opencode-session..."}`，
  导致「说一句话就记账」和理财 AI 问答实际不可用。`health.py` 一直带着该头（`wuage-health`），
  所以只有健康助手正常——这也是它一直没被发现的原因。
  修法：`parse.py` 补 `x-opencode-session: wuage-ledger`，`finance.py` 补 `x-opencode-session: wuage-finance`。
  已实测：解析返回正确结构化 JSON，理财问答正常返回。
- **多轮对话只保留最后一轮正文（会造成刷新后内容丢失）**：模型调用工具时会有多轮——
  第一轮往往就写完整答案（那时才看得到用户的问题），第二轮只是"记好了"的收尾。
  而 `chat()` 用 `reply = 最后一轮内容` **覆盖**，于是：页面流式显示完整答案，`done` 事件一到
  又被替换成收尾句；落库的也只有收尾句 → **刷新后完整答案消失**。
  实测一例：第一轮 591 字的用药警告（明确拦下青霉素类药），落库只剩 72 字收尾。
  修法：多轮正文**按顺序拼接**保留，流式内容、`done` 返回、落库三者一致。
  回归测试 `test_multiround.py` 固化。
- **健康助手「姓名核对」实际失效（护栏形同虚设）**：解析提示词一边要求"读报告上的姓名"，
  一边又追加"person_name 请填这个值"（填的是当前档案人名），后者覆盖前者 →
  `person_name_on_report` 永远等于目标人名，核对永远不会触发。
  修法：提示词改为**照抄报告上实际印的姓名**，报告没写才填 `null`；`ingest_report()` 真正比对并返回
  `person_name_on_report` / `name_match`（`true`/`false`/`null`），透出到 `/api/upload`、`/api/chat`、
  `list_reports` 与 `parse-report` CLI；`name_match=false` 时页面显示红色警示，且**无论用户是否打字**，
  都会把"报告姓名与档案不符"作为系统提示交给对话模型，让它先提醒确认再解读。
- **健康助手档案身高体重一直空**：报告里的身高/体重只进了 `indicators`，档案页的独立字段始终为空。
  现于 `ingest_report()` 回填 `height_cm` / `weight_kg`，**仅在姓名对得上（或报告未写姓名）时做，
  且不覆盖用户已填的值**。
- **digest 超长时计划未被收窄**：截断分支只处理了指标/记录/记忆，没管计划，而计划正文可能很长
  （实测 20 份大计划时 digest 达 35 万字符）。现 `plans` 只留索引（id/类型/标题/周期/状态）、
  `active_plans` 保留正文（截断到 1200 字/份），**同一个计划的内容不再在 digest 里出现两遍**；
  实测 352165 → 13278 字符。
- **对话提示词补强**：新增"用户问的问题一定要正面回答完，不要只回一句『记下了』"；
  以及"涉及吃药时先拿档案里的过敏史/长期用药/既往病史交叉核对，有冲突第一时间提醒"。
- 修掉 `health.py` 模块文档里过时的模型说明（`deepseek-v4-flash-vision-exp` → 实际使用的 `deepseek-flash`）。

### Changed
- **健康助手重做 v2（2026-09-10 用户拍板：主页面只做 AI 问答 + 传报告）**：
  - **主页面 = AI 对话**：粘贴体检报告（PDF/图片）或直接说话；AI 读全档作答，并在对话中自动把日常流水 /
    长期记忆 / 口头报的指标 / 资料变更写进库里，用户不用填表。
  - **第二页 = 健康档案**：基础信息（姓名/称呼/性别/生日/身高/体重/血型/手机/职业/常住地）、
    扩展信息（过敏史/既往病史/手术史/长期用药/家族史/疫苗接种）、生活方式（吸烟/饮酒/运动/饮食/作息/月经）、
    其他（紧急联系人/医保/备注）——共 25 个字段，**支持编辑保存**；同页可查看体检报告原件、异常指标、日常记录、长期记忆。
  - **字段表驱动**：`PROFILE_GROUPS` 一处定义 → 后端白名单落库 + 前端表单自动渲染 + `/api/meta` 下发，加字段只改一处。
  - **模型改为与 DSH 同一个**：`deepseek-flash`（**原生多模态**，读图 + 工具调用同一次请求完成），
    不再用 `deepseek-v4-flash-vision-exp` 实验通道；凭据复用 DSH 的 `$DSH_HOME/.credentials.yaml`。
  - **报告解析链路**：PDF 用 poppler(`pdftoppm`) 渲染成图 → 视觉模型逐页结构化 → 合并入库；
    图片直读；txt/csv/json 直读；`zbarimg` 解报告里的查验二维码一并存证。
  - **安全护栏**：报告上的姓名 ≠ 当前人时，明确提醒"可能传错人/是别人的报告"，**不把别人数据写进本人档案**。
  - 新增接口：`POST /api/chat`（multipart，消息+文件一起发）、`/api/upload`、`/api/profile`、
    `GET /api/meta`、`/api/files/<id>/raw`（看原件）、报告/记录/记忆的删除接口。
  - 数据目录新增 `files/`（报告原件，sha256 命名）与 `trash/`（删人时原件转移，不真删）。
  - `wuage-health.service` 补 `DSH_HOME`（否则服务读不到模型密钥）。
  - 技能卡 `wuage-health` 重写：新增报告姓名核对、`parse-report` 用法、四组资料字段说明。

### Fixed
- **健康助手「保存资料」报 `no such column: alias`**：根因是 v1 老库的 `persons` 表自带 `sex`/`birth_date`，
  而 `PERSON_FIELDS` 误把 `alias` 也归到 persons（实际在 profiles）→ 保存时 `UPDATE persons SET alias=?` 炸。
  修法：`PERSONS_KEEP` 收敛 persons 为 id/name/role/created_at；补 `_migrate_person_extras()` 把老库的
  `persons.sex/birth_date` 迁进 `profiles` 后**删列**（SQLite < 3.35 无 DROP COLUMN，走 `_rebuild_persons()` 重建表）；
  `PERSON_FIELDS` 只剩 `name`。不修的话"改性别"会写进 persons、profiles 那份永远不更新。
- **保存资料不再要求填满**：改为只有「姓名」必填（且非空、不重名），其余 24 个字段留空一律存空值不报错。
- **健康助手对话流式**：`POST /api/chat` 支持 `stream=1`，走 SSE（`delta`/`tool`/`attach`/`done`/`ping`），
  页面逐字显示而不是转圈等整段；前端按下发送**立刻清空输入框和待发文件**。
- **修复流式回答完一直卡在「✍️ 记进档案…」**：SSE 没有 `Content-Length`，而 handler 是 HTTP/1.1，
  默认 keep-alive 复用连接 → 浏览器 `reader.read()` 永远等不到流结束（curl 表现为 240s 超时、exit 124）。
  修法：`_sse_open()` 置 `self.close_connection = True` + `Connection: close`，`finally` 里写完 `[DONE]` 关闭 wfile；
  另加 8s 心跳线程（`ping`）避免写库耗时长被误判超时，前端加 90s 看门狗兜底（超时也不再永久卡住），
  并在 `done` 到达时用真实写入结果替换掉过程中的占位提示。
- **健康助手 multipart 解析**：boundary 必须从原始 `Content-Type` 头取（`ctypes` 里没有该参数），
  且浏览器发的 boundary 带 `--` 前缀需剥掉，否则文件字段解析错乱（表现为 `invalid literal for int()`）。
- **健康助手对话历史**：multipart 里的 `history` 是 JSON 字符串，服务端按 list 处理会崩（`'str' object has no attribute 'get'`），
  现做类型归一 + 逐条过滤。

### Added
- **工具箱 #02「理财助手」MVP**：基金/ETF 持仓记录、自选行情快照、每日复盘、自研回测（定投/均线/RSI）、策略/指标收藏、Web 问答自动记笔记；spec=`docs/specs/finance-toolbox.md`。
- **工具箱入口 Hub**：主页「工具箱」卡先进入 `/home/toolbox/`（`web/toolbox.html`），再从 Hub 进入文件盒子/理财助手；主页新增「理财助手」直达卡。
- 网关新增 `/home/toolbox/` 静态 Hub 与 `/home/finance/` 反代；配套脚本 `scripts/apply-toolbox-gateway.py`（幂等、自动备份）。
- **工具 #03「健康助手」**：个人健康档案 + 体检/化验指标 + 日常记录；服务 `scripts/health.py`（零依赖）+ 页面 `web/health.html` + 数据 `WUAGE_DATA/health/health.db` + 技能卡 `wuage-health`；网关 `/home/health/`；配套 `scripts/apply-health-gateway.py`、`start-health.sh`/`stop-health.sh`。（v2 重做见上）

### Fixed
- **记账会话数据目录（WUAGE_DATA）踩坑修复**：`scripts/ledger.py` / `scripts/finance.py` 数据目录默认值在数据根迁移后未同步，未设置 `WUAGE_DATA` 时会落到仓库内 `data/`（/root/project/data），导致 DSH 记账会话「面板看不到刚记的账 / 查账为空」；现改为 `WUAGE_DATA` → `WUAGE_HOME/data` → `/root/wuage/data` 解析，并处理空字符串（避免 `Path('')` 落到当前目录）。同步更新 `start-wuage.sh`/`start-finance.sh` 默认值与 wuage-ledger 技能卡（每次命令先 `export WUAGE_DATA="${WUAGE_DATA:-/root/wuage/data}"`）。

### Changed
- **财务页改版（2026-09-06 用户拍板 + 视觉模型 4 图上限应对）**：
  - 移除「月度结算」（工资 18,000 + 公积金 3,000 自动入账）卡片及前端逻辑；
  - 新增「AI 一句话记账」入口（财务页一键跳转 AI 记账页），用自然语言记账，确认后自动「从活钱扣」；
  - 资产视图由 11 个细项账户简化为 4 组：活钱 / 基金 / 存款 / 黄金；
  - 新增 `GET /api/cashflow`（`ledger.cashflow_data`）：活钱=现金类账户最新余额合计 + 自 2026-09-01 起净收支（收入-支出），实现「9 月起消费从活钱扣」的自动联动；纯派生计算、不改历史数据；
  - 新增「本月结余」卡：本月收入 − 支出（`month_income/month_expense/month_balance`），收支自动计入活钱；
  - 净资产卡新增「资产评价与理财建议」一行：按 活钱/基金/存款/黄金 占比给出简要评价与配置建议。
- **记账成员简化（2026-09-07 用户拍板）**：「本人」= 残雪，系统不再保留「本人」角色（只有 残雪/蛙哥）；member_id=1（本人）的 90 笔流水并入残雪，本人归档；默认成员/设备身份/账单导入/解析 fallback 全部改为「残雪」（ledger.py / web.py / parse.py / import_bills.py / web/index.html）。
- **工具级「长时记忆」**：health（`memories` 表 + `memo` CLI/API + `digest` 带 memories）、finance（`memories` 表 + `--memo-add/--memo-list` + `/api/memories`）、ledger（`memories` 表 + `memo` 子命令）。每个工具按域独立记忆（隔离），回答前先读该域记忆；「偏好/目标」由模型自行判断记录，无需用户手工维护。配套技能卡更新：wuage-health / wuage-finance（新增）/ wuage-ledger 均加入「先读记忆再作答 + 自动记」协议。
- **工具服务纳管 systemd（修复 Bad Gateway）**：理财(17625)/健康(17626) 原为 nohup 手工拉起，会话结束即掉、网关报 502；现新增 `wuage-finance.service` / `wuage-health.service`（`enable` 开机自启、`Restart=on-failure`），与 `wuage-panel`/`dsh-filebox` 统一托管；`docs/OPS.md` 与 `AGENTS.md` 同步。启停改用 `systemctl`，不再用 `start-finance.sh`/`start-health.sh`。

## [v0.3.0-rc.1] - 2026-08-23

### Added
- **工具箱模块**：主页「🧰 工具箱」卡片从占位变为入口，指向工具 01；新增 [docs/TOOLBOX.md](docs/TOOLBOX.md) 工具清册。
- **工具 01 · 文件盒子（跨端文件互传/上传下载）**：
  - 纯 Python 标准库服务 `scripts/filebox.py`（零依赖），手机友好页面 `web/filebox.html`；
  - 本机 `http://127.0.0.1:17624`，家人经公网网关 `https://47.99.118.10:8443/home/files/`（家庭入口命名空间，复用网关账号/30 天免登/HTTPS 证书；`/files*` 302 兼容跳转）；
  - 上传（多选/进度条/同名自动加序号/并发安全）、下载（attachment + 单段 Range）、页面删除（带确认）；
  - 自动清理（默认保留 30 天：启动清一次 + 每 6 小时 + 手动触发），单文件上限与总配额可配；
  - 数据固定目录 `data/files/`，迁移 = 拷目录；
  - 网关补丁脚本 `scripts/apply-filebox-gateway.py`（幂等、自动备份）。

### Changed
- 网关路由：新增 `/home/files/` → 17624（先于 `/home` 通用转发判断；`/files*` 302 兼容跳 `/home/files/`），补丁脚本 `scripts/apply-filebox-gateway.py`。
- **运维解耦**：dsh web(3081) 与公网网关(8443) 拆分为两个 systemd 单元（dsh-web.service / dsh-gateway.service），改造网关路由只重启网关；文件盒子接入开机自启（dsh-filebox.service）。切换脚本 `scripts/systemd/install.sh`（幂等，先备份旧单元；执行时中断当前 dsh 会话一次）。

## [v0.2.0-rc.2] - 2026-08-23

### Added
- **支出性质**：分类挂「固定/必要/非必要」性质标签（种子分类预设、可改），记账自动归类；财务 Tab 提供本月/全年三性质统计（按月/按年分段切换）。
- **分类管理**（财务 Tab）：新增分类（带性质）、删除分类（被引用时确认迁移历史流水到「其他」后软归档；种子/其他不可删）。
- **收入录入**：手动录入支持 支出/收入 切换；AI 解析结果卡显示 收入/支出/转账 彩色标签；账单页收入绿色 +号（原已支持）。
- **设备身份**：首次进入选择使用角色（蛙哥/残雪），本机记住，顶部可「切换」；AI 记账未提人时归设备主人，手动录入成员默认设备主人。
- **首页重构为纯入口 launcher**：🐸蛙哥家庭助手 = 家的大门（问候 + 模块卡片：记账/问问家里/工具箱）；记账统计移入「记账工作区」概览。
- **路由体系**：hash 路由 `#/home`、`#/ledger`、`#/ledger/{ai,bills,finance,family}`；网关 `/home/`、`/home/ledger`、`/ledger`（兼容 → 记账工作区）。
- **数据根迁移**：`/root/wuage`（`WUAGE_HOME`）与代码仓库分离；每日备份脚本 backup.sh（cron 04:00，SQLite 拷贝+完整性校验，保留 7 份）。
- 全局记忆统一：README / OPS / DATA_MODEL / DECISIONS / ARCHITECTURE / VERSION_PLAN / AGENTS.md 同步新数据根与模块化结构。

### Changed
- 首页从「记账 Hub」改为「纯入口 launcher」（无记账数据）；记账统计进记账工作区。
- 家庭角色改为蛙哥/残雪；历史「本人」9 笔记录迁移到蛙哥；正式库测试流水已清空（等待真实数据导入）。
- 页面标题/角标：🐸蛙哥家庭助手 v0.2.0-rc.2。

## [v0.2.0-rc.1] - 2026-08-22

### Added
- 首页 Dashboard：日均花费（当月按已过天数、历史月按整月）；环比（较上月）+ 同比（较去年同月）Pill（涨红跌绿，悬停/长按显示基准月份与金额）。
- 账单分类彩色 Pill 标签（按分类名稳定配色）；自定义 Toast 轻提示（替换 alert，删除确认框除外）。
- 空状态引导（🧾 + 用 AI 记一笔直达）；底部 Tab 上移动效。
- 手动录入默认展开，AI 记账 / 手动录入卡片分层；账单筛选默认「全部类别 / 全部成员」（下拉框不再空白）。
- 后端 dashboard 新增 last_year_month / last_year_total_cents / yoy_pct；近 7 日窗口随所选月份（严格 date 过滤）。

### Changed
- 版本命名纠正：统一 v0.x 系列，**无 v2.0**；v0.2 系列 = 财务健康迭代（v0.2.0-rc.x → v0.2.0）。
- 移除旧 v0.2.0 里程碑 tag（本地 + 远程）与 dist 误命名，阶段一归档以 v0.1.7 为准（dist 改名 wuage-home-v0.1.7.*）。
- 移除首页「近 7 日每日支出」柱图；饼图放大、图例两列。
- 月份联动修复：历史月份（如 2026-07）数据正常显示（原 offset 正负号反了）。
- VERSION_PLAN 重写：v0.2 系列范围 = 支出性质 / 预算 / 家庭资产负债表 / 理财，产品决策已记录。
## [v0.1.7] - 2026-08-22

### Added
- 4 Tab 移动端重构（首页 / AI记账 / 账单 / 家庭），底部导航固定。
- 新主题：背景 #FAF8F3、主色 #D99025、卡片圆角 20px、数字加粗（Apple Health + 支付宝清爽风）。
- 首页 Dashboard：月份切换（近 12 个月）、本月支出 + 上月环比、分类占比饼图（前 5 + 其他，conic-gradient）、近 7 日每日支出柱图。
- 账单页：时间（全部/今天/近7天/本月/自定义）+ 分类 + 成员筛选；支出红色/收入绿色；行内编辑与删除；空态提示。
- 家庭页：成员列表 + 当月消费、新增成员（姓名/关系）、移除（有消费记录禁止）。
- 后端：GET /api/dashboard（聚合）、categories/members 增删改 API（含软归档分类复活、重名 400、引用与种子保护）。

### Changed
- PRD 阶段一全部完成；面板版本 v0.1.7。
## [v0.1.6] - 2026-08-22

### Added
- AI 记账结果卡：解析后展示金额/日期/分类/成员/商户/备注 + 置信度；确认、修改（全字段可编辑）、取消。
- 草稿机制：解析后未确认自动存草稿（24h 过期），刷新页面顶部提示可恢复/丢弃。
- /api/parse 改为只解析（不再直接入库）；确认后经 POST /api/entries 保存（source=ai_parsed、raw_text、ai_confidence）。
- GET /api/members；草稿 CRUD API（GET/POST/DELETE /api/drafts）。
- parse.py 契约升级：识别成员（候选=家庭成员，默认本人）、商户、置信度；候选动态读库。

### Changed
- AI 记账流程由"解析即入库"升级为"解析 → 结果卡确认 → 入库"；面板版本 v0.1.6。

## [v0.1.5] - 2026-08-22

### Added
- 数据层 v2（PRD 对齐）：families / members / accounts / categories / transactions / drafts 表。
- 分类管理 CLI：手动新增、改名、删除（**被交易引用禁止删除**、种子与"其他"不可删）；LLM 不自动建类。
- 成员管理 CLI（默认"本人"；有消费记录禁止删除）。
- 草稿 CLI（24h 过期自动清理）。
- 迁移命令：旧账表 → transactions（幂等；分类映射：医疗健康→医疗、育儿教育→教育、未知→其他）。
- docs/prd/：用户 PRD 原文、PRD 评审、分类体系设计三份文档入库。

### Changed
- /api/health、/api/categories 改为读取分类表（新增分类实时反映）。
- 存量 6 笔真实数据已迁移到 v2 模型（分类口径转换正确）。

### Fixed
- row_to_dict 对无 tags 列的表（members/categories/drafts）崩溃问题。
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