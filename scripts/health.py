#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wuage-home 工具 #03 · 健康助手（对话式 + 完整健康档案）。

定位：一个"懂这个人的"健康 AI。主页面只做两件事——聊天、传体检报告。
所有结构化数据（个人资料 / 体检指标 / 日常记录）由 AI 在对话中沉淀，另设"档案"页查看编辑。

三类数据：
  资料 profile  —— 基础信息（姓名/性别/生日/身高体重/血型）+ 扩展信息（过敏/病史/手术/用药/家族史/生活方式/备注）
  报告 report   —— 一次体检：上传的原始文件（PDF/图片）+ 结构化后的全部指标
  记录 record   —— 日常流水（饮食/运动/睡眠/症状/用药/心情），聊天里随手说、AI 自动落库

另有两类"让助手有连续性"的数据：
  会话 conversation/message —— 聊天记录落库。页面刷新、换设备、隔几天再来，上下文都还在
                               （服务端历史，不再只靠前端传最近 10 条）。
  计划 plan                 —— 饮食/运动方案落库。AI 规划后存下来，跨会话可续、可跟踪、
                               可暂停/完成；digest 会把 active 计划一并带给模型。

设计：
  - 纯 Python 标准库（零第三方依赖），风格与 scripts/finance.py 一致。
  - 数据落 $WUAGE_DATA/health/（默认 /root/wuage/data/health）：
        health.db      结构化数据
        files/         上传的体检报告原件
  - 页面 web/health.html；经公网网关 /home/health/ 前缀转发后相对路径不变。
  - 多模态解析用与 DSH 同一个模型（OpenCode Go 网关 + deepseek-flash，原生多模态）。
  - PDF 先用 poppler 的 pdftoppm 渲染成图再交给视觉模型；二维码用 zbarimg 解出查验链接。

用法：
  python3.11 scripts/health.py --port 17626
  python3.11 scripts/health.py init
  python3.11 scripts/health.py persons
  python3.11 scripts/health.py profile --name 残雪
  python3.11 scripts/health.py digest --name 残雪
  python3.11 scripts/health.py record --name 残雪 --category diet --content "早餐燕麦+鸡蛋"
  python3.11 scripts/health.py memo --name 残雪 --kind fact --topic 过敏 --content "青霉素过敏"
  python3.11 scripts/health.py parse-report --name 残雪 --file /path/report.pdf
  python3.11 scripts/health.py chat --name 残雪 --text "我最近老失眠"

环境变量：
  HEALTH_HOST / HEALTH_PORT / HEALTH_DATA
  WUAGE_PARSE_API / WUAGE_VISION_MODEL / WUAGE_CHAT_MODEL
  OPENCODE_GO_API_KEY / DSH_HOME
"""

import argparse
import base64
import datetime
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


# ── 路径 / 配置 ─────────────────────────────────────────────────────
def _resolve_data_dir():
    d = os.environ.get("WUAGE_DATA") or ""
    if d.strip():
        return Path(d)
    h = os.environ.get("WUAGE_HOME") or ""
    if h.strip():
        return Path(h) / "data"
    return Path("/root/wuage/data")


WUAGE_DATA = _resolve_data_dir()
HEALTH_DATA = Path(os.environ.get("HEALTH_DATA", str(WUAGE_DATA / "health")))
DB_PATH = HEALTH_DATA / "health.db"
FILES_DIR = HEALTH_DATA / "files"
TRASH_DIR = HEALTH_DATA / "trash"
WEB_PATH = REPO_ROOT / "web" / "health.html"
MAX_UPLOAD = 32 << 20          # 单个上传文件上限 32MB
MAX_PDF_PAGES = 20             # 单份 PDF 最多解析页数
TEXT_READ_LIMIT = 12000        # 文本类报告读取上限（字符）
API_URL = os.environ.get("WUAGE_PARSE_API", "https://opencode.ai/zen/go/v1/chat/completions")
# 与 DSH 本会话同一个模型（~/.dsh/settings.yaml 的 agent-default-model）。
# deepseek-flash 原生多模态：读图 + 工具调用同一次请求搞定，不需要 vision-exp 实验通道。
MODEL = os.environ.get("WUAGE_HEALTH_MODEL", "deepseek-flash")
VISION_MODEL = os.environ.get("WUAGE_VISION_MODEL", MODEL)
CHAT_MODEL = os.environ.get("WUAGE_CHAT_MODEL", MODEL)
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

SEED_PERSONS = [
    ("残雪", "self", "女"),
    ("蛙哥", "family", "男"),
]

# 一个"人"的全部字段定义：分组 → [(字段, 标签, 控件, 占位/说明)]
# 前端按这份定义渲染表单，后端按这份定义白名单落库——加字段只改这里。
TEXT = "text"
AREA = "area"
DATE = "date"
NUM = "num"
PROFILE_GROUPS = [
    ("basic", "基础信息", [
        ("name", "姓名", TEXT, "如：残雪"),
        ("alias", "称呼", TEXT, "如：老婆 / 蛙哥"),
        ("sex", "性别", TEXT, "男 / 女"),
        ("birth_date", "出生日期", DATE, ""),
        ("height_cm", "身高(cm)", NUM, "如 165"),
        ("weight_kg", "体重(kg)", NUM, "如 55"),
        ("blood_type", "血型", TEXT, "A / B / O / AB"),
        ("phone", "手机号", TEXT, "紧急联系用"),
        ("occupation", "职业", TEXT, "如：教师"),
        ("city", "常住地", TEXT, "如：杭州"),
    ]),
    ("extended", "扩展信息（病史相关）", [
        ("allergies", "过敏史", AREA, "如：青霉素、花生（逗号或换行分隔）"),
        ("history", "既往病史", AREA, "如：高血压（2023 年确诊）"),
        ("surgeries", "手术史", AREA, "如：2021 年阑尾切除"),
        ("medications", "长期用药", AREA, "如：氨氯地平 5mg 每日一次"),
        ("family_history", "家族史", AREA, "如：父亲糖尿病、母亲高血压"),
        ("vaccinations", "疫苗接种", AREA, "如：新冠疫苗 3 针"),
    ]),
    ("lifestyle", "生活方式", [
        ("smoking", "吸烟", TEXT, "不吸 / 已戒 / 每日 X 支"),
        ("drinking", "饮酒", TEXT, "不饮 / 偶尔 / 每周 X 次"),
        ("exercise", "运动习惯", TEXT, "如：每周跑步 3 次，每次 5km"),
        ("diet_habit", "饮食偏好", AREA, "如：口味偏咸，爱吃甜食，不吃香菜"),
        ("sleep_habit", "作息", TEXT, "如：23:30 睡，7:00 起"),
        ("menstruation", "月经情况", TEXT, "女性填写，如：规律 / 已绝经"),
    ]),
    ("other", "其他", [
        ("emergency_contact", "紧急联系人", TEXT, "如：蛙哥 138xxxx"),
        ("insurance", "医保/商保", TEXT, "如：职工医保 + 百万医疗"),
        ("notes", "备注", AREA, "任何 AI 该知道的事"),
    ]),
]

LIST_FIELDS = {"allergies", "history", "surgeries", "medications",
               "family_history", "vaccinations"}
NUM_FIELDS = {"height_cm", "weight_kg"}
# persons 表只放「身份」：id/name/role/created_at。
# 除 name 外的所有资料字段（含 alias/sex/birth_date）都在 profiles 表。
PERSON_FIELDS = {"name"}
ALL_PROFILE_FIELDS = [f[0] for g in PROFILE_GROUPS for f in g[2]]

RECORD_CATEGORIES = {"diet": "饮食", "exercise": "运动", "sleep": "睡眠",
                     "symptom": "症状", "medication": "用药", "mood": "心情", "other": "其他"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS persons (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  role        TEXT NOT NULL DEFAULT 'family',
  created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profiles (
  person_id      INTEGER PRIMARY KEY,
  sex            TEXT, birth_date TEXT, height_cm REAL, weight_kg REAL,
  blood_type     TEXT, phone TEXT, id_card TEXT, occupation TEXT, city TEXT, alias TEXT,
  allergies      TEXT NOT NULL DEFAULT '[]',
  history        TEXT NOT NULL DEFAULT '[]',
  surgeries      TEXT NOT NULL DEFAULT '[]',
  medications    TEXT NOT NULL DEFAULT '[]',
  family_history TEXT NOT NULL DEFAULT '[]',
  vaccinations   TEXT NOT NULL DEFAULT '[]',
  smoking        TEXT, drinking TEXT, exercise TEXT, diet_habit TEXT,
  sleep_habit    TEXT, menstruation TEXT, emergency_contact TEXT, insurance TEXT,
  notes          TEXT NOT NULL DEFAULT '',
  updated_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
  id           INTEGER PRIMARY KEY,
  person_id    INTEGER NOT NULL,
  report_date  TEXT NOT NULL,
  institution  TEXT NOT NULL DEFAULT '',
  category     TEXT NOT NULL DEFAULT '体检',
  summary      TEXT NOT NULL DEFAULT '',
  conclusion   TEXT NOT NULL DEFAULT '',
  raw_json     TEXT NOT NULL DEFAULT '{}',
  created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
  id              INTEGER PRIMARY KEY,
  person_id       INTEGER NOT NULL,
  report_id       INTEGER,
  orig_name       TEXT NOT NULL DEFAULT '',
  stored_name     TEXT NOT NULL DEFAULT '',
  mime            TEXT NOT NULL DEFAULT '',
  size_bytes      INTEGER NOT NULL DEFAULT 0,
  sha256          TEXT NOT NULL DEFAULT '',
  kind            TEXT NOT NULL DEFAULT 'report',
  page_count      INTEGER NOT NULL DEFAULT 0,
  created_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS indicators (
  id           INTEGER PRIMARY KEY,
  person_id    INTEGER NOT NULL,
  report_id    INTEGER,
  file_id      INTEGER,
  name         TEXT NOT NULL,
  value        TEXT,
  unit         TEXT,
  ref_text     TEXT,
  ref_min      TEXT,
  ref_max      TEXT,
  status       TEXT,
  measured_at  TEXT NOT NULL,
  created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS records (
  id          INTEGER PRIMARY KEY,
  person_id   INTEGER NOT NULL,
  record_date TEXT NOT NULL,
  category    TEXT NOT NULL,
  content     TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memories (
  id          INTEGER PRIMARY KEY,
  domain      TEXT NOT NULL DEFAULT 'health',
  person_id   INTEGER NOT NULL,
  kind        TEXT NOT NULL DEFAULT 'fact',
  topic       TEXT NOT NULL DEFAULT '',
  content     TEXT NOT NULL,
  importance  TEXT NOT NULL DEFAULT 'medium',
  source      TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  last_used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ind_person ON indicators(person_id, measured_at);
CREATE INDEX IF NOT EXISTS idx_rec_person ON records(person_id, record_date);
CREATE INDEX IF NOT EXISTS idx_file_person ON files(person_id, id);
CREATE INDEX IF NOT EXISTS idx_mem_person ON memories(person_id, importance, updated_at);

-- 对话上下文：会话 + 消息。聊天记录落库，页面刷新/换设备后能接着聊。
CREATE TABLE IF NOT EXISTS conversations (
  id          INTEGER PRIMARY KEY,
  person_id   INTEGER NOT NULL,
  title       TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
  id              INTEGER PRIMARY KEY,
  conversation_id INTEGER NOT NULL,
  role            TEXT NOT NULL,
  content         TEXT NOT NULL DEFAULT '',
  actions         TEXT NOT NULL DEFAULT '[]',
  created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_person ON conversations(person_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, id);

-- 饮食/运动计划：AI 规划后落库，跨会话可续、可跟踪进度。
CREATE TABLE IF NOT EXISTS plans (
  id          INTEGER PRIMARY KEY,
  person_id   INTEGER NOT NULL,
  kind        TEXT NOT NULL DEFAULT 'diet',
  title       TEXT NOT NULL DEFAULT '',
  period      TEXT NOT NULL DEFAULT '',
  content     TEXT NOT NULL DEFAULT '',
  status      TEXT NOT NULL DEFAULT 'active',
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plan_person ON plans(person_id, status, updated_at);
"""


# ── 基础设施 ────────────────────────────────────────────────────────
def connect():
    HEALTH_DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn):
    conn.executescript(SCHEMA)
    _ensure_columns(conn)
    conn.commit()


# persons 表只保留这三列。v1 老库里 persons 还带着 sex/birth_date，
# 那是历史遗留：v2 起这两个字段归 profiles 管，留着会让"改性别"这类操作
# 悄悄写错表（persons 成功、profiles 不动），所以显式清理掉。
PERSONS_KEEP = {"id", "name", "role", "created_at"}


def _ensure_columns(conn):
    """轻量迁移：老库缺列就补、多余的列就丢，避免升级时丢数据或写错表。"""
    want = {
        "persons": ["created_at"],
        "profiles": ["sex", "birth_date", "height_cm", "weight_kg", "blood_type", "phone",
                     "id_card", "occupation", "city", "alias", "allergies", "history",
                     "surgeries", "medications", "family_history", "vaccinations",
                     "smoking", "drinking", "exercise", "diet_habit", "sleep_habit",
                     "menstruation", "emergency_contact", "insurance", "notes", "updated_at"],
        "reports": ["report_date", "institution", "category", "summary", "conclusion",
                    "raw_json", "created_at"],
        "indicators": ["report_id", "file_id", "ref_text"],
    }
    for table, cols in want.items():
        have = {r["name"] for r in conn.execute("PRAGMA table_info({})".format(table))}
        if not have:
            continue
        for c in cols:
            if c not in have:
                conn.execute("ALTER TABLE {} ADD COLUMN {}".format(table, c))
        # persons：把 v1 遗留的 sex/birth_date 迁进 profiles 后删列
        if table == "persons":
            _migrate_person_extras(conn, have)
    conn.commit()


def _migrate_person_extras(conn, have):
    """v1 的 persons.sex / persons.birth_date → profiles（仅在 profiles 还是空的时候搬）。"""
    extra = [c for c in ("sex", "birth_date") if c in have and c not in PERSONS_KEEP]
    if not extra:
        return
    for pid, sex, bd in conn.execute(
            "SELECT id, {}, {} FROM persons".format(*extra)).fetchall():
        row = conn.execute("SELECT person_id FROM profiles WHERE person_id=?", (pid,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO profiles (person_id, sex, birth_date, updated_at)"
                         " VALUES (?,?,?,?)", (pid, sex or "", bd or "", now_iso()))
        else:
            sets, params = [], []
            if sex:
                cur = conn.execute("SELECT sex FROM profiles WHERE person_id=?",
                                   (pid,)).fetchone()
                if not (cur and cur["sex"]):
                    sets.append("sex=?"); params.append(sex)
            if bd:
                cur = conn.execute("SELECT birth_date FROM profiles WHERE person_id=?",
                                   (pid,)).fetchone()
                if not (cur and cur["birth_date"]):
                    sets.append("birth_date=?"); params.append(bd)
            if sets:
                params.append(pid)
                conn.execute("UPDATE profiles SET {} WHERE person_id=?".format(", ".join(sets)),
                             params)
    # 删列：SQLite < 3.35 不支持 ALTER TABLE DROP COLUMN，用「重建表」的方式。
    # 这是必要的（不是洁癖）：persons 里留着的 sex/birth_date 会让
    # "保存资料" 这类操作悄悄写错表，而 profiles 那份永远不更新。
    try:
        conn.execute("ALTER TABLE persons DROP COLUMN " + extra[0])
    except sqlite3.Error:
        _rebuild_persons(conn)


def _rebuild_persons(conn):
    """重建 persons 表，只保留 PERSONS_KEEP 里的列。"""
    keep = [r["name"] for r in conn.execute("PRAGMA table_info(persons)")
            if r["name"] in PERSONS_KEEP]
    conn.execute("PRAGMA foreign_keys=off")
    conn.execute("DROP TABLE IF EXISTS persons_new")
    conn.execute("CREATE TABLE persons_new ("
                 "  id INTEGER PRIMARY KEY,"
                 "  name TEXT NOT NULL UNIQUE,"
                 "  role TEXT NOT NULL DEFAULT 'family',"
                 "  created_at TEXT NOT NULL)")
    conn.execute("INSERT INTO persons_new ({}) SELECT {} FROM persons".format(
        ", ".join(keep), ", ".join(keep)))
    conn.execute("DROP TABLE persons")
    conn.execute("ALTER TABLE persons_new RENAME TO persons")
    conn.commit()


def now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def today():
    return datetime.date.today().isoformat()


def _json_default(o):
    if isinstance(o, (datetime.date, datetime.datetime)):
        return o.isoformat()
    return str(o)


def _jload(s, fallback=None):
    try:
        v = json.loads(s or "[]")
        return v if v is not None else (fallback if fallback is not None else [])
    except Exception:
        return fallback if fallback is not None else []


def _s(v):
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def _to_float(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _split_list(v):
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if not v:
        return []
    return [p.strip() for p in re.split(r"[,，、;\n]+", str(v)) if p.strip()]


# ── 人 / 档案 ───────────────────────────────────────────────────────
def ensure_person(conn, name, role="family"):
    name = (name or "").strip()
    if not name:
        raise ValueError("成员名不能为空")
    row = conn.execute("SELECT id FROM persons WHERE name=?", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO persons (name, role, created_at) VALUES (?,?,?)",
                       (name, role, now_iso()))
    conn.commit()
    return cur.lastrowid


def seed(conn):
    init_db(conn)
    for name, role, sex in SEED_PERSONS:
        pid = ensure_person(conn, name, role=role)
        row = conn.execute("SELECT person_id FROM profiles WHERE person_id=?", (pid,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO profiles (person_id, sex, updated_at) VALUES (?,?,?)",
                         (pid, sex, now_iso()))
    conn.commit()


def person_by_name(conn, name):
    row = conn.execute("SELECT * FROM persons WHERE name=?", ((name or "").strip(),)).fetchone()
    return dict(row) if row else None


def person_by_id(conn, pid):
    row = conn.execute("SELECT * FROM persons WHERE id=?", (pid,)).fetchone()
    return dict(row) if row else None


def list_persons(conn):
    out = []
    for r in conn.execute("SELECT * FROM persons ORDER BY CASE role WHEN 'self' THEN 0 ELSE 1 END, id"):
        d = dict(r)
        d["indicator_count"] = conn.execute(
            "SELECT COUNT(*) c FROM indicators WHERE person_id=?", (d["id"],)).fetchone()["c"]
        d["file_count"] = conn.execute(
            "SELECT COUNT(*) c FROM files WHERE person_id=?", (d["id"],)).fetchone()["c"]
        d["record_count"] = conn.execute(
            "SELECT COUNT(*) c FROM records WHERE person_id=?", (d["id"],)).fetchone()["c"]
        out.append(d)
    return out


def get_profile(conn, person_id):
    p = person_by_id(conn, person_id)
    if p is None:
        return None
    row = conn.execute("SELECT * FROM profiles WHERE person_id=?", (person_id,)).fetchone()
    prof = dict(row) if row else {}
    for k in LIST_FIELDS:
        prof[k] = _jload(prof.get(k))
    prof["name"] = p["name"]
    prof["role"] = p["role"]
    return prof


def save_profile(conn, person_id, fields):
    """白名单落库：只接受 ALL_PROFILE_FIELDS 里的键，其余忽略。

    只有「姓名」必填，其余字段留空一律存空值（NULL/空串），不报错——
    用户本来就是想起来才补一格，逼着填完整只会让人不想用。
    """
    if person_by_id(conn, person_id) is None:
        raise ValueError("成员不存在")
    if "name" in fields:
        nm = (fields.get("name") or "").strip()
        if not nm:
            raise ValueError("姓名不能为空")
        if conn.execute("SELECT id FROM persons WHERE name=? AND id<>?",
                        (nm, person_id)).fetchone():
            raise ValueError("已经有叫「{}」的人了".format(nm))
    now = now_iso()
    row = conn.execute("SELECT person_id FROM profiles WHERE person_id=?", (person_id,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO profiles (person_id, updated_at) VALUES (?,?)", (person_id, now))
        conn.commit()
    person_sets, person_params = [], []
    sets, params = [], []
    for f in ALL_PROFILE_FIELDS:
        if f not in fields:
            continue
        v = fields[f]
        if f in LIST_FIELDS:
            v = json.dumps(_split_list(v), ensure_ascii=False)
        elif f in NUM_FIELDS:
            v = _to_float(v)
        else:
            v = str(v if v is not None else "").strip()
        if f in PERSON_FIELDS:
            person_sets.append(f + "=?"); person_params.append(v)
        else:
            sets.append(f + "=?"); params.append(v)
    if person_sets:
        conn.execute("UPDATE persons SET {} WHERE id=?".format(", ".join(person_sets)),
                     person_params + [person_id])
    if sets:
        sets.append("updated_at=?"); params.append(now)
        params.append(person_id)
        conn.execute("UPDATE profiles SET {} WHERE person_id=?".format(", ".join(sets)), params)
    conn.commit()
    return get_profile(conn, person_id)


def create_person(conn, name, role="family", sex=""):
    name = (name or "").strip()
    if not name:
        raise ValueError("姓名不能为空")
    if conn.execute("SELECT id FROM persons WHERE name=?", (name,)).fetchone():
        raise ValueError("已经有叫「{}」的人了".format(name))
    pid = ensure_person(conn, name, role=role or "family")
    conn.execute("INSERT INTO profiles (person_id, sex, updated_at) VALUES (?,?,?)",
                 (pid, sex or "", now_iso()))
    conn.commit()
    return pid


def delete_person(conn, pid):
    p = person_by_id(conn, pid)
    if p is None:
        raise ValueError("成员不存在")
    if p["role"] == "self":
        raise ValueError("不能删除本人档案")
    if conn.execute("SELECT COUNT(*) c FROM persons").fetchone()["c"] <= 1:
        raise ValueError("至少保留一位成员")
    counts = {}
    for tb in ("indicators", "reports", "records", "memories", "files", "plans"):
        counts[tb] = conn.execute(
            "SELECT COUNT(*) c FROM {} WHERE person_id=?".format(tb), (pid,)).fetchone()["c"]
    counts["conversations"] = conn.execute(
        "SELECT COUNT(*) c FROM conversations WHERE person_id=?", (pid,)).fetchone()["c"]
    counts["messages"] = conn.execute(
        "SELECT COUNT(*) c FROM messages WHERE conversation_id IN"
        " (SELECT id FROM conversations WHERE person_id=?)", (pid,)).fetchone()["c"]
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    for r in conn.execute("SELECT stored_name FROM files WHERE person_id=?", (pid,)).fetchall():
        src = FILES_DIR / r["stored_name"]
        try:
            if src.exists():
                shutil.move(str(src), str(TRASH_DIR / (stamp + "-" + r["stored_name"])))
        except OSError:
            pass
    conn.execute("DELETE FROM messages WHERE conversation_id IN"
                 " (SELECT id FROM conversations WHERE person_id=?)", (pid,))
    conn.execute("DELETE FROM conversations WHERE person_id=?", (pid,))
    for tb in ("indicators", "reports", "records", "memories", "files", "plans"):
        conn.execute("DELETE FROM {} WHERE person_id=?".format(tb), (pid,))
    conn.execute("DELETE FROM profiles WHERE person_id=?", (pid,))
    conn.execute("DELETE FROM persons WHERE id=?", (pid,))
    conn.commit()
    return counts


# ── 记忆（长时记忆：事实/偏好/摘要，模型自行判断记什么） ──────────────
def add_memory(conn, person_id, kind="fact", topic="", content="", importance="medium", source=""):
    if not content or not content.strip():
        raise ValueError("memory content 不能为空")
    kind = (kind or "fact").strip().lower()
    if kind not in ("fact", "preference", "digest"):
        kind = "fact"
    imp = (importance or "medium").strip().lower()
    if imp not in ("high", "medium", "low"):
        imp = "medium"
    now = now_iso()
    cur = conn.execute(
        "INSERT INTO memories (domain, person_id, kind, topic, content, importance, source,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("health", person_id, kind, (topic or "").strip(), content.strip(), imp, source, now, now))
    conn.commit()
    return cur.lastrowid


def list_memories(conn, person_id, limit=50, topic=None):
    where = ["person_id=?"]
    params = [person_id]
    if topic:
        where.append("topic LIKE ?")
        params.append("%" + topic + "%")
    sql = ("SELECT * FROM memories WHERE " + " AND ".join(where)
           + " ORDER BY CASE importance WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,"
             " updated_at DESC LIMIT ?")
    params.append(limit)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def update_memory(conn, mid, fields):
    if conn.execute("SELECT id FROM memories WHERE id=?", (mid,)).fetchone() is None:
        raise ValueError("memory 不存在")
    sets, params = [], []
    for f in ("content", "topic", "source"):
        if f in fields:
            sets.append(f + "=?"); params.append(str(fields[f] or "").strip())
    for f, allowed, dflt in (("kind", ("fact", "preference", "digest"), "fact"),
                             ("importance", ("high", "medium", "low"), "medium")):
        if f in fields:
            v = str(fields[f] or dflt).strip().lower()
            sets.append(f + "=?"); params.append(v if v in allowed else dflt)
    if not sets:
        raise ValueError("没有可更新的字段")
    sets.append("updated_at=?"); params.append(now_iso())
    params.append(mid)
    conn.execute("UPDATE memories SET {} WHERE id=?".format(", ".join(sets)), params)
    conn.commit()
    return mid


def delete_memory(conn, mid):
    conn.execute("DELETE FROM memories WHERE id=?", (mid,))
    conn.commit()


# ── 对话上下文（会话 / 消息） ────────────────────────────────────────
def create_conversation(conn, person_id, title=""):
    now = now_iso()
    cur = conn.execute(
        "INSERT INTO conversations (person_id, title, created_at, updated_at) VALUES (?,?,?,?)",
        (person_id, (title or "").strip()[:120], now, now))
    conn.commit()
    return cur.lastrowid


def get_conversation(conn, conv_id):
    row = conn.execute("SELECT * FROM conversations WHERE id=?", (conv_id,)).fetchone()
    return dict(row) if row else None


def latest_conversation(conn, person_id):
    row = conn.execute(
        "SELECT * FROM conversations WHERE person_id=? ORDER BY updated_at DESC, id DESC LIMIT 1",
        (person_id,)).fetchone()
    return dict(row) if row else None


def list_conversations(conn, person_id, limit=50):
    rows = conn.execute(
        "SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id) AS msg_count"
        " FROM conversations c WHERE c.person_id=? ORDER BY c.updated_at DESC, c.id DESC LIMIT ?",
        (person_id, limit)).fetchall()
    return [dict(r) for r in rows]


def touch_conversation(conn, conv_id, title=None):
    sets, params = ["updated_at=?"], [now_iso()]
    if title:
        sets.append("title=?")
        params.append(title.strip()[:120])
    params.append(conv_id)
    conn.execute("UPDATE conversations SET {} WHERE id=?".format(", ".join(sets)), params)
    conn.commit()


def add_message(conn, conv_id, role, content, actions=None):
    cur = conn.execute(
        "INSERT INTO messages (conversation_id, role, content, actions, created_at)"
        " VALUES (?,?,?,?,?)",
        (conv_id, role, content or "", json.dumps(actions or [], ensure_ascii=False), now_iso()))
    conn.commit()
    return cur.lastrowid


def list_messages(conn, conv_id, limit=200, tail=True):
    """tail=True 取最近 limit 条（仍按时间正序返回）；False 取最早 limit 条。"""
    if tail:
        rows = conn.execute(
            "SELECT * FROM (SELECT * FROM messages WHERE conversation_id=?"
            " ORDER BY id DESC LIMIT ?) ORDER BY id ASC", (conv_id, limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY id ASC LIMIT ?",
            (conv_id, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["actions"] = json.loads(d.get("actions") or "[]")
        except (ValueError, TypeError):
            d["actions"] = []
        out.append(d)
    return out


def delete_conversation(conn, conv_id, person_id=None):
    if person_id is not None:
        row = conn.execute("SELECT person_id FROM conversations WHERE id=?", (conv_id,)).fetchone()
        if row is None or row["person_id"] != person_id:
            raise ValueError("会话不存在")
    n = conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=?",
                     (conv_id,)).fetchone()[0]
    conn.execute("DELETE FROM messages WHERE conversation_id=?", (conv_id,))
    conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))
    conn.commit()
    return {"conversation": 1, "messages": n}


def conversation_history(conn, conv_id, limit=10):
    """给模型的历史上下文：只取 user/assistant 的正文，最近 limit 条。"""
    msgs = list_messages(conn, conv_id, limit=limit)
    return [{"role": m["role"], "content": m["content"]}
            for m in msgs if m["role"] in ("user", "assistant") and m["content"]]


def save_turn(conn, conv_id, user_text, reply, actions=None, attachments=None):
    """把一轮问答落库，并维护会话标题与更新时间。

    这样页面刷新、换设备、隔几天再来，上下文都还在——不再"一次对话就结束"。
    """
    label = (user_text or "").strip()
    if not label:
        label = "（上传了体检报告）" if attachments else "（空消息）"
    add_message(conn, conv_id, "user", label)
    add_message(conn, conv_id, "assistant", reply or "", actions)
    conv = get_conversation(conn, conv_id) or {}
    if not (conv.get("title") or "").strip():
        # 标题取会话里第一条用户消息（而不是当前这轮），这样从旧前端播种过来的
        # 历史也能得到正确的标题。
        first = conn.execute(
            "SELECT content FROM messages WHERE conversation_id=? AND role='user'"
            " ORDER BY id ASC LIMIT 1", (conv_id,)).fetchone()
        title = (first["content"] if first else label) or "新对话"
        touch_conversation(conn, conv_id, title=title[:40])
    else:
        touch_conversation(conn, conv_id)


# ── 饮食 / 运动计划 ────────────────────────────────────────────────
PLAN_KINDS = ("diet", "exercise", "mixed", "other")
PLAN_STATUS = ("active", "paused", "done", "archived")


def _norm_plan_kind(k):
    k = (k or "diet").strip().lower()
    return k if k in PLAN_KINDS else "other"


def _norm_plan_status(s):
    s = (s or "active").strip().lower()
    return s if s in PLAN_STATUS else "active"


def add_plan(conn, person_id, kind="diet", title="", period="", content="", status="active"):
    if not (content or "").strip():
        raise ValueError("计划内容不能为空")
    now = now_iso()
    cur = conn.execute(
        "INSERT INTO plans (person_id, kind, title, period, content, status, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (person_id, _norm_plan_kind(kind), (title or "").strip()[:120],
         (period or "").strip()[:40], content.strip(), _norm_plan_status(status), now, now))
    conn.commit()
    return cur.lastrowid


def list_plans(conn, person_id, status=None, limit=50):
    where, params = ["person_id=?"], [person_id]
    if status:
        where.append("status=?")
        params.append(_norm_plan_status(status))
    params.append(limit)
    rows = conn.execute(
        "SELECT * FROM plans WHERE " + " AND ".join(where)
        + " ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 ELSE 2 END,"
          " updated_at DESC LIMIT ?", params).fetchall()
    return [dict(r) for r in rows]


def update_plan(conn, plan_id, fields):
    if conn.execute("SELECT id FROM plans WHERE id=?", (plan_id,)).fetchone() is None:
        raise ValueError("计划不存在")
    sets, params = [], []
    for f in ("title", "period", "content"):
        if f in fields:
            sets.append(f + "=?"); params.append(str(fields[f] or "").strip())
    if "kind" in fields:
        sets.append("kind=?"); params.append(_norm_plan_kind(fields["kind"]))
    if "status" in fields:
        sets.append("status=?"); params.append(_norm_plan_status(fields["status"]))
    if not sets:
        raise ValueError("没有可更新的字段")
    sets.append("updated_at=?"); params.append(now_iso())
    params.append(plan_id)
    conn.execute("UPDATE plans SET {} WHERE id=?".format(", ".join(sets)), params)
    conn.commit()
    return plan_id


def delete_plan(conn, plan_id, person_id=None):
    if person_id is not None:
        row = conn.execute("SELECT person_id FROM plans WHERE id=?", (plan_id,)).fetchone()
        if row is None or row["person_id"] != person_id:
            raise ValueError("计划不存在")
    conn.execute("DELETE FROM plans WHERE id=?", (plan_id,))
    conn.commit()


# ── 报告 / 指标 / 文件 ──────────────────────────────────────────────
def _infer_status(value, ref_min, ref_max):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    try:
        if ref_min not in (None, "") and v < float(ref_min):
            return "low"
        if ref_max not in (None, "") and v > float(ref_max):
            return "high"
        return "normal"
    except (TypeError, ValueError):
        return None


def _split_ref(text):
    """'3.9-6.1' / '<5.2' / '>1.0' / '0.4~1.7' → (min, max)"""
    if text is None:
        return None, None
    t = str(text).strip().replace("～", "~").replace("—", "-").replace("–", "-")
    if not t:
        return None, None
    m = re.match(r"^[<≤]\s*(-?[\d.]+)$", t)
    if m:
        return None, m.group(1)
    m = re.match(r"^[>≥]\s*(-?[\d.]+)$", t)
    if m:
        return m.group(1), None
    m = re.match(r"^(-?[\d.]+)\s*[-~至]\s*(-?[\d.]+)$", t)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r"^(-?[\d.]+)$", t)
    if m:
        return None, m.group(1)
    return None, None


ALLOWED_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff",
               ".txt", ".csv", ".json", ".md", ".html", ".htm"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
TEXT_EXT = {".txt", ".csv", ".json", ".md", ".html", ".htm"}


def save_upload(orig_name, data):
    """把上传的原始文件落盘（sha256 前缀命名，内容相同天然可辨）。"""
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()
    ext = Path(orig_name or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        ext = ".bin"
    stored = "{}-{}{}".format(digest[:16], datetime.datetime.now().strftime("%H%M%S"), ext)
    (FILES_DIR / stored).write_bytes(data)
    return stored, digest


def add_file(conn, person_id, orig_name, data, mime="", kind="report", report_id=None,
             page_count=0):
    stored, digest = save_upload(orig_name, data)
    cur = conn.execute(
        "INSERT INTO files (person_id, report_id, orig_name, stored_name, mime, size_bytes,"
        " sha256, kind, page_count, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (person_id, report_id, orig_name or "", stored, mime or "", len(data), digest, kind,
         page_count, now_iso()))
    conn.commit()
    return cur.lastrowid


def list_files(conn, person_id=None, limit=200):
    if person_id:
        rows = conn.execute("SELECT * FROM files WHERE person_id=? ORDER BY id DESC LIMIT ?",
                            (person_id, limit)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM files ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def _indicator_row(ind):
    """把模型给的指标 dict 归一成入库字段（拆参考区间 + 判高低）。名字为空返回 None。"""
    name = (ind.get("name") or "").strip()
    if not name:
        return None
    rmin = _s(ind.get("ref_min"))
    rmax = _s(ind.get("ref_max"))
    ref_text = _s(ind.get("ref_text")) or _s(ind.get("reference")) or _s(ind.get("ref"))
    if (rmin is None or rmax is None) and ref_text:
        a, b = _split_ref(ref_text)
        rmin = rmin or a
        rmax = rmax or b
    if ref_text is None and (rmin or rmax):
        ref_text = "{}-{}".format(rmin or "", rmax or "")
    value = _s(ind.get("value"))
    status = _s(ind.get("status")) or _infer_status(value, rmin, rmax)
    return {"name": name, "value": value, "unit": _s(ind.get("unit")), "ref_text": ref_text,
            "ref_min": rmin, "ref_max": rmax, "status": status}


def add_indicators(conn, person_id, indicators, measured_at=None, report_id=None, file_id=None):
    """只写指标，**不建「报告」**。

    口头报的数值（"我血压 145"）走这里：一次读数不是一份体检报告，
    否则每天报一次血压，档案页的「体检报告」就会堆出一长串「自测」条目，
    概览的"报告 N 份"也会失真。
    """
    measured_at = (measured_at or today()).strip() or today()
    n = 0
    for ind in (indicators or []):
        row = _indicator_row(ind)
        if row is None:
            continue
        conn.execute(
            "INSERT INTO indicators (person_id, report_id, file_id, name, value, unit, ref_text,"
            " ref_min, ref_max, status, measured_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (person_id, report_id, file_id, row["name"], row["value"], row["unit"],
             row["ref_text"], row["ref_min"], row["ref_max"], row["status"],
             measured_at, now_iso()))
        n += 1
    conn.commit()
    return n


def add_report(conn, person_id, report_date=None, institution="", category="体检", summary="",
               conclusion="", indicators=None, file_id=None, raw=None):
    report_date = (report_date or today()).strip() or today()
    cur = conn.execute(
        "INSERT INTO reports (person_id, report_date, institution, category, summary, conclusion,"
        " raw_json, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (person_id, report_date, institution or "", category or "体检", summary or "",
         conclusion or "", json.dumps(raw or {}, ensure_ascii=False), now_iso()))
    rid = cur.lastrowid
    conn.commit()
    n = add_indicators(conn, person_id, indicators, measured_at=report_date,
                       report_id=rid, file_id=file_id)
    if file_id:
        conn.execute("UPDATE files SET report_id=? WHERE id=?", (rid, file_id))
        conn.commit()
    return rid, n


def list_reports(conn, person_id, limit=100):
    rows = conn.execute("SELECT * FROM reports WHERE person_id=? ORDER BY report_date DESC, id DESC"
                        " LIMIT ?", (person_id, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["indicator_count"] = conn.execute(
            "SELECT COUNT(*) c FROM indicators WHERE report_id=?", (d["id"],)).fetchone()["c"]
        d["abnormal_count"] = conn.execute(
            "SELECT COUNT(*) c FROM indicators WHERE report_id=? AND status IN ('high','low')",
            (d["id"],)).fetchone()["c"]
        d["files"] = [dict(x) for x in conn.execute(
            "SELECT id, orig_name, stored_name, mime, size_bytes FROM files WHERE report_id=?",
            (d["id"],)).fetchall()]
        # 把姓名核对结果提到顶层，档案页不用自己去解 raw_json
        raw = _jload(d.get("raw_json"), {}) or {}
        d["person_name_on_report"] = raw.get("person_name_on_report")
        d["name_match"] = raw.get("name_match")
        out.append(d)
    return out


def list_indicators(conn, person_id, limit=300, report_id=None, standalone=False):
    """standalone=True 只取**不属于任何报告**的指标（口头/自测报的数值）。"""
    if report_id:
        rows = conn.execute("SELECT * FROM indicators WHERE person_id=? AND report_id=?"
                            " ORDER BY id LIMIT ?", (person_id, report_id, limit)).fetchall()
    elif standalone:
        rows = conn.execute("SELECT * FROM indicators WHERE person_id=? AND report_id IS NULL"
                            " ORDER BY measured_at DESC, id DESC LIMIT ?",
                            (person_id, limit)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM indicators WHERE person_id=?"
                            " ORDER BY measured_at DESC, id DESC LIMIT ?",
                            (person_id, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("status") is None:
            d["status"] = _infer_status(d.get("value"), d.get("ref_min"), d.get("ref_max"))
        out.append(d)
    return out


def delete_report(conn, report_id, person_id):
    conn.execute("DELETE FROM indicators WHERE report_id=?", (report_id,))
    conn.execute("UPDATE files SET report_id=NULL WHERE report_id=?", (report_id,))
    conn.execute("DELETE FROM reports WHERE id=? AND person_id=?", (report_id, person_id))
    conn.commit()


# ── 日常记录 ────────────────────────────────────────────────────────
def _normalize_category(c):
    c = (c or "other").strip().lower()
    if c in RECORD_CATEGORIES:
        return c
    for k, v in RECORD_CATEGORIES.items():
        if c == v or c == v.lower():
            return k
    return "other"


def add_record(conn, person_id, record_date, category, content):
    if not (content or "").strip():
        raise ValueError("内容不能为空")
    cur = conn.execute(
        "INSERT INTO records (person_id, record_date, category, content, created_at)"
        " VALUES (?,?,?,?,?)",
        (person_id, (record_date or today()).strip() or today(),
         _normalize_category(category), content.strip(), now_iso()))
    conn.commit()
    return cur.lastrowid


def list_records(conn, person_id, limit=60):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM records WHERE person_id=? ORDER BY record_date DESC, id DESC LIMIT ?",
        (person_id, limit)).fetchall()]


def delete_record(conn, rid, person_id):
    conn.execute("DELETE FROM records WHERE id=? AND person_id=?", (rid, person_id))
    conn.commit()


# ── 供 AI 读的全档 ──────────────────────────────────────────────────
def digest(conn, person_id, records_limit=20, indicators_limit=80):
    p = person_by_id(conn, person_id)
    if p is None:
        return None
    reports = list_reports(conn, person_id, limit=10)
    inds = list_indicators(conn, person_id, limit=indicators_limit)
    abnormal = [i for i in inds if i.get("status") in ("high", "low")]
    plans = list_plans(conn, person_id, limit=20)
    active_plans = [x for x in plans if x.get("status") == "active"]
    return {
        "person": p,
        "profile": get_profile(conn, person_id),
        "reports": reports,
        "abnormal_indicators": abnormal[:40],
        "recent_indicators": inds,
        "records": list_records(conn, person_id, limit=records_limit),
        "memories": list_memories(conn, person_id, limit=30),
        "plans": plans,
        "active_plans": active_plans,
        "stats": {"report_count": len(reports), "indicator_count": len(inds),
                  "abnormal_count": len(abnormal),
                  "plan_count": len(plans), "active_plan_count": len(active_plans)},
    }


# ══════════════════════════════════════════════════════════════════════
#  LLM：与 DSH 用同一个模型（OpenCode Go 网关）
# ══════════════════════════════════════════════════════════════════════
def _read_key_file(path):
    """从 credentials.yaml 里读 OPENCODE_GO_API_KEY（简单 key: value 格式）。"""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("OPENCODE_GO_API_KEY:"):
                    return line.split(":", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def api_key():
    """凭据解析顺序（前者优先）：

    1. 环境变量 `OPENCODE_GO_API_KEY` —— systemd 注入，最推荐（不依赖任何文件布局）
    2. wuage 自己的 `$WUAGE_HOME/.credentials.yaml` —— 让本模块**不依赖 DSH 存活**
    3. DSH 的 `$DSH_HOME/.credentials.yaml` —— 历史来源，保留兼容

    这样即使 DSH 被升级/移除/换路径，健康助手照常工作。
    """
    k = os.environ.get("OPENCODE_GO_API_KEY")
    if k and k.strip():
        return k.strip()
    candidates = [WUAGE_DATA.parent / ".credentials.yaml",
                  Path(os.environ.get("DSH_HOME") or os.path.expanduser("~/.dsh"))
                  / ".credentials.yaml"]
    for p in candidates:
        v = _read_key_file(p)
        if v:
            return v
    return None


def _llm(messages, model, max_tokens=4096, timeout=300, json_mode=False):
    """调一次 chat/completions，返回 assistant 文本内容。"""
    key = api_key()
    if not key:
        raise RuntimeError("OPENCODE_GO_API_KEY 未配置（环境变量 / $WUAGE_HOME/.credentials.yaml / "
                           "$DSH_HOME/.credentials.yaml 三处都没有）")
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        API_URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key,
                 "User-Agent": USER_AGENT, "x-opencode-session": "wuage-health"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8")[:300]
        except Exception:
            pass
        raise RuntimeError("模型服务 {}：{}".format(e.code, detail))
    msg = (data.get("choices") or [{}])[0].get("message") or {}
    content = msg.get("content") or ""
    if not content.strip() and msg.get("reasoning_content"):
        content = _recover_content(msg["reasoning_content"])
    return content


def _recover_content(reasoning):
    """模型把答案写进了 reasoning_content 时兜底捞一次（视觉模型偶发）。"""
    m = re.search(r"\{[\s\S]*\}", reasoning)
    return m.group(0) if m else ""


def _extract_json(content):
    if not content or not isinstance(content, str):
        raise ValueError("模型未返回内容")
    s = content.strip()
    fence = chr(96) * 3
    if s.startswith(fence):
        s = s[3:]
        if s[:4].lower() == "json":
            s = s[4:]
        if s.rstrip().endswith(fence):
            s = s.rstrip()[:-3]
        s = s.strip()
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("模型输出里没有 JSON：" + content[:200])
    return json.loads(s[a:b + 1])


# ── 把上传文件变成"能喂给模型的东西" ────────────────────────────────
def _which(tool):
    return shutil.which(tool)


def pdf_to_pngs(path, max_pages=MAX_PDF_PAGES, dpi=150):
    """poppler 渲染 PDF → PNG 字节列表。缺 poppler 时返回空列表。"""
    exe = _which("pdftoppm")
    if not exe:
        return []
    tmpd = tempfile.mkdtemp(prefix="health-pdf-")
    out = []
    try:
        subprocess.run([exe, "-r", str(dpi), "-png", "-f", "1", "-l", str(max_pages),
                        str(path), os.path.join(tmpd, "p")],
                       check=True, capture_output=True, timeout=240)
        for p in sorted(Path(tmpd).glob("p*.png")):
            out.append(p.read_bytes())
    except (subprocess.SubprocessError, OSError):
        out = []
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)
    return out


def pdf_page_count(path):
    exe = _which("pdfinfo")
    if not exe:
        return 0
    try:
        r = subprocess.run([exe, str(path)], capture_output=True, text=True, timeout=30)
        for line in r.stdout.splitlines():
            if line.startswith("Pages:"):
                return int(line.split(":", 1)[1].strip())
    except (subprocess.SubprocessError, ValueError, OSError):
        pass
    return 0


def decode_qr(image_bytes):
    """从图片里解二维码/条码文本（体检报告常有查验二维码）。"""
    exe = _which("zbarimg")
    if not exe:
        return []
    fd, tmp = tempfile.mkstemp(suffix=".png")
    try:
        os.write(fd, image_bytes)
        os.close(fd)
        r = subprocess.run([exe, "-q", "--raw", tmp], capture_output=True, text=True, timeout=60)
        seen, out = set(), []
        for line in r.stdout.splitlines():
            line = line.strip()
            if line and line not in seen:
                seen.add(line)
                out.append(line)
        return out
    except (subprocess.SubprocessError, OSError):
        return []
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _b64(data):
    return base64.b64encode(data).decode("ascii")


REPORT_SCHEMA_PROMPT = """你是体检报告结构化助手。请仔细阅读用户提供的体检报告（图片或文本），
把里面的信息**原样、完整**抽出来，不要编造、不要省略任何一项指标。

输出一个 JSON 对象，字段如下：
{
  "person_name": "报告上的姓名（没有就 null）",
  "sex": "男/女（没有就 null）",
  "age": 年龄数字或 null,
  "report_date": "报告日期 YYYY-MM-DD（优先取体检日期，没有就 null）",
  "institution": "体检机构名称或 null",
  "category": "报告类型，如 体检/血常规/生化/影像/病理，默认 体检",
  "summary": "一句话概括这份报告（30 字内）",
  "conclusion": "报告的总检结论/医生建议原文（没有就空字符串）",
  "indicators": [
    {"name": "指标名", "value": "结果值", "unit": "单位或 null",
     "ref_text": "参考区间原文，如 3.9-6.1 或 <5.2 或 null",
     "ref_min": "下限或 null", "ref_max": "上限或 null",
     "status": "high/low/normal 之一（根据参考区间判断，拿不准就 null）"}
  ]
}

要求：
1. **先判断这是不是一份体检/化验/检查报告**。如果图/文件里是别的东西（购物小票、发票、说明书、
   聊天截图、风景照…），或者内容根本看不清：**indicators 必须返回空数组 []**，category 填「非报告」，
   summary 用一句话说明这看起来是什么。**绝对不要**把商品名、金额、电话、地址、订单号之类当成体检指标。
2. indicators 要把报告里**每一个**检查项目都列出来，包括身高体重血压这类一般检查，一项都不能漏。
3. value 原样保留报告上写的字符串（异常箭头去掉，数值照抄）。
4. 参考区间能拆成数值上下限就填 ref_min/ref_max，拆不了就只填 ref_text。
5. 只输出 JSON，不要任何解释、不要 Markdown 代码块。"""


def parse_report_text(text, person_name=None):
    """纯文本报告（导出的电子报告、txt/csv 等）→ 结构化。"""
    prompt = REPORT_SCHEMA_PROMPT
    if person_name:
        prompt += ("\n6. 重要：person_name 必须是**报告上实际印的姓名**，照抄原文，"
                   "不要替换成别的名字；报告上确实没有姓名才填 null。"
                   "（本次上传的档案是「{}」，只用来给你做上下文参考，**不要直接填它**。）"
                   .format(person_name))
    content = _llm([{"role": "user",
                     "content": prompt + "\n\n--- 报告内容 ---\n" + text[:TEXT_READ_LIMIT]}],
                   CHAT_MODEL, max_tokens=8000, json_mode=True)
    return _extract_json(content)


def parse_report_images(images, person_name=None, chunk=1):
    """图片报告（含 PDF 渲染出的页）→ 结构化。多页时逐页解析再合并，避免超上下文。"""
    if not images:
        raise ValueError("没有可解析的图片")
    results = []
    for i in range(0, len(images), chunk):
        batch = images[i:i + chunk]
        prompt = REPORT_SCHEMA_PROMPT
        extra = []
        if person_name:
            extra.append("重要：person_name 必须是**报告上实际印的姓名**，照抄原文，"
                         "不要替换成别的名字；报告上没有姓名才填 null。"
                         "（本次上传的档案是「{}」，只作上下文参考，**不要直接填它**。）"
                         .format(person_name))
        if len(images) > 1:
            extra.append("这是报告的其中一页（第 {} 页，共 {} 页），只抽这一页能看到的指标。"
                         .format(i + 1, len(images)))
        if extra:
            prompt += "\n" + "\n".join("{}. {}".format(j + 6, e) for j, e in enumerate(extra))
        parts = [{"type": "text", "text": prompt}]
        for img in batch:
            parts.append({"type": "image_url",
                          "image_url": {"url": "data:image/png;base64," + _b64(img)}})
        content = _llm([{"role": "user", "content": parts}], VISION_MODEL,
                       max_tokens=8000, timeout=420, json_mode=True)
        try:
            results.append(_extract_json(content))
        except (ValueError, json.JSONDecodeError):
            continue
    if not results:
        raise ValueError("模型没能从这份报告里解析出结构化内容")
    return _merge_report_json(results)


def _merge_report_json(parts):
    base = dict(parts[0])
    inds, seen = [], set()
    for p in parts:
        for k in ("person_name", "sex", "age", "report_date", "institution", "category"):
            if not base.get(k) and p.get(k):
                base[k] = p[k]
        for k in ("summary", "conclusion"):
            if p.get(k) and p[k] not in (base.get(k) or ""):
                base[k] = ((base.get(k) or "") + " " + p[k]).strip()
        for ind in (p.get("indicators") or []):
            key = (str(ind.get("name") or "").strip(), str(ind.get("value") or "").strip())
            if not key[0] or key in seen:
                continue
            seen.add(key)
            inds.append(ind)
    base["indicators"] = inds
    return base


# ── 对话：读全档 → 回答 → 自动落库 ──────────────────────────────────
CHAT_SYSTEM = """你是「吾家」家庭健康助手，服务对象是这家人。你的回答必须**基于下面这份真实档案**，
不要泛泛而谈，不要假装知道档案里没有的事。

【档案】
{digest}

【工作方式】
1. 回答前先在心里过一遍档案：这人的基础情况（年龄/身高体重/血型）、既往病史、过敏、长期用药、
   最近一次体检的异常指标、最近的饮食运动睡眠记录、以及已经沉淀下来的长期记忆。
2. 回答要**具体到这个人和这些数字**。比如不要说"注意血脂"，而要说"你 8 月甘油三酯 2.4，
   比参考上限 1.7 高了不少，结合你最近运动记录偏少，建议……"。
3. 信息不足时明确说缺什么，并提示"建议就医/补一次检查"，**不要编造数值、不要做医疗诊断**。
4. 涉及用药调整、异常指标解读、急症，明确提示"请咨询医生/及时就医"。
5. 用中文、口语化、简洁。可以适当用短列表，但别写成说明书。
6. 如果用户只是随口聊健康/饮食/运动/心情，也正常回应，不用每次都搬出指标。
7. **规划必须落到计划里**。当用户要你安排饮食/运动（"帮我排一下这周吃什么"、"给我定个运动计划"），
   基于档案里的真实情况（体重、异常指标、病史、过敏、作息、戒酒等）出具体方案，
   然后**调用 plan_save 把方案存下来**，不要只在对话里说一遍就算完。
   档案里已有 active 计划时，接着用 plan_update 更新/微调，**不要每次重新造一份**；
   用户反馈"没按计划来""改成每周三次""这个计划先停掉"，也要落到计划上。
   计划要可执行：写清每天/每周做什么、量多少、忌什么，别写空话。
8. **用户问的问题一定要正面回答完**。写库是顺带的，回答才是主体——不要只回一句
   "已经记下了"就结束。先答问题，再落库；落库之后如果还有没说清的建议，补上。
9. **涉及吃药/用药时，先拿档案里的过敏史、长期用药、既往病史交叉核对**：
   例如档案写着"青霉素过敏"，用户问"能不能吃阿莫西林"（青霉素类），
   必须**第一时间明确提醒不能用**，再谈别的；不要等用户自己想起来。
   档案里没有相关记录时，也要说明"档案里没有你的过敏记录，建议先确认清楚"。

【档案更新】用户这轮如果说了值得长期记住的事（新症状、新用药、过敏、确诊、习惯变化、目标、
体检结论），在回答**之后**调用工具写库。用户没说的别硬造。工具用法：
- 记日常流水（吃了什么/运动/睡眠/不舒服）：record_add，category 取 diet/exercise/sleep/symptom/medication/mood/other
- 记长期事实/偏好/阶段摘要：memory_add，kind 取 fact/preference/digest，importance 取 high/medium/low
- 用户口头报的体检数值（不是上传报告，就是口头说"我血压 145"）：indicator_add
- 用户改了资料（"我搬去杭州了"、"我现在吃 XX 药"）：profile_update
- 新出一份饮食/运动计划：plan_save（kind 取 diet/exercise/mixed，content 写完整可执行方案）
- 调整已有计划（改内容/暂停/完成）：plan_update，带上 plan_id
**重要**：如果上传的报告上写的姓名不是「{person_name}」，那这是别人的报告，**绝对不要**把报告里的
身高体重年龄等写进 {person_name} 的档案；提醒用户这份报告可能传错了人或属于别的家人。
工具调用前后都不用跟用户解释"我正在写入数据库"，正常说完话就行。"""

CHAT_TOOLS = [
    {"type": "function", "function": {
        "name": "record_add",
        "description": "记录一条日常流水（饮食/运动/睡眠/症状/用药/心情）",
        "parameters": {"type": "object", "properties": {
            "category": {"type": "string",
                         "enum": ["diet", "exercise", "sleep", "symptom", "medication", "mood", "other"]},
            "content": {"type": "string", "description": "记录内容，用用户的原话"},
            "record_date": {"type": "string", "description": "YYYY-MM-DD，缺省今天"}},
            "required": ["category", "content"]}}},
    {"type": "function", "function": {
        "name": "memory_add",
        "description": "写入长期记忆（跨会话保留，下次回答前会被读到）",
        "parameters": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["fact", "preference", "digest"]},
            "topic": {"type": "string", "description": "主题词：过敏/慢病/用药/运动/饮食/睡眠/目标…"},
            "content": {"type": "string", "description": "记忆内容，一句话说清"},
            "importance": {"type": "string", "enum": ["high", "medium", "low"]}},
            "required": ["kind", "content"]}}},
    {"type": "function", "function": {
        "name": "indicator_add",
        "description": "记录用户口头报的一个体检/自测指标值",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}, "value": {"type": "string"},
            "unit": {"type": "string"}, "ref_min": {"type": "string"},
            "ref_max": {"type": "string"},
            "measured_at": {"type": "string", "description": "YYYY-MM-DD，缺省今天"}},
            "required": ["name", "value"]}}},
    {"type": "function", "function": {
        "name": "profile_update",
        "description": "更新这个人的档案字段（身高体重血型过敏病史用药生活习惯等）",
        "parameters": {"type": "object", "properties": {
            "fields": {"type": "object", "description":
                       "要更新的字段，键取 alias/sex/birth_date/height_cm/weight_kg/blood_type/"
                       "phone/occupation/city/allergies/history/surgeries/medications/"
                       "family_history/vaccinations/smoking/drinking/exercise/diet_habit/"
                       "sleep_habit/menstruation/emergency_contact/insurance/notes"}}}}},
    {"type": "function", "function": {
        "name": "plan_save",
        "description": "新建一份饮食/运动计划并落库（跨会话保留，之后可继续跟踪和调整）",
        "parameters": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["diet", "exercise", "mixed", "other"]},
            "title": {"type": "string", "description": "计划标题，如「减脂期一周饮食」"},
            "period": {"type": "string", "description": "周期，如 2026-W42 / 2026-10 / 长期"},
            "content": {"type": "string",
                        "description": "完整可执行方案正文：每天/每周做什么、量多少、忌什么"}},
            "required": ["kind", "content"]}}},
    {"type": "function", "function": {
        "name": "plan_update",
        "description": "调整已有计划：改内容、改周期、暂停、标记完成或归档",
        "parameters": {"type": "object", "properties": {
            "plan_id": {"type": "integer", "description": "档案 plans 列表里的 id"},
            "title": {"type": "string"}, "period": {"type": "string"},
            "content": {"type": "string"},
            "status": {"type": "string",
                       "enum": ["active", "paused", "done", "archived"]}},
            "required": ["plan_id"]}}}]



def _chat_llm(messages, model=None, max_tokens=2048, tools=None, on_delta=None):
    """带工具定义的一轮调用，返回 message dict。

    on_delta(text) 非空时走 SSE 流式：每收到一段正文就回调一次（用于页面逐字显示），
    同时把 tool_calls 的分片拼回完整调用列表。
    """
    key = api_key()
    if not key:
        raise RuntimeError("OPENCODE_GO_API_KEY 未配置")
    payload = {"model": model or CHAT_MODEL, "messages": messages, "max_tokens": max_tokens}
    tlist = CHAT_TOOLS if tools is None else tools
    if tlist:
        payload["tools"] = tlist
    if on_delta is None:
        payload["stream"] = False
        req = urllib.request.Request(
            API_URL, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + key,
                     "User-Agent": USER_AGENT, "x-opencode-session": "wuage-health"})
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError("模型服务 {}：{}".format(e.code, _err_detail(e)))
        return (data.get("choices") or [{}])[0].get("message") or {}

    # ── 流式 ────────────────────────────────────────────────────────
    payload["stream"] = True
    req = urllib.request.Request(
        API_URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key,
                 "User-Agent": USER_AGENT, "x-opencode-session": "wuage-health",
                 "Accept": "text/event-stream"})
    content, calls = [], {}
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    obj = json.loads(chunk)
                except ValueError:
                    continue
                delta = ((obj.get("choices") or [{}])[0].get("delta") or {})
                piece = delta.get("content")
                if piece:
                    content.append(piece)
                    try:
                        on_delta(piece)
                    except Exception:
                        pass
                for tc in (delta.get("tool_calls") or []):
                    idx = tc.get("index", 0)
                    slot = calls.setdefault(idx, {"id": "", "type": "function",
                                                  "function": {"name": "", "arguments": ""}})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["function"]["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["function"]["arguments"] += fn["arguments"]
    except urllib.error.HTTPError as e:
        raise RuntimeError("模型服务 {}：{}".format(e.code, _err_detail(e)))
    msg = {"role": "assistant", "content": "".join(content)}
    if calls:
        msg["tool_calls"] = [calls[k] for k in sorted(calls)]
    return msg


def _err_detail(e):
    try:
        return e.read().decode("utf-8")[:300]
    except Exception:
        return ""


def _apply_tool(conn, person_id, name, args):
    """执行模型请求的工具调用，返回一句话结果（回灌给模型）。"""
    try:
        if name == "record_add":
            rid = add_record(conn, person_id, args.get("record_date"),
                             args.get("category") or "other", args.get("content") or "")
            return "已记录日常流水 #{}".format(rid)
        if name == "memory_add":
            mid = add_memory(conn, person_id, kind=args.get("kind") or "fact",
                             topic=args.get("topic") or "", content=args.get("content") or "",
                             importance=args.get("importance") or "medium", source="chat")
            return "已写入长期记忆 #{}".format(mid)
        if name == "indicator_add":
            # 口头报的数值只写指标，不建「报告」——一次读数不是一份体检报告
            n = add_indicators(conn, person_id,
                               [{"name": args.get("name"), "value": args.get("value"),
                                 "unit": args.get("unit"), "ref_min": args.get("ref_min"),
                                 "ref_max": args.get("ref_max")}],
                               measured_at=args.get("measured_at"))
            return "已记录指标 {} 项".format(n)
        if name == "profile_update":
            fields = args.get("fields") or {}
            if not isinstance(fields, dict):
                return "profile_update 失败：fields 不是对象"
            save_profile(conn, person_id, fields)
            return "已更新档案字段：" + "、".join(list(fields.keys())[:8])
        if name == "plan_save":
            plan_id = add_plan(conn, person_id, kind=args.get("kind") or "diet",
                               title=args.get("title") or "", period=args.get("period") or "",
                               content=args.get("content") or "")
            return "已保存计划 #{}".format(plan_id)
        if name == "plan_update":
            if not args.get("plan_id"):
                return "plan_update 失败：缺 plan_id"
            _pid = int(args["plan_id"])
            update_plan(conn, _pid, {k: v for k, v in args.items() if k != "plan_id"})
            return "已更新计划 #{}".format(_pid)
        return "未知工具：" + str(name)
    except (ValueError, sqlite3.Error) as e:
        return "工具执行失败：{}".format(e)


def _slim_plans(plans, limit=8, content_chars=1500, keep_content=True):
    """计划正文可能很长，digest 超限时收窄。

    keep_content=False 时只留索引（id/kind/title/period/status），用于"全部计划"列表——
    正文由 active_plans 那份承载，避免同一个计划的内容在 digest 里出现两遍。
    """
    out = []
    for p in (plans or [])[:limit]:
        q = {k: v for k, v in p.items() if k != "content"}
        if keep_content:
            c = p.get("content") or ""
            q["content"] = c if len(c) <= content_chars else c[:content_chars] + "…（正文已截断）"
        out.append(q)
    return out


def chat(conn, person_id, message, history=None, max_rounds=3, on_event=None):
    """一轮对话：带全档 + 工具，返回 (回复文本, 工具执行记录)。

    on_event(kind, data) 用于向页面实时推送：
      ("delta", 文本片段)  正文逐字输出
      ("tool",  名称)      模型决定写库（此时还没有正文）
    """
    d = digest(conn, person_id)
    if d is None:
        raise ValueError("成员不存在")
    digest_text = json.dumps(d, ensure_ascii=False, default=_json_default)
    if len(digest_text) > 24000:
        d_light = dict(d)
        d_light["recent_indicators"] = d.get("recent_indicators", [])[:40]
        d_light["records"] = d.get("records", [])[:12]
        d_light["memories"] = d.get("memories", [])[:15]
        d_light["plans"] = _slim_plans(d.get("plans"), limit=12, keep_content=False)
        d_light["active_plans"] = _slim_plans(d.get("active_plans"), limit=6,
                                              content_chars=1200)
        digest_text = json.dumps(d_light, ensure_ascii=False, default=_json_default)
    messages = [{"role": "system", "content": CHAT_SYSTEM.format(
        digest=digest_text, person_name=d["person"]["name"])}]
    for h in (history or [])[-10:]:
        if not isinstance(h, dict):
            continue
        role = h.get("role")
        if role in ("user", "assistant") and h.get("content"):
            messages.append({"role": role, "content": str(h["content"])[:4000]})
    messages.append({"role": "user", "content": message})
    actions = []
    reply = ""
    for _ in range(max_rounds):
        streamed = []

        def _delta(piece, _s=streamed):
            _s.append(piece)
            if on_event:
                try:
                    on_event("delta", piece)
                except Exception:
                    pass

        msg = _chat_llm(messages, on_delta=_delta if on_event else None)
        calls = msg.get("tool_calls") or []
        piece = (msg.get("content") or "").strip()
        if piece:
            # 多轮时把每轮正文都留下来。模型经常第一轮就写完整答案（因为那时才看得到
            # 用户的问题），第二轮只是"记好了"的收尾——只保留最后一轮会把最有价值的
            # 内容丢掉：页面上流式看得到，刷新后就没了，落库的也只是那句收尾。
            reply = (reply + "\n\n" + piece) if reply else piece
        if not calls:
            break
        if on_event:
            for c in calls:
                try:
                    on_event("tool", (c.get("function") or {}).get("name") or "")
                except Exception:
                    pass
        messages.append({"role": "assistant", "content": msg.get("content") or "",
                         "tool_calls": calls})
        for c in calls:
            fn = (c.get("function") or {}).get("name") or ""
            raw = (c.get("function") or {}).get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except json.JSONDecodeError:
                args = {}
            result = _apply_tool(conn, person_id, fn, args)
            actions.append({"tool": fn, "args": args, "result": result})
            messages.append({"role": "tool", "tool_call_id": c.get("id") or fn,
                             "content": result})
    return reply, actions


# ── 上传并解析一份报告 ──────────────────────────────────────────────
_IMAGE_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a",
                b"BM", b"II*\x00", b"MM\x00*")


def _looks_like_image(data):
    """按文件头判断是不是真图片——扩展名不可信（改名的、截断的、空文件都会骗过它）。"""
    if not data or len(data) < 12:
        return False
    if data.startswith(_IMAGE_MAGIC):
        return True
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP"      # WEBP


def ingest_report(conn, person_id, filename, data, mime="", person_name=None):
    """上传文件 → 落盘 → 结构化 → 入库。返回结果字典。"""
    ext = Path(filename or "").suffix.lower()
    page_count = 0
    images = []
    text = ""
    qrs = []
    if not data:
        raise RuntimeError("这个文件是空的，重新传一份吧。")
    if ext == ".pdf" or (mime or "").endswith("pdf"):
        if not data.startswith(b"%PDF"):
            raise RuntimeError("这个文件不是有效的 PDF（可能已损坏或改了扩展名）。"
                               "重新导出一次，或者直接拍张清晰的照片发我。")
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
            tf.write(data)
            tmp_pdf = tf.name
        try:
            page_count = pdf_page_count(tmp_pdf)
            images = pdf_to_pngs(tmp_pdf)
        finally:
            try:
                os.unlink(tmp_pdf)
            except OSError:
                pass
        if not images:
            raise RuntimeError("这份 PDF 读不出内容：服务器缺少 poppler-utils（pdftoppm）。"
                               "请先安装，或把报告关键指标用文字发给我。")
    elif ext in IMAGE_EXT:
        if not _looks_like_image(data):
            raise RuntimeError("这个文件不是有效的图片（可能已损坏，或只是把扩展名改成了 {}）。"
                               "换一张清晰的照片、或重新导出 PDF 再试。".format(ext))
        images = [data]
        page_count = 1
    elif ext in TEXT_EXT:
        text = data.decode("utf-8", errors="replace")
        if not text.strip():
            raise RuntimeError("这个文件没有可读的文字内容。")
    else:
        raise RuntimeError("暂不支持的文件类型：{}".format(ext or "(无扩展名)"))

    for img in images:
        qrs.extend(decode_qr(img))
    # 超长 PDF 会被 MAX_PDF_PAGES 截断（每页一次视觉调用，太多会拖很久）。
    # 截断了必须告诉用户，否则档案里就是一份"看起来完整、其实少了几页"的报告。
    pages_parsed = len(images)
    truncated = bool(page_count and page_count > pages_parsed)
    if not images and text:
        parsed = parse_report_text(text, person_name=person_name)
    else:
        parsed = parse_report_images(images, person_name=person_name)

    # 姓名核对：报告上实际印的姓名 vs 本次上传选的档案。
    # 两者不一致 → 很可能是别的家人的报告或传错了人，要提醒用户，而不是默默入库。
    on_report = (parsed.get("person_name") or "").strip()
    target = (person_name or "").strip()
    name_match = None
    if on_report and target:
        name_match = (on_report == target)

    # 不是体检报告（模型判定「非报告」，或一个指标都没抽出来）：文件留档，但
    # **不建「报告」、不写指标**——否则购物小票里的"合计 87.50 元"会混进健康档案。
    if not (parsed.get("indicators") or []) or \
            (parsed.get("category") or "").strip() == "非报告":
        file_id = add_file(conn, person_id, filename, data, mime=mime, kind="other",
                           page_count=page_count or len(images))
        return {"report_id": None, "file_id": file_id, "indicator_count": 0,
                "page_count": page_count or len(images), "qr": qrs, "parsed": parsed,
                "person_name_on_report": None, "name_match": None,
                "not_a_report": True, "abnormal": [],
                "pages_total": page_count or pages_parsed, "pages_parsed": pages_parsed,
                "truncated": truncated}

    # 报告里的身高/体重回填到档案（这俩在档案页有独立字段，一直空着会显得档案没建）。
    # 只在姓名对得上、或报告没写姓名时做；且不覆盖用户已经填过的值。
    if name_match is not False:
        prof = get_profile(conn, person_id) or {}
        fills = {}
        for ind in (parsed.get("indicators") or []):
            nm = (ind.get("name") or "").strip().replace(" ", "")
            val = _to_float(ind.get("value"))
            if val is None:
                continue
            if nm in ("身高", "身高cm", "身高(cm)", "身高（cm）") and not prof.get("height_cm"):
                fills["height_cm"] = val
            elif nm in ("体重", "体重kg", "体重(kg)", "体重（kg）") and not prof.get("weight_kg"):
                fills["weight_kg"] = val
        if fills:
            save_profile(conn, person_id, fills)

    file_id = add_file(conn, person_id, filename, data, mime=mime, kind="report",
                       page_count=page_count or len(images))
    rid, n = add_report(
        conn, person_id,
        report_date=parsed.get("report_date"),
        institution=parsed.get("institution") or "",
        category=parsed.get("category") or "体检",
        summary=parsed.get("summary") or "",
        conclusion=parsed.get("conclusion") or "",
        indicators=parsed.get("indicators") or [],
        file_id=file_id,
        raw={"parsed": parsed, "qr": qrs,
             "person_name_on_report": on_report or None,
             "name_match": name_match})
    return {"report_id": rid, "file_id": file_id, "indicator_count": n,
            "page_count": page_count or len(images), "qr": qrs, "parsed": parsed,
            "person_name_on_report": on_report or None,
            "name_match": name_match,
            "pages_total": page_count or pages_parsed, "pages_parsed": pages_parsed,
            "truncated": truncated,
            "abnormal": [i for i in list_indicators(conn, person_id, report_id=rid)
                         if i.get("status") in ("high", "low")]}


# ══════════════════════════════════════════════════════════════════════
#  HTTP
# ══════════════════════════════════════════════════════════════════════
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "wuage-health/2.0"

    def log_message(self, fmt, *args):
        pass

    # ── 收发 ────────────────────────────────────────────────────────
    def _send(self, status, body, ctype="application/json; charset=utf-8", extra=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _json(self, status, obj):
        self._send(status, json.dumps(obj, ensure_ascii=False, default=_json_default))

    def _error(self, status, msg):
        self._json(status, {"ok": False, "error": str(msg)})

    def _body(self, limit=MAX_UPLOAD):
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            raise ValueError("内容太大（上限 {}MB）".format(limit // (1 << 20)))
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        ctype = (self.headers.get("Content-Type") or "").lower()
        if ctype.startswith("multipart/form-data"):
            return {"__multipart__": self._parse_multipart(raw, ctype)}
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("请求体不是合法 JSON")

    def _parse_multipart(self, raw, ctype):
        # 必须从原始 Content-Type 取 boundary：ctypes 里没有 boundary 参数，
        # 而浏览器发的 boundary 以 "--" 开头，切分时要剥掉，否则解析全乱。
        m = re.search(r"boundary=(?:\"([^\"]+)\"|([^;]+))", self.headers.get("Content-Type") or "")
        if not m:
            raise ValueError("multipart 缺少 boundary")
        boundary = (m.group(1) or m.group(2)).strip()
        if boundary.startswith("--"):
            boundary = boundary[2:]
        if not boundary:
            raise ValueError("multipart boundary 为空")
        delim = b"--" + boundary.encode("ascii", "ignore")
        fields, files = {}, []
        for chunk in raw.split(delim):
            if not chunk or chunk in (b"--\r\n", b"--", b"\r\n", b"--\r\n\r\n"):
                continue
            head, _, body = chunk.partition(b"\r\n\r\n")
            if not _:
                continue
            body = body.rstrip(b"\r\n")
            if body.endswith(b"--"):
                body = body[:-2].rstrip(b"\r\n")
            head_text = head.decode("utf-8", errors="replace")
            name_m = re.search(r'name="([^"]*)"', head_text)
            if not name_m:
                continue
            field = name_m.group(1)
            fname_m = re.search(r'filename="([^"]*)"', head_text)
            if fname_m is None:
                fields[field] = body.decode("utf-8", errors="replace")
            else:
                files.append((field, os.path.basename(fname_m.group(1)), body))
        return {"fields": fields, "files": files}

    def _person(self, conn, pid):
        p = person_by_id(conn, pid) if pid else None
        if p is None:
            raise ValueError("成员不存在：{}".format(pid))
        return p

    # ── SSE 流式对话 ────────────────────────────────────────────────
    def _sse_open(self):
        # 关键：SSE 没有 Content-Length，若保持 HTTP/1.1 keep-alive，浏览器
        # 的 ReadableStream 永远等不到流结束（reader.read() 不 resolve），
        # 前端就会一直卡在"记进档案…"。所以显式关闭连接，让流有个尽头。
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()

    def _sse(self, obj):
        try:
            self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False,
                                                    default=_json_default) + "\n\n")
                             .encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _stream_chat(self, conn, pid, text, hist, attach, conv_id=None, created_new=False):
        """边生成边推给浏览器：delta 是正文增量，tool 是模型决定写库，done 收尾。

        写库（工具调用）是同步阻塞的，模型可能连调好几轮；这期间不能让前端
        以为连接死了，所以起一个心跳线程定期发 ping。
        """
        self._sse_open()
        if attach:
            self._sse({"type": "attach", "attachments": attach})
        stop = threading.Event()

        def beat():
            while not stop.wait(8):
                self._sse({"type": "ping"})

        hb = threading.Thread(target=beat, daemon=True)
        hb.start()
        try:
            def on_event(kind, data):
                if kind == "delta":
                    self._sse({"type": "delta", "text": data})
                elif kind == "tool":
                    self._sse({"type": "tool", "name": data})
            reply, actions = chat(conn, pid, text, history=hist, on_event=on_event)
            if not reply:
                reply = "（模型没有返回文字，请再问一次）"
                self._sse({"type": "delta", "text": reply})
            # 先落库再发 done：这样用户看到收尾时刷新页面，上下文一定还在。
            if conv_id:
                try:
                    save_turn(conn, conv_id, text, reply, actions, attach)
                except sqlite3.Error:
                    pass
            self._sse({"type": "done", "reply": reply, "actions": actions,
                       "conversation_id": conv_id})
        except (RuntimeError, ValueError) as e:
            # 同上：失败时不留空会话
            if created_new and not hist and conv_id:
                try:
                    delete_conversation(conn, conv_id)
                except (ValueError, sqlite3.Error):
                    pass
            self._sse({"type": "error", "error": str(e)})
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            stop.set()
            try:
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                self.wfile.close()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    # ── GET ─────────────────────────────────────────────────────────
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            try:
                data = WEB_PATH.read_bytes()
            except OSError:
                self._send(500, "<h1>web/health.html 缺失</h1>", "text/html; charset=utf-8")
                return
            self._send(200, data, "text/html; charset=utf-8")
            return

        if path == "/api/meta":
            self._json(200, {"ok": True, "service": "wuage-health", "version": "2.0",
                             "db": str(DB_PATH), "files_dir": str(FILES_DIR),
                             "vision_model": VISION_MODEL, "chat_model": CHAT_MODEL,
                             "model": MODEL,
                             "profile_groups": [
                                 {"key": g[0], "label": g[1],
                                  "fields": [{"key": f[0], "label": f[1], "type": f[2],
                                              "hint": f[3]} for f in g[2]]}
                                 for g in PROFILE_GROUPS],
                             "record_categories": RECORD_CATEGORIES,
                             "tools": {"pdftoppm": bool(_which("pdftoppm")),
                                       "zbarimg": bool(_which("zbarimg")),
                                       "api_key": bool(api_key())}})
            return

        conn = connect()
        try:
            if path == "/api/health":
                self._json(200, {"ok": True, "db": str(DB_PATH),
                                 "persons": [p["name"] for p in list_persons(conn)]})
                return

            m = re.match(r"^/api/files/(\d+)/raw$", path)
            if m:
                row = conn.execute("SELECT * FROM files WHERE id=?", (int(m.group(1)),)).fetchone()
                if row is None:
                    self._error(404, "文件不存在")
                    return
                fp = FILES_DIR / row["stored_name"]
                if not fp.exists():
                    self._error(404, "文件已丢失")
                    return
                mime = row["mime"] or "application/octet-stream"
                inline = qs.get("dl", [""])[0] != "1"
                disp = "inline" if inline else "attachment"
                name = urllib.parse.quote(row["orig_name"] or row["stored_name"])
                self._send(200, fp.read_bytes(), mime,
                           {"Content-Disposition": "{}; filename*=UTF-8''{}".format(disp, name)})
                return

            if path == "/api/persons":
                self._json(200, {"ok": True, "persons": list_persons(conn)})
                return

            m = re.match(r"^/api/persons/(\d+)$", path)
            if m:
                pid = int(m.group(1))
                p = self._person(conn, pid)
                self._json(200, {"ok": True, "person": p, "profile": get_profile(conn, pid),
                                 "reports": list_reports(conn, pid),
                                 "indicators": list_indicators(conn, pid, limit=200),
                                 "standalone_indicators": list_indicators(
                                     conn, pid, standalone=True, limit=100),
                                 "records": list_records(conn, pid, limit=60),
                                 "memories": list_memories(conn, pid, limit=50),
                                 "plans": list_plans(conn, pid),
                                 "conversations": list_conversations(conn, pid, limit=20),
                                 "files": list_files(conn, pid)})
                return

            m = re.match(r"^/api/persons/(\d+)/digest$", path)
            if m:
                d = digest(conn, int(m.group(1)))
                if d is None:
                    self._error(404, "成员不存在")
                    return
                self._json(200, d)
                return

            m = re.match(r"^/api/persons/(\d+)/indicators$", path)
            if m:
                rid = qs.get("report_id", [""])[0]
                self._json(200, {"ok": True, "indicators": list_indicators(
                    conn, int(m.group(1)), report_id=int(rid) if rid else None)})
                return

            m = re.match(r"^/api/persons/(\d+)/records$", path)
            if m:
                self._json(200, {"ok": True, "records": list_records(conn, int(m.group(1)), limit=200)})
                return

            m = re.match(r"^/api/persons/(\d+)/reports$", path)
            if m:
                self._json(200, {"ok": True, "reports": list_reports(conn, int(m.group(1)))})
                return

            m = re.match(r"^/api/persons/(\d+)/memories$", path)
            if m:
                topic = qs.get("topic", [""])[0]
                self._json(200, {"ok": True, "memories": list_memories(
                    conn, int(m.group(1)), limit=100, topic=topic or None)})
                return

            m = re.match(r"^/api/persons/(\d+)/conversations$", path)
            if m:
                self._json(200, {"ok": True, "conversations": list_conversations(
                    conn, int(m.group(1)))})
                return

            m = re.match(r"^/api/persons/(\d+)/plans$", path)
            if m:
                st = qs.get("status", [""])[0]
                self._json(200, {"ok": True, "plans": list_plans(
                    conn, int(m.group(1)), status=st or None)})
                return

            m = re.match(r"^/api/conversations/(\d+)/messages$", path)
            if m:
                cid = int(m.group(1))
                conv = get_conversation(conn, cid)
                if conv is None:
                    self._error(404, "会话不存在")
                    return
                try:
                    limit = int(qs.get("limit", ["200"])[0] or 200)
                except ValueError:
                    limit = 200
                self._json(200, {"ok": True, "conversation": conv,
                                 "messages": list_messages(conn, cid, limit=limit)})
                return

            self._error(404, "not found: " + path)
        except ValueError as e:
            self._error(400, e)
        except sqlite3.Error as e:
            self._error(500, "数据库错误：" + str(e))
        finally:
            conn.close()

    # ── POST ────────────────────────────────────────────────────────
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            body = self._body()
        except ValueError as e:
            self._error(400, e)
            return
        if isinstance(body, dict) and "__multipart__" in body:
            mp = body["__multipart__"]
            body = mp["fields"]
            uploads = mp["files"]
        else:
            uploads = []

        conn = connect()
        try:
            if path == "/api/chat":
                pid = int(body.get("person_id") or 0)
                self._person(conn, pid)
                text = (body.get("message") or "").strip()
                stream = str(body.get("stream") or "") in ("1", "true", "True")
                if not text and not uploads:
                    self._error(400, "说点什么，或传一份报告")
                    return
                reply, actions, attach = "", [], []
                if uploads:
                    for (_f, fname, data) in uploads[:8]:
                        if len(data) > MAX_UPLOAD:
                            attach.append({"file": fname, "ok": False,
                                           "error": "文件超过 {}MB".format(MAX_UPLOAD // (1 << 20))})
                            continue
                        try:
                            res = ingest_report(conn, pid, fname, data,
                                                person_name=(person_by_id(conn, pid) or {}).get("name"))
                            attach.append({"file": fname, "ok": True,
                                           "report_id": res["report_id"],
                                           "indicator_count": res["indicator_count"],
                                           "abnormal_count": len(res["abnormal"]),
                                           "person_name_on_report": res.get("person_name_on_report"),
                                           "name_match": res.get("name_match"),
                                           "not_a_report": res.get("not_a_report", False),
                                           "pages_total": res.get("pages_total"),
                                           "pages_parsed": res.get("pages_parsed"),
                                           "truncated": res.get("truncated", False),
                                           "abnormal": [{"name": i["name"], "value": i["value"],
                                                         "unit": i["unit"], "status": i["status"]}
                                                        for i in res["abnormal"][:15]],
                                           "summary": (res["parsed"] or {}).get("summary") or "",
                                           "report_date": (res["parsed"] or {}).get("report_date")})
                        except (RuntimeError, ValueError, OSError) as e:
                            attach.append({"file": fname, "ok": False, "error": str(e)})
                    mismatched = [a for a in attach
                                  if a.get("ok") and a.get("name_match") is False]
                    not_reports = [a for a in attach
                                   if a.get("ok") and a.get("not_a_report")]
                    if not text:
                        text = "我刚上传了一份体检报告，请帮我看一下，说说重点。"
                    if mismatched:
                        # 报告姓名和当前选的档案对不上 → 把这件事摆到模型面前，让它提醒用户，
                        # 而不是当成这个人的报告直接解读。（用户自己打了字时也要带上）
                        names = "、".join(sorted({a.get("person_name_on_report") or "（没写姓名）"
                                                  for a in mismatched}))
                        text += ("\n\n（系统提示：这份报告上印的姓名是「{}」，"
                                 "与当前选的档案不是同一个人，请先提醒用户确认是不是传错了人。）"
                                 ).format(names)
                    if not_reports:
                        # 传的根本不是体检报告 → 别硬解读，让模型说明并请用户换一份
                        names = "、".join(sorted({a.get("file") or "（文件）"
                                                  for a in not_reports}))
                        text += ("\n\n（系统提示：{} 看起来不是体检/化验报告（没有可用的检查指标），"
                                 "请如实告诉用户你没在里面找到体检指标，并请 TA 换一份报告；"
                                 "不要编造指标，也不要把单据上的金额/商品名当成健康数据。）"
                                 ).format(names)
                    cut = [a for a in attach if a.get("ok") and a.get("truncated")]
                    if cut:
                        parts = "、".join(sorted({
                            "{}（共 {} 页，只解析了前 {} 页）".format(
                                a.get("file"), a.get("pages_total"), a.get("pages_parsed"))
                            for a in cut}))
                        text += ("\n\n（系统提示：{} —— 报告页数超过解析上限被截断了。"
                                 "请明确告诉用户后半部分**没有入库**，并建议把报告拆成两份分别上传；"
                                 "解读时只依据已解析的那部分，不要假装看到了后面的内容。）"
                                 ).format(parts)
                hist = body.get("history") or []
                if isinstance(hist, str):          # multipart 里是 JSON 字符串
                    try:
                        hist = json.loads(hist)
                    except (ValueError, TypeError):
                        hist = []
                if not isinstance(hist, list):
                    hist = []
                hist = [h for h in hist if isinstance(h, dict)]

                # 会话：优先用服务端落库的历史。前端没给 id（或 id 不属于这个人）就开新会话；
                # 旧前端仍会带 history 过来，只在新会话时当开场种子，免得老对话上下文丢掉。
                conv_id = int(body.get("conversation_id") or 0)
                conv = get_conversation(conn, conv_id) if conv_id else None
                created_new = conv is None or conv.get("person_id") != pid
                if created_new:
                    conv_id = create_conversation(conn, pid)
                    for h in hist:
                        if h.get("role") in ("user", "assistant") and h.get("content"):
                            add_message(conn, conv_id, h["role"], str(h["content"])[:4000])
                hist_db = conversation_history(conn, conv_id, limit=10)
                try:
                    if stream:
                        self._stream_chat(conn, pid, text, hist_db, attach, conv_id,
                                          created_new=created_new)
                        return
                    reply, actions = chat(conn, pid, text, history=hist_db)
                except (RuntimeError, ValueError) as e:
                    # 模型失败时别留下空会话（只在本次新建、且没播种历史时才回收）
                    if created_new and not hist_db:
                        try:
                            delete_conversation(conn, conv_id)
                        except (ValueError, sqlite3.Error):
                            pass
                    self._error(502, str(e))
                    return
                if not reply:
                    reply = "（模型没有返回文字，请再问一次）"
                save_turn(conn, conv_id, text, reply, actions, attach)
                self._json(200, {"ok": True, "reply": reply, "actions": actions,
                                 "attachments": attach, "conversation_id": conv_id})
                return

            if path == "/api/upload":
                pid = int(body.get("person_id") or 0)
                self._person(conn, pid)
                if not uploads:
                    self._error(400, "没有收到文件")
                    return
                out = []
                for (_f, fname, data) in uploads[:8]:
                    try:
                        res = ingest_report(conn, pid, fname, data,
                                            person_name=(person_by_id(conn, pid) or {}).get("name"))
                        out.append({"file": fname, "ok": True, "report_id": res["report_id"],
                                    "indicator_count": res["indicator_count"],
                                    "abnormal_count": len(res["abnormal"]),
                                    "person_name_on_report": res.get("person_name_on_report"),
                                    "name_match": res.get("name_match"),
                                    "not_a_report": res.get("not_a_report", False),
                                    "pages_total": res.get("pages_total"),
                                    "pages_parsed": res.get("pages_parsed"),
                                    "truncated": res.get("truncated", False),
                                    "summary": (res["parsed"] or {}).get("summary") or "",
                                    "report_date": (res["parsed"] or {}).get("report_date")})
                    except (RuntimeError, ValueError, OSError) as e:
                        out.append({"file": fname, "ok": False, "error": str(e)})
                self._json(200, {"ok": True, "results": out})
                return

            if path == "/api/persons":
                pid = create_person(conn, body.get("name"), role=body.get("role") or "family",
                                    sex=body.get("sex") or "")
                self._json(201, {"ok": True, "id": pid, "person": person_by_id(conn, pid)})
                return

            if path == "/api/persons/delete":
                pid = int(body.get("person_id") or 0)
                counts = delete_person(conn, pid)
                self._json(200, {"ok": True, "deleted": counts})
                return

            if path == "/api/profile":
                pid = int(body.get("person_id") or 0)
                self._person(conn, pid)
                fields = {k: v for k, v in body.items() if k != "person_id"}
                self._json(200, {"ok": True, "profile": save_profile(conn, pid, fields)})
                return

            if path == "/api/records":
                pid = int(body.get("person_id") or 0)
                self._person(conn, pid)
                rid = add_record(conn, pid, body.get("record_date"),
                                 body.get("category") or "other", body.get("content") or "")
                self._json(201, {"ok": True, "id": rid})
                return

            if path == "/api/records/delete":
                delete_record(conn, int(body.get("id") or 0), int(body.get("person_id") or 0))
                self._json(200, {"ok": True})
                return

            if path == "/api/reports/delete":
                delete_report(conn, int(body.get("report_id") or 0), int(body.get("person_id") or 0))
                self._json(200, {"ok": True})
                return

            if path == "/api/memories":
                pid = int(body.get("person_id") or 0)
                self._person(conn, pid)
                if body.get("id") and (body.get("content") is not None or body.get("kind")):
                    update_memory(conn, int(body["id"]), body)
                    self._json(200, {"ok": True})
                    return
                mid = add_memory(conn, pid, kind=body.get("kind") or "fact",
                                 topic=body.get("topic") or "", content=body.get("content") or "",
                                 importance=body.get("importance") or "medium",
                                 source=body.get("source") or "ui")
                self._json(201, {"ok": True, "id": mid})
                return

            if path == "/api/memories/delete":
                delete_memory(conn, int(body.get("id") or 0))
                self._json(200, {"ok": True})
                return

            if path == "/api/conversations/new":
                pid = int(body.get("person_id") or 0)
                self._person(conn, pid)
                cid = create_conversation(conn, pid, title=body.get("title") or "")
                self._json(201, {"ok": True, "conversation_id": cid})
                return

            if path == "/api/conversations/delete":
                counts = delete_conversation(conn, int(body.get("conversation_id") or 0),
                                             int(body.get("person_id") or 0) or None)
                self._json(200, {"ok": True, "deleted": counts})
                return

            if path == "/api/plans":
                pid = int(body.get("person_id") or 0)
                self._person(conn, pid)
                if body.get("id"):
                    update_plan(conn, int(body["id"]), body)
                    self._json(200, {"ok": True})
                    return
                plan_id = add_plan(conn, pid, kind=body.get("kind") or "diet",
                                   title=body.get("title") or "", period=body.get("period") or "",
                                   content=body.get("content") or "",
                                   status=body.get("status") or "active")
                self._json(201, {"ok": True, "id": plan_id})
                return

            if path == "/api/plans/delete":
                delete_plan(conn, int(body.get("id") or 0),
                            int(body.get("person_id") or 0) or None)
                self._json(200, {"ok": True})
                return

            self._error(404, "not found: " + path)
        except ValueError as e:
            self._error(400, e)
        except sqlite3.IntegrityError as e:
            self._error(400, "数据冲突：" + str(e))
        except sqlite3.Error as e:
            self._error(500, "数据库错误：" + str(e))
        finally:
            conn.close()


def serve(host, port):
    conn = connect()
    try:
        seed(conn)
    finally:
        conn.close()
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    print("健康助手已启动：http://{}:{}".format(host, port))
    print("  数据：{}".format(DB_PATH))
    print("  文件：{}".format(FILES_DIR))
    print("  视觉模型：{} / 对话模型：{}".format(VISION_MODEL, CHAT_MODEL))
    if not _which("pdftoppm"):
        print("  [警告] 未找到 pdftoppm（poppler-utils），PDF 报告无法解析")
    if not _which("zbarimg"):
        print("  [警告] 未找到 zbarimg，报告里的二维码不会被解出")
    srv.serve_forever()


# ══════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════
def _open(args, name=None):
    conn = connect()
    seed(conn)
    p = person_by_name(conn, name or getattr(args, "name", None) or "残雪")
    return conn, p


def _need_person(conn, args):
    p = person_by_name(conn, getattr(args, "name", None) or "残雪")
    if p is None:
        print("error: 成员不存在：{}".format(getattr(args, "name", None)), file=sys.stderr)
        sys.exit(1)
    return p


def cmd_init(args):
    conn = connect()
    try:
        seed(conn)
        print("ok: health db ready at {}".format(DB_PATH))
        print("  成员：" + "、".join(p["name"] for p in list_persons(conn)))
    finally:
        conn.close()


def cmd_persons(args):
    conn = connect()
    try:
        seed(conn)
        for p in list_persons(conn):
            mark = "（本人）" if p["role"] == "self" else "（家人）"
            print("#{} {}{}  报告 {} / 指标 {} / 记录 {}".format(
                p["id"], p["name"], mark, p["file_count"], p["indicator_count"], p["record_count"]))
    finally:
        conn.close()


def cmd_profile(args):
    conn = connect()
    try:
        seed(conn)
        p = _need_person(conn, args)
        prof = get_profile(conn, p["id"])
        if args.set:
            fields = {}
            for kv in args.set:
                if "=" not in kv:
                    print("error: --set 需要 key=value 形式：" + kv, file=sys.stderr)
                    sys.exit(2)
                k, v = kv.split("=", 1)
                fields[k.strip()] = v
            prof = save_profile(conn, p["id"], fields)
            print("ok: 已更新 " + "、".join(fields.keys()))
        print(json.dumps(prof, ensure_ascii=False, default=_json_default, indent=2))
    finally:
        conn.close()


def cmd_digest(args):
    conn = connect()
    try:
        seed(conn)
        p = _need_person(conn, args)
        print(json.dumps(digest(conn, p["id"]), ensure_ascii=False, default=_json_default))
    finally:
        conn.close()


def cmd_record(args):
    conn = connect()
    try:
        seed(conn)
        p = _need_person(conn, args)
        rid = add_record(conn, p["id"], args.date, args.category, args.content)
        print("ok: 已记录 #{}（{} {}）".format(
            rid, RECORD_CATEGORIES.get(_normalize_category(args.category), args.category),
            args.date or today()))
    finally:
        conn.close()


def cmd_memo(args):
    conn = connect()
    try:
        seed(conn)
        p = _need_person(conn, args)
        if args.delete:
            delete_memory(conn, args.delete)
            print("ok: 已删除记忆 #{}".format(args.delete))
            return
        if args.content:
            mid = add_memory(conn, p["id"], kind=args.kind, topic=args.topic,
                             content=args.content, importance=args.importance, source="cli")
            print("ok: 已记住 #{}（{} / {}）".format(mid, args.kind, args.topic or "无主题"))
            return
        rows = list_memories(conn, p["id"], limit=args.limit, topic=args.topic or None)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, default=_json_default))
            return
        if not rows:
            print("（还没有长期记忆）")
        for r in rows:
            print("#{} [{}|{}] {}  {}".format(r["id"], r["kind"], r["importance"],
                                              r["topic"] or "-", r["content"]))
    finally:
        conn.close()


def cmd_parse_report(args):
    conn = connect()
    try:
        seed(conn)
        p = _need_person(conn, args)
        fp = Path(args.file)
        if not fp.exists():
            print("error: 文件不存在：" + str(fp), file=sys.stderr)
            sys.exit(1)
        data = fp.read_bytes()
        res = ingest_report(conn, p["id"], fp.name, data, person_name=p["name"])
        print("ok: 报告 #{} 入档，解析出 {} 项指标（{} 页）".format(
            res["report_id"], res["indicator_count"], res["page_count"]))
        on_report = res.get("person_name_on_report")
        if on_report and res.get("name_match") is False:
            print("  ⚠️ 姓名不符：报告上是「{}」，当前档案是「{}」——先确认是不是传错人，"
                  "别当成这个人的报告解读。".format(on_report, p["name"]))
        elif on_report:
            print("  姓名核对：报告上的「{}」与档案一致".format(on_report))
        else:
            print("  报告上没写姓名，无法核对归属")
        if res["qr"]:
            print("  二维码：{}".format(" | ".join(res["qr"][:3])))
        ab = res["abnormal"]
        if ab:
            print("  异常 {} 项：".format(len(ab)))
            for i in ab[:20]:
                print("    {} {} {} [{}]".format(i["name"], i["value"], i["unit"] or "",
                                                 "偏高" if i["status"] == "high" else "偏低"))
        else:
            print("  未发现超出参考区间的指标")
    finally:
        conn.close()


def cmd_plan(args):
    conn = connect()
    try:
        seed(conn)
        p = _need_person(conn, args)
        if args.delete:
            delete_plan(conn, args.delete, p["id"])
            print("ok: 已删除计划 #{}".format(args.delete))
            return
        if args.update:
            fields = {}
            for k in ("title", "period", "content", "kind", "status"):
                v = getattr(args, k, None)
                if v:
                    fields[k] = v
            update_plan(conn, args.update, fields)
            print("ok: 已更新计划 #{}".format(args.update))
            return
        if args.content:
            new_id = add_plan(conn, p["id"], kind=args.kind, title=args.title,
                              period=args.period, content=args.content, status=args.status)
            print("ok: 已保存计划 #{}（{} / {}）".format(
                new_id, args.kind, args.status or "active"))
            return
        rows = list_plans(conn, p["id"], status=args.status or None, limit=args.limit)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, default=_json_default))
            return
        if not rows:
            print("（还没有计划）")
        for r in rows:
            print("#{} [{}|{}] {} {}  ({})".format(
                r["id"], r["kind"], r["status"], r["title"] or "-", r["period"] or "-",
                (r["updated_at"] or "")[:10]))
            for line in (r["content"] or "").splitlines():
                print("    " + line)
    finally:
        conn.close()


def cmd_chat(args):
    conn = connect()
    try:
        seed(conn)
        p = _need_person(conn, args)
        reply, actions = chat(conn, p["id"], args.text)
        print(reply)
        for a in actions:
            print("  · [{}] {}".format(a["tool"], a["result"]), file=sys.stderr)
    finally:
        conn.close()


def main():
    p = argparse.ArgumentParser(prog="health", description="wuage-home 健康助手")
    p.add_argument("--host", default=os.environ.get("HEALTH_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("HEALTH_PORT", 17626)))
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("init", help="建库 + 种子成员"); sp.set_defaults(fn=cmd_init)
    sp = sub.add_parser("persons", help="列出成员"); sp.set_defaults(fn=cmd_persons)

    sp = sub.add_parser("profile", help="查看/更新个人资料")
    sp.add_argument("--name", default="残雪")
    sp.add_argument("--set", action="append", metavar="KEY=VALUE",
                    help="更新字段，可多次，如 --set city=杭州 --set allergies=青霉素")
    sp.set_defaults(fn=cmd_profile)

    sp = sub.add_parser("digest", help="某人全档案摘要（JSON）")
    sp.add_argument("--name", default="残雪"); sp.set_defaults(fn=cmd_digest)

    sp = sub.add_parser("record", help="记一条日常流水")
    sp.add_argument("--name", default="残雪"); sp.add_argument("--date")
    sp.add_argument("--category", default="other"); sp.add_argument("--content", required=True)
    sp.set_defaults(fn=cmd_record)

    sp = sub.add_parser("memo", help="长期记忆：记 / 查 / 删（内容由模型自行判断）")
    sp.add_argument("--name", default="残雪")
    sp.add_argument("--kind", default="fact", choices=["fact", "preference", "digest"])
    sp.add_argument("--topic", default=""); sp.add_argument("--content", default="")
    sp.add_argument("--importance", default="medium", choices=["high", "medium", "low"])
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--delete", type=int, metavar="ID")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_memo)

    sp = sub.add_parser("parse-report", help="解析一份体检报告入库（PDF/图片/文本）")
    sp.add_argument("--name", default="残雪"); sp.add_argument("--file", required=True)
    sp.set_defaults(fn=cmd_parse_report)

    sp = sub.add_parser("chat", help="对话式问答（读全档 + 自动落库）")
    sp.add_argument("--name", default="残雪"); sp.add_argument("--text", required=True)
    sp.set_defaults(fn=cmd_chat)

    sp = sub.add_parser("plan", help="饮食/运动计划：存 / 查 / 改 / 删")
    sp.add_argument("--name", default="残雪")
    sp.add_argument("--kind", default="diet", choices=list(PLAN_KINDS))
    sp.add_argument("--title", default=""); sp.add_argument("--period", default="")
    sp.add_argument("--content", default="")
    sp.add_argument("--status", default="", choices=[""] + list(PLAN_STATUS))
    sp.add_argument("--update", type=int, metavar="ID", help="更新已有计划")
    sp.add_argument("--delete", type=int, metavar="ID", help="删除计划")
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_plan)

    args = p.parse_args()
    if hasattr(args, "fn"):
        args.fn(args)
        return
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
