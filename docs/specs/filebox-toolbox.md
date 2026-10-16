# Spec：工具箱模块 + 文件盒子「跨端文件传输」（v0.3.0-rc.1）

> 工具箱是 wuage-home 的新模块：一个在「蛙哥家庭助手」主页下挂载工具类能力的容器；
> 本版本引入工具 01「文件盒子」，解决全家设备（iPhone / Android / 电脑）之间的文件上传下载。

## 目标

一句话：家人不用装任何 App，用浏览器在 https://47.99.118.10:8443/home/files/ 上传/下载/删除
服务器上的文件，覆盖「设备间互传」（A 上传 → B 下载）与「上传到本服务器」两个诉求。

## 范围

- 做：
  - 纯 Python 标准库 Web 服务（scripts/filebox.py），零第三方依赖。
  - 手机友好页面（web/filebox.html），风格与记账面板一致。
  - 上传（多选、串行、进度条）、下载（attachment、支持单段 Range）、页面删除（带确认）。
  - 定期自动清理（默认保留 30 天，启动清一次 + 每 6 小时清一次 + 手动触发）。
  - 数据固定目录 data/files/（迁移 = 拷目录）；单据文件上限与总配额可配。
  - 公网网关 /home/files/ 反向代理（家庭入口命名空间，先于 /home 通用转发），/files* 302 兼容；复用网关登录/30 天免登/HTTPS 证书。
- 不做（本期排除）：
  - 不建 App、不做登录注册（复用网关账号）。
  - 不做 WebDAV / 断点续传（连传中断只支持浏览器/下载工具 Range 续下，上传续传留待 copyparty 或后续）。
  - 不做文件预览/分享链接/多用户隔离（全家人共用一个盒子）。

## 数据契约

- 无数据库；文件平铺 `<data>/files/` 目录，隐藏/临时文件以 `.` 开头被列表忽略。
- 冲突策略：同文件名自动加序号（a.txt → a (1).txt），硬链接占位保证并发安全。
- 配置（环境变量，命令行参数可覆盖）：FILEBOX_HOST / FILEBOX_PORT / FILEBOX_DATA /
  FILEBOX_RETENTION_DAYS / FILEBOX_MAX_MB / FILEBOX_QUOTA_MB / FILEBOX_CLEANUP_HOURS。

## 接口（API / 命令）

- HTTP（相对路径，经网关 /home/files/ 前缀转发不变语义）：
  - GET  /                    → 页面
  - GET  /api/list            → 文件列表 + 用量/配额/保留期
  - POST /api/upload?name=XX  → 请求体为原始字节上传
  - GET  /f/<name>            → 下载（attachment；支持 bytes= 单段 Range）
  - DELETE /api/delete?name=  → 永久删除
  - POST /api/cleanup         → 立即清理超期文件
- CLI：`python3.11 scripts/filebox.py`（服务）/ `--cleanup-now`（一次性清理，适合 cron）。
- 脚本：scripts/start-filebox.sh / stop-filebox.sh。

## 依赖 LLM 的点

- 无。全部是确定性代码（与记账解析不同，本工具不需要模型参与）。

## 验收标准（可测试）

- [ ] 本机 curl 全链路：list 空 → 上传（含中文名/同名冲突/路径穿越拦截/超限拒绝）→
      下载（含 Range）→ 删除 → 清理。
- [ ] 页面在 127.0.0.1:17624 打开正常，上传/下载/删除/用量展示可用。
- [ ] 网关打补丁后 https://…:8443/home/files/ 登录可达；/files 302 到 /home/files/，与 /home/ 同样受网关账号保护。
- [ ] 服务重启后数据仍在；迁移 = 拷走 data/files。

## 发布与归档标尺

- 本版本 CHANGELOG 条目、tag、归档文件就绪时即完成（归档待网关补丁应用后补）。
