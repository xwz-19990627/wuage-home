#!/usr/bin/env python3
"""wuage-home web panel — 家庭账本 Web 面板（Python 标准库自托管，零第三方依赖）。

启动：
  python3 scripts/web.py --port 17623        # 默认 127.0.0.1:17623（仅本机）
  python3 scripts/web.py --host 0.0.0.0     # 局域网可访问（注意：局域网内可见数据）

环境变量：WUAGE_DATA 数据目录（当前 /root/wuage/data）；WUAGE_WEB_HOST / WUAGE_WEB_PORT。
API（读同一份 $WUAGE_DATA/ledger.db）：
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
import datetime
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
import import_bills as IB

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
                cats = L.list_categories(conn, fam_id)
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
        if path == "/api/dashboard":
            try:
                offset = int(self._query().get("offset") or 0)
            except ValueError:
                offset = 0
            conn = L.connect()
            try:
                d = L.dashboard_data(conn, month_offset=offset)
            finally:
                conn.close()
            self._json(200, d)
            return
        if path == "/api/members":
            conn = L.connect()
            try:
                fam_id = L.ensure_seed(conn)["family_id"]
                ms = L.list_members(conn, fam_id)
            finally:
                conn.close()
            self._json(200, {"members": ms})
            return
        if path == "/api/nature":
            q = self._query()
            try:
                year = int(q.get("year") or datetime.date.today().year)
                month = int(q["month"]) if q.get("month") else None
            except ValueError:
                self._error(400, "year/month must be int")
                return
            conn = L.connect()
            try:
                d = L.nature_data(conn, year=year, month=month)
            except L.LedgerError as e:
                conn.close()
                self._error(400, str(e))
                return
            finally:
                conn.close()
            self._json(200, d)
            return
        if path == "/api/drafts":
            conn = L.connect()
            try:
                fam_id = L.ensure_seed(conn)["family_id"]
                ds = L.list_drafts(conn, fam_id)
            finally:
                conn.close()
            self._json(200, {"drafts": ds})
            return
        if path == "/api/accounts":
            conn = L.connect()
            try:
                fam_id = L.ensure_seed(conn)["family_id"]
                accs = L.list_accounts(conn, fam_id)
                total = sum(a["latest_balance"] for a in accs)
            finally:
                conn.close()
            self._json(200, {"accounts": accs, "total_cents": total})
            return
        if path == "/api/networth":
            conn = L.connect()
            try:
                fam_id = L.ensure_seed(conn)["family_id"]
                d = L.networth_data(conn, fam_id)
            finally:
                conn.close()
            self._json(200, d)
            return
        if path == "/api/funds":
            conn = L.connect()
            try:
                fam_id = L.ensure_seed(conn)["family_id"]
                d = L.list_positions(conn, fam_id)
            finally:
                conn.close()
            self._json(200, d)
            return
        if path == "/api/import/files":
            files = []
            if IB.UPLOAD_DIR.exists():
                for p in sorted(IB.UPLOAD_DIR.iterdir()):
                    if not p.is_file():
                        continue
                    plat = IB.detect_platform(p.name)
                    if plat is None and p.suffix.lower() not in (".xlsx", ".csv"):
                        continue
                    files.append({
                        "name": p.name,
                        "size": p.stat().st_size,
                        "platform": plat or "unknown",
                        "platform_name": IB.PLATFORM_NAME.get(plat, "未知"),
                    })
            self._json(200, {"dir": str(IB.UPLOAD_DIR), "files": files})
            return
        self._error(404, "not found: " + path)

    # ── POST ───────────────────────────────────────────────────────────────
    def do_POST(self):
        path = urlparse(self.path).path
        if path in ("/api/import/preview", "/api/import/run"):
            try:
                body = self._read_body()
            except (ValueError, json.JSONDecodeError) as e:
                self._error(400, "bad body: {}".format(e))
                return
            fname = (body.get("file") or "").strip()
            if not fname or "/" in fname or "\\" in fname or ".." in fname:
                self._error(400, "file 参数非法")
                return
            fpath = IB.UPLOAD_DIR / fname
            if not fpath.is_file():
                self._error(404, "文件不存在：{}".format(fname))
                return
            try:
                records, skipped = IB.normalize_all(str(fpath))
            except ValueError as e:
                self._error(400, str(e))
                return
            conn = L.connect()
            try:
                prev = IB.build_preview(records, skipped, conn)
                if path == "/api/import/run":
                    member = (body.get("member") or "本人").strip()
                    result = IB.do_import(records, conn, member=member)
                    prev["imported"] = result["imported"]
                    prev["dup_skipped"] = result["dup"]
            except L.LedgerError as e:
                self._error(400, str(e))
                return
            finally:
                conn.close()
            self._json(200, prev)
            return
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
            # 候选：动态读取家庭成员与分类（LLM 只能从中选）
            conn = L.connect()
            try:
                fam_id = L.ensure_seed(conn)["family_id"]
                cats = [c["name"] for c in L.list_categories(conn, fam_id)]
                mems = [m["name"] for m in L.list_members(conn, fam_id)]
            finally:
                conn.close()
            try:
                parsed = P.parse_text(text.strip(), categories=cats, members=mems)
            except Exception as e:  # noqa: BLE001 — LLM/网络/解析错误
                self._error(502, "解析失败：{}".format(e))
                return
            if int(parsed.get("amount_cents") or 0) <= 0:
                self._error(422, "没听出具体金额，请带上金额再说，如：今天买西瓜花了10块")
                return
            # 只返回解析结果（前端结果卡确认后才入库）
            self._json(200, {"ok": True, "parsed": parsed})
            return
        if path == "/api/drafts":
            try:
                body = self._read_body()
            except (ValueError, json.JSONDecodeError) as e:
                self._error(400, "bad body: {}".format(e))
                return
            if not isinstance(body.get("data"), dict):
                self._error(400, "data required")
                return
            conn = L.connect()
            try:
                fam_id = L.ensure_seed(conn)["family_id"]
                did = L.add_draft(conn, fam_id, body["data"])
            finally:
                conn.close()
            self._json(201, {"ok": True, "id": did})
            return
        if path == "/api/funds":
            try:
                body = self._read_body()
            except (ValueError, json.JSONDecodeError) as e:
                self._error(400, "bad body: {}".format(e))
                return
            conn = L.connect()
            try:
                fam_id = L.ensure_seed(conn)["family_id"]
                pid = L.add_position(conn, fam_id, body.get("name"),
                                     platform=body.get("platform") or "",
                                     kind=body.get("kind") or "index",
                                     cost_cents=body.get("cost_cents") or 0,
                                     market_cents=body.get("market_cents") or 0,
                                     holding_pnl_cents=body.get("holding_pnl_cents"),
                                     total_pnl_cents=body.get("total_pnl_cents") or 0,
                                     code=body.get("code") or "", note=body.get("note") or "")
            except L.LedgerError as e:
                conn.close()
                self._error(400, str(e))
                return
            finally:
                conn.close()
            self._json(201, {"ok": True, "id": pid})
            return
        if path.startswith("/api/funds/"):
            m = re.match(r"^/api/funds/(d+)/trade$", path)
            if m:
                try:
                    body = self._read_body()
                except (ValueError, json.JSONDecodeError) as e:
                    self._error(400, "bad body: {}".format(e))
                    return
                conn = L.connect()
                try:
                    fam_id = L.ensure_seed(conn)["family_id"]
                    res = L.add_trade(conn, fam_id, int(m.group(1)), body.get("action"),
                                      body.get("date"), body.get("amount_cents") or 0,
                                      shares=body.get("shares"), fee_cents=body.get("fee_cents") or 0,
                                      note=body.get("note") or "",
                                      holding_pnl_delta=body.get("holding_pnl_delta"),
                                      market_cents=body.get("market_cents"))
                except (L.LedgerError, ValueError) as e:
                    conn.close()
                    self._error(400, str(e))
                    return
                finally:
                    conn.close()
                self._json(200, res)
                return
        if path in ("/api/accounts", "/api/balances", "/api/monthly-settle", "/api/monthly-pnl"):
            try:
                body = self._read_body()
            except (ValueError, json.JSONDecodeError) as e:
                self._error(400, "bad body: {}".format(e))
                return
            conn = L.connect()
            try:
                fam_id = L.ensure_seed(conn)["family_id"]
                if path == "/api/accounts":
                    if not body.get("name"):
                        raise L.LedgerError("name required")
                    aid = L.add_account(conn, fam_id, body["name"],
                                        atype=body.get("type") or "cash",
                                        note=body.get("note") or "")
                    result = {"ok": True, "id": aid}
                elif path == "/api/balances":
                    if not body.get("account_id") or "balance_cents" not in body:
                        raise L.LedgerError("account_id / balance_cents required")
                    bid = L.set_balance(conn, fam_id, int(body["account_id"]),
                                        body.get("date") or datetime.date.today().isoformat(),
                                        body["balance_cents"], note=body.get("note") or "")
                    result = {"ok": True, "id": bid}
                elif path == "/api/monthly-pnl":
                    year = int(body.get("year") or datetime.date.today().year)
                    month = int(body.get("month") or datetime.date.today().month)
                    result = L.monthly_pnl(conn, fam_id, year, month,
                                           body.get("amount_cents") or 0)
                else:  # monthly-settle
                    year = int(body.get("year") or datetime.date.today().year)
                    month = int(body.get("month") or datetime.date.today().month)
                    result = L.monthly_settle(conn, fam_id, year, month)
            except (L.LedgerError, ValueError) as e:
                conn.close()
                self._error(400, str(e))
                return
            finally:
                conn.close()
            self._json(201, result)
            return
        if path == "/api/categories" or path == "/api/members":
            try:
                body = self._read_body()
            except (ValueError, json.JSONDecodeError) as e:
                self._error(400, "bad body: {}".format(e))
                return
            conn = L.connect()
            try:
                fam_id = L.ensure_seed(conn)["family_id"]
                if path == "/api/categories":
                    if not body.get("name"):
                        raise L.LedgerError("name required")
                    cid = L.add_category(conn, fam_id, body["name"], nature=body.get("nature"),
                                         parent=body.get("parent"))
                    result = {"ok": True, "id": cid}
                else:
                    if not body.get("name"):
                        raise L.LedgerError("name required")
                    mid = L.add_member(conn, fam_id, body["name"], body.get("relation"))
                    result = {"ok": True, "id": mid}
            except L.LedgerError as e:
                conn.close()
                self._error(400, str(e))
                return
            finally:
                conn.close()
            self._json(201, result)
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
        def _by_id(name_prefix, fn):
            p = urlparse(self.path).path
            if not p.startswith("/api/" + name_prefix + "/"):
                return None
            try:
                rid = int(p.rsplit("/", 1)[1])
            except ValueError:
                return False
            conn = L.connect()
            try:
                fam_id = L.ensure_seed(conn)["family_id"]
                row = conn.execute("SELECT * FROM {} WHERE id=? AND family_id=?".format(name_prefix), (rid, fam_id)).fetchone()
                if row is None:
                    return False
                try:
                    body = self._read_body()
                except (ValueError, json.JSONDecodeError) as e:
                    raise L.LedgerError("bad body: {}".format(e))
                if name_prefix == "categories":
                    if not body:
                        raise L.LedgerError("缺少修改字段（name 或 nature）")
                    if body.get("name"):
                        L.rename_category(conn, fam_id, rid, body["name"])
                    if "nature" in body:
                        L.set_category_nature(conn, fam_id, rid, body["nature"])
                else:
                    raise L.LedgerError("members 改名暂不支持")
            except L.LedgerError as e:
                conn.close()
                self._error(400, str(e))
                return True
            finally:
                conn.close()
            self._json(200, {"ok": True})
            return True
        # 基金持仓 PATCH /api/funds/<id>
        fp = urlparse(self.path).path
        if fp.startswith("/api/funds/"):
            try:
                rid = int(fp.rsplit("/", 1)[1])
            except ValueError:
                self._error(400, "bad id")
                return
            try:
                fields = self._read_body()
            except (ValueError, json.JSONDecodeError) as e:
                self._error(400, "bad body: {}".format(e))
                return
            conn = L.connect()
            try:
                fam_id = L.ensure_seed(conn)["family_id"]
                L.update_position(conn, fam_id, rid, fields)
            except L.LedgerError as e:
                conn.close()
                self._error(400, str(e))
                return
            finally:
                conn.close()
            self._json(200, {"ok": True})
            return
        if _by_id("categories", None) is not None:
            return
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
        def _del_by_id(name_prefix):
            p = urlparse(self.path).path
            if not p.startswith("/api/" + name_prefix + "/"):
                return None
            try:
                rid = int(p.rsplit("/", 1)[1])
            except ValueError:
                return False
            conn = L.connect()
            try:
                fam_id = L.ensure_seed(conn)["family_id"]
                row = conn.execute("SELECT * FROM {} WHERE id=? AND family_id=?".format(name_prefix), (rid, fam_id)).fetchone()
                if row is None:
                    return False
                if name_prefix == "categories":
                    q = parse_qs(urlparse(self.path).query)
                    merge_to = (q.get("merge_to") or [None])[0]
                    L.delete_category(conn, fam_id, row["name"], merge_to)
                else:
                    L.delete_member(conn, fam_id, row["name"])
            except L.LedgerError as e:
                conn.close()
                self._error(400, str(e))
                return True
            finally:
                conn.close()
            self._json(200, {"ok": True})
            return True
        # 基金持仓 DELETE /api/funds/<id>
        fp = urlparse(self.path).path
        if fp.startswith("/api/funds/"):
            try:
                rid = int(fp.rsplit("/", 1)[1])
            except ValueError:
                self._error(400, "bad id")
                return
            conn = L.connect()
            try:
                fam_id = L.ensure_seed(conn)["family_id"]
                L.delete_position(conn, fam_id, rid)
            except L.LedgerError as e:
                conn.close()
                self._error(400, str(e))
                return
            finally:
                conn.close()
            self._json(200, {"ok": True})
            return
        if _del_by_id("categories") is not None:
            return
        if _del_by_id("members") is not None:
            return
        if urlparse(self.path).path.startswith("/api/drafts/"):
            conn = L.connect()
            try:
                fam_id = L.ensure_seed(conn)["family_id"]
                L.delete_draft(conn, fam_id, int(urlparse(self.path).path.rsplit("/", 1)[1]))
            except (L.LedgerError, ValueError) as e:
                conn.close()
                self._error(404, str(e))
                return
            finally:
                conn.close()
            self._json(200, {"ok": True})
            return
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