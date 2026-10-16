#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""为公网网关添加「理财助手」路由（幂等；先备份后修改）。

路由方案（与文件盒子一致）：
  /home/finance/* → 去前缀转发 127.0.0.1:17625（正式入口）
  /finance/*     → 302 跳到 /home/finance/（兼容旧书签）

目标文件：/root/dsh_rsc/public-gateway/server.js（可用环境变量 FINANCE_GATEWAY 覆盖）
应用后只重启网关：sudo systemctl restart dsh-gateway.service
"""
import datetime
import os
import shutil
import sys

GATEWAY = os.environ.get("FINANCE_GATEWAY", "/root/dsh_rsc/public-gateway/server.js")
MARK = "// finance (wuage-home 工具 02) START"
TARGET = "http://127.0.0.1:17625"

PROXY_BLOCK = """// finance (wuage-home 工具 02) START
// 理财助手反代：https://<host>:8443/home/finance/ → %(TARGET)s
const FINANCE_TARGET = "%(TARGET)s"
const financeProxy = httpProxy.createProxyServer({ target: FINANCE_TARGET, xfwd: true })
financeProxy.on('proxyReq', (proxyReq, req) => {
  req.headers.connection = 'keep-alive'
  proxyReq.setHeader('host', '127.0.0.1:17625')
})
financeProxy.on('error', (err, req, res) => {
  console.error('[finance proxy error]', err.message)
  if (res && !res.headersSent) {
    res.writeHead(502, { 'Content-Type': 'text/plain; charset=utf-8' })
    res.end('Bad Gateway')
  }
})
// finance (wuage-home 工具 02) END
""" % {"TARGET": TARGET}

ROUTE_BLOCK = """  // finance (wuage-home 工具 02) START
  // 家庭工具 · 理财助手：/finance* → /home/finance/（兼容）；/home/finance* → 17625
  const financeBase = '/home/finance'
  if (req.url === '/finance' || req.url.startsWith('/finance/')) {
    res.writeHead(302, { Location: financeBase + '/' })
    return res.end()
  }
  if (req.url === financeBase || req.url.startsWith(financeBase + '/')) {
    req.url = req.url.slice(financeBase.length)
    if (req.url === '') req.url = '/'
    if (!req.url.startsWith('/')) req.url = '/' + req.url
    return financeProxy.web(req, res)
  }
  // finance (wuage-home 工具 02) END
"""


def main():
    if not os.path.isfile(GATEWAY):
        print("未找到网关文件：%s" % GATEWAY, file=sys.stderr)
        return 1
    src = open(GATEWAY, encoding="utf-8").read()
    if MARK in src:
        print("已打过补丁，跳过（%s）" % GATEWAY)
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = GATEWAY + ".bak-" + stamp
    shutil.copy2(GATEWAY, backup)
    print("备份 -> %s" % backup)

    anchor = "const server = https.createServer({ cert, key }, async (req, res) => {"
    if anchor not in src:
        print("锚点找不到：%r" % anchor, file=sys.stderr)
        return 1
    src = src.replace(anchor, PROXY_BLOCK + "\n\n" + anchor, 1)

    route_anchor = "  // 家庭入口路由：/home/ = 主页(纯入口 launcher)；/ledger* 与 /home/ledger* = 记账工作区"
    if route_anchor not in src:
        print("路由锚点找不到，请检查 server.js 结构", file=sys.stderr)
        return 1
    src = src.replace(route_anchor, ROUTE_BLOCK + "\n\n" + route_anchor, 1)

    open(GATEWAY, "w", encoding="utf-8").write(src)
    print("补丁完成：%s" % GATEWAY)
    print("接下来只重启网关：sudo systemctl restart dsh-gateway.service")
    return 0


if __name__ == "__main__":
    sys.exit(main())