#!/usr/bin/env python3
"""wuage-home ledger — 家庭账本 CLI（纯标准库，无第三方依赖）。

数据目录：$WUAGE_DATA，默认 <repo>/data —— 迁移 = 拷走该目录。
分类：9 项固定大类（餐饮/交通/居住/购物/娱乐/育儿教育/医疗健康/人情往来/其他）。
金额一律以"分"存储，避免浮点误差。

用法示例：
  ledger.py init
  ledger.py add --json '{"amount_cents":1000,"category":"餐饮","note":"今天买西瓜花了10块"}'
  ledger.py list --json
  ledger.py summary --from 2026-08-18 --to 2026-08-24
  ledger.py weekly --json
  ledger.py update --id 1 --category 下馆子   # 注意：category 必须来自清单
  ledger.py categories
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
        raise SystemExit(f"error: bad date {s!r}, use YYYY-MM-DD")


def row_to_dict(r):
    d = dict(r)
    try:
        d["tags"] = json.loads(d["tags"] or "[]")
    except (json.JSONDecodeError, TypeError):
        d["tags"] = []
    return d


def cmd_init(args, conn):
    init_db(conn)
    print(f"ok: ledger db ready at {DB_PATH}")


def cmd_add(args, conn):
    init_db(conn)
    record = {}
    if args.json:
        record = json.loads(args.json)
        if not isinstance(record, dict):
            raise SystemExit("error: --json must be an object")
    amount = record.get("amount_cents", args.amount_cents)
    if amount is None:
        raise SystemExit("error: amount_cents required (单位:分)")
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        raise SystemExit(f"error: amount_cents must be int, got {amount!r}")
    if args.kind and args.kind not in KINDS:
        raise SystemExit(f"error: kind must be in {sorted(KINDS)}")
    category = record.get("category") or args.category
    if category is None:
        raise SystemExit("error: category required (9 项固定清单之一)")
    if category not in CANONICAL_CATEGORIES:
        raise SystemExit(
            f"error: category {category!r} not in canonical list: {CANONICAL_CATEGORIES}"
        )
    note = record.get("note") if record.get("note") is not None else (args.note or "")
    date_s = parse_date(record.get("date") or args.date)
    kind = record.get("kind") or args.kind or "expense"
    source = record.get("source") or args.source or "manual"
    if source not in SOURCES:
        raise SystemExit(f"error: source must be in {sorted(SOURCES)}")
    tags = record.get("tags") if "tags" in record else []
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise SystemExit("error: tags must be a list of strings")
    member = record.get("member")
    cur = conn.execute(
        """INSERT INTO ledger
           (amount_cents, date, kind, category, tags, note, source, member, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            amount, date_s, kind, category, json.dumps(tags, ensure_ascii=False),
            note, source, member,
            datetime.datetime.now().isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    rid = cur.lastrowid
    print(json.dumps(row_to_dict(conn.execute(
        "SELECT * FROM ledger WHERE id=?", (rid,)).fetchone()),
        ensure_ascii=False))


def cmd_update(args, conn):
    init_db(conn)
    row = conn.execute("SELECT * FROM ledger WHERE id=?", (args.id,)).fetchone()
    if row is None:
        raise SystemExit(f"error: no ledger row with id={args.id}")
    sets, params = [], []
    for field, val in (("category", args.category), ("note", args.note),
                       ("date", args.date), ("kind", args.kind),
                       ("source", args.source), ("member", args.member)):
        if val is not None:
            if field == "category" and val not in CANONICAL_CATEGORIES:
                raise SystemExit(f"error: category {val!r} not in canonical list: {CANONICAL_CATEGORIES}")
            if field == "kind" and val not in KINDS:
                raise SystemExit(f"error: kind must be in {sorted(KINDS)}")
            if field == "date":
                val = parse_date(val)
            if field == "source" and val not in SOURCES:
                raise SystemExit(f"error: source must be in {sorted(SOURCES)}")
            if field == "member" and val == "":
                val = None
            sets.append(f"{field}=?")
            params.append(val)
    if args.tags is not None:
        sets.append("tags=?")
        params.append(json.dumps(args.tags, ensure_ascii=False))
    if not sets:
        raise SystemExit("error: nothing to update")
    params.append(args.id)
    conn.execute(f"UPDATE ledger SET {', '.join(sets)} WHERE id=?", params)
    conn.commit()
    print(json.dumps(row_to_dict(conn.execute(
        "SELECT * FROM ledger WHERE id=?", (args.id,)).fetchone()),
        ensure_ascii=False))


def cmd_delete(args, conn):
    init_db(conn)
    cur = conn.execute("DELETE FROM ledger WHERE id=?", (args.id,))
    conn.commit()
    print(f"ok: deleted {cur.rowcount} row(s)")



def _query_clause(args):
    where, params = [], []
    if getattr(args, "from_", None):
        where.append("date>=?"); params.append(args.from_)
    if getattr(args, "to", None):
        where.append("date<=?"); params.append(args.to)
    if getattr(args, "category", None):
        where.append("category=?"); params.append(args.category)
    if getattr(args, "kind", None):
        where.append("kind=?"); params.append(args.kind)
    sql = "SELECT * FROM ledger"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY date DESC, id DESC"
    return sql, params


def cmd_list(args, conn):
    init_db(conn)
    sql, params = _query_clause(args)
    if getattr(args, "limit", None):
        sql += f" LIMIT {int(args.limit)}"
    rows = [row_to_dict(r) for r in conn.execute(sql, params).fetchall()]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
        return
    if not rows:
        print("(empty)")
        return
    total_cents = sum(r["amount_cents"] for r in rows)
    print(f"{len(rows)} 笔，合计 {total_cents/100:.2f} 元")
    for r in rows:
        tag = " / ".join(r["tags"]) if r["tags"] else ""
        print(f"  #{r['id']} {r['date']} {r['category']}{' ' + tag if tag else ''} "
              f"{r['amount_cents']/100:.2f} 元  [{r['kind']}] {r['note']}")


def cmd_categories(args, conn):
    for i, c in enumerate(CANONICAL_CATEGORIES, 1):
        print(f"{i}. {c}")


def _aggregate(rows):
    """rows -> {category: {count, total_cents, notes[]}}，note 按频率取前 3。"""
    from collections import Counter
    agg = {}
    for r in rows:
        a = agg.setdefault(r["category"], {"count": 0, "total_cents": 0, "top_notes": []})
        a["count"] += 1
        a["total_cents"] += r["amount_cents"]
    # compute top notes per category
    for cat, a in agg.items():
        notes = [r["note"] for r in rows if r["category"] == cat and r["note"]]
        a["top_notes"] = [n for n, _ in Counter(notes).most_common(3)]
    return agg


def cmd_summary(args, conn):
    init_db(conn)
    sql, params = _query_clause(args)
    rows = [row_to_dict(r) for r in conn.execute(sql, params).fetchall()]
    agg = _aggregate(rows)
    total = sum(r["amount_cents"] for r in rows)
    if args.json:
        print(json.dumps({
            "from": getattr(args, "from_", None), "to": getattr(args, "to", None),
            "total_cents": total, "count": len(rows),
            "by_category": {k: {kk: vv for kk, vv in v.items() if kk != "top_notes"}
                            for k, v in agg.items()},
        }, ensure_ascii=False))
        return
    if not rows:
        print(f"期间无记录（{getattr(args, 'from_', '?')} ~ {getattr(args, 'to', '?')}）")
        return
    print(f"共 {len(rows)} 笔，支出合计 {total/100:.2f} 元")
    for cat, a in sorted(agg.items(), key=lambda kv: -kv[1]["total_cents"]):
        notes = "、".join(a["top_notes"]) if a["top_notes"] else "-"
        print(f"  {cat}: {a['total_cents']/100:.2f} 元（{a['count']}笔） 常记: {notes}")


def cmd_weekly(args, conn):
    init_db(conn)
    now = datetime.date.today()
    start = now - datetime.timedelta(days=now.weekday())
    end = start + datetime.timedelta(days=6)
    sql = "SELECT * FROM ledger WHERE date>=? AND date<=? ORDER BY date DESC, id DESC"
    rows = [row_to_dict(r) for r in conn.execute(sql, (start.isoformat(), end.isoformat())).fetchall()]
    total = sum(r["amount_cents"] for r in rows)
    agg = _aggregate(rows)
    if args.json:
        print(json.dumps({
            "week_start": start.isoformat(), "week_end": end.isoformat(),
            "total_cents": total, "count": len(rows),
            "by_category": {k: {kk: vv for kk, vv in v.items() if kk != "top_notes"}
                            for k, v in agg.items()},
            "entries": rows,
        }, ensure_ascii=False, default=str))
        return
    print(f"本周 {start.isoformat()} ~ {end.isoformat()}：{len(rows)} 笔，合计 {total/100:.2f} 元")
    if not rows:
        print("（无记录）")
        return
    for cat, a in sorted(agg.items(), key=lambda kv: -kv[1]["total_cents"]):
        notes = "、".join(a["top_notes"]) if a["top_notes"] else "-"
        print(f"  {cat}: {a['total_cents']/100:.2f} 元（{a['count']}笔） 常记: {notes}")


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
    finally:
        conn.close()


if __name__ == "__main__":
    main()