#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""为公网网关添加「工具箱 Hub + 理财助手」路由（幂等；先备份后修改）。

一次补齐两条家庭入口：
  1. /home/toolbox/ → 静态工具箱 Hub 页面（web/toolbox.html）
  2. /home/finance/ → 理财助手反代 127.0.0.1:17625（/finance* 302 兼容）

与 apply-filebox-gateway.py 是同一套风格；已存在的 finance 补丁会被识别并跳过，
不会重复打。
"""
import datetime
import os
import shutil
import sys

GATEWAY = os.environ.get("TOOLBOX_GATEWAY", "/root/dsh_rsc/public-gateway/server.js")
TOOLBOX_HTML = os.environ.get("TOOLBOX_HTML", "/root/project/web/toolbox.html")
FINANCE_MARK = "// finance (wuage-home 工具 02) START"
TOOLBOX_MARK = "// toolbox (wuage-home 工具入口) START"
FINANCE_TARGET = "http://127.0.0.1:17625"

FINANCE_PROXY_BLOCK = """// finance (wuage-home 工具 02) START
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
""" % {"TARGET": FINANCE_TARGET}

FINANCE_ROUTE_BLOCK = """  // finance (wuage-home 工具 02) START
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

TOOLBOX_ROUTE_BLOCK = """  // toolbox (wuage-home 工具入口) START
  // 家庭工具 · 工具箱 Hub：/toolbox* → /home/toolbox/（兼容）；/home/toolbox/ → 静态页
  const toolboxBase = '/home/toolbox'
  if (req.url === '/toolbox' || req.url === '/home/toolbox') {
    res.writeHead(302, { Location: toolboxBase + '/' })
    return res.end()
  }
  if (req.url === toolboxBase || req.url.startsWith(toolboxBase + '/')) {
    try {
      const data = fs.readFileSync(%(TOOLBOX_HTML)r)
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' })
      return res.end(data)
    } catch (e) {
      return deny(res, 404, 'Toolbox not found')
    }
  }
  // toolbox (wuage-home 工具入口) END
""" % {"TOOLBOX_HTML": TOOLBOX_HTML}


def main():
    if not os.path.isfile(GATEWAY):
        print("未找到网关文件：%s" % GATEWAY, file=sys.stderr)
        return 1
    src = open(GATEWAY, encoding="utf-8").read()
    changed = False

    if FINANCE_MARK not in src:
        anchor = "const server = https.createServer({ cert, key }, async (req, res) => {"
        if anchor not in src:
            print("proxy 锚点找不到：%r" % anchor, file=sys.stderr)
            return 1
        src = src.replace(anchor, FINANCE_PROXY_BLOCK + "\n\n" + anchor, 1)
        route_anchor = "  // 家庭入口路由：/home/ = 主页(纯入口 launcher)；/ledger* 与 /home/ledger* = 记账工作区"
        if route_anchor not in src:
            print("路由锚点找不到，请检查 server.js 结构", file=sys.stderr)
            return 1
        src = src.replace(route_anchor, FINANCE_ROUTE_BLOCK + "\n\n" + route_anchor, 1)
        changed = True
        print("已插入理财助手路由")
    else:
        print("理财助手路由已存在，跳过")

    if TOOLBOX_MARK not in src:
        route_anchor = "  // 家庭入口路由：/home/ = 主页(纯入口 launcher)；/ledger* 与 /home/ledger* = 记账工作区"
        if route_anchor not in src:
            print("路由锚点找不到，请检查 server.js 结构", file=sys.stderr)
            return 1
        src = src.replace(route_anchor, TOOLBOX_ROUTE_BLOCK + "\n\n" + route_anchor, 1)
        changed = True
        print("已插入工具箱 Hub 静态页路由")
    else:
        print("工具箱 Hub 路由已存在，跳过")

    if not changed:
        print("没有需要新增的补丁")
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = GATEWAY + ".bak-" + stamp
    shutil.copy2(GATEWAY, backup)
    print("备份 -> %s" % backup)
    open(GATEWAY, "w", encoding="utf-8").write(src)
    print("补丁完成：%s" % GATEWAY)
    print("接下来只重启网关：sudo systemctl restart dsh-gateway.service")
    return 0


if __name__ == "__main__":
    sys.exit(main())