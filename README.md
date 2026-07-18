# Monitor Intelligence Hub

精简版「Meltwater」——以**品牌为根**的品牌 / 竞品数据情报平台。围绕真实数据采集，把销售、营销、用户之声、网页动态聚合到一个工作区，并支持跨品牌竞品对比。

- 后端：FastAPI + Uvicorn + Pydantic + SQLite（WAL），连接器（Connector）框架驱动真实采集。
- 前端：React + Vite + TypeScript + React Router + TanStack Query + Recharts，Tailwind v4 + Geist，Vercel 风格亮/暗双主题（设计参考根目录 `DESIGN.md`）。
- 截图引擎：优先 Playwright（整页），否则回退本机 Edge/Chrome，再回退 SVG 文本快照。

## 信息架构

```
品牌（自家 / 竞品）
├── 经营总览        跨维度 KPI 与高优先级信号
├── 销售监控        Amazon / 独立站 / 其他电商 / 线下（时序 + 人工录入）
├── 营销监控        媒体公关 / 广告 / 红人 / 社群 / 社交
├── 用户之声(VoC)   情感·主题分析、预警、责任分派、闭环
├── 网页快照        每日截图 + 文本变更分析 + 历史回溯
└── 数据源采集      连接器控制台（分档采集）

全局：竞品对比 · 品牌管理（品牌 / 产品 / 链接）
```

## 数据源分档

| 档位 | 含义 | 连接器 |
| --- | --- | --- |
| 第 1 档 | 免费、无需凭证、可直接采真实数据 | Google News、Reddit 搜索、App Store 评论、品牌站点分析、网页快照 |
| 第 2 档 | 需配置凭证后启用 | Meta 广告库、YouTube、Discord、Facebook 群组 |
| 第 3 档 | 付费 / 难获取，预留接缝 + 手动录入 | Amazon 竞品销量、Instagram / TikTok 监听、线下销售 |

第 2 档凭证通过环境变量提供（可选）：

```
YOUTUBE_API_KEY=...
REDDIT_BEARER_TOKEN=...
DISCORD_BOT_TOKEN=...
FACEBOOK_ACCESS_TOKEN=...   # 同时用于 Meta 广告库
KEEPA_API_KEY=...           # 第 3 档 Amazon 销量接缝
SELLERSPRITE_SECRET_KEY=... # 卖家精灵 OpenAPI（可选，也可在「设置」中填写）
```

## 销售监控

「销售监控」围绕**链接 → Listing → 每日快照**展开，配置即自动开启监控：

1. 在「品牌管理」的销售渠道里填入链接并保存，系统立即开始首次采集：
   - Amazon：填**店铺/品牌页**（如 `https://www.amazon.com/s?me=<卖家ID>&marketplaceID=...`）会展开该店铺的全部在售 Listing；填单品 `/dp/<ASIN>` 则监控单个 Listing。
   - 独立站 DTC：填**店铺/系列页**会发现其商品页；填单品页则监控该页。
2. 每个 Listing 进入 Listing List（`sales_listings`），每天采集一条快照（价格、排名/BSR、评分、评论数、SKU、库存、销量估算）写入 `sales_metrics`，并对标题/图片/SKU/库存做**变更识别**。
3. 在销售监控页可：按**全局维度**（产品聚合总销量）/**渠道维度**查看，按**产品筛选**，给每个 Listing**映射产品**、单独**开关监控**、查看**历史趋势与变更记录**，或**立即同步**。

数据获取采用**可插拔的 Provider 适配器**：

| 渠道 | 默认 Provider | 升级路径 |
| --- | --- | --- |
| Amazon | 尽力爬取（受反爬限制，字段可能不全） | 配置卖家精灵 `secret-key` 后优先用其 OpenAPI（销量/排名更可靠） |
| 独立站 / 其他电商 | 通用爬取（解析 schema.org `Product`/`Offer` JSON-LD） | — |
| 线下 / 其他 | 人工录入 / CSV | — |

> 卖家精灵 OpenAPI 为**独立付费**产品（按接口计费，`secret-key` 请求头），需联系其商务开通；常规网页/插件账号不含 API。未配置时销售监控自动走爬取，配置后无需改动即切换。在「设置」弹窗填写 `secret-key`（也支持 `SELLERSPRITE_SECRET_KEY` 环境变量）。

## 本地运行

前置：Python 3.10+、Node 18+。

```bash
# 1) 后端依赖
pip install -r requirements.txt

# 可选：整页截图（不装则用本机浏览器/SVG 回退）
pip install playwright && python -m playwright install chromium

# 2) 前端依赖与构建
npm install
npm run build

# 3) 启动（serve 构建产物 + API + /snapshots）
npm run server
# 打开 http://127.0.0.1:8790
```

开发模式（前端热更新，自动代理 /api 与 /snapshots 到 8790）：

```bash
npm run server      # 终端 A
npm run dev         # 终端 B → http://localhost:5173
```

## Railway 部署与维护

- 生产环境部署在 Railway，并连接 GitHub 的 `main` 分支；本地修改不会自动上线，提交并推送到 GitHub 后才会触发 Railway 重新构建和部署。
- 生产 Docker 镜像会安装 Playwright Chromium，用于生成真实网页 PNG；如果时间线出现 `Generated visual snapshot fallback`，说明浏览器启动或页面导航失败，应先检查 Railway 构建/运行日志。
- SQLite 数据库与网页快照都存放在 `/app/data`，Railway Volume 需要挂载到该目录，避免重新部署后丢失历史数据和截图。
- 每次功能修改或 Bug 修复完成后，应先运行相关测试与 `npm run build`，必要时同步更新 README，然后提交并推送到 GitHub。
- 推送后检查 Railway 部署状态、`/api/health`，并在生产页面复核本次修改涉及的功能。

### Codex 代码交付约定

- Codex 完成任何代码修改后，默认必须运行与改动风险相匹配的测试或构建检查。
- 验证通过后，Codex 必须自动将本次相关改动提交并推送到 GitHub，不需要用户重复提醒或另行授权。
- 默认只暂存本次任务涉及的文件或代码片段，不能把工作区中其他未确认的改动一并提交。
- 从默认分支开发时使用 `codex/` 前缀创建分支；推送后优先创建 Draft PR，并向用户提供分支名、提交记录和 PR 链接。
- 如果 GitHub 身份验证、远端权限或分支冲突阻止发布，Codex 应先自行排查和修复；只有确实需要用户完成账号授权时才请求一次必要操作。

### 网页快照任务调度

- 每个网页监控任务都可以单独编辑名称、URL、范围、子页上限、运行状态，以及页面检查/完整截图频率。
- 页面检查只抓取可见文本并判断相对最近快照是否发生变化；完整截图会启动 Chromium 并保存 PNG，两种频率独立配置。
- 监控列表展示下次检查时间、下次截图时间及倒计时；暂停任务后自动调度停止，仍可手动点击「立即截图」。
- 当前后台调度器默认每小时扫描一次到期任务，因此实际开始时间可能晚于预计时间少量时间。
- 完整截图前会自动逐屏滚动页面，触发懒加载图片、视频、iframe 和背景资源，再回到顶部生成整页 PNG。

## 快速上手

1. 进入「品牌管理」，输入官网 URL 一键抓取信息创建品牌（可标记竞品），填写监控关键词。
2. 在「数据源采集」对该品牌运行第 1 档连接器（如 Google News）拉取真实数据。
3. 在「网页快照」添加竞品官网 / 定价页，自动截图并跟踪变更。
4. 在「用户之声」录入或导入 CSV 反馈，查看情感 / 主题分析与闭环任务。
5. 用「竞品对比」横向比较多品牌表现。

## 目录结构

```
server/
  app.py            FastAPI 入口（python -m server.app）
  config.py db.py   配置 / 数据层（schema + 迁移 + WAL）
  fetchers.py       带 SSRF 防护的抓取 + RSS/页面解析
  nlp.py records.py 情感/主题分析 + 统一记录读写
  snapshot.py       截图引擎 + 变更分析
  scheduler.py      后台定时采集
  connectors/       连接器框架 + 注册表 + 采集器实现
  domains/          分域 router（brands/content/sales/web/sources/insights）
src/
  lib/              api 客户端、query hooks、格式化
  components/       ui 基础组件、charts、布局壳
  features/         各板块视图
DESIGN.md           Vercel 设计语言参考（来自 awesome-design-md）
```
