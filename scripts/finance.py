#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wuage-home 工具 02 · 理财助手（基金为主的学习型理财工具）。

功能：
  - 持仓管理：基金/ETF 买入、卖出、定投，自动计算成本/市值/收益
  - 自选列表：场外基金按最新净值、ETF 按当日行情
  - 每日快照：收盘后拉取净值/行情落库，支持每日复盘
  - 自研回测：定投（DCA）/ 均线交叉（MA）/ RSI 三套简单策略
  - 策略/指标收藏
  - Web 问答 → LLM 回答 → 自动写入笔记（可检索）

设计：
  - 纯 Python 标准库（零第三方依赖），风格与 scripts/filebox.py 一致。
  - 数据落 $WUAGE_DATA/finance/finance.db，迁移 = 拷目录。
  - 页面 web/finance.html；经公网网关 /home/finance/ 前缀转发后相对路径不变。

用法：
  python3.11 scripts/finance.py                        # 127.0.0.1:17625
  python3.11 scripts/finance.py --port 17625
  python3.11 scripts/finance.py --init                 # 只初始化数据库
  python3.11 scripts/finance.py --refresh              # 手动拉一次今日快照后退出
  python3.11 scripts/finance.py --backtest-demo        # 跑一次示例回测

环境变量：
  FINANCE_HOST / FINANCE_PORT / FINANCE_DATA
  WUAGE_PARSE_API / WUAGE_PARSE_MODEL（问答/复盘走与记账相同的 OpenCode Go 网关）
"""

import argparse
import datetime
import json
import math
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from bisect import bisect_right
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _resolve_data_dir():
    """数据目录解析：WUAGE_DATA（模块数据目录）> WUAGE_HOME/data > 迁移后默认。"""
    d = os.environ.get("WUAGE_DATA") or ""
    if d.strip():
        return Path(d)
    h = os.environ.get("WUAGE_HOME") or ""
    if h.strip():
        return Path(h) / "data"
    return Path("/root/wuage/data")


WUAGE_DATA = _resolve_data_dir()
FINANCE_DATA = Path(os.environ.get("FINANCE_DATA", str(WUAGE_DATA / "finance")))
DB_PATH = FINANCE_DATA / "finance.db"
WEB_PATH = REPO_ROOT / "web" / "finance.html"
MAX_BODY = 1 << 20

API_URL = os.environ.get("WUAGE_PARSE_API", "https://opencode.ai/zen/go/v1/chat/completions")
MODEL = os.environ.get("WUAGE_PARSE_MODEL", "deepseek-v4-flash")
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# ---------------------------------------------------------------- 基础工具

def _json_default(o):
    if isinstance(o, (datetime.date, datetime.datetime)):
        return o.isoformat()
    raise TypeError(type(o))


def _send_json(resp, obj, status=200):
    body = json.dumps(obj, ensure_ascii=False, default=_json_default).encode("utf-8")
    resp.send_response(status)
    resp.send_header("Content-Type", "application/json; charset=utf-8")
    resp.send_header("Content-Length", str(len(body)))
    resp.send_header("Cache-Control", "no-store")
    resp.end_headers()
    resp.wfile.write(body)


def _send_text(resp, text, status=200, content_type="text/plain; charset=utf-8"):
    body = text.encode("utf-8")
    resp.send_response(status)
    resp.send_header("Content-Type", content_type)
    resp.send_header("Content-Length", str(len(body)))
    resp.end_headers()
    resp.wfile.write(body)


def _read_payload(resp):
    length = int(resp.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    if length > MAX_BODY:
        raise ValueError("body too large")
    raw = resp.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def _http_get(url, headers=None, decode="utf-8", timeout=20):
    """GET 并返回解码后的文本；自动处理 gzip。"""
    req = urllib.request.Request(url, headers=headers or {"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            import gzip
            data = gzip.decompress(data)
        return data.decode(decode, errors="replace")


def _http_get_json(url, headers=None, timeout=20):
    return json.loads(_http_get(url, headers=headers, timeout=timeout))


def _today():
    return datetime.date.today().isoformat()


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _parse_date(s):
    if not s:
        return None
    try:
        return datetime.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None

# ---------------------------------------------------------------- 数据层

def connect():
    FINANCE_DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        name TEXT NOT NULL DEFAULT '',
        asset_type TEXT NOT NULL DEFAULT 'fund' CHECK(asset_type IN ('fund','etf')),
        direction TEXT NOT NULL CHECK(direction IN ('buy','sell')),
        trade_date TEXT NOT NULL,
        shares REAL NOT NULL DEFAULT 0,
        price REAL NOT NULL DEFAULT 0,
        amount_cents INTEGER NOT NULL DEFAULT 0,
        fee_cents INTEGER NOT NULL DEFAULT 0,
        source TEXT NOT NULL DEFAULT 'manual',
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_tx_symbol ON transactions(symbol);
    CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(trade_date);

    CREATE TABLE IF NOT EXISTS watchlist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL DEFAULT '',
        asset_type TEXT NOT NULL DEFAULT 'fund' CHECK(asset_type IN ('fund','etf')),
        category TEXT NOT NULL DEFAULT '',
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS daily_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        asset_type TEXT NOT NULL DEFAULT 'fund',
        trade_date TEXT NOT NULL,
        open REAL,
        high REAL,
        low REAL,
        close REAL,
        pre_close REAL,
        change_pct REAL,
        volume REAL,
        amount REAL,
        nav REAL,
        source TEXT NOT NULL DEFAULT '',
        fetched_at TEXT NOT NULL,
        UNIQUE(symbol, trade_date)
    );

    CREATE TABLE IF NOT EXISTS strategies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        rules_json TEXT NOT NULL DEFAULT '{}',
        tags TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS indicators (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT '',
        summary TEXT NOT NULL DEFAULT '',
        detail TEXT NOT NULL DEFAULT '',
        formula TEXT NOT NULL DEFAULT '',
        tags TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        question TEXT NOT NULL,
        answer TEXT NOT NULL,
        tags TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT 'ask',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL DEFAULT 'fact',
        topic TEXT NOT NULL DEFAULT '',
        content TEXT NOT NULL,
        importance TEXT NOT NULL DEFAULT 'medium',
        source TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_fin_mem ON memories(importance, updated_at);

    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """)
    conn.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version','1')")
    conn.commit()


def add_memory(kind="fact", topic="", content="", importance="medium", source=""):
    if not content or not content.strip():
        raise ValueError("memory content 不能为空")
    kind = (kind or "fact").strip().lower()
    if kind not in ("fact", "preference", "digest"):
        kind = "fact"
    imp = (importance or "medium").strip().lower()
    if imp not in ("high", "medium", "low"):
        imp = "medium"
    conn = connect()
    now = _now()
    cur = conn.execute(
        "INSERT INTO memories (kind, topic, content, importance, source, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (kind, (topic or "").strip(), content.strip(), imp, source, now, now))
    conn.commit()
    conn.close()
    return cur.lastrowid


def list_memories(limit=50, topic=None):
    conn = connect()
    where, params = [], []
    if topic:
        where.append("topic LIKE ?")
        params.append("%" + topic + "%")
    sql = ("SELECT * FROM memories" + (" WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY CASE importance WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,"
             " updated_at DESC LIMIT ?")
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def normalize_symbol(symbol, asset_type=None):
    s = str(symbol or "").strip().lower()
    if not s:
        return ""
    if s.startswith(("sh", "sz")):
        return s
    if s.isdigit() and len(s) == 6:
        # 场外基金用纯数字代码；场内 ETF/个股按交易所加前缀
        if asset_type == "fund":
            return s
        return ("sh" if s[0] in "569" else "sz") + s
    return s


def infer_asset_type(symbol, asset_type=None):
    if asset_type in ("fund", "etf"):
        return asset_type
    s = str(symbol or "").strip().lower()
    # 纯 6 位数字且首位 1/5 的常见是场内 ETF/LOF；没有显式类型时默认当基金（场外/场内外均可由用户修正）
    return "fund"


def add_transaction(conn, payload):
    symbol = normalize_symbol(payload.get("symbol"), payload.get("asset_type"))
    if not symbol:
        raise ValueError("缺少 symbol")
    asset_type = infer_asset_type(symbol, payload.get("asset_type"))
    direction = str(payload.get("direction") or "buy").strip().lower()
    if direction in ("买入", "买"):
        direction = "buy"
    if direction in ("卖出", "卖"):
        direction = "sell"
    if direction not in ("buy", "sell"):
        raise ValueError("direction 必须是 buy/sell")
    trade_date = str(payload.get("trade_date") or _today())
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", trade_date):
        raise ValueError("trade_date 格式应为 YYYY-MM-DD")
    shares = float(payload.get("shares") or 0)
    price = float(payload.get("price") or 0)
    if shares <= 0 or price <= 0:
        raise ValueError("shares/price 必须大于 0")
    amount_cents = int(round(float(payload.get("amount_cents") or shares * price * 100)))
    fee_cents = int(round(float(payload.get("fee_cents") or 0)))
    name = str(payload.get("name") or q_attr(symbol, conn) or "").strip()
    cur = conn.execute(
        """INSERT INTO transactions
           (symbol,name,asset_type,direction,trade_date,shares,price,amount_cents,fee_cents,source,note,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (symbol, name, asset_type, direction, trade_date, shares, price,
         amount_cents, fee_cents, "manual", str(payload.get("note") or ""), _now()),
    )
    conn.commit()
    return cur.lastrowid


def q_attr(symbol, conn=None):
    """取 watchlist/snapshot 里已知的基金名，避免每次手工输入。"""
    own = conn
    if own is None:
        own = connect()
    row = own.execute("SELECT name FROM watchlist WHERE symbol=?", (symbol,)).fetchone()
    if row and row["name"]:
        return row["name"]
    row = own.execute(
        "SELECT name FROM transactions WHERE symbol=? ORDER BY id DESC LIMIT 1",
        (symbol,)).fetchone()
    if row:
        return row["name"]
    return ""


def list_transactions(conn, symbol=None):
    if symbol:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE symbol=? ORDER BY trade_date,id", (symbol,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM transactions ORDER BY trade_date DESC,id DESC").fetchall()
    return [dict(r) for r in rows]


def calc_positions(conn):
    """从交易流水实时计算当前持仓。"""
    rows = conn.execute(
        """SELECT symbol,name,asset_type,direction,shares,price,amount_cents,fee_cents
           FROM transactions ORDER BY trade_date,id""").fetchall()
    pos = {}
    for r in rows:
        sym = r["symbol"]
        p = pos.setdefault(sym, {
            "symbol": sym,
            "name": r["name"] or "",
            "asset_type": r["asset_type"],
            "shares": 0.0,
            "cost_cents": 0,
            "sell_proceeds_cents": 0,
            "realized_pnl_cents": 0,
            "fee_cents": 0,
            "trade_count": 0,
        })
        p["trade_count"] += 1
        p["fee_cents"] += r["fee_cents"]
        if r["direction"] == "buy":
            p["shares"] += r["shares"]
            p["cost_cents"] += r["amount_cents"] + r["fee_cents"]
        else:
            if p["shares"] > 0:
                avg_cost = p["cost_cents"] / p["shares"]
                cost_removed = avg_cost * r["shares"]
                p["cost_cents"] = max(0, int(round(p["cost_cents"] - cost_removed)))
                proceeds = r["amount_cents"] - r["fee_cents"]
                p["realized_pnl_cents"] += int(round(proceeds - cost_removed))
                p["sell_proceeds_cents"] += int(round(proceeds))
            p["shares"] = max(0.0, p["shares"] - r["shares"])

    positions = []
    for sym, p in pos.items():
        last = conn.execute(
            """SELECT * FROM daily_snapshots WHERE symbol=? ORDER BY trade_date DESC LIMIT 1""",
            (sym,)).fetchone()
        price = None
        market_value_cents = None
        if last is not None:
            price = (last["close"] if last["close"] is not None else last["nav"])
            if price:
                market_value_cents = int(round(p["shares"] * price * 100))
        p["last_price"] = price
        p["market_value_cents"] = market_value_cents
        if market_value_cents is not None and p["cost_cents"] > 0:
            p["unrealized_pnl_cents"] = market_value_cents - p["cost_cents"]
            p["unrealized_pnl_pct"] = p["unrealized_pnl_cents"] / p["cost_cents"] * 100
        else:
            p["unrealized_pnl_cents"] = None
            p["unrealized_pnl_pct"] = None
        p["cost_cents"] = int(round(p["cost_cents"]))
        if p["shares"] > 0:
            positions.append(p)
    positions.sort(key=lambda x: (x["asset_type"], x["symbol"]))
    return positions


def add_watchlist(conn, payload):
    symbol = normalize_symbol(payload.get("symbol"), payload.get("asset_type"))
    if not symbol:
        raise ValueError("缺少 symbol")
    asset_type = infer_asset_type(symbol, payload.get("asset_type"))
    name = str(payload.get("name") or q_attr(symbol, conn) or "").strip()
    category = str(payload.get("category") or "").strip()
    cur = conn.execute(
        """INSERT INTO watchlist(symbol,name,asset_type,category,sort_order,created_at)
           VALUES (?,?,?,?,?,?)""",
        (symbol, name, asset_type, category, int(payload.get("sort_order") or 0), _now()),
    )
    conn.commit()
    return cur.lastrowid


def list_watchlist(conn):
    rows = conn.execute(
        "SELECT * FROM watchlist ORDER BY sort_order,asset_type,symbol").fetchall()
    out = []
    for r in rows:
        item = dict(r)
        last = conn.execute(
            """SELECT * FROM daily_snapshots WHERE symbol=? ORDER BY trade_date DESC LIMIT 1""",
            (r["symbol"],)).fetchone()
        if last:
            item["latest"] = dict(last)
        out.append(item)
    return out


def remove_watchlist(conn, wid):
    conn.execute("DELETE FROM watchlist WHERE id=?", (int(wid),))
    conn.commit()


# ---------------------------------------------------------------- 数据源

# ── 行情口径与数据源回退（2026-10-16）────────────────────────────────────
# 复权口径：基金默认用**复权净值**（把分红按当时净值再投资，收益口径等价于累计净值），
# 单位净值在分红/拆分后会凭空跳水，直接拿去回测会低估收益；ETF 默认前复权（qfq）。
PRICE_MODES = {
    "fund": {
        "adj": "复权净值（累计净值口径：分红计入收益，不做再投资复利）",
        "acc": "累计净值（与复权同源，分红累加）",
        "nav": "单位净值（未复权，分红后会低估收益）",
    },
    "etf": {
        "qfq": "前复权（分红/拆分回填到起点前）",
        "hfq": "后复权（起点价保持原值）",
        "raw": "不复权（原始成交价）",
    },
}
DEFAULT_PRICE_MODE = {"fund": "adj", "etf": "qfq"}
FUND_SOURCES = ("eastmoney_pingzhong", "eastmoney_lsjz")
ETF_KLINE_SOURCES = ("tencent", "eastmoney")


def _norm_price_mode(asset_type, mode):
    modes = PRICE_MODES.get(asset_type) or PRICE_MODES["etf"]
    m = (mode or "").strip().lower()
    if m in modes:
        return m
    return DEFAULT_PRICE_MODE.get(asset_type, "qfq")


def _with_adj(rows):
    """给基金序列补一列**复权净值** adj_nav = 累计净值口径（归一化到起点单位净值）。

    即 adj_t = nav_t × (acc_t / nav_t) / (acc_0 / nav_0)，涨跌幅与累计净值一致：
    分红被记进收益（不会像单位净值那样在除息日凭空跳水），但分红部分不计复利。

    ⚠️ 为什么不做"逐日分红再投资"（2026-10-16 实测踩坑）：
    东财 pingzhongdata 的 `Data_ACWorthTrend`（累计净值）在部分老基金上**逐日抖动**——
    实测 163402 有近一半交易日 acc−nav 的差额在**下降**，而累计分红只增不减。
    无论逐日相减还是取运行最大值，这些噪声都会被当成小额分红反复复利：
    直接相减把 20 年序列放大到 1e29（回测收益 1.5e14%），取运行最大值也有 3345 倍。
    在拿到干净的「分红送配明细表」之前，只用累计净值口径：单调、不炸、也是天天基金页面上的标准读法；
    代价是分红不计复利（长期低估幅度通常个位数百分比，远小于单位净值的低估）。
    """
    if not rows:
        return rows
    base = None
    for r in rows:
        nav = float(r.get("nav") or 0)
        acc = r.get("acc_nav")
        acc = float(acc) if acc else 0.0
        if nav <= 0 or acc <= 0:
            r["adj_nav"] = nav
            continue
        ratio = acc / nav
        if base is None:
            base = ratio
        r["adj_nav"] = round(nav * ratio / base, 6)
    return rows


def _fetch_pingzhong(code):
    """主源：fund.eastmoney.com pingzhongdata（单位净值 + 累计净值）。"""
    url = "https://fund.eastmoney.com/pingzhongdata/{}.js".format(code)
    text = _http_get(url, headers={"User-Agent": USER_AGENT,
                                  "Referer": "http://fund.eastmoney.com/"})
    name = ""
    m = re.search(r'fS_name\s*=\s*"([^"]*)"', text)
    if m:
        name = m.group(1)
    m = re.search(r"Data_netWorthTrend\s*=\s*(\[.*?\])\s*;", text, re.S)
    if not m:
        raise ValueError("未找到基金净值数据")
    arr = json.loads(m.group(1))
    acc_map = {}
    m2 = re.search(r"Data_ACWorthTrend\s*=\s*(\[.*?\])\s*;", text, re.S)
    if m2:
        try:
            for item in json.loads(m2.group(1)):
                if isinstance(item, list) and len(item) >= 2:
                    d = datetime.datetime.fromtimestamp(item[0] / 1000).date().isoformat()
                    acc_map[d] = float(item[1])
        except Exception:
            acc_map = {}
    rows = []
    for item in arr:
        ts = item.get("x")
        if ts is None:
            continue
        d = datetime.datetime.fromtimestamp(ts / 1000).date().isoformat()
        rows.append({
            "date": d,
            "nav": float(item.get("y") or 0),
            "acc_nav": acc_map.get(d),
            "change_pct": (float(item.get("equityReturn"))
                           if item.get("equityReturn") is not None else None),
        })
    rows.sort(key=lambda x: x["date"])
    if not rows:
        raise ValueError("净值序列为空")
    return name, rows


def _fetch_lsjz(code, max_pages=25):
    """备源 1：fundf10 历史净值接口（分页，含单位净值与累计净值）。"""
    rows, name = [], ""
    page = 1
    got = 0
    while page <= max_pages:
        url = ("https://api.fund.eastmoney.com/f10/lsjz?fundCode={}&pageIndex={}"
               "&pageSize=49").format(code, page)
        data = _http_get_json(url, headers={
            "User-Agent": USER_AGENT,
            "Referer": "http://fundf10.eastmoney.com/jjjz_{}.html".format(code),
        })
        node = (data or {}).get("Data") or {}
        lst = node.get("LSJZList") or []
        if not name:
            name = node.get("SHORTNAME") or node.get("FUNDNAME") or ""
        if not lst:
            break
        for it in lst:
            d = it.get("FSRQ")
            if not d:
                continue
            try:
                nav = float(it.get("DWJZ") or 0)
            except (TypeError, ValueError):
                continue
            try:
                acc = float(it.get("LJJZ")) if it.get("LJJZ") not in (None, "", "--") else None
            except (TypeError, ValueError):
                acc = None
            try:
                chg = float(it.get("JZZZL")) if it.get("JZZZL") not in (None, "", "--") else None
            except (TypeError, ValueError):
                chg = None
            rows.append({"date": d, "nav": nav, "acc_nav": acc, "change_pct": chg})
        if not lst:
            break
        got += len(lst)
        if len(lst) < 2:            # 只有一条 → 到末尾了
            break
        page += 1
    rows.sort(key=lambda x: x["date"])
    if not rows:
        raise ValueError("历史净值接口没有返回数据")
    return name, rows


def fetch_fund_full(fund_code):
    """取基金全量净值序列（**带数据源回退**）。

    返回 {'name', 'rows': [{date,nav,acc_nav,adj_nav,change_pct}], 'source', 'errors'}
    主源 pingzhongdata（一次拿全）+ 备源 fundf10 分页历史净值。
    """
    code = str(fund_code).strip()
    if not code.isdigit():
        raise ValueError("基金代码必须为数字")
    errors = []
    for src, fn in (("eastmoney_pingzhong", _fetch_pingzhong),
                    ("eastmoney_lsjz", _fetch_lsjz)):
        try:
            name, rows = fn(code)
            return {"name": name, "rows": _with_adj(rows), "source": src, "errors": errors}
        except Exception as e:                                  # noqa: BLE001
            errors.append("{}: {}".format(src, e))
    raise ValueError("基金数据源全部失败 —— " + "；".join(errors))


def fetch_fund_latest(fund_code):
    data = fetch_fund_full(fund_code)
    if not data["rows"]:
        raise ValueError("基金无净值数据：" + fund_code)
    row = data["rows"][-1]
    return {
        "symbol": fund_code,
        "name": data["name"],
        "asset_type": "fund",
        "trade_date": row["date"],
        "nav": row["nav"],
        "acc_nav": row.get("acc_nav"),
        "adj_nav": row.get("adj_nav"),
        "close": row["nav"],
        "pre_close": None,
        "change_pct": row["change_pct"],
        "source": data["source"],
    }


def _quote_from_tencent(sym):
    url = "https://qt.gtimg.cn/q=" + sym
    text = _http_get(url, decode="gbk")
    m = re.search(r'v_' + re.escape(sym) + r'="([^"]+)"', text)
    if not m:
        raise ValueError("未找到 ETF 行情")
    parts = m.group(1).split("~")
    if len(parts) < 40:
        raise ValueError("行情字段不完整")
    ts = parts[30]
    trade_date = "{}-{}-{}".format(ts[:4], ts[4:6], ts[6:8])
    return {
        "symbol": sym, "name": parts[1], "asset_type": "etf", "trade_date": trade_date,
        "open": float(parts[5] or 0), "high": float(parts[33] or 0), "low": float(parts[34] or 0),
        "close": float(parts[3] or 0), "pre_close": float(parts[4] or 0),
        "volume": float(parts[6] or 0), "amount": float(parts[37] or 0),
        "change_pct": float(parts[32] or 0) if parts[32] else None,
        "source": "tencent",
    }


def _secid(sym):
    """sh510300 → 1.510300；sz159915 → 0.159915（东财 secid 规则）。"""
    s = sym.lower()
    if s.startswith("sh"):
        return "1." + s[2:]
    if s.startswith("sz"):
        return "0." + s[2:]
    return ("1." if s.startswith(("5", "6", "9")) else "0.") + s


def _quote_from_eastmoney(sym):
    url = ("https://push2.eastmoney.com/api/qt/stock/get?secid={}"
           "&fields=f43,f44,f45,f46,f47,f48,f57,f58,f60,f170").format(_secid(sym))
    data = _http_get_json(url, headers={"User-Agent": USER_AGENT})
    d = (data or {}).get("data") or {}
    if not d:
        raise ValueError("东财行情为空")
    f = lambda k, div=100.0: (float(d[k]) / div if d.get(k) not in (None, "-") else 0.0)  # noqa: E731
    return {
        "symbol": sym, "name": d.get("f58") or "", "asset_type": "etf",
        "trade_date": _today(),
        "open": f("f46"), "high": f("f44"), "low": f("f45"),
        "close": f("f43"), "pre_close": f("f60"),
        "volume": float(d.get("f47") or 0), "amount": float(d.get("f48") or 0),
        "change_pct": f("f170"), "source": "eastmoney",
    }


def fetch_etf_quote(symbol):
    """ETF 行情：腾讯为主、东财兜底。"""
    sym = normalize_symbol(symbol)
    errors = []
    for src, fn in (("tencent", _quote_from_tencent), ("eastmoney", _quote_from_eastmoney)):
        try:
            return fn(sym)
        except Exception as e:                                  # noqa: BLE001
            errors.append("{}: {}".format(src, e))
    raise ValueError("ETF 行情源全部失败 —— " + "；".join(errors))


def _kline_from_tencent(sym, limit, start, end, price_mode):
    suffix = {"qfq": "qfq", "hfq": "hfq", "raw": ""}[price_mode]
    if start and end:
        param = "{},day,{},{},{},{}".format(sym, start, end, limit, suffix)
    else:
        param = "{},day,,,{},{}".format(sym, limit, suffix)
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param="
           + urllib.parse.quote(param))
    data = _http_get_json(url)
    node = (data.get("data") or {}).get(sym) or {}
    arr = node.get(price_mode + "day") or node.get("day") or []
    rows = []
    for it in arr:
        rows.append({"date": it[0], "open": float(it[1]), "close": float(it[2]),
                     "high": float(it[3]), "low": float(it[4]),
                     "volume": float(it[5]) if len(it) > 5 else 0})
    if not rows:
        raise ValueError("腾讯 K 线为空")
    rows.sort(key=lambda x: x["date"])
    return rows


def _kline_from_eastmoney(sym, limit, start, end, price_mode):
    fqt = {"raw": 0, "qfq": 1, "hfq": 2}[price_mode]
    beg = (start or "1990-01-01").replace("-", "")
    fin = (end or "2050-01-01").replace("-", "")
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={}"
           "&fields1=f1,f2,f3,f4,f5,f6"
           "&fields2=f51,f52,f53,f54,f55,f56,f57&klt=101&fqt={}&beg={}&end={}"
           "&lmt={}").format(_secid(sym), fqt, beg, fin, limit)
    data = _http_get_json(url, headers={"User-Agent": USER_AGENT})
    lines = ((data or {}).get("data") or {}).get("klines") or []
    rows = []
    for line in lines:
        p = line.split(",")
        if len(p) < 6:
            continue
        rows.append({"date": p[0], "open": float(p[1]), "close": float(p[2]),
                     "high": float(p[3]), "low": float(p[4]), "volume": float(p[5])})
    if not rows:
        raise ValueError("东财 K 线为空")
    rows.sort(key=lambda x: x["date"])
    return rows


def fetch_etf_kline(symbol, limit=1000, start=None, end=None, price_mode="qfq"):
    """ETF 日 K：腾讯为主、东财兜底；price_mode = qfq / hfq / raw。"""
    sym = normalize_symbol(symbol)
    mode = _norm_price_mode("etf", price_mode)
    errors = []
    for src, fn in (("tencent", _kline_from_tencent), ("eastmoney", _kline_from_eastmoney)):
        try:
            return fn(sym, limit, start, end, mode)
        except Exception as e:                                  # noqa: BLE001
            errors.append("{}: {}".format(src, e))
    raise ValueError("ETF K 线源全部失败 —— " + "；".join(errors))


def load_series_ex(asset_type, symbol, start_date=None, end_date=None, price_mode=None):
    """统一取价序列（带口径与来源信息）。

    返回 {'rows': [{date, price}], 'source', 'price_mode', 'price_note'}
    - fund：price_mode = adj（默认，复权净值）/ acc（累计净值）/ nav（单位净值）
    - etf ：price_mode = qfq（默认，前复权）/ hfq / raw
    """
    sym = normalize_symbol(symbol, asset_type)
    atype = "fund" if str(asset_type or "").lower() == "fund" else "etf"
    mode = _norm_price_mode(atype, price_mode)
    if atype == "etf":
        rows = fetch_etf_kline(sym, start=start_date, end=end_date, price_mode=mode)
        key = "close"
    else:
        data = fetch_fund_full(sym)
        rows = data["rows"]
        src_tag = data["source"]
        key = {"adj": "adj_nav", "acc": "acc_nav", "nav": "nav"}[mode]
        if mode == "acc" and any(r.get("acc_nav") is None for r in rows):
            # 备源没给累计净值时退回复权净值（口径接近，收益不低估）
            key = "adj_nav"
            mode = "adj"
        rows = list(rows)
    out = [{"date": r["date"], "price": float(r.get(key) or 0)} for r in rows]
    out = [r for r in out if r["price"] > 0]
    if atype == "etf":
        src_tag = "tencent/eastmoney"
    return {
        "rows": out,
        "source": src_tag,
        "price_mode": mode,
        "price_note": PRICE_MODES[atype][mode],
    }


def load_series(asset_type, symbol, start_date=None, end_date=None, price_mode=None):
    """返回统一的 [{date, price}] 序列，供快照与回测使用。"""
    return load_series_ex(asset_type, symbol, start_date, end_date, price_mode)["rows"]


def refresh_quotes(conn, symbols=None):
    """拉取今日/最新净值行情，写入 daily_snapshots。失败单个不影响其他。"""
    if symbols is None:
        watch = conn.execute("SELECT symbol,asset_type,name FROM watchlist").fetchall()
        tx = conn.execute(
            "SELECT DISTINCT symbol,asset_type,name FROM transactions").fetchall()
        seen = {}
        for r in list(watch) + list(tx):
            seen.setdefault(r["symbol"], {"asset_type": r["asset_type"], "name": r["name"]})
        symbols = [{"symbol": k, "asset_type": v["asset_type"], "name": v["name"]}
                   for k, v in seen.items()]
    results = {"ok": [], "errors": []}
    for item in symbols:
        sym = normalize_symbol(item.get("symbol"), item.get("asset_type"))
        asset_type = item.get("asset_type") or infer_asset_type(sym)
        try:
            if asset_type == "etf":
                q = fetch_etf_quote(sym)
                q["symbol"] = sym if not sym.startswith(("sh", "sz")) else sym
                if not q.get("name"):
                    q["name"] = item.get("name") or ""
                conn.execute(
                    """INSERT INTO daily_snapshots
                       (symbol,asset_type,trade_date,open,high,low,close,pre_close,change_pct,
                        volume,amount,nav,source,fetched_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(symbol,trade_date) DO UPDATE SET
                         open=excluded.open, high=excluded.high, low=excluded.low,
                         close=excluded.close, pre_close=excluded.pre_close,
                         change_pct=excluded.change_pct, volume=excluded.volume,
                         amount=excluded.amount, nav=excluded.nav,
                         source=excluded.source, fetched_at=excluded.fetched_at""",
                    (sym, "etf", q["trade_date"], q.get("open"), q.get("high"),
                     q.get("low"), q.get("close"), q.get("pre_close"), q.get("change_pct"),
                     q.get("volume"), q.get("amount"), None, q.get("source"), _now()),
                )
                results["ok"].append({"symbol": sym, "name": q.get("name"), "date": q["trade_date"]})
            else:
                q = fetch_fund_latest(sym)
                if not q.get("name"):
                    q["name"] = item.get("name") or ""
                conn.execute(
                    """INSERT INTO daily_snapshots
                       (symbol,asset_type,trade_date,open,high,low,close,pre_close,change_pct,
                        volume,amount,nav,source,fetched_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(symbol,trade_date) DO UPDATE SET
                         open=excluded.open, high=excluded.high, low=excluded.low,
                         close=excluded.close, pre_close=excluded.pre_close,
                         change_pct=excluded.change_pct, volume=excluded.volume,
                         amount=excluded.amount, nav=excluded.nav,
                         source=excluded.source, fetched_at=excluded.fetched_at""",
                    (sym, "fund", q["trade_date"], None, None, None, q["close"], q.get("pre_close"),
                     q.get("change_pct"), None, None, q["nav"], q.get("source"), _now()),
                )
                results["ok"].append({"symbol": sym, "name": q.get("name"), "date": q["trade_date"]})
        except Exception as e:
            results["errors"].append({"symbol": sym, "error": str(e)})
    conn.commit()
    if results["ok"]:
        # 顺手把已知名字回填到 watchlist / transactions，方便显示
        for ok in results["ok"]:
            if ok.get("name"):
                conn.execute("UPDATE watchlist SET name=? WHERE symbol=?",
                             (ok["name"], ok["symbol"]))
                conn.execute("UPDATE transactions SET name=? WHERE symbol=? AND name=''",
                             (ok["name"], ok["symbol"]))
        conn.commit()
    return results


# ---------------------------------------------------------------- 回测

def _ma(values, window):
    if window <= 1:
        return [None] * len(values)
    out = [None] * len(values)
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= window:
            s -= values[i - window]
        if i >= window - 1:
            out[i] = s / window
    return out


def _rsi(values, period=14):
    if len(values) <= period:
        return [None] * len(values)
    out = [None] * len(values)
    gains = []
    losses = []
    prev = values[0]
    for v in values[1:]:
        delta = v - prev
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
        prev = v
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    out[period] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    for i in range(period + 1, len(values)):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def _add_month(d, month):
    y = d.year + (d.month - 1 + month) // 12
    m = ((d.month - 1 + month) % 12) + 1
    import calendar
    day = min(d.day, calendar.monthrange(y, m)[1])
    return datetime.date(y, m, day)


def _closest_after(dates, target):
    """返回 >= target 的第一个日期；没有则 None。"""
    i = bisect_right(dates, target)
    if i >= len(dates):
        return None
    return dates[i]


def run_backtest(payload):
    """自研回测入口。支持 dca / ma / rsi 三种策略。"""
    symbol = normalize_symbol(payload.get("symbol"), payload.get("asset_type"))
    if not symbol:
        raise ValueError("缺少 symbol")
    asset_type = payload.get("asset_type") or infer_asset_type(symbol)
    strategy = str(payload.get("strategy") or "dca").lower()
    if strategy not in ("dca", "ma", "rsi"):
        raise ValueError("strategy 必须是 dca/ma/rsi")
    start = _parse_date(payload.get("start_date")) or datetime.date(2018, 1, 1)
    end = _parse_date(payload.get("end_date")) or datetime.date.today()
    if start >= end:
        raise ValueError("start_date 必须早于 end_date")
    fee_rate = float(payload.get("fee_rate") or 0.0015)
    meta = load_series_ex(asset_type, symbol, start.isoformat(), end.isoformat(),
                          payload.get("price_mode"))
    series = [s for s in meta["rows"] if start <= datetime.date.fromisoformat(s["date"]) <= end]
    if len(series) < 30:
        raise ValueError("该区间数据不足（需要至少 30 个交易日/净值点）")
    dates = [s["date"] for s in series]
    prices = [s["price"] for s in series]

    if strategy == "dca":
        result = _backtest_dca(payload, dates, prices, fee_rate)
    elif strategy == "ma":
        result = _backtest_ma(payload, dates, prices, fee_rate)
    else:
        result = _backtest_rsi(payload, dates, prices, fee_rate)
    result["symbol"] = symbol
    result["asset_type"] = asset_type
    result["strategy"] = strategy
    result["price_mode"] = meta["price_mode"]
    result["price_note"] = meta["price_note"]
    result["data_source"] = meta["source"]
    result["start_date"] = dates[0]
    result["end_date"] = dates[-1]
    return result


def _equity_metrics(equity_curve, initial_cents):
    """equity_curve: [(date, equity_cents)]"""
    final = equity_curve[-1][1]
    days = (datetime.date.fromisoformat(equity_curve[-1][0])
            - datetime.date.fromisoformat(equity_curve[0][0])).days
    total_return = final / initial_cents - 1 if initial_cents else 0
    years = max(days, 1) / 365.0
    annualized = (final / initial_cents) ** (1 / years) - 1 if initial_cents > 0 else 0
    peak = -1e18
    max_dd = 0.0
    for _, eq in equity_curve:
        if eq > peak:
            peak = eq
        if peak > 0:
            dd = (eq - peak) / peak
            if dd < max_dd:
                max_dd = dd
    daily_ret = []
    prev_eq = None
    for _, eq in equity_curve:
        if prev_eq:
            daily_ret.append(eq / prev_eq - 1)
        prev_eq = eq
    mean = sum(daily_ret) / len(daily_ret) if daily_ret else 0
    var = sum((x - mean) ** 2 for x in daily_ret) / len(daily_ret) if daily_ret else 0
    std = math.sqrt(var)
    sharpe = (mean / std * math.sqrt(252)) if std > 0 else 0
    return {
        "total_return_pct": total_return * 100,
        "annualized_return_pct": annualized * 100,
        "max_drawdown_pct": max_dd * 100,
        "sharpe": sharpe,
        "final_value_cents": int(round(final)),
        "initial_cents": int(round(initial_cents)),
        "days": days,
        "equity_curve": equity_curve,
    }


def _backtest_dca(payload, dates, prices, fee_rate):
    amount = float(payload.get("amount") or 1000)
    freq = str(payload.get("frequency") or "monthly").lower()
    day_of_month = int(payload.get("day") or 1)
    if freq not in ("weekly", "monthly"):
        raise ValueError("frequency 必须 weekly/monthly")
    start = _parse_date(payload.get("start_date")) or datetime.date.fromisoformat(dates[0])
    end = _parse_date(payload.get("end_date")) or datetime.date.fromisoformat(dates[-1])
    start = max(start, datetime.date.fromisoformat(dates[0]))
    dt_dates = [datetime.date.fromisoformat(d) for d in dates]
    payment_dates = []
    if freq == "monthly":
        cur = start.replace(day=min(day_of_month, 28))
        while cur <= end:
            payment_dates.append(cur)
            cur = _add_month(cur.replace(day=min(day_of_month, 28)), 1)
    else:
        # 每周一（ISO week start）扣款；没有行情就顺延到下一个交易日
        cur = start - datetime.timedelta(days=start.weekday())
        while cur <= end:
            payment_dates.append(cur)
            cur += datetime.timedelta(days=7)
    invested = 0.0
    shares = 0.0
    trades = []
    for pd in payment_dates:
        target = _closest_after(dt_dates, pd)
        if target is None:
            break
        idx = dt_dates.index(target)
        price = prices[idx]
        fee = amount * fee_rate
        buy_amount = amount - fee
        if buy_amount <= 0:
            continue
        got = buy_amount / price
        shares += got
        invested += amount
        trades.append({
            "date": dates[idx], "type": "buy", "price": price,
            "amount_cents": int(round(amount * 100)),
            "fee_cents": int(round(fee * 100)),
            "shares": got,
        })
    if not trades:
        raise ValueError("区间内没有可执行的扣款日")
    # 用每个交易日的持仓市值生成净值曲线（金额单位：分）
    curve = []
    acquired = 0.0
    cursor = 0
    for i, d in enumerate(dates):
        while cursor < len(trades) and trades[cursor]["date"] <= d:
            acquired += trades[cursor]["shares"]
            cursor += 1
        curve.append((d, int(round(acquired * prices[i] * 100))))
    invested_cents = int(round(invested * 100))
    metrics = _equity_metrics(curve, invested_cents)
    metrics["trades"] = trades
    metrics["total_invested_cents"] = invested_cents
    metrics["final_shares"] = shares
    return metrics


def _cross_state(short_ma, long_ma):
    """返回从 False 开始的交易信号序列；True=持有/买入。"""
    signal = []
    prev = False
    for i in range(len(short_ma)):
        if short_ma[i] is None or long_ma[i] is None:
            signal.append(False)
            prev = False
            continue
        cur = short_ma[i] > long_ma[i]
        signal.append(cur)
        prev = cur
    return signal


def _backtest_ma(payload, dates, prices, fee_rate):
    short_w = int(payload.get("short_window") or 20)
    long_w = int(payload.get("long_window") or 60)
    if short_w <= 0 or long_w <= short_w:
        raise ValueError("需要 0 < short_window < long_window")
    initial = float(payload.get("initial_amount") or 10000)
    short_ma = _ma(prices, short_w)
    long_ma = _ma(prices, long_w)
    signal = _cross_state(short_ma, long_ma)
    cash = initial
    shares = 0.0
    curve = []
    trades = []
    entry_date = entry_price = None
    for i, d in enumerate(dates):
        price = prices[i]
        if signal[i] and shares == 0 and cash > 0:
            fee = cash * fee_rate
            buy_amount = cash - fee
            shares = buy_amount / price
            cash = 0
            entry_date, entry_price = d, price
        elif not signal[i] and shares > 0:
            proceeds = shares * price
            fee = proceeds * fee_rate
            cash = proceeds - fee
            pnl = cash - initial if entry_price else 0
            ret = (price - entry_price) / entry_price if entry_price else 0
            trades.append({
                "entry_date": entry_date, "exit_date": d,
                "entry_price": entry_price, "exit_price": price,
                "return_pct": ret * 100,
                "pnl_cents": int(round(pnl * 100)),
            })
            shares = 0
            entry_date = entry_price = None
        curve.append((d, int(round((cash + shares * price) * 100))))
    metrics = _equity_metrics(curve, int(round(initial * 100)))
    metrics["trades"] = trades
    metrics["final_shares"] = shares
    metrics["final_cash_cents"] = int(round(cash * 100))
    return metrics


def _backtest_rsi(payload, dates, prices, fee_rate):
    period = int(payload.get("period") or 14)
    oversold = float(payload.get("oversold") or 30)
    overbought = float(payload.get("overbought") or 70)
    if oversold <= 0 or overbought >= 100 or oversold >= overbought:
        raise ValueError("需要 0 < oversold < overbought < 100")
    initial = float(payload.get("initial_amount") or 10000)
    rsi = _rsi(prices, period)
    cash = initial
    shares = 0.0
    curve = []
    trades = []
    entry_date = entry_price = None
    for i, d in enumerate(dates):
        price = prices[i]
        r = rsi[i]
        if r is not None and r < oversold and shares == 0 and cash > 0:
            fee = cash * fee_rate
            buy_amount = cash - fee
            shares = buy_amount / price
            cash = 0
            entry_date, entry_price = d, price
        elif r is not None and r > overbought and shares > 0:
            proceeds = shares * price
            fee = proceeds * fee_rate
            cash = proceeds - fee
            pnl = cash - initial if entry_price else 0
            ret = (price - entry_price) / entry_price if entry_price else 0
            trades.append({
                "entry_date": entry_date, "exit_date": d,
                "entry_price": entry_price, "exit_price": price,
                "return_pct": ret * 100,
                "pnl_cents": int(round(pnl * 100)),
            })
            shares = 0
            entry_date = entry_price = None
        curve.append((d, int(round((cash + shares * price) * 100))))
    metrics = _equity_metrics(curve, int(round(initial * 100)))
    metrics["trades"] = trades
    metrics["final_shares"] = shares
    metrics["final_cash_cents"] = int(round(cash * 100))
    return metrics


# ---------------------------------------------------------------- CRUD 收藏/笔记

def list_simple(conn, table, order="created_at DESC"):
    rows = conn.execute("SELECT * FROM {} ORDER BY {}".format(table, order)).fetchall()
    return [dict(r) for r in rows]


def insert_simple(conn, table, payload):
    now = _now()
    cols = {
        "strategies": ("name", "description", "rules_json", "tags", "created_at", "updated_at"),
        "indicators": ("name", "category", "summary", "detail", "formula", "tags", "created_at"),
        "notes": ("question", "answer", "tags", "source", "created_at", "updated_at"),
    }
    if table not in cols:
        raise ValueError("unknown table")
    names = cols[table]
    vals = []
    for c in names:
        if c in ("created_at", "updated_at"):
            vals.append(now)
        else:
            vals.append(str(payload.get(c) or ""))
    q = "INSERT INTO {}({}) VALUES ({})".format(
        table, ",".join(names), ",".join("?" * len(names)))
    cur = conn.execute(q, vals)
    conn.commit()
    return cur.lastrowid


def delete_row(conn, table, rid):
    if table not in ("strategies", "indicators", "notes"):
        raise ValueError("unknown table")
    conn.execute("DELETE FROM {} WHERE id=?".format(table), (int(rid),))
    conn.commit()


# ---------------------------------------------------------------- LLM

def _api_key():
    """凭据解析顺序（前者优先）：

    1. 环境变量 OPENCODE_GO_API_KEY（systemd 注入，推荐）
    2. wuage 自己的 $WUAGE_HOME/.credentials.yaml —— 不依赖 DSH 存活
    3. DSH 的 $DSH_HOME/.credentials.yaml（历史来源，保留兼容）
    """
    k = os.environ.get("OPENCODE_GO_API_KEY")
    if k and k.strip():
        return k.strip()
    candidates = [WUAGE_DATA.parent / ".credentials.yaml",
                  Path(os.environ.get("DSH_HOME") or os.path.expanduser("~/.dsh"))
                  / ".credentials.yaml"]
    for path in candidates:
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("OPENCODE_GO_API_KEY:"):
                        return line.split(":", 1)[1].strip().strip('"').strip("'")
        except OSError:
            continue
    return None


def chat(messages, max_tokens=1200):
    key = _api_key()
    if not key:
        raise RuntimeError("OPENCODE_GO_API_KEY 未配置")
    payload = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + key,
            "User-Agent": USER_AGENT,
            # OpenCode Go 网关要求带会话标识，否则 400 MissingSessionID
            "x-opencode-session": "wuage-finance",
        },
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"].strip()


def ask_question(question, tags=""):
    system = (
        "你是 wuage-home 的理财学习助手。用通俗中文回答基金/ETF/理财入门问题，"
        "适当解释技术指标（均线、缩量放量、RSI 等）。回答只做知识学习，不构成投资建议。"
        "如果问题涉及具体买卖建议，明确说明不提供投资建议。"
    )
    answer = chat([
        {"role": "system", "content": system},
        {"role": "user", "content": str(question)},
    ])
    conn = connect()
    nid = insert_simple(conn, "notes", {
        "question": question, "answer": answer, "tags": tags, "source": "ask",
    })
    note = conn.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()
    return dict(note)


def generate_review(date=None):
    """生成某日复盘：先确定性汇总，再尝试 LLM 人话解读。"""
    d = _parse_date(date) or datetime.date.today()
    dstr = d.isoformat()
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM daily_snapshots WHERE trade_date=? ORDER BY change_pct DESC", (dstr,)).fetchall()
    items = []
    for r in rows:
        item = dict(r)
        row = conn.execute("SELECT name FROM watchlist WHERE symbol=?", (r["symbol"],)).fetchone()
        if row and row["name"]:
            item["name"] = row["name"]
        else:
            row = conn.execute(
                "SELECT name FROM transactions WHERE symbol=? ORDER BY id DESC LIMIT 1",
                (r["symbol"],)).fetchone()
            if row:
                item["name"] = row["name"]
        items.append(item)
    summary = {
        "date": dstr,
        "snapshot_count": len(items),
        "items": items,
    }
    if not items:
        summary["text"] = "这一天没有快照数据，请先点击刷新行情。"
        return summary
    lines = ["{} 理财复盘：".format(dstr)]
    up = [r for r in items if (r["change_pct"] or 0) > 0]
    down = [r for r in items if (r["change_pct"] or 0) < 0]
    lines.append("共 {} 个标的，上涨 {} 个、下跌 {} 个。".format(len(items), len(up), len(down)))
    for r in items[:6]:
        chg = r["change_pct"]
        if chg is not None:
            arrow = "▲" if chg >= 0 else "▼"
            lines.append("- {} {}：{} {:.2f}%".format(
                r["name"] or r["symbol"], r["symbol"], arrow, chg))
    text = "\n".join(lines)
    summary["text"] = text
    try:
        llm = chat([
            {"role": "system", "content": "你是家庭理财助手，把以下结构化数字整理成一段简短、通俗的每日复盘；不要编造数据。"},
            {"role": "user", "content": text},
        ], max_tokens=800)
        summary["llm_text"] = llm
    except Exception as e:
        summary["llm_text"] = None
        summary["llm_error"] = str(e)
    return summary


# ---------------------------------------------------------------- HTTP 服务

class FinanceHandler(BaseHTTPRequestHandler):
    server_version = "Finance/0.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[finance] %s - %s\n" % (self.address_string(), fmt % args))

    # ---- GET ----
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/" or path == "/index.html":
                if not WEB_PATH.exists():
                    self._err(500, "web/finance.html 不存在")
                    return
                _send_text(self, WEB_PATH.read_text(encoding="utf-8"),
                           content_type="text/html; charset=utf-8")
                return
            if path == "/api/health":
                _send_json(self, {"ok": True, "time": _now()})
                return
            if path == "/api/positions":
                conn = connect()
                _send_json(self, {"positions": calc_positions(conn)})
                return
            if path == "/api/transactions":
                conn = connect()
                _send_json(self, {"transactions": list_transactions(conn)})
                return
            if path == "/api/watchlist":
                conn = connect()
                _send_json(self, {"watchlist": list_watchlist(conn)})
                return
            if path == "/api/review":
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                _send_json(self, generate_review(qs.get("date", [None])[0]))
                return
            if path == "/api/strategies":
                conn = connect()
                _send_json(self, {"strategies": list_simple(conn, "strategies")})
                return
            if path == "/api/indicators":
                conn = connect()
                _send_json(self, {"indicators": list_simple(conn, "indicators")})
                return
            if path == "/api/notes":
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                q = (qs.get("q") or [""])[0]
                tag = (qs.get("tag") or [""])[0]
                conn = connect()
                if tag:
                    rows = conn.execute(
                        "SELECT * FROM notes WHERE tags LIKE ? ORDER BY created_at DESC",
                        ("%" + tag + "%",)).fetchall()
                elif q:
                    rows = conn.execute(
                        """SELECT * FROM notes WHERE question LIKE ? OR answer LIKE ?
                           ORDER BY created_at DESC""",
                        ("%" + q + "%", "%" + q + "%")).fetchall()
                else:
                    rows = conn.execute("SELECT * FROM notes ORDER BY created_at DESC").fetchall()
                _send_json(self, {"notes": [dict(r) for r in rows]})
                return
            if path == "/api/memories":
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                topic = (qs.get("topic") or [""])[0]
                _send_json(self, {"memories": list_memories(topic=topic or None)})
                return
            self._err(404, "not found: " + path)
        except Exception as e:
            self._err(500, str(e))

    # ---- POST ----
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/api/transactions":
                payload = _read_payload(self)
                nid = add_transaction(connect(), payload)
                _send_json(self, {"ok": True, "id": nid})
                return
            if path == "/api/watchlist":
                payload = _read_payload(self)
                nid = add_watchlist(connect(), payload)
                _send_json(self, {"ok": True, "id": nid})
                return
            if path == "/api/refresh":
                payload = _read_payload(self)
                results = refresh_quotes(connect(), payload.get("symbols"))
                _send_json(self, {"ok": not results["errors"], "results": results})
                return
            if path == "/api/strategies":
                nid = insert_simple(connect(), "strategies", _read_payload(self))
                _send_json(self, {"ok": True, "id": nid})
                return
            if path == "/api/indicators":
                nid = insert_simple(connect(), "indicators", _read_payload(self))
                _send_json(self, {"ok": True, "id": nid})
                return
            if path == "/api/memories":
                payload = _read_payload(self)
                mid = add_memory(kind=payload.get("kind") or "fact",
                                 topic=payload.get("topic") or "",
                                 content=payload.get("content") or "",
                                 importance=payload.get("importance") or "medium",
                                 source=payload.get("source") or "")
                _send_json(self, {"ok": True, "id": mid})
                return
            if path == "/api/ask":
                payload = _read_payload(self)
                question = str(payload.get("question") or "").strip()
                if not question:
                    raise ValueError("question 不能为空")
                note = ask_question(question, tags=payload.get("tags") or "")
                _send_json(self, {"ok": True, "note": note})
                return
            if path == "/api/backtest":
                _send_json(self, {"ok": True, "result": run_backtest(_read_payload(self))})
                return
            self._err(404, "not found: " + path)
        except Exception as e:
            self._err(500, str(e))

    # ---- PATCH / DELETE ----
    def do_PATCH(self):
        path = urllib.parse.urlparse(self.path).path
        m = re.match(r"^/api/transactions/(\d+)$", path)
        if not m:
            self._err(404, "not found: " + path)
            return
        try:
            payload = _read_payload(self)
            conn = connect()
            rid = int(m.group(1))
            row = conn.execute("SELECT * FROM transactions WHERE id=?", (rid,)).fetchone()
            if not row:
                self._err(404, "transaction not found")
                return
            symbol = normalize_symbol(payload.get("symbol") or row["symbol"],
                                      payload.get("asset_type") or row["asset_type"])
            asset_type = infer_asset_type(symbol, payload.get("asset_type") or row["asset_type"])
            direction = str(payload.get("direction") or row["direction"])
            if direction in ("买入", "买"):
                direction = "buy"
            if direction in ("卖出", "卖"):
                direction = "sell"
            trade_date = str(payload.get("trade_date") or row["trade_date"])
            shares = float(payload.get("shares") if payload.get("shares") is not None else row["shares"])
            price = float(payload.get("price") if payload.get("price") is not None else row["price"])
            amount_cents = int(round(float(
                payload.get("amount_cents")
                if payload.get("amount_cents") is not None
                else row["amount_cents"])))
            fee_cents = int(round(float(
                payload.get("fee_cents")
                if payload.get("fee_cents") is not None
                else row["fee_cents"])))
            name = payload.get("name") if payload.get("name") is not None else row["name"]
            note = payload.get("note") if payload.get("note") is not None else row["note"]
            conn.execute(
                """UPDATE transactions SET symbol=?,name=?,asset_type=?,direction=?,trade_date=?,
                   shares=?,price=?,amount_cents=?,fee_cents=?,note=? WHERE id=?""",
                (symbol, name, asset_type, direction, trade_date, shares, price,
                 amount_cents, fee_cents, note, rid))
            conn.commit()
            _send_json(self, {"ok": True, "id": rid})
        except Exception as e:
            self._err(500, str(e))

    def do_DELETE(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            m = re.match(r"^/api/transactions/(\d+)$", path)
            if m:
                conn = connect()
                conn.execute("DELETE FROM transactions WHERE id=?", (int(m.group(1)),))
                conn.commit()
                _send_json(self, {"ok": True})
                return
            m = re.match(r"^/api/watchlist/(\d+)$", path)
            if m:
                remove_watchlist(connect(), int(m.group(1)))
                _send_json(self, {"ok": True})
                return
            m = re.match(r"^/api/(strategies|indicators|notes)/(\d+)$", path)
            if m:
                delete_row(connect(), m.group(1), int(m.group(2)))
                _send_json(self, {"ok": True})
                return
            self._err(404, "not found: " + path)
        except Exception as e:
            self._err(500, str(e))

    def _err(self, status, msg):
        _send_json(self, {"ok": False, "error": str(msg)}, status=status)


def serve(host="127.0.0.1", port=17625):
    conn = connect()
    init_db(conn)
    conn.close()
    httpd = ThreadingHTTPServer((host, port), FinanceHandler)
    print("理财助手已启动：http://{}:{}/（数据：{}）".format(host, port, FINANCE_DATA))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()


def main():
    parser = argparse.ArgumentParser(description="wuage-home 理财助手")
    parser.add_argument("--host", default=os.environ.get("FINANCE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("FINANCE_PORT", "17625")))
    parser.add_argument("--init", action="store_true", help="只初始化数据库")
    parser.add_argument("--refresh", action="store_true", help="拉取一次今日快照后退出")
    parser.add_argument("--backtest-demo", action="store_true", help="跑一次示例回测")
    parser.add_argument("--memo-add", action="store_true", help="记一条理财长时记忆")
    parser.add_argument("--memo-list", action="store_true", help="列出理财长时记忆")
    parser.add_argument("--memo-kind", default="fact")
    parser.add_argument("--memo-topic", default="")
    parser.add_argument("--memo-content", default="")
    parser.add_argument("--memo-importance", default="medium")
    args = parser.parse_args()

    conn = connect()
    init_db(conn)
    conn.close()

    if args.init:
        print("初始化完成：{}".format(DB_PATH))
        return 0
    if args.refresh:
        results = refresh_quotes(connect())
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0
    if args.memo_add:
        if not args.memo_content:
            print("--memo-content 不能为空")
            return 1
        mid = add_memory(kind=args.memo_kind, topic=args.memo_topic, content=args.memo_content,
                         importance=args.memo_importance, source="cli")
        print("ok: memory #{} 已记（{} / {}）".format(mid, args.memo_kind, args.memo_topic or "无主题"))
        return 0
    if args.memo_list:
        rows = list_memories(topic=args.memo_topic or None)
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=_json_default))
        return 0
    if args.backtest_demo:
        demo = {
            "symbol": "110022",
            "asset_type": "fund",
            "strategy": "dca",
            "amount": 1000,
            "frequency": "monthly",
            "start_date": "2018-01-01",
            "end_date": datetime.date.today().isoformat(),
        }
        print(json.dumps(run_backtest(demo), ensure_ascii=False, indent=2, default=_json_default))
        return 0
    serve(args.host, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())