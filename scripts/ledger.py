#!/usr/bin/env python3
"""wuage-home ledger — 家庭财务数据层 + CLI（纯标准库）。

v2 数据模型（v0.1.5 升级，PRD 对齐）：
  families      家庭（默认"我的家庭"）
  members       成员（默认"本人"；AI 识别候选）
  accounts      资金账户（默认"现金"，v1.0 不展示）
  categories    分类（种子 10 项 + 用户自定义；被引用禁止删除；parent_id 预留二级）
  transactions  财务事件（核心：member/category/account 关联，AI 字段预留）
  drafts        草稿（AI 结果未确认，24h 过期）
旧 ledger 表保留为备份（legacy_ledger），migrate 后新数据只写 transactions。

数据目录：$WUAGE_DATA，默认 <repo>/data —— 迁移 = 拷走该目录。
"""

import argparse
import json
import os
import sqlite3
import sys
import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("WUAGE_DATA", str(REPO_ROOT / "data")))
DB_PATH = DATA_DIR / "ledger.db"

SEED_CATEGORIES = ["餐饮", "交通", "医疗", "购物", "教育", "娱乐", "居住", "通讯", "人情", "其他"]
CATEGORY_MIGRATE_MAP = {"医疗健康": "医疗", "育儿教育": "教育"}
KINDS = {"expense", "income", "transfer"}
SOURCES = {"manual", "ai_parsed", "ai_corrected"}
CHANNELS = {"manual", "voice", "wechat", "import"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS families (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS members (
  id         INTEGER PRIMARY KEY,
  family_id  INTEGER NOT NULL,
  name       TEXT NOT NULL,
  relation   TEXT,
  is_active  INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
  id         INTEGER PRIMARY KEY,
  family_id  INTEGER NOT NULL,
  name       TEXT NOT NULL,
  type       TEXT NOT NULL DEFAULT 'cash',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS categories (
  id          INTEGER PRIMARY KEY,
  family_id   INTEGER NOT NULL,
  name        TEXT NOT NULL,
  type        TEXT NOT NULL DEFAULT 'expense',
  parent_id   INTEGER,
  is_system   INTEGER NOT NULL DEFAULT 0,
  icon        TEXT,
  color       TEXT,
  sort_order  INTEGER NOT NULL DEFAULT 0,
  is_archived INTEGER NOT NULL DEFAULT 0,
  UNIQUE(family_id, name)
);
CREATE TABLE IF NOT EXISTS transactions (
  id             INTEGER PRIMARY KEY,
  family_id      INTEGER NOT NULL,
  account_id     INTEGER,
  member_id      INTEGER,
  category_id    INTEGER NOT NULL,
  amount_cents   INTEGER NOT NULL,
  kind           TEXT NOT NULL DEFAULT 'expense',
  date           TEXT NOT NULL,
  remark         TEXT NOT NULL DEFAULT '',
  merchant       TEXT,
  source         TEXT NOT NULL DEFAULT 'manual',
  channel        TEXT NOT NULL DEFAULT 'manual',
  raw_text       TEXT,
  ai_confidence  REAL,
  corrected_from INTEGER,
  tags           TEXT NOT NULL DEFAULT '[]',
  created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_tx_category ON transactions(category_id);
CREATE INDEX IF NOT EXISTS idx_tx_member ON transactions(member_id);
CREATE TABLE IF NOT EXISTS drafts (
  id         INTEGER PRIMARY KEY,
  family_id  INTEGER NOT NULL,
  data_json  TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expired_at TEXT NOT NULL
);
-- 旧表保留作迁移备份
CREATE TABLE IF NOT EXISTS legacy_ledger (
  id           INTEGER PRIMARY KEY,
  amount_cents INTEGER NOT NULL,
  date         TEXT NOT NULL,
  kind         TEXT NOT NULL DEFAULT 'expense',
  category     TEXT NOT NULL,
  tags         TEXT NOT NULL DEFAULT '[]',
  note         TEXT NOT NULL DEFAULT '',
  source       TEXT NOT NULL DEFAULT 'manual',
  member       TEXT,
  created_at   TEXT NOT NULL
);
"""


class LedgerError(Exception):
    pass


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def today():
    return datetime.date.today().isoformat()


def parse_date(s, default=None):
    if not s:
        return default or today()
    try:
        y, m, d = (int(x) for x in s.split("-"))
        return datetime.date(y, m, d).isoformat()
    except (ValueError, TypeError):
        raise LedgerError("bad date {!r}, use YYYY-MM-DD".format(s))


def row_to_dict(r):
    d = dict(r)
    if "tags" in d:
        try:
            d["tags"] = json.loads(d["tags"] or "[]")
        except (json.JSONDecodeError, TypeError):
            d["tags"] = []
    return d


def ensure_seed(conn):
    """幂等：默认家庭 / 本人成员 / 现金账户 / 种子 10 分类。"""
    init_db(conn)
    now = now_iso()
    fam = conn.execute("SELECT id FROM families ORDER BY id LIMIT 1").fetchone()
    if fam is None:
        cur = conn.execute("INSERT INTO families (name, created_at) VALUES (?,?)", ("我的家庭", now))
        fam_id = cur.lastrowid
    else:
        fam_id = fam["id"]
    mem = conn.execute("SELECT id FROM members WHERE family_id=? AND name='本人' LIMIT 1", (fam_id,)).fetchone()
    if mem is None:
        cur = conn.execute(
            "INSERT INTO members (family_id, name, is_active, created_at) VALUES (?,?,1,?)",
            (fam_id, "本人", now))
        mem_id = cur.lastrowid
    else:
        mem_id = mem["id"]
    acc = conn.execute("SELECT id FROM accounts WHERE family_id=? AND name='现金' LIMIT 1", (fam_id,)).fetchone()
    if acc is None:
        cur = conn.execute("INSERT INTO accounts (family_id, name, type, created_at) VALUES (?,?,'cash',?)",
                           (fam_id, "现金", now))
        acc_id = cur.lastrowid
    else:
        acc_id = acc["id"]
    for i, name in enumerate(SEED_CATEGORIES):
        row = conn.execute("SELECT id FROM categories WHERE family_id=? AND name=? LIMIT 1", (fam_id, name)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO categories (family_id, name, type, is_system, sort_order) VALUES (?,?,'expense',1,?)",
                (fam_id, name, i))
    conn.commit()
    return {"family_id": fam_id, "member_id": mem_id, "account_id": acc_id}


def migrate(conn):
    """旧 ledger 表 → transactions（幂等）。返回迁移条数。"""
    ensure_seed(conn)
    if conn.execute("SELECT COUNT(*) c FROM transactions").fetchone()["c"] > 0:
        return 0
    rows = []
    for tname in ("legacy_ledger", "ledger"):
        try:
            rows = [row_to_dict(rec) for rec in conn.execute(
                "SELECT * FROM {} ORDER BY id".format(tname)).fetchall()]
        except sqlite3.OperationalError:
            continue
        if rows:
            break
    if not rows:
        return 0
    seed = ensure_seed(conn)
    fam_id, mem_id, acc_id = seed["family_id"], seed["member_id"], seed["account_id"]
    other_id = None
    moved = 0
    for r in rows:
        cat_name = CATEGORY_MIGRATE_MAP.get(r["category"], r["category"])
        cat = conn.execute("SELECT id FROM categories WHERE family_id=? AND name=? LIMIT 1",
                           (fam_id, cat_name)).fetchone()
        if cat is None:
            if other_id is None:
                other_id = conn.execute("SELECT id FROM categories WHERE family_id=? AND name='其他'",
                                        (fam_id,)).fetchone()["id"]
            cid = other_id
        else:
            cid = cat["id"]
        conn.execute(
            """INSERT INTO transactions
               (family_id, account_id, member_id, category_id, amount_cents, kind, date, remark,
                merchant, source, channel, raw_text, ai_confidence, corrected_from, tags, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (fam_id, acc_id, mem_id, cid, r["amount_cents"], r["kind"], r["date"], r["note"],
             None, "manual", r["source"], None, None, None,
             json.dumps(r["tags"], ensure_ascii=False), r["created_at"]))
        moved += 1
    conn.commit()
    return moved


def default_ids(conn):
    return ensure_seed(conn)


def category_by_name(conn, family_id, name, default_other=False):
    row = conn.execute("SELECT id FROM categories WHERE family_id=? AND name=? AND is_archived=0",
                       (family_id, name)).fetchone()
    if row:
        return row["id"]
    if default_other:
        o = conn.execute("SELECT id FROM categories WHERE family_id=? AND name='其他'", (family_id,)).fetchone()
        if o:
            return o["id"]
    return None


def list_categories(conn, family_id):
    rows = conn.execute(
        "SELECT * FROM categories WHERE family_id=? AND is_archived=0 ORDER BY is_system DESC, sort_order, id",
        (family_id,)).fetchall()
    return [row_to_dict(r) for r in rows]


def add_category(conn, family_id, name):
    name = (name or "").strip()
    if not name:
        raise LedgerError("分类名不能为空")
    row = conn.execute("SELECT * FROM categories WHERE family_id=? AND name=?",
                       (family_id, name)).fetchone()
    if row is not None:
        if row["is_archived"]:
            # 同名软归档分类 → 复活（唯一约束仍占用，不能重复插入）
            conn.execute("UPDATE categories SET is_archived=0, sort_order=999 WHERE id=?", (row["id"],))
            conn.commit()
            return row["id"]
        raise LedgerError("分类已存在：{}".format(name))
    cur = conn.execute(
        "INSERT INTO categories (family_id, name, type, is_system, sort_order) VALUES (?,?,'expense',0,999)",
        (family_id, name))
    conn.commit()
    return cur.lastrowid


def rename_category(conn, family_id, cid, new_name):
    new_name = (new_name or "").strip()
    if not new_name:
        raise LedgerError("分类名不能为空")
    row = conn.execute("SELECT * FROM categories WHERE id=?", (cid,)).fetchone()
    if row is None or row["family_id"] != family_id:
        raise LedgerError("分类不存在")
    if row["name"] == new_name:
        raise LedgerError("新旧名称相同")
    if category_by_name(conn, family_id, new_name):
        raise LedgerError("分类已存在：{}".format(new_name))
    conn.execute("UPDATE categories SET name=? WHERE id=?", (new_name, cid))
    conn.commit()


def delete_category(conn, family_id, name):
    row = conn.execute("SELECT * FROM categories WHERE family_id=? AND name=? AND is_archived=0",
                       (family_id, name)).fetchone()
    if row is None:
        raise LedgerError("分类不存在：{}".format(name))
    if row["is_system"]:
        raise LedgerError("种子分类不可删除（可改名；'其他'为兜底）")
    n = conn.execute("SELECT COUNT(*) c FROM transactions WHERE category_id=?", (row["id"],)).fetchone()["c"]
    if n > 0:
        raise LedgerError("已有 {} 笔记录使用了该分类，无法删除；可改名".format(n))
    conn.execute("UPDATE categories SET is_archived=1 WHERE id=?", (row["id"],))
    conn.commit()
    return row["id"]


def _member_id_by_name(conn, family_id, name):
    row = conn.execute("SELECT id FROM members WHERE family_id=? AND name=? AND is_active=1",
                       (family_id, name)).fetchone()
    return row["id"] if row else None


def list_members(conn, family_id):
    return [row_to_dict(r) for r in conn.execute(
        "SELECT * FROM members WHERE family_id=? AND is_active=1 ORDER BY id", (family_id,)).fetchall()]


def add_member(conn, family_id, name, relation=None):
    name = (name or "").strip()
    if not name:
        raise LedgerError("成员名不能为空")
    if _member_id_by_name(conn, family_id, name):
        raise LedgerError("成员已存在：{}".format(name))
    cur = conn.execute(
        "INSERT INTO members (family_id, name, relation, is_active, created_at) VALUES (?,?,?,1,?)",
        (family_id, name, relation, now_iso()))
    conn.commit()
    return cur.lastrowid


def delete_member(conn, family_id, name):
    row = conn.execute("SELECT * FROM members WHERE family_id=? AND name=? AND is_active=1",
                       (family_id, name)).fetchone()
    if row is None:
        raise LedgerError("成员不存在：{}".format(name))
    if row["name"] == "本人":
        raise LedgerError("默认成员'本人'不可删除")
    n = conn.execute("SELECT COUNT(*) c FROM transactions WHERE member_id=?", (row["id"],)).fetchone()["c"]
    if n > 0:
        raise LedgerError("该成员已有 {} 笔消费记录，无法删除".format(n))
    conn.execute("UPDATE members SET is_active=0 WHERE id=?", (row["id"],))
    conn.commit()


def add_draft(conn, family_id, data, ttl_hours=24):
    created = now_iso()
    expired = (datetime.datetime.now() + datetime.timedelta(hours=ttl_hours)).isoformat(timespec="seconds")
    cur = conn.execute("INSERT INTO drafts (family_id, data_json, created_at, expired_at) VALUES (?,?,?,?)",
                       (family_id, json.dumps(data, ensure_ascii=False), created, expired))
    conn.commit()
    return cur.lastrowid


def list_drafts(conn, family_id):
    cur = conn.execute("DELETE FROM drafts WHERE family_id=? AND expired_at<=?", (family_id, now_iso()))
    if cur.rowcount:
        conn.commit()
    out = []
    for r in conn.execute("SELECT * FROM drafts WHERE family_id=? ORDER BY created_at DESC", (family_id,)).fetchall():
        d = row_to_dict(r)
        d["data"] = json.loads(d.pop("data_json") or "{}")
        out.append(d)
    return out


def delete_draft(conn, family_id, did):
    cur = conn.execute("DELETE FROM drafts WHERE id=? AND family_id=?", (did, family_id))
    conn.commit()
    if cur.rowcount == 0:
        raise LedgerError("草稿不存在")


def tx_to_dict(conn, tid):
    r = conn.execute(
        """SELECT t.*, c.name AS category_name, m.name AS member_name
           FROM transactions t
           LEFT JOIN categories c ON c.id = t.category_id
           LEFT JOIN members m ON m.id = t.member_id
           WHERE t.id=?""", (tid,)).fetchone()
    d = row_to_dict(r)
    d["category"] = d.pop("category_name")
    d["member"] = d.pop("member_name")
    d["note"] = d.pop("remark")
    return d


def add_record(conn, record):
    seed = ensure_seed(conn)
    fam_id = seed["family_id"]
    amount = record.get("amount_cents")
    if amount is None:
        raise LedgerError("amount_cents required (单位:分)")
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        raise LedgerError("amount_cents must be int, got {!r}".format(amount))
    kind = record.get("kind") or "expense"
    if kind not in KINDS:
        raise LedgerError("kind must be in {}".format(sorted(KINDS)))
    cid = record.get("category_id")
    if cid is None:
        cname = record.get("category") or "其他"
        cid = category_by_name(conn, fam_id, cname, default_other=True)
    if cid is None:
        raise LedgerError("分类无效（LLM 不自动建类，请先手动新增）")
    mid = record.get("member_id")
    if mid is None:
        mname = record.get("member")
        if mname:
            mid = _member_id_by_name(conn, fam_id, mname)
            if mid is None:
                raise LedgerError("成员不存在：{}（请先在家庭成员里添加）".format(mname))
        else:
            mid = seed["member_id"]
    acc_id = record.get("account_id") or seed["account_id"]
    source = record.get("source") or "manual"
    if source not in SOURCES:
        raise LedgerError("source must be in {}".format(sorted(SOURCES)))
    channel = record.get("channel") or "manual"
    if channel not in CHANNELS:
        channel = "manual"
    tags = record.get("tags") if "tags" in record else []
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise LedgerError("tags must be a list of strings")
    date_s = parse_date(record.get("date"))
    cur = conn.execute(
        """INSERT INTO transactions
           (family_id, account_id, member_id, category_id, amount_cents, kind, date, remark,
            merchant, source, channel, raw_text, ai_confidence, corrected_from, tags, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (fam_id, acc_id, mid, cid, amount, kind, date_s,
         (record.get("remark") or record.get("note") or "").strip(),
         record.get("merchant"), source, channel, record.get("raw_text"),
         record.get("ai_confidence"), record.get("corrected_from"),
         json.dumps(tags, ensure_ascii=False), now_iso()))
    conn.commit()
    return tx_to_dict(conn, cur.lastrowid)


def update_record(conn, tid, fields):
    row = conn.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()
    if row is None:
        raise LedgerError("no transaction with id={}".format(tid))
    fam_id = row["family_id"]
    sets, params = [], []
    for field, val in fields.items():
        if val is None:
            continue
        if field in ("category", "category_id"):
            cid = val if field == "category_id" else category_by_name(conn, fam_id, val, default_other=True)
            if cid is None:
                raise LedgerError("分类无效")
            sets.append("category_id=?"); params.append(cid)
        elif field in ("member", "member_id"):
            mid = val if field == "member_id" else _member_id_by_name(conn, fam_id, val)
            if mid is None:
                raise LedgerError("成员不存在")
            sets.append("member_id=?"); params.append(mid)
        elif field in ("remark", "note"):
            sets.append("remark=?"); params.append((val or "").strip())
        elif field == "date":
            sets.append("date=?"); params.append(parse_date(val))
        elif field == "kind":
            if val not in KINDS:
                raise LedgerError("kind 非法")
            sets.append("kind=?"); params.append(val)
        elif field == "amount_cents":
            try:
                params.append(int(val))
            except (TypeError, ValueError):
                raise LedgerError("amount_cents must be int")
            sets.append("amount_cents=?")
        elif field == "merchant":
            sets.append("merchant=?"); params.append(val)
        elif field == "tags":
            if not isinstance(val, list) or not all(isinstance(t, str) for t in val):
                raise LedgerError("tags must be list of strings")
            sets.append("tags=?"); params.append(json.dumps(val, ensure_ascii=False))
        elif field == "source":
            if val not in SOURCES:
                raise LedgerError("source 非法")
            sets.append("source=?"); params.append(val)
        else:
            raise LedgerError("unknown field {!r}".format(field))
    if not sets:
        raise LedgerError("nothing to update")
    params.append(tid)
    conn.execute("UPDATE transactions SET {} WHERE id=?".format(", ".join(sets)), params)
    conn.commit()
    return tx_to_dict(conn, tid)


def delete_record(conn, tid):
    cur = conn.execute("DELETE FROM transactions WHERE id=?", (tid,))
    conn.commit()
    return cur.rowcount


def list_entries(conn, from_=None, to=None, category=None, member=None, kind=None, limit=None):
    fam_id = ensure_seed(conn)["family_id"]
    where, params = [], []
    if from_:
        where.append("t.date>=?"); params.append(from_)
    if to:
        where.append("t.date<=?"); params.append(to)
    if category:
        cid = category_by_name(conn, fam_id, category)
        if cid is not None:
            where.append("t.category_id=?"); params.append(cid)
    if member:
        mid = _member_id_by_name(conn, fam_id, member)
        if mid is not None:
            where.append("t.member_id=?"); params.append(mid)
    if kind:
        where.append("t.kind=?"); params.append(kind)
    sql = ("SELECT t.*, c.name AS category_name, m.name AS member_name"
           " FROM transactions t"
           " LEFT JOIN categories c ON c.id=t.category_id"
           " LEFT JOIN members m ON m.id=t.member_id")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY t.date DESC, t.id DESC"
    if limit:
        sql += " LIMIT {}".format(int(limit))
    out = []
    for r in conn.execute(sql, params).fetchall():
        d = row_to_dict(r)
        d["category"] = d.pop("category_name")
        d["member"] = d.pop("member_name")
        d["note"] = d.pop("remark")
        out.append(d)
    return out


def _aggregate(rows):
    from collections import Counter
    agg = {}
    for r in rows:
        a = agg.setdefault(r["category"], {"count": 0, "total_cents": 0, "top_notes": []})
        a["count"] += 1
        a["total_cents"] += r["amount_cents"]
    for cat, a in agg.items():
        notes = [r["note"] for r in rows if r["category"] == cat and r["note"]]
        a["top_notes"] = [n for n, _ in Counter(notes).most_common(3)]
    return agg


def _by_category_json(agg):
    return {k: {kk: vv for kk, vv in v.items() if kk != "top_notes"} for k, v in agg.items()}


def summary_data(conn, from_=None, to=None, category=None, member=None, kind="expense"):
    rows = list_entries(conn, from_=from_, to=to, category=category, member=member, kind=kind)
    agg = _aggregate(rows)
    return {
        "from": from_, "to": to, "count": len(rows),
        "total_cents": sum(r["amount_cents"] for r in rows),
        "by_category": _by_category_json(agg),
    }



def dashboard_data(conn, month_offset=0):
    """首页仪表盘：本月/上月支出与环比、前5分类占比、近7日、成员当月支出。"""
    today_d = datetime.date.today()
    def shift_month(d, off):
        y = d.year + (d.month - 1 + off) // 12
        m = (d.month - 1 + off) % 12 + 1
        return datetime.date(y, m, 1)
    m_start = shift_month(today_d, month_offset)
    if m_start.month == 12:
        m_end_d = datetime.date(m_start.year + 1, 1, 1) - datetime.timedelta(days=1)
    else:
        m_end_d = datetime.date(m_start.year, m_start.month + 1, 1) - datetime.timedelta(days=1)
    m_end = m_end_d.isoformat()
    fam_id = ensure_seed(conn)["family_id"]
    month_rows = list_entries(conn, from_=m_start.isoformat(), to=m_end, kind="expense")
    prev_start = shift_month(today_d, month_offset - 1)
    prev_end = (shift_month(today_d, month_offset) - datetime.timedelta(days=1)).isoformat()
    prev_rows = list_entries(conn, from_=prev_start.isoformat(), to=prev_end, kind="expense")
    month_total = sum(row["amount_cents"] for row in month_rows)
    prev_total = sum(row["amount_cents"] for row in prev_rows)
    change_pct = None
    if prev_total > 0:
        change_pct = round((month_total - prev_total) * 100.0 / prev_total, 1)
    # 分类 TOP5 + 其他
    agg = _aggregate(month_rows)
    cats_sorted = sorted(agg.items(), key=lambda kv: -kv[1]["total_cents"])
    top = cats_sorted[:5]
    rest = sum(a["total_cents"] for _, a in cats_sorted[5:])
    cat_share = [{"name": name, "total_cents": a["total_cents"], "count": a["count"]}
                 for name, a in top]
    if rest > 0:
        cat_share.append({"name": "其他", "total_cents": rest, "count": sum(a["count"] for _, a in cats_sorted[5:])})
    # 近 7 日
    d7_start = (today_d - datetime.timedelta(days=6)).isoformat()
    d7_rows = list_entries(conn, from_=d7_start, to=today_d.isoformat(), kind="expense")
    by_day = {}
    for row in d7_rows:
        by_day[row["date"]] = by_day.get(row["date"], 0) + row["amount_cents"]
    last7 = []
    for i in range(6, -1, -1):
        d = (today_d - datetime.timedelta(days=i)).isoformat()
        last7.append({"date": d, "total_cents": by_day.get(d, 0)})
    # 成员当月
    member_spend = {}
    for row in month_rows:
        name = row.get("member") or "本人"
        member_spend[name] = member_spend.get(name, 0) + row["amount_cents"]
    members = []
    for name, cents in member_spend.items():
        members.append({"name": name, "total_cents": cents})
    return {
        "month": m_start.isoformat()[:7],
        "month_total_cents": month_total,
        "prev_total_cents": prev_total,
        "change_pct": change_pct,
        "count": len(month_rows),
        "cat_share": cat_share,
        "last7": last7,
        "members": members,
    }
def weekly_data(conn):
    now = datetime.date.today()
    start = now - datetime.timedelta(days=now.weekday())
    end = start + datetime.timedelta(days=6)
    rows = list_entries(conn, from_=start.isoformat(), to=end.isoformat())
    agg = _aggregate(rows)
    return {
        "week_start": start.isoformat(), "week_end": end.isoformat(),
        "count": len(rows),
        "total_cents": sum(r["amount_cents"] for r in rows),
        "by_category": _by_category_json(agg),
        "entries": rows,
    }

# ── CLI ─────────────────────────────────────────────────────────────────────

def cmd_init(args, conn):
    ensure_seed(conn)
    print("ok: db ready + seeds (家庭/本人/现金/{} 分类) at {}".format(len(SEED_CATEGORIES), DB_PATH))


def cmd_migrate(args, conn):
    print("migrated {} rows from legacy ledger -> transactions".format(migrate(conn)))


def cmd_add(args, conn):
    if args.json:
        record = json.loads(args.json)
        if not isinstance(record, dict):
            raise LedgerError("--json must be an object")
    else:
        record = {
            "amount_cents": args.amount_cents, "date": args.date, "kind": args.kind,
            "category": args.category, "tags": args.tags or [], "note": args.note,
            "member": args.member, "merchant": args.merchant, "channel": "manual",
        }
    print(json.dumps(add_record(conn, record), ensure_ascii=False))


def cmd_update(args, conn):
    fields = {"category": args.category, "member": args.member, "note": args.note,
              "date": args.date, "kind": args.kind, "amount_cents": args.amount_cents,
              "merchant": args.merchant}
    if args.tags is not None:
        fields["tags"] = args.tags
    print(json.dumps(update_record(conn, args.id, fields), ensure_ascii=False))


def cmd_delete(args, conn):
    print("ok: deleted {} row(s)".format(delete_record(conn, args.id)))


def cmd_list(args, conn):
    rows = list_entries(conn, from_=args.from_, to=args.to, category=args.category,
                        member=args.member, kind=args.kind, limit=args.limit)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
        return
    if not rows:
        print("(empty)")
        return
    print("{} 笔，合计 {:.2f} 元".format(len(rows), sum(r["amount_cents"] for r in rows) / 100))
    for rec in rows:
        who = "（{}）".format(rec.get("member") or "本人") if rec.get("member") else ""
        print("  #{} {} {}{} {:.2f} 元  [{}] {}".format(
            rec["id"], rec["date"], rec["category"], who,
            rec["amount_cents"] / 100, rec["kind"], rec["note"]))


def cmd_categories(args, conn):
    fam_id = ensure_seed(conn)["family_id"]
    if args.add:
        cid = add_category(conn, fam_id, args.add)
        print("ok: category #{} 已新增（手动新建；LLM 不做自动扩展）".format(cid))
        return
    if args.rename and args.to:
        row = conn.execute("SELECT id FROM categories WHERE family_id=? AND name=?",
                           (fam_id, args.rename)).fetchone()
        if row is None:
            raise LedgerError("分类不存在：{}".format(args.rename))
        rename_category(conn, fam_id, row["id"], args.to)
        print("ok: {} -> {}".format(args.rename, args.to))
        return
    if args.delete:
        delete_category(conn, fam_id, args.delete)
        print("ok: 已删除（软归档）：{}".format(args.delete))
        return
    if args.json:
        print(json.dumps(list_categories(conn, fam_id), ensure_ascii=False))
        return
    for c in list_categories(conn, fam_id):
        mark = "系统" if c["is_system"] else "自定义"
        print("#{} {}  [{}]".format(c["id"], c["name"], mark))


def cmd_members(args, conn):
    fam_id = ensure_seed(conn)["family_id"]
    if args.add:
        mid = add_member(conn, fam_id, args.add, args.relation)
        print("ok: member #{} {} 已加入（AI 可识别）".format(mid, args.add))
        return
    if args.delete:
        delete_member(conn, fam_id, args.delete)
        print("ok: 已移除成员：{}".format(args.delete))
        return
    if args.json:
        print(json.dumps(list_members(conn, fam_id), ensure_ascii=False))
        return
    for m in list_members(conn, fam_id):
        rel = "（{}）".format(m["relation"]) if m.get("relation") else ""
        print("#{} {}{}".format(m["id"], m["name"], rel))


def cmd_drafts(args, conn):
    fam_id = ensure_seed(conn)["family_id"]
    if args.add:
        did = add_draft(conn, fam_id, json.loads(args.add))
        print("ok: draft #{}（24h 过期）".format(did))
        return
    if args.delete is not None:
        delete_draft(conn, fam_id, args.delete)
        print("ok: draft #{} 已删除".format(args.delete))
        return
    if args.json:
        print(json.dumps(list_drafts(conn, fam_id), ensure_ascii=False))
        return
    ds = list_drafts(conn, fam_id)
    print("{} 个草稿".format(len(ds)))
    for d in ds:
        print("#{} {} {}".format(d["id"], d["expired_at"], json.dumps(d["data"], ensure_ascii=False)))


def cmd_summary(args, conn):
    d = summary_data(conn, from_=args.from_, to=args.to, category=args.category,
                     member=args.member, kind=args.kind or "expense")
    if args.json:
        print(json.dumps(d, ensure_ascii=False))
        return
    if not d["count"]:
        print("期间无记录（{} ~ {}）".format(d["from"] or "?", d["to"] or "?"))
        return
    print("共 {} 笔，支出合计 {:.2f} 元".format(d["count"], d["total_cents"] / 100))
    for cat, a in sorted(d["by_category"].items(), key=lambda kv: -kv[1]["total_cents"]):
        print("  {}: {:.2f} 元（{}笔）".format(cat, a["total_cents"] / 100, a["count"]))


def cmd_weekly(args, conn):
    d = weekly_data(conn)
    if args.json:
        print(json.dumps(d, ensure_ascii=False, default=str))
        return
    print("本周 {} ~ {}：{} 笔，合计 {:.2f} 元".format(
        d["week_start"], d["week_end"], d["count"], d["total_cents"] / 100))
    if not d["count"]:
        print("（无记录）")
        return
    for cat, a in sorted(d["by_category"].items(), key=lambda kv: -kv[1]["total_cents"]):
        print("  {}: {:.2f} 元（{}笔）".format(cat, a["total_cents"] / 100, a["count"]))


def main():
    p = argparse.ArgumentParser(prog="ledger", description="wuage-home 家庭财务 v2")
    sub = p.add_subparsers(dest="cmd")
    sp = sub.add_parser("init", help="建表 + 种子"); sp.set_defaults(fn=cmd_init)
    sp = sub.add_parser("migrate", help="旧账迁移"); sp.set_defaults(fn=cmd_migrate)
    sp = sub.add_parser("add", help="记一笔")
    sp.add_argument("--json"); sp.add_argument("--amount-cents", type=int)
    sp.add_argument("--date"); sp.add_argument("--kind", choices=sorted(KINDS), default="expense")
    sp.add_argument("--category"); sp.add_argument("--member"); sp.add_argument("--merchant")
    sp.add_argument("--tags", nargs="*"); sp.add_argument("--note")
    sp.add_argument("--channel", choices=sorted(CHANNELS), default="manual")
    sp.set_defaults(fn=cmd_add)
    sp = sub.add_parser("update", help="改一笔")
    sp.add_argument("--id", type=int, required=True); sp.add_argument("--category")
    sp.add_argument("--member"); sp.add_argument("--tags", nargs="*"); sp.add_argument("--note")
    sp.add_argument("--date"); sp.add_argument("--kind", choices=sorted(KINDS))
    sp.add_argument("--amount-cents", type=int); sp.add_argument("--merchant")
    sp.set_defaults(fn=cmd_update)
    sp = sub.add_parser("delete", help="删一笔"); sp.add_argument("--id", type=int, required=True)
    sp.set_defaults(fn=cmd_delete)
    sp = sub.add_parser("list", help="查账")
    sp.add_argument("--from", dest="from_"); sp.add_argument("--to"); sp.add_argument("--category")
    sp.add_argument("--member"); sp.add_argument("--kind", choices=sorted(KINDS))
    sp.add_argument("--limit", type=int); sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_list)
    sp = sub.add_parser("categories", help="分类管理（LLM 不自动建类）")
    sp.add_argument("--add"); sp.add_argument("--rename"); sp.add_argument("--to")
    sp.add_argument("--delete"); sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_categories)
    sp = sub.add_parser("members", help="家庭成员")
    sp.add_argument("--add"); sp.add_argument("--relation"); sp.add_argument("--delete")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_members)
    sp = sub.add_parser("drafts", help="AI 草稿（24h）")
    sp.add_argument("--add"); sp.add_argument("--delete", type=int); sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_drafts)
    sp = sub.add_parser("summary", help="区间汇总")
    sp.add_argument("--from", dest="from_"); sp.add_argument("--to"); sp.add_argument("--category")
    sp.add_argument("--member"); sp.add_argument("--kind", choices=sorted(KINDS))
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_summary)
    sp = sub.add_parser("weekly", help="本周汇总"); sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_weekly)
    args = p.parse_args()
    if not hasattr(args, "fn"):
        p.print_help()
        sys.exit(2)
    conn = connect()
    try:
        args.fn(args, conn)
    except LedgerError as e:
        print("error: {}".format(e))
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()