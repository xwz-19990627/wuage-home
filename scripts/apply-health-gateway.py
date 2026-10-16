#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""为公网网关添加「健康助手」路由（幂等；先备份后修改）。

路由方案（与理财助手/文件盒子一致）：
  /home/health/* → 去前缀转发 127.0.0.1:17626（正式入口）
  /health/*      → 302 跳到 /home/health/（兼容旧书签）

目标文件：/root/dsh_rsc/public-gateway/server.js（可用环境变量 HEALTH_GATEWAY 覆盖）
应用后只重启网关：sudo systemctl restart dsh-gateway.service
"""
import datetime
import os
import shutil
import sys

GATEWAY = os.environ.get("HEALTH_GATEWAY", "/root/dsh_rsc/public-gateway/server.js")
MARK = "// health (wuage-home 工具 03) START"
TARGET = "http://127.0.0.1:17626"

PROXY_BLOCK = """// health (wuage-home 工具 03) START
// 健康助手反代：https://<host>:8443/home/health/ → %(TARGET)s
const HEALTH_TARGET = "%(TARGET)s"
const healthProxy = httpProxy.createProxyServer({ target: HEALTH_TARGET, xfwd: true })
healthProxy.on('proxyReq', (proxyReq, req) => {
  req.headers.connection = 'keep-alive'
  proxyReq.setHeader('host', '127.0.0.1:17626')
})
healthProxy.on('error', (err, req, res) => {
  console.error('[health proxy error]', err.message)
  if (res && !res.headersSent) {
    res.writeHead(502, { 'Content-Type': 'text/plain; charset=utf-8' })
    res.end('Bad Gateway')
  }
})
// health (wuage-home 工具 03) END
""" % {"TARGET": TARGET}

ROUTE_BLOCK = """  // health (wuage-home 工具 03) START
  // 家庭工具 · 健康助手：/health* → /home/health/（兼容）；/home/health* → 17626
  const healthBase = '/home/health'
  if (req.url === '/health' || req.url.startsWith('/health/')) {
    res.writeHead(302, { Location: healthBase + '/' })
    return res.end()
  }
  if (req.url === healthBase || req.url.startsWith(healthBase + '/')) {
    req.url = req.url.slice(healthBase.length)
    if (req.url === '') req.url = '/'
    if (!req.url.startsWith('/')) req.url = '/' + req.url
    return healthProxy.web(req, res)
  }
  // health (wuage-home 工具 03) END
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
    src = src.replace(route_anchor, ROUTE_BLOCK + "\n\n" + route_anchor, 1)

    open(GATEWAY, "w", encoding="utf-8").write(src)
    print("补丁完成：%s" % GATEWAY)
    print("接下来只重启网关：sudo systemctl restart dsh-gateway.service")
    return 0


if __name__ == "__main__":
    sys.exit(main())
