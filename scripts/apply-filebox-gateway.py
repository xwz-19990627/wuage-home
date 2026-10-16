#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""为公网网关添加「文件盒子」路由（幂等；先备份后修改）。

路由方案（与家庭入口 /home 体系一致）：
  /home/files/*  → 去前缀转发 127.0.0.1:17624   （正式入口）
  /files/*      → 302 跳到 /home/files/        （兼容旧书签）

目标文件：/root/dsh_rsc/public-gateway/server.js（可用环境变量 FILEBOX_GATEWAY 覆盖）
应用后只重启网关：sudo systemctl restart dsh-gateway.service（不再影响 dsh web）
"""
import datetime
import os
import shutil
import sys

GATEWAY = os.environ.get("FILEBOX_GATEWAY", "/root/dsh_rsc/public-gateway/server.js")
MARK = "// filebox (wuage-home 工具 01) START"
TARGET = "http://127.0.0.1:17624"

PROXY_BLOCK = """// filebox (wuage-home 工具 01) START
// 文件盒子反代：https://<host>:8443/home/files/ → %(TARGET)s
const FILEBOX_TARGET = "%(TARGET)s"
const fileboxProxy = httpProxy.createProxyServer({ target: FILEBOX_TARGET, xfwd: true })
fileboxProxy.on('proxyReq', (proxyReq, req) => {
  req.headers.connection = 'keep-alive'
  proxyReq.setHeader('host', '127.0.0.1:17624')
})
fileboxProxy.on('error', (err, req, res) => {
  console.error('[filebox proxy error]', err.message)
  if (res && !res.headersSent) {
    res.writeHead(502, { 'Content-Type': 'text/plain; charset=utf-8' })
    res.end('Bad Gateway')
  }
})
// filebox (wuage-home 工具 01) END
""" % {"TARGET": TARGET}

ROUTE_BLOCK = """  // filebox (wuage-home 工具 01) START
  // 家庭工具 · 文件盒子：/files* → /home/files/（兼容）；/home/files* → 17624
  const fileboxBase = '/home/files'
  if (req.url === '/files' || req.url.startsWith('/files/')) {
    res.writeHead(302, { Location: fileboxBase + '/' })
    return res.end()
  }
  if (req.url === fileboxBase || req.url.startsWith(fileboxBase + '/')) {
    req.url = req.url.slice(fileboxBase.length)
    if (req.url === '') req.url = '/'
    if (!req.url.startsWith('/')) req.url = '/' + req.url
    return fileboxProxy.web(req, res)
  }
  // filebox (wuage-home 工具 01) END
"""

def main():
    if not os.path.isfile(GATEWAY):
        print("未找到网关文件：%s" % GATEWAY, file=sys.stderr)
        return 1
    src = open(GATEWAY, encoding="utf-8").read()
    if MARK in src:
        print("已打过补丁，跳过（%s）" % GATEWAY)
        return 0

    # 1. 备份
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = GATEWAY + ".bak-" + stamp
    shutil.copy2(GATEWAY, backup)
    print("备份 -> %s" % backup)

    # 2. 插入反代对象（在 https server 之前）
    anchor = "const server = https.createServer({ cert, key }, async (req, res) => {"
    if anchor not in src:
        print("锚点找不到：%r" % anchor, file=sys.stderr)
        return 1
    src = src.replace(anchor, PROXY_BLOCK + "\n\n" + anchor, 1)

    # 3. 插入 /home/files 路由（在家庭入口路由注释之前，必须先于 /home 通用转发）
    route_anchor = "  // 家庭入口路由：/home/ = 主页(纯入口 launcher)；/ledger* 与 /home/ledger* = 记账工作区"
    if route_anchor not in src:
        print("路由锚点找不到，请检查 server.js 结构", file=sys.stderr)
        return 1
    src = src.replace(route_anchor, ROUTE_BLOCK + "\n\n" + route_anchor, 1)

    open(GATEWAY, "w", encoding="utf-8").write(src)
    print("补丁完成：%s" % GATEWAY)
    print("接下来只重启网关：sudo systemctl restart dsh-gateway.service")
    print("验证：curl -sk -o /dev/null -w '%{http_code}\n' https://127.0.0.1:8443/login && 登录后访问 /home/files/")
    return 0

if __name__ == "__main__":
    sys.exit(main())
