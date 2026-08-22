#!/usr/bin/env python3
"""wuage-home web panel — 家庭账本 Web 面板（Python 标准库自托管，零第三方依赖）。

启动：
  python3 scripts/web.py --port 17623        # 默认 127.0.0.1:17623（仅本机）
  python3 scripts/web.py --host 0.0.0.0     # 局域网可访问（注意：局域网内可见数据）

环境变量：WUAGE_DATA 数据目录；WUAGE_WEB_HOST / WUAGE_WEB_PORT。
API（读同一份 data/ledger.db）：
  GET  /                  → 面板页面
  GET  /api/categories    → 9 项类别
  GET  /api/entries       → 流水（?from=&to=&category=&kind=&limit=）
  GET  /api/summary       → 区间汇总（?from=&to=）
  GET  /api/weekly        → 本周汇总
  POST /api/entries       → 记一笔（body: amount_cents/date/category/note/...）
  PATCH /api/entries/<id> → 改一笔（body: category/note/date/...）
  DELETE /api/entries/<id>→ 删一笔
"""

import argparse
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ledger as L
import parse as P

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_PATH = os.path.join(REPO_ROOT, "web", "index.html")
MAX_BODY = 65536

ENTRY_RE = re.compile(r"^/api/entries/(\d+)$")


class ThreadingServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 静默访问日志，错误仍在 stderr
        pass

    # ── helpers ────────────────────────────────────────────────────────────
    def _send(self, status, body, ctype):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status, obj):
        self._send(status, json.dumps(obj, ensure_ascii=False, default=str),
                   "application/json; charset=utf-8")

    def _error(self, status, msg):
        self._json(status, {"error": msg})

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("body too large")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        obj = json.loads(raw.decode("utf-8"))
        if not isinstance(obj, dict):
            raise ValueError("body must be a JSON object")
        return obj

    def _query(self):
        q = parse_qs(urlparse(self.path).query)
        return {k: (v[0] if v else "") for k, v in q.items()}

    # ── GET ────────────────────────────────────────────────────────────────
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/" or path == "/index.html":
            try:
                with open(INDEX_PATH, "rb") as f:
                    # 禁用缓存：页面改动成功后不希望用户看到旧版（修复"改了没生效"）
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
                    self.send_header("Pragma", "no-cache")
                    self.send_header("Expires", "0")
                    data = f.read()
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
            except OSError:
                self._error(500, "index.html not found at " + INDEX_PATH)
            return
        q = self._query()
        if path == "/api/health":
            conn = L.connect()
            try:
                fam_id = L.ensure_seed(conn)["family_id"]
                cats = [c["name"] for c in L.list_categories(conn, fam_id)]
            finally:
                conn.close()
            self._json(200, {"ok": True, "db": str(L.DB_PATH), "categories": cats})
            return
        if path == "/api/categories":
            conn = L.connect()
            try:
                fam_id = L.ensure_seed(conn)["family_id"]
                cats = [c["name"] for c in L.list_categories(conn, fam_id)]
            finally:
                conn.close()
            self._json(200, {"categories": cats})
            return
        if path == "/api/entries":
            limit = None
            try:
                if q.get("limit"):
                    limit = int(q["limit"])
            except ValueError:
                self._error(400, "limit must be int")
                return
            conn = L.connect()
            try:
                rows = L.list_entries(
                    conn, from_=q.get("from") or None, to=q.get("to") or None,
                    category=q.get("category") or None, kind=q.get("kind") or None,
                    limit=limit)
            finally:
                conn.close()
            self._json(200, {"entries": rows})
            return
        if path == "/api/summary":
            conn = L.connect()
            try:
                d = L.summary_data(conn, from_=q.get("from") or None,
                                   to=q.get("to") or None,
                                   category=q.get("category") or None,
                                   kind=q.get("kind") or None)
            finally:
                conn.close()
            self._json(200, d)
            return
        if path == "/api/weekly":
            conn = L.connect()
            try:
                d = L.weekly_data(conn)
            finally:
                conn.close()
            self._json(200, d)
            return
        self._error(404, "not found: " + path)

    # ── POST ───────────────────────────────────────────────────────────────
    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/parse":
            try:
                body = self._read_body()
            except (ValueError, json.JSONDecodeError) as e:
                self._error(400, "bad body: {}".format(e))
                return
            text = body.get("text")
            if not text or not isinstance(text, str) or not text.strip():
                self._error(400, "text required")
                return
            try:
                parsed = P.parse_text(text.strip())
            except Exception as e:  # noqa: BLE001 — LLM/网络/解析错误
                self._error(502, "解析失败：{}".format(e))
                return
            if int(parsed.get("amount_cents") or 0) <= 0:
                self._error(422, "没听出具体金额，请带上金额再说，如：今天买西瓜花了10块")
                return
            conn = L.connect()
            try:
                row = L.add_record(conn, parsed)
            except L.LedgerError as e:
                self._error(400, str(e))
                return
            finally:
                conn.close()
            self._json(201, row)
            return
        if path != "/api/entries":
            self._error(404, "not found")
            return
        try:
            record = self._read_body()
        except (ValueError, json.JSONDecodeError) as e:
            self._error(400, "bad body: {}".format(e))
            return
        conn = L.connect()
        try:
            row = L.add_record(conn, {k: v for k, v in record.items()})
        except L.LedgerError as e:
            self._error(400, str(e))
            return
        finally:
            conn.close()
        self._json(201, row)

    # ── PATCH ──────────────────────────────────────────────────────────────
    def do_PATCH(self):
        m = ENTRY_RE.match(urlparse(self.path).path)
        if not m:
            self._error(404, "not found")
            return
        rid = int(m.group(1))
        try:
            fields = self._read_body()
        except (ValueError, json.JSONDecodeError) as e:
            self._error(400, "bad body: {}".format(e))
            return
        conn = L.connect()
        try:
            row = L.update_record(conn, rid, fields)
        except L.LedgerError as e:
            status = 404 if str(e).startswith("no ledger row") else 400
            self._error(status, str(e))
            return
        finally:
            conn.close()
        self._json(200, row)

    # ── DELETE ─────────────────────────────────────────────────────────────
    def do_DELETE(self):
        m = ENTRY_RE.match(urlparse(self.path).path)
        if not m:
            self._error(404, "not found")
            return
        rid = int(m.group(1))
        conn = L.connect()
        try:
            n = L.delete_record(conn, rid)
        finally:
            conn.close()
        if n == 0:
            self._error(404, "no ledger row with id={}".format(rid))
            return
        self._json(200, {"ok": True, "deleted": n})


def main():
    ap = argparse.ArgumentParser(prog="wuage-web",
                                 description="wuage-home 家庭账本 Web 面板")
    ap.add_argument("--host", default=os.environ.get("WUAGE_WEB_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("WUAGE_WEB_PORT", "8000")))
    args = ap.parse_args()
    server = ThreadingServer((args.host, args.port), Handler)
    print("wuage-home web panel: http://{}:{}  db={}".format(
        args.host, args.port, L.DB_PATH), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()