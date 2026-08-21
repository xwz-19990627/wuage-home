#!/usr/bin/env python3
"""wuage-home ledger — 家庭账本数据层 + CLI（纯标准库，无第三方依赖）。

数据目录：$WUAGE_DATA，默认 <repo>/data —— 迁移 = 拷走该目录。
分类：9 项固定大类（餐饮/交通/居住/购物/娱乐/育儿教育/医疗健康/人情往来/其他）。
金额一律以"分"存储，避免浮点误差。

CLI 用法示例：
  ledger.py init
  ledger.py add --json '{"amount_cents":1000,"category":"餐饮","note":"今天买西瓜花了10块"}'
  ledger.py list --json
  ledger.py summary --from 2026-08-18 --to 2026-08-24
  ledger.py weekly --json
  ledger.py update --id 1 --category 餐饮
  ledger.py categories

数据函数（供 web.py 等复用）：add_record / update_record / delete_record /
list_entries / summary_data / weekly_data，异常统一抛 LedgerError。
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

CANONICAL_CATEGORIES = [
    "餐饮", "交通", "居住", "购物", "娱乐",
    "育儿教育", "医疗健康", "人情往来", "其他",
]
KINDS = {"expense", "income", "transfer"}
SOURCES = {"manual", "voice", "wechat", "import"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
  id           INTEGER PRIMARY KEY,
  amount_cents INTEGER NOT NULL,
  date         TEXT NOT NULL,              -- YYYY-MM-DD
  kind         TEXT NOT NULL DEFAULT 'expense',
  category     TEXT NOT NULL,
  tags         TEXT NOT NULL DEFAULT '[]', -- JSON array（预留，MVP 恒空）
  note         TEXT NOT NULL DEFAULT '',
  source       TEXT NOT NULL DEFAULT 'manual',
  member       TEXT,                       -- 预留：后期按设备区分
  created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_date ON ledger(date);
CREATE INDEX IF NOT EXISTS idx_ledger_category ON ledger(category);
"""


class LedgerError(Exception):
    """业务/校验错误，CLI 与 Web 统一捕获。"""


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn):
    conn.executescript(SCHEMA)
    conn.commit()


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
    try:
        d["tags"] = json.loads(d["tags"] or "[]")
    except (json.JSONDecodeError, TypeError):
        d["tags"] = []
    return d


# ── 数据函数（CLI 与 Web 共用）──────────────────────────────────────────────

def add_record(conn, record):
    """record: {amount_cents, date?, kind?, category, tags?, note?, source?, member?}
    返回入库后的行 dict；校验失败抛 LedgerError。"""
    init_db(conn)
    if not isinstance(record, dict):
        raise LedgerError("record must be an object")
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
    category = record.get("category")
    if category is None:
        raise LedgerError("category required (9 项固定清单之一)")
    if category not in CANONICAL_CATEGORIES:
        raise LedgerError("category {!r} not in canonical list: {}".format(
            category, CANONICAL_CATEGORIES))
    date_s = parse_date(record.get("date"))
    source = record.get("source") or "manual"
    if source not in SOURCES:
        raise LedgerError("source must be in {}".format(sorted(SOURCES)))
    tags = record.get("tags") if "tags" in record else []
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise LedgerError("tags must be a list of strings")
    note = (record.get("note") or "").strip()
    member = record.get("member")
    now = datetime.datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        """INSERT INTO ledger
           (amount_cents, date, kind, category, tags, note, source, member, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (amount, date_s, kind, category, json.dumps(tags, ensure_ascii=False),
         note, source, member, now),
    )
    conn.commit()
    rid = cur.lastrowid
    return row_to_dict(conn.execute(
        "SELECT * FROM ledger WHERE id=?", (rid,)).fetchone())


def update_record(conn, rid, fields):
    """fields: {category?, note?, date?, kind?, source?, member?, tags?, amount_cents?}
    返回更新后的行 dict；无此行或参数非法抛 LedgerError。"""
    init_db(conn)
    row = conn.execute("SELECT * FROM ledger WHERE id=?", (rid,)).fetchone()
    if row is None:
        raise LedgerError("no ledger row with id={}".format(rid))
    sets, params = [], []
    for field, val in fields.items():
        if val is None:
            continue
        if field == "category":
            if val not in CANONICAL_CATEGORIES:
                raise LedgerError("category {!r} not in canonical list: {}".format(
                    val, CANONICAL_CATEGORIES))
        elif field == "kind":
            if val not in KINDS:
                raise LedgerError("kind must be in {}".format(sorted(KINDS)))
        elif field == "source":
            if val not in SOURCES:
                raise LedgerError("source must be in {}".format(sorted(SOURCES)))
        elif field == "date":
            val = parse_date(val)
        elif field == "tags":
            if not isinstance(val, list) or not all(isinstance(t, str) for t in val):
                raise LedgerError("tags must be a list of strings")
            val = json.dumps(val, ensure_ascii=False)
        elif field == "amount_cents":
            try:
                val = int(val)
            except (TypeError, ValueError):
                raise LedgerError("amount_cents must be int")
        elif field == "member":
            if val == "":
                val = None
        elif field != "note":
            raise LedgerError("unknown field {!r}".format(field))
        if field == "note":
            val = (val or "").strip()
        sets.append("{} = ?".format(field))
        params.append(val)
    if not sets:
        raise LedgerError("nothing to update")
    params.append(rid)
    conn.execute("UPDATE ledger SET {} WHERE id=?".format(", ".join(sets)), params)
    conn.commit()
    return row_to_dict(conn.execute(
        "SELECT * FROM ledger WHERE id=?", (rid,)).fetchone())


def delete_record(conn, rid):
    init_db(conn)
    cur = conn.execute("DELETE FROM ledger WHERE id=?", (rid,))
    conn.commit()
    return cur.rowcount


def list_entries(conn, from_=None, to=None, category=None, kind=None, limit=None):
    init_db(conn)
    where, params = [], []
    if from_:
        where.append("date>=?"); params.append(from_)
    if to:
        where.append("date<=?"); params.append(to)
    if category:
        where.append("category=?"); params.append(category)
    if kind:
        where.append("kind=?"); params.append(kind)
    sql = "SELECT * FROM ledger"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY date DESC, id DESC"
    if limit:
        sql += " LIMIT {}".format(int(limit))
    return [row_to_dict(r) for r in conn.execute(sql, params).fetchall()]


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
    return {k: {kk: vv for kk, vv in v.items() if kk != "top_notes"}
            for k, v in agg.items()}


def summary_data(conn, from_=None, to=None, category=None, kind=None):
    rows = list_entries(conn, from_=from_, to=to, category=category, kind=kind)
    agg = _aggregate(rows)
    return {
        "from": from_, "to": to, "count": len(rows),
        "total_cents": sum(r["amount_cents"] for r in rows),
        "by_category": _by_category_json(agg),
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


# ── CLI 包装 ─────────────────────────────────────────────────────────────────

def cmd_init(args, conn):
    init_db(conn)
    print("ok: ledger db ready at {}".format(DB_PATH))


def cmd_add(args, conn):
    if args.json:
        record = json.loads(args.json)
        if not isinstance(record, dict):
            raise LedgerError("--json must be an object")
    else:
        record = {
            "amount_cents": args.amount_cents, "date": args.date,
            "kind": args.kind, "category": args.category,
            "tags": args.tags or [], "note": args.note, "source": args.source,
        }
    print(json.dumps(add_record(conn, record), ensure_ascii=False))


def cmd_update(args, conn):
    fields = {
        "category": args.category, "note": args.note, "date": args.date,
        "kind": args.kind, "source": args.source, "member": args.member,
    }
    if args.tags is not None:
        fields["tags"] = args.tags
    print(json.dumps(update_record(conn, args.id, fields), ensure_ascii=False))


def cmd_delete(args, conn):
    n = delete_record(conn, args.id)
    print("ok: deleted {} row(s)".format(n))


def cmd_list(args, conn):
    rows = list_entries(conn, from_=args.from_, to=args.to,
                        category=args.category, kind=args.kind, limit=args.limit)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
        return
    if not rows:
        print("(empty)")
        return
    print("{} 笔，合计 {:.2f} 元".format(len(rows),
          sum(r["amount_cents"] for r in rows) / 100))
    for r in rows:
        tag = " / ".join(r["tags"]) if r["tags"] else ""
        print("  #{} {} {}{} {:.2f} 元  [{}] {}".format(
            r["id"], r["date"], r["category"], (" " + tag) if tag else "",
            r["amount_cents"] / 100, r["kind"], r["note"]))


def cmd_categories(args, conn):
    for i, c in enumerate(CANONICAL_CATEGORIES, 1):
        print("{}. {}".format(i, c))


def cmd_summary(args, conn):
    d = summary_data(conn, from_=args.from_, to=args.to,
                     category=args.category, kind=args.kind)
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
    p = argparse.ArgumentParser(prog="ledger", description="wuage-home 家庭账本")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("init", help="初始化数据库")
    sp.set_defaults(fn=cmd_init)

    sp = sub.add_parser("add", help="记一笔（优先用 --json 传解析结果）")
    sp.add_argument("--json", help="JSON 对象: amount_cents/date/kind/category/tags/note/source/member")
    sp.add_argument("--amount-cents", type=int)
    sp.add_argument("--date")
    sp.add_argument("--kind", choices=sorted(KINDS), default="expense")
    sp.add_argument("--category")
    sp.add_argument("--tags", nargs="*")
    sp.add_argument("--note")
    sp.add_argument("--source", choices=sorted(SOURCES), default="manual")
    sp.set_defaults(fn=cmd_add)

    sp = sub.add_parser("update", help="改一笔（如：改成下馆子 = update --id N --category 餐饮）")
    sp.add_argument("--id", type=int, required=True)
    sp.add_argument("--category")
    sp.add_argument("--tags", nargs="*")
    sp.add_argument("--note")
    sp.add_argument("--date")
    sp.add_argument("--kind", choices=sorted(KINDS))
    sp.add_argument("--source", choices=sorted(SOURCES))
    sp.add_argument("--member")
    sp.set_defaults(fn=cmd_update)

    sp = sub.add_parser("delete", help="删一笔")
    sp.add_argument("--id", type=int, required=True)
    sp.set_defaults(fn=cmd_delete)

    sp = sub.add_parser("list", help="查账")
    sp.add_argument("--from", dest="from_")
    sp.add_argument("--to")
    sp.add_argument("--category")
    sp.add_argument("--kind", choices=sorted(KINDS))
    sp.add_argument("--limit", type=int)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_list)

    sp = sub.add_parser("categories", help="打印 9 项固定类别")
    sp.set_defaults(fn=cmd_categories)

    sp = sub.add_parser("summary", help="区间汇总（按类别）")
    sp.add_argument("--from", dest="from_")
    sp.add_argument("--to")
    sp.add_argument("--category")
    sp.add_argument("--kind", choices=sorted(KINDS))
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_summary)

    sp = sub.add_parser("weekly", help="本周汇总（周一 0 点起）")
    sp.add_argument("--json", action="store_true")
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
