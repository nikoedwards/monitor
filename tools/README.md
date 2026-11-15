# 本地 BOSS 浏览器 worker

`boss_browser_worker.py` 是给本地 Codex 定时任务或 Windows Task Scheduler 调用的 BOSS 直聘采集脚本。它使用独立的 Playwright 持久化 profile，不读取或复制 Chrome 主 profile 的 Cookie。首次运行时以有头模式打开浏览器，人工完成 BOSS 登录和安全验证；之后每日任务复用这个 profile。

## 安装

```bash
pip install playwright
python -m playwright install chromium
```

Monitor API 默认使用 `http://127.0.0.1:8790`。如果 Monitor 部署在线上，可设置 `MONITOR_BASE_URL`，或传入 `--base-url https://...`。worker 会从该 Monitor 读取 active BOSS 招聘链接并把结果提交回同一地址；本机 API 暂时不可用时会回退读取本地数据库，并直接写入同一个本地 SQLite 数据库，不要求定时任务同时启动 Web 服务。

在品牌管理中配置 `dimension=hiring`、`platform=boss` 的 BOSS 链接。尚未配置链接时也可以先执行首次登录命令，worker 会打开 BOSS 首页并保存专用 profile。

## 首次登录

```bash
python -m tools.boss_browser_worker --login-only --headed --wait-for-login 180
```

浏览器打开后，由用户人工登录并完成任何安全验证。这个命令只保存专用 profile 后退出，不读取招聘源、不采集、不入库，也不判断登录是否成功。默认 profile 为 `data/browser_profiles/boss`，与普通 Chrome 会话分开；用户在普通 Chrome 中已登录不会自动让 worker 登录。需要更长操作时间可增加等待秒数（最大 1800）。

首次登录和每日运行必须使用相同的 `--profile-dir` 和 `BOSS_BROWSER_CHANNEL`。如选择已安装 Chrome，两个阶段都设置 `BOSS_BROWSER_CHANNEL=chrome`；默认使用 Playwright Chromium。不要把 Chrome 主 profile 传给 worker，不要并发使用同一个专用 profile。

## 每日运行

```bash
python -m tools.boss_browser_worker --headless --max-jobs 100
```

本地验证完成后，可由主任务配置 Codex 每日任务或 Windows Task Scheduler：工作目录设为项目根目录，程序选安装 Playwright 的 Python 绝对路径，参数使用上述每日命令的 `-m tools.boss_browser_worker ...` 部分；每日一次，并禁止重叠运行。保留 `data/browser_profiles/boss`。如需远程 Monitor，必须由主任务明确设置并授权 `--base-url`。本次基础设施修复没有创建调度、访问生产或执行真实采集。

脚本只提交当次实际读取到的职位。登录/验证码/安全验证、当前网址不明、浏览器不可用时立即全局停止本轮，剩余来源不再访问；保留此前已验证的职位。它不会尝试登录、刷新重试验证页或绕过验证。若 Computer Use 自身报告无法确认当前网址，调用方同样必须立即停止本轮，不得切换 Playwright 或其他通道继续该轮采集；用户恢复可读页面后再另起一轮。

列表总数无法核对、单条详情失败、缺少 JD、超过 `--max-jobs` 时返回 `partial`；列表发现数不足已公布总数时保守返回 `blocked` 并停止。日志及 API 响应包含 `failed_details`、`skipped_details`、`discovered_jobs`，入库接口保留 `partial`/`blocked` 和错误说明，不会因存在成功职位而改成 `ok`。缺少字段的补采保留已有 JD；不会根据本次未出现而关闭岗位，仅详情页明确下线才更新 closed。

退出码：`0` 完整成功（或仅保存登录 profile），`1` blocked/partial，`2` 配置或启动失败。调度器应记录非零退出状态，blocked 后等待人工处理，不要在同一天自动重复挑战。公开职位保留来源链接、职位 URL、字段、抓取时间与 open/closed；登录页、错误页和无有效职位内容不会入库为岗位。

离线验证（不打开浏览器、不连接 Monitor、不写本地业务库）：

```bash
python -m unittest tests.test_boss_worker_reliability tests.test_hiring_browser_capture tests.test_hiring_boss
```
