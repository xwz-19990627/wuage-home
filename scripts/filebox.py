#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wuage-home 工具 01 · 跨端文件盒子（纯 Python 标准库，零第三方依赖）。

家人/设备（iPhone / Android / 电脑）通过浏览器上传、下载、删除服务器上的文件：
  上传到本服务器  →  文件落到固定数据目录
  设备间互传     →  A 上传，B 打开页面下载（数据都经过本服务器，不额外装 App）

关键设计（对应家人需求）：
  - 零依赖：只用标准库 http.server / threading / os，与 scripts/web.py 同风格。
  - 固定数据目录：所有文件平铺在 <data>/files/ 下，迁移 = 拷走这个目录即可。
  - 页面删除：DELETE /api/delete?name=...（页面有确认框）。
  - 定期清理：守护线程每 N 小时删一次超期文件（默认 30 天）；启动时也清一次；
    也可手工触发 POST /api/cleanup，或用 --cleanup-now 一次性清理（适合加 cron）。

用法：
  python3.11 scripts/filebox.py                              # 127.0.0.1:17624
  环境变量：FILEBOX_HOST / FILEBOX_PORT / FILEBOX_DATA / FILEBOX_RETENTION_DAYS
            FILEBOX_MAX_MB（单文件上限）/ FILEBOX_QUOTA_MB（总配额）
            FILEBOX_CLEANUP_HOURS（自动清理周期）
  python3.11 scripts/filebox.py --cleanup-now                # 只做一次清理后退出

API（全部为相对路径；经公网网关 /home/files/ 前缀转发后同样可用）：
  GET  /                    → 页面（web/filebox.html）
  GET  /api/list            → 文件列表 + 用量/配额/保留期
  POST /api/upload?name=XX  → 上传（请求体为文件原始字节）
  GET  /f/<name>            → 下载（attachment；支持单段 Range 续传）
  DELETE /api/delete?name=  → 删除（永久删除）
  POST /api/cleanup         → 立即清理超期文件
"""
import argparse
import json
import os
import re
import signal
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_PATH = os.path.join(REPO_ROOT, "web", "filebox.html")
CHUNK = 64 * 1024  # 读写缓冲 64 KB
MB = 1024 * 1024


def _resolve_data_dir():
    """数据根解析：WUAGE_DATA > WUAGE_HOME/data > /root/wuage/data。

    与 ledger.py / finance.py / health.py 同一套规则：即使没设环境变量也指向数据根，
    **绝不落到仓库内的 REPO_ROOT/data**。
    2026-10-16 修正：历史默认值是 REPO_ROOT/data/files，导致服务器上的文件盒子数据
    实际写在 /root/project/data/files（仓库目录内），脱离数据根 → 备份和迁移都会漏。
    """
    d = os.environ.get("WUAGE_DATA") or ""
    if d.strip():
        return d
    h = os.environ.get("WUAGE_HOME") or ""
    if h.strip():
        return os.path.join(h, "data")
    return "/root/wuage/data"


WUAGE_DATA = _resolve_data_dir()


def env_int(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class Options:
    """文件盒子的运行时配置（命令行参数 > 环境变量 > 默认值）。"""

    def __init__(self):
        self.host = os.environ.get("FILEBOX_HOST", "127.0.0.1")
        self.port = env_int("FILEBOX_PORT", 17624)
        self.data_dir = os.environ.get("FILEBOX_DATA") or os.path.join(WUAGE_DATA, "files")
        self.retention_days = env_int("FILEBOX_RETENTION_DAYS", 30)
        self.max_mb = env_int("FILEBOX_MAX_MB", 512)
        self.quota_mb = env_int("FILEBOX_QUOTA_MB", 2048)
        self.cleanup_hours = env_int("FILEBOX_CLEANUP_HOURS", 6)


OPTS = Options()


# ---------------------------------------------------------------------------
# 存储层
# ---------------------------------------------------------------------------

def ensure_store():
    os.makedirs(OPTS.data_dir, exist_ok=True)


def list_files():
    """返回 [{name,size,mtime,is_dir:False}]，按 mtime 倒序；跳过隐藏/临时文件。"""
    out = []
    with os.scandir(OPTS.data_dir) as it:
        for e in it:
            if e.name.startswith("."):
                continue
            if not e.is_file():
                continue
            try:
                st = e.stat()
            except OSError:
                continue
            out.append({"name": e.name, "size": st.st_size, "mtime": int(st.st_mtime * 1000)})
    out.sort(key=lambda f: f["mtime"], reverse=True)
    return out


def total_usage():
    total = 0
    count = 0
    for f in list_files():
        total += f["size"]
        count += 1
    return total, count


def sanitize_name(raw):
    """把任意输入收敛成一个安全的裸文件名（无路径、无控制符、防隐藏文件）。"""
    if raw is None:
        raise ValueError("name 不能为空")
    name = raw.replace("\\", "/")
    name = os.path.basename(name)
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    name = name.lstrip(".")  # 禁止隐藏文件/目录穿越
    name = name.strip(" ").strip(".")
    if not name or name in (".", ".."):
        raise ValueError("非法的文件名")
    if len(name.encode("utf-8")) > 220:
        raise ValueError("文件名过长")
    return name


def write_upload(name, size, stream):
    """把流式请求体落盘为最终文件名，返回最终文件名。

    原子写 + 并发安全：先写隐藏临时文件，再用硬链接占位（已存在则自动换序号），
    不存在 os.replace 覆盖同名文件的竞态。
    """
    tmp = os.path.join(OPTS.data_dir, ".upload-{0}.part".format(uuid.uuid4().hex))
    try:
        # 精确读取 Content-Length 字节：HTTP keep-alive 下连接不会在体后关闭，
        # 不能读 EOF（会永久阻塞），必须按声明长度读满为止。
        with open(tmp, "wb") as fh:
            remaining = size
            while remaining > 0:
                chunk = stream.read(min(CHUNK, remaining))
                if not chunk:  # 客户端提前断开
                    raise ValueError("请求体提前结束")
                fh.write(chunk)
                remaining -= len(chunk)
        n = 0
        while True:
            stem, ext = os.path.splitext(name)
            candidate = name if n == 0 else "{0} ({1}){2}".format(stem, n, ext)
            target = os.path.join(OPTS.data_dir, candidate)
            try:
                os.link(tmp, target)  # 独占占位：目标已存在则抛 FileExistsError
                return candidate
            except FileExistsError:
                n += 1
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def delete_file(name):
    safe = sanitize_name(name)
    path = os.path.join(OPTS.data_dir, safe)
    if not os.path.isfile(path):
        raise FileNotFoundError(safe)
    os.unlink(path)
    return safe


def cleanup_once():
    """删除超过保留期的文件；返回被删文件名列表。"""
    cutoff = time.time() - OPTS.retention_days * 86400
    deleted = []
    for f in list_files():
        if f["mtime"] / 1000 < cutoff:
            try:
                os.unlink(os.path.join(OPTS.data_dir, f["name"]))
                deleted.append(f["name"])
            except OSError:
                pass
    return deleted


def cleanup_loop():
    """后台守护线程：启动后先清一次，之后每 CLEANUP_HOURS 清一次。"""
    while True:
        try:
            deleted = cleanup_once()
            if deleted:
                print("[filebox] 自动清理 {0} 个文件: {1}".format(len(deleted), ", ".join(deleted[:5])), flush=True)
        except Exception as exc:  # 守护线程不允许静默死亡
            print("[filebox] 清理异常: {0}".format(exc), file=sys.stderr, flush=True)
        time.sleep(OPTS.cleanup_hours * 3600)


# ---------------------------------------------------------------------------
# HTTP 层
# ---------------------------------------------------------------------------

def json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def decode_utf8_name(value):
    """http.server 把请求行按 latin-1 解码：curl 直发原始 UTF-8 文件名会变乱码。
    这里兜底二次解码：仅在「latin-1 重编码后恰为合法 UTF-8」时还原，
    其余情况（真正的 ASCII/已解好的 Unicode 名）原样返回。"""
    if not value or ord(max(value)) < 128:
        return value
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


class FileboxHandler(BaseHTTPRequestHandler):
    server_version = "Filebox/1.0"
    protocol_version = "HTTP/1.1"  # 连接复用，减少移动端 TLS 握手

    def log_message(self, fmt, *args):
        print("[filebox] {0} {1}".format(self.address_string(), fmt % args), flush=True)

    # ---- 基础工具 ----------------------------------------------------------

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        extra = dict(extra or {})
        length = str(len(body))
        if "Content-Length" in extra:  # 允许调用方（如 HEAD 探测）覆盖长度
            length = extra.pop("Content-Length")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", length)
        self.send_header("Cache-Control", "no-store")
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json_bytes(obj))

    def _err(self, code, message, extra=None):
        if extra:
            self._send(code, json_bytes({"ok": False, "error": message}), extra=extra)
        else:
            self._json(code, {"ok": False, "error": message})

    def _query(self):
        q = parse_qs(urlparse(self.path).query)
        return {k: [decode_utf8_name(v) for v in vals] for k, vals in q.items()}

    def _path(self):
        return decode_utf8_name(unquote(urlparse(self.path).path))

    # ---- 路由 ---------------------------------------------------------------

    def do_GET(self):
        path = self._path()
        try:
            if path in ("/", "/index.html"):
                self._serve_ui()
            elif path == "/api/list":
                self._api_list()
            elif path.startswith("/f/"):
                self._api_download(path[len("/f/"):])
            elif path == "/favicon.ico":
                self._send(204, b"")
            else:
                self._err(404, "Not Found")
        except BrokenPipeError:
            pass

    def do_HEAD(self):
        # 下载链接做头探测时返回（无 body）
        path = self._path()
        if path.startswith("/f/"):
            try:
                self._api_download(path[len("/f/"):], head=True)
            except BrokenPipeError:
                pass
        else:
            self._send(200, b"")

    def do_POST(self):
        path = self._path()
        try:
            if path == "/api/upload":
                self._api_upload()
            elif path == "/api/cleanup":
                self._api_cleanup()
            else:
                self._err(404, "Not Found")
        except BrokenPipeError:
            pass

    def do_DELETE(self):
        path = self._path()
        try:
            if path == "/api/delete":
                self._api_delete()
            else:
                self._err(404, "Not Found")
        except BrokenPipeError:
            pass

    # ---- 页面 ---------------------------------------------------------------

    def _serve_ui(self):
        if not os.path.isfile(UI_PATH):
            self._err(500, "未找到页面文件 web/filebox.html")
            return
        with open(UI_PATH, "rb") as fh:
            body = fh.read()
        self._send(200, body, "text/html; charset=utf-8")

    # ---- 列表 ---------------------------------------------------------------

    def _api_list(self):
        total, count = total_usage()
        self._json(200, {
            "ok": True,
            "files": list_files(),
            "usageBytes": total,
            "fileCount": count,
            "maxMbPerFile": OPTS.max_mb,
            "quotaMb": OPTS.quota_mb,
            "retentionDays": OPTS.retention_days,
            "storedAt": OPTS.data_dir,
        })

    # ---- 上传 ---------------------------------------------------------------

    def _api_upload(self):
        query = self._query()
        try:
            name = sanitize_name(query.get("name", [None])[0])
        except ValueError as exc:
            return self._err(400, str(exc))

        length = self.headers.get("Content-Length")
        try:
            size = int(length) if length else -1
        except ValueError:
            size = -1
        if size < 0:
            self.close_connection = True
            return self._err(411, "缺少 Content-Length（请用原始字节上传）")

        if size > OPTS.max_mb * MB:
            self.close_connection = True  # 请求体不再读取，关闭连接避免协议错位
            return self._err(413, "单文件超过上限 {0} MB".format(OPTS.max_mb))

        total, _ = total_usage()
        if total + size > OPTS.quota_mb * MB:
            self.close_connection = True
            return self._err(507, "服务器配额不足（已用 {0:.1f} / {1} MB），请先删除旧文件".format(total / MB, OPTS.quota_mb))

        try:
            final = write_upload(name, size, self.rfile)
        except ValueError as exc:
            return self._err(400, str(exc))
        except OSError as exc:
            return self._err(500, "写入失败：{0}".format(exc))

        # 如果没有按 Content-Length 传足（异常中断），把残留文件删掉
        actual = os.path.getsize(os.path.join(OPTS.data_dir, final))
        if actual != size:
            try:
                os.unlink(os.path.join(OPTS.data_dir, final))
            except OSError:
                pass
            return self._err(400, "上传中断（收到 {0} 字节，预期 {1}）".format(actual, size))

        self._json(200, {"ok": True, "name": final, "size": actual})

    # ---- 下载 ---------------------------------------------------------------

    def _api_download(self, raw_name, head=False):
        try:
            name = sanitize_name(unquote(raw_name))
        except ValueError:
            return self._err(400, "非法文件名")
        path = os.path.join(OPTS.data_dir, name)
        if not os.path.isfile(path):
            return self._err(404, "文件不存在")

        size = os.path.getsize(path)
        start, end = 0, size - 1
        status = 200
        headers = {}
        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                a, b = m.groups()
                if a == "" and b == "":
                    pass
                elif a == "":
                    start = max(0, size - int(b))
                elif b == "":
                    start = int(a)
                    end = size - 1
                else:
                    start, end = int(a), int(b)
                if start >= size or start > end:
                    return self._err(416, "Range 无效", extra={
                        "Content-Range": "bytes */{0}".format(size)})
                end = min(end, size - 1)
                status = 206
                headers["Content-Range"] = "bytes {0}-{1}/{2}".format(start, end, size)

        ascii_name = re.sub(r"[^ -~]", "_", name).replace('"', "_") or "file"
        headers["Content-Disposition"] = (
            "attachment; filename=\"" + ascii_name + "\"; filename*=UTF-8''" + quote(name))
        headers["Accept-Ranges"] = "bytes"

        body_len = end - start + 1
        if head:
            head_extra = dict(headers)
            head_extra["Content-Length"] = str(body_len)
            head_extra.pop("Content-Range", None)
            self._send(status, b"", "application/octet-stream", head_extra)
            return

        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(body_len))
        self.send_header("Cache-Control", "no-store")
        for k, v in headers.items():
            if v:
                self.send_header(k, v)
        self.end_headers()
        with open(path, "rb") as fh:
            fh.seek(start)
            remain = body_len
            while remain > 0:
                chunk = fh.read(min(CHUNK, remain))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remain -= len(chunk)

    # ---- 删除 ---------------------------------------------------------------

    def _api_delete(self):
        query = self._query()
        name = query.get("name", [None])[0]
        try:
            deleted = delete_file(name)
        except (ValueError, FileNotFoundError) as exc:
            return self._err(400, str(exc))
        self._json(200, {"ok": True, "deleted": deleted})

    # ---- 清理 ---------------------------------------------------------------

    def _api_cleanup(self):
        deleted = cleanup_once()
        self._json(200, {"ok": True, "deleted": deleted, "count": len(deleted)})


def build_server(opts):
    class Server(ThreadingHTTPServer):
        daemon_threads = True

    return Server((opts.host, opts.port), FileboxHandler)


def main():
    ap = argparse.ArgumentParser(description="wuage-home 跨端文件盒子")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--data", default=None)
    ap.add_argument("--retention-days", type=int, default=None)
    ap.add_argument("--max-mb", type=int, default=None)
    ap.add_argument("--quota-mb", type=int, default=None)
    ap.add_argument("--cleanup-hours", type=float, default=None)
    ap.add_argument("--cleanup-now", action="store_true", help="只执行一次清理后退出")
    args = ap.parse_args()

    if args.host is not None:
        OPTS.host = args.host
    if args.port is not None:
        OPTS.port = args.port
    if args.data is not None:
        OPTS.data_dir = args.data
    if args.retention_days is not None:
        OPTS.retention_days = args.retention_days
    if args.max_mb is not None:
        OPTS.max_mb = args.max_mb
    if args.quota_mb is not None:
        OPTS.quota_mb = args.quota_mb
    if args.cleanup_hours is not None:
        OPTS.cleanup_hours = args.cleanup_hours

    ensure_store()

    if args.cleanup_now:
        deleted = cleanup_once()
        print("清理完成：删除 {0} 个文件".format(len(deleted)))
        if deleted:
            print("  " + "\n  ".join(deleted))
        return

    # 启动后先清一次 + 周期清理
    try:
        cleanup_once()
    except Exception as exc:
        print("[filebox] 启动清理失败: {0}".format(exc), file=sys.stderr)
    threading.Thread(target=cleanup_loop, daemon=True).start()

    server = build_server(OPTS)
    print("文件盒子已启动：http://{0}:{1}".format(OPTS.host, OPTS.port), flush=True)
    print("  数据目录：{0}（迁移 = 拷走该目录）".format(OPTS.data_dir), flush=True)
    print("  保留 {0} 天自动清理 · 单文件 ≤ {1} MB · 总配额 {2} MB".format(
        OPTS.retention_days, OPTS.max_mb, OPTS.quota_mb), flush=True)

    def _term(_sig, _frame):
        # 注意：不能在信号处理器里调 server.shutdown()——它是在 serve_forever
        # 所在线程内同步等待事件，会自锁；直接退出，由 finally 收尾。
        print("[filebox] 收到退出信号，停止服务", flush=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
