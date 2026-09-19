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
python -m tools.boss_browser_worker --headed --wait-for-login 180
```

浏览器打开后，在 BOSS 页面人工登录并完成任何安全验证。worker 会再次访问源链接，采集成功后把结果提交到 `/api/hiring/browser-capture`。

## 每日运行

```bash
python -m tools.boss_browser_worker --headless --max-jobs 40
```

建议在 Codex 的本地 cron 任务中使用项目目录作为工作目录，并保留 `data/browser_profiles/boss`。如果需要多个账号，给每个账号指定不同的 `--profile-dir`，不要并发使用同一个 profile。

脚本只提交当次实际读取到的职位。列表不完整、详情访问失败或登录状态失效时，它会标记 `blocked`/`partial`，不会根据“本次没看到”关闭已有岗位。CAPTCHA、安全验证和 MFA 必须由人工完成。
