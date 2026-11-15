# Monitor 腾讯云迁移

目标：新加坡 Lighthouse 2 核 4GB，先包月试运行。代码从 GitHub 构建，
运行目录 `/opt/monitor`。数据、API 配置和历史备份均在 release 目录之外。

## 验收前禁止切换

- 导出 Railway `/app/data` 全部内容（含隐藏文件、SQLite WAL/SHM、截图、HTML、浏览器配置）。
- 核对 Railway 服务变量、共享变量、应用 settings 表，以及持久卷之外是否有业务文件。
- Railway 自带 `*.up.railway.app` 地址不能搬到腾讯云；确认新域名和 HTTPS，并更新所有调用方。
- 本机每日 BOSS 任务使用电脑上的独立登录 profile；保留其原文件，在切换时更新 API 地址。
  Windows 浏览器登录状态不能靠复制目录保证在 Linux 可用，不擅自删除或替代本地采集能力。
- 当前工作目录有尚未提交的功能改动。迁移以已核实的线上提交为基准，不能偷偷把工作区改动一起发布。
- 停止原端写入后制作最终清单；首次启动新应用前比对完整清单，之后再验收应用启动带来的数据变动。
- 逐一验证品牌、产品、销售、营销、用户之声、招聘、创作者、市场份额、网页监控、历史截图/HTML、
  设置与第三方 API、手动采集、后台定时采集、本地 BOSS 回传、GitHub 更新部署。
- `health` 成功不等于完成。受外部登录/API 限制的功能要记录阻塞项，不以通过单元测试代替线上验收。

## 数据清单

需要 Python 3.10+。必须先停止全部写入者；`--writers-stopped` 是操作方确认，
不是自动检测/停止写入的功能。文件扫描前后的变化检测也不能替代真正停写。

```sh
python3 data_inventory.py create --data /app/data --manifest /tmp/source-manifest.json --writers-stopped
tar -czf /tmp/monitor-data.tar.gz -C /app data
sha256sum /tmp/monitor-data.tar.gz > /tmp/monitor-data.tar.gz.sha256
```

在新机**空的** `/opt/monitor/shared` 目录恢复，解压后尚不可启动应用：

```sh
sha256sum -c monitor-data.tar.gz.sha256
tar -xzf monitor-data.tar.gz -C /opt/monitor/shared
python3 data_inventory.py verify --data /opt/monitor/shared/data --manifest /opt/monitor/shared/source-manifest.json --writers-stopped
```

原文件逐一 SHA-256 校验，记录空目录、隐藏文件、符号链接及权限。
所有 SQLite 文件在临时副本中执行完整性检查，包含 WAL 中已提交的数据；
逐表比较行数、内容指纹、表结构及已有外键问题，不输出任何行内容或 API 密钥。
清单和数据包保存在仓库外的受限目录，保留一份独立于 VPS 的备份。

只有核验通过后才创建 `/opt/monitor/shared/data-verified`。这个文件是人工验收标记，
不能作为“所有功能迁移成功”的证明。

## 服务器文件与配置

安装 Docker Engine、Compose v2（支持 `up --wait`）、Python 3、curl、flock。
建立只有运维账号可读写的目录：

```text
/opt/monitor/shared/data/                  全部业务数据
/opt/monitor/shared/app.env                原应用凭证与运行配置（600 权限）
/opt/monitor/shared/compose.env            下列部署设置（600 权限）
/opt/monitor/shared/source-manifest.json    原端数据清单
/opt/monitor/backups/                      更新前备份（还需另存异地）
/opt/monitor/releases/<git-sha>/            每次代码发布，不混放业务数据
```

验收阶段的 `compose.env`：

```dotenv
MONITOR_SCHEDULER=0
MONITOR_SITE=localhost
PROXY_BIND_ADDRESS=127.0.0.1
```

首先通过 SSH 隧道访问本机 8790 端口。确认 DNS、HTTPS 和数据功能验收后，
设置真实 `MONITOR_SITE`、公网绑定地址，启用 proxy profile。
切换单一写入者、更新本地回传端点后，恢复原有调度设置。
此过程不增加应用原先没有的 API 认证协议，也不能把密钥写进公开配置。

## GitHub 自动更新

工作流 `.github/workflows/monitor-vps.yml` 在 GitHub 构建完整 Docker 镜像，
测试数据校验工具及真实 Chromium 启动，保留 3 天构建制品。
首次构建与测试不会连接 VPS。

服务器配置和数据验收完成后，在仓库加密 Secrets 中配置：
`MONITOR_SSH_HOST`、`MONITOR_SSH_USER`、`MONITOR_SSH_PORT`、
`MONITOR_SSH_KEY`、`MONITOR_SSH_KNOWN_HOSTS`。
通过腾讯云可信控制台核验 SSH 主机指纹；不能盲目信任现场扫描的密钥。
部署账号需要 Docker 及 `/opt/monitor` 权限，Docker 权限等同于宿主机高权限，
使用此项目专用密钥，不复用个人密钥。

仓库变量 `MONITOR_VPS_DEPLOY_ENABLED=true` 才启用 main 分支自动部署。
分支构建始终不部署。每次发布校验镜像 SHA-256 和代码版本，停应用制作完整备份，
启动后等待健康检查。失败会让 GitHub 构建标红；应用启动过后不自动倒退数据库，
必须根据新写入情况恢复，避免假回滚造成数据丢失。

最后做一次 main 更新演练，确认代码自动上线且历史数据不变。
迁移期暂留 Railway 原卷与备份，最终停用计费资源要在完整验收之后安排。
