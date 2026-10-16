---
name: wuage-health
description: 个人健康档案与健康问答：先读该人完整档案（基础信息/既往病史/过敏/用药 + 体检报告指标 + 日常饮食运动睡眠 + 长期记忆 + 在执行中的计划），再基于事实回答健康、饮食、运动、用药问题；也可解析体检报告入库、录入日常记录、制定并跟踪饮食/运动计划。当用户问自己或家人健康/体检/饮食/运动/睡眠/症状/用药相关问题时，或要求安排饮食/运动计划时使用本技能。
---

# wuage-health · 健康技能卡

## 定位

wuage-home 的健康模块。数据在 `$WUAGE_DATA/health/`（当前 `/root/wuage/data/health`：
`health.db` 结构化数据 + `files/` 上传的报告原件）。环境变量 `WUAGE_DATA` / `HEALTH_DATA` 可覆盖。

脚本：`scripts/health.py`（纯 Python3 标准库，零第三方依赖）。
页面：http://127.0.0.1:17626 或 `https://<host>:8443/home/health/`（主页是对话，第二页是健康档案）。

当前角色只有**残雪**（默认，用户本人）、**蛙哥**。

## 核心原则（最重要）

**回答任何健康/饮食/运动/睡眠/用药问题前，必须先读该人的完整档案**，不要凭空答。

```bash
export WUAGE_DATA="${WUAGE_DATA:-/root/wuage/data}"
python3 $REPO/scripts/health.py digest --name 残雪   # 或 蛙哥
```

digest 返回：person、profile（基础信息+扩展信息+生活方式）、reports（历次体检）、
abnormal_indicators（异常指标）、recent_indicators、records（近期日常）、**memories（长期记忆）**、
**plans（全部计划）+ active_plans（在执行中的）**、stats。

把 relevant 的部分**用人话转述**给用户，并基于这些事实回答；
**信息不足时明确说"缺X，建议就医/补记录"，不做医疗诊断**。

## 饮食 / 运动计划（规划要落库，别只在对话里说一遍）

用户要你安排饮食/运动时，**基于档案的真实情况**（体重、异常指标、病史、过敏、作息、戒酒等）出方案，
然后**存进计划库**——这样下次会话还接着用，能改、能跟踪，不是一次性回答。

```bash
python3 $REPO/scripts/health.py plan --name 残雪 --kind diet --title "减脂一周" \
    --period 2026-W42 --content "早餐：燕麦+鸡蛋
午餐：杂粮饭+鸡胸
忌：油炸"
python3 $REPO/scripts/health.py plan --name 残雪                      # 列出全部
python3 $REPO/scripts/health.py plan --name 残雪 --status active       # 只看在执行中的
python3 $REPO/scripts/health.py plan --name 残雪 --update 1 --content "改成每周三次"
python3 $REPO/scripts/health.py plan --name 残雪 --update 1 --status done   # done/paused/archived
python3 $REPO/scripts/health.py plan --name 残雪 --delete 1
```

- **kind**：`diet` / `exercise` / `mixed` / `other`。
- **status**：`active`（默认）/ `paused` / `done` / `archived`。
- **规矩**：档案里已有 `active` 计划就**接着更新**，不要每次重新造一份；用户说"没按计划来""改成每周三次"
  "先停掉"，都要落到计划上。计划要可执行——写清每天/每周做什么、量多少、忌什么，别写空话。
- 页面上「健康档案」页有「📋️ 计划」卡片，能看到全部计划并手动删除。

## 模型与凭据（已与 DSH 解耦）

默认 `deepseek-flash`——**原生多模态**，读图和工具调用在同一次请求里完成，不需要 vision 实验通道。
`WUAGE_HEALTH_MODEL` / `WUAGE_VISION_MODEL` / `WUAGE_CHAT_MODEL` 可覆盖。

**凭据解析顺序**（前者优先，见 `api_key()`）：

1. 环境变量 `OPENCODE_GO_API_KEY` —— systemd 可注入，最推荐
2. **`$WUAGE_HOME/.credentials.yaml`**（当前 `/root/wuage/.credentials.yaml`）—— wuage 自有，
   让健康助手**不依赖 DSH 存活**
3. `$DSH_HOME/.credentials.yaml` —— 历史来源，保留兼容

> 这三级是有意设计的：DSH 升级/搬迁/移除都不会影响健康助手。
> 实测：`DSH_HOME` 指向不存在路径时，凭据仍能从第 2 级取到；两处都没有时优雅返回 `None` 并给出明确报错。

## 上传体检报告（用户丢 PDF/图片过来时）

用户可能直接把报告发到 DSH 会话里，也可能在健康页面传。两条路：

**A. 在 DSH 会话里收到报告**——用 CLI 解析入库（推荐，直接落进健康库）：
```bash
python3 $REPO/scripts/health.py parse-report --name 残雪 --file /root/wuage/data/health/files/xxx.pdf
```
支持 PDF（poppler 渲染成图）、图片、txt/csv/json。自动解二维码、自动判异常、自动入库。
**注意单次 ≤4 页/图**（模型限制），超长报告分批。

**B. 让用户自己在页面传**——页面主页 📎 按钮，选人后发送即可，AI 会自动解析并点评。

### 姓名核对（重要）
入库时脚本会**读出报告上实际印的姓名**，和当前档案比对，结果在返回值里：

- `person_name_on_report`：报告上的姓名（没写就是 `null`）
- `name_match`：`true` 一致 / `false` 不一致 / `null` 无法判断（报告没写姓名）

**`name_match` 为 `false` 时**：这多半是别的家人的报告或传错了人。要提醒用户确认，
**绝对不要**把报告里的身高体重年龄写进当前这个人档案，也不要当成他的体检结果解读。
CLI 会直接打印 ⚠️ 警告；页面也会显示红色提示，并把这件事带给对话模型。

另外：姓名一致（或报告没写姓名）时，脚本会把报告里的**身高/体重自动回填进档案**
（档案页的独立字段），但**不覆盖**用户已经填过的值。

## 长时记忆（自动记 · 由模型自行判断）

健康工具像一个「会记住你的管家」。对话里出现**值得长期记住的信息**就写库；下次回答前先读出来。

```bash
python3 $REPO/scripts/health.py memo --name 残雪 --kind fact --topic 过敏 --content "对青霉素过敏" --importance high
python3 $REPO/scripts/health.py memo --name 残雪 --kind preference --topic 运动 --content "喜欢晚上跑步"
python3 $REPO/scripts/health.py memo --name 残雪 --topic 过敏          # 按主题查
python3 $REPO/scripts/health.py memo --name 残雪 --delete 3            # 删
```

- **kind**：`fact`（过敏/慢病/用药/手术/体检结论）、`preference`（作息/口味/忌讳）、`digest`（阶段摘要）。
- **importance**：`high`（过敏、长期用药、慢性病、手术史——每次必读）、`medium`、`low`。
- **何时记**：用户明确说了病史/过敏/用药/习惯/目标；或重要体检结论；或一段对话要点（digest）。
- **偏好不需要用户事先设置**：由你判断值得记的再记；没新信息就别硬造。

## 对话上下文（落库，不再"一次对话就结束"）

页面上的聊天记录会**落库**（`conversations` / `messages` 两张表）：刷新页面、换设备、隔几天再来，
上下文都还在。服务端每轮自动带最近 10 条历史，前端不再自己拼 history。

- 页面顶部「＋️ 新对话」= 开一段新会话；旧的那段留在库里，不会丢。
- 排查"上次说到哪了"，可以直接查接口（服务在 17626）：
  `GET /api/persons/<id>/conversations` → 最近的会话列表（带 msg_count）
  `GET /api/conversations/<cid>/messages` → 该会话全部消息（含每轮的工具执行记录）

## 其他常用操作

```bash
python3 $REPO/scripts/health.py persons                    # 列出成员
python3 $REPO/scripts/health.py profile --name 残雪         # 看个人资料
python3 $REPO/scripts/health.py profile --name 残雪 --set city=杭州 --set allergies=青霉素
python3 $REPO/scripts/health.py record --name 残雪 --category diet --content "早餐燕麦+鸡蛋"
python3 $REPO/scripts/health.py chat --name 残雪 --text "我最近老失眠"   # 对话式问答（会自动落库）
```

日常记录类别：`diet/exercise/sleep/symptom/medication/mood/other`。

**两类「数值」要分清**（页面上也是分开显示的）：

| 来源 | 入库方式 | 页面位置 |
| --- | --- | --- |
| 上传的体检报告（PDF/图片） | `parse-report` → `add_report()`，建「报告」+ 指标 | 📄 体检报告 |
| 用户口头报的数值（"我血压 145"） | 对话工具 `indicator_add` → `add_indicators()`，**只写指标、不建报告** | 📏 自测指标 |

口头数值刻意不建「报告」：一次读数不是一份体检报告，否则每天报一次血压就会在档案里堆出一串「自测」条目。
两者的指标都会进 `digest`（`recent_indicators`），也都会参与异常判定，所以回答时都能看到。

个人资料字段分四组：基础信息（姓名/称呼/性别/生日/身高/体重/血型/手机/职业/常住地）、
扩展信息（过敏史/既往病史/手术史/长期用药/家族史/疫苗接种）、
生活方式（吸烟/饮酒/运动/饮食/作息/月经）、其他（紧急联系人/医保/备注）。

**只有「姓名」必填**，其余字段用户想填才填，留空是正常状态——不要催用户补全档案。
`persons` 表只放身份（id/name/role/created_at），其余字段全在 `profiles` 表。

## 回答风格（人话，不贴 JSON）

- 开头点明参考了档案的哪些部分（"你 8 月那份体检、甘油三酯偏高的那条"）。
- **具体到这个人和这些数字**：不说"注意血脂"，要说"你甘油三酯 2.4，参考上限 1.7"。
- 结合生活方式记录给针对性建议；提醒遵医嘱，不构成医疗诊断。
- 涉及用药/慢性病/异常指标 → 明确"建议线下就医/咨询医生"。
