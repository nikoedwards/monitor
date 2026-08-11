# Monitor Intelligence Hub

精简版「Meltwater」——以**品牌为根**的品牌 / 竞品数据情报平台。围绕真实数据采集，把销售、营销、用户之声、网页动态聚合到一个工作区，并支持跨品牌竞品对比。

- 后端：FastAPI + Uvicorn + Pydantic + SQLite（WAL），连接器（Connector）框架驱动真实采集。
- 前端：React + Vite + TypeScript + React Router + TanStack Query + Recharts，Tailwind v4 + Geist，Vercel 风格亮/暗双主题（设计参考根目录 `DESIGN.md`）。
- 网页快照：Playwright 同时生成整页 PNG 与离线 HTML 归档，并结合视觉差异、文本证据和多模态大模型分析网站变化。

## 信息架构

```
品牌（自家 / 竞品）
├── 经营总览        跨维度 KPI 与高优先级信号
├── 销售监控        Amazon / 独立站 / 其他电商 / 线下（时序 + 人工录入）
├── 招聘监控        Boss 直聘 / LinkedIn 职位、JD 变化与员工动态
├── 营销监控        媒体公关 / 广告 / 红人 / 社群 / 社交
├── 用户之声(VoC)   情感·主题分析、预警、责任分派、闭环
├── 网页快照        PNG + 离线网页归档 + 视觉/文本变化与周期分析
└── 数据源采集      连接器控制台（分档采集）

全局：竞品对比 · 品牌管理（品牌 / 产品 / 链接）
```

## 数据源分档

| 档位 | 含义 | 连接器 |
| --- | --- | --- |
| 第 1 档 | 免费、无需凭证、可直接采真实数据 | Google News、Reddit 搜索、YouTube/Instagram/TikTok 官方账号、App Store 评论、品牌站点分析、网页快照 |
| 第 2 档 | 需配置凭证后启用 | Meta 广告库、YouTube、Discord、Facebook 群组 |
| 第 3 档 | 难获取，预留接缝 + 手动录入 | Amazon 竞品销量、Instagram / TikTok / X 全站关键词发现、线下销售 |

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

## 招聘监控

招聘监控支持 Boss 直聘、LinkedIn 职位与 LinkedIn 人员动态。通过品牌下 `dimension='hiring'` 的链接配置公司 Jobs/People 采集入口，并按日保存岗位/JD 快照、上下线状态和人员公开动态。公司 People 页发现的普通员工只进入候选名单；手动添加或标记为“重点”的人员会额外按日保存个人页头衔/职位快照并检测履历变化。

Boss 与 LinkedIn 反爬严格。后台 Cookie 采集仍作为最佳努力路径；对于打开开发者工具就刷新、登录跳转或自动化浏览器被拦截的页面，使用 [`browser_extensions/hiring_capture`](browser_extensions/hiring_capture) 浏览器助手：在用户正常登录且能看到页面的前提下，一键把当前列表/详情页的可见职位回传 Monitor，不打开 F12、不复制 Cookie，也不处理验证码或绕过安全验证。列表页用于发现岗位，详情页补充 JD 并记录明确的岗位下线；不完整列表不会把未出现的岗位误判为下线。

重点人员页提供“导入企业员工”：从当前品牌启用的 LinkedIn 公司 `/people/` 页面更新员工候选池，再通过搜索、全选或逐人勾选决定哪些公司员工进入持续监控；批量选择不会取消手动添加的重点人员。

## 社媒官方账号自采

在「品牌管理 → 社媒」配置公开账号主页后，系统会立即采集并按日刷新：

- YouTube：公开 Atom feed + 频道页结构化数据 + 网页互动指标。
- Instagram：无登录 Chromium 会话读取公开主页接口，采集最近帖子、粉丝、点赞、评论及公开视频播放量（平台返回时）。
- TikTok：公开主页资料 + `yt-dlp` 网页提取，采集最近视频、播放、点赞、评论、转发和粉丝数。

以上路径不需要 Ensemble Data 或其他付费聚合 Token，也不会绕过登录、验证码、私密账号或平台访问控制。公开网页结构和风控可能变化，失败会保留在对应账号链接的采集状态中。X、Facebook、LinkedIn 官方账号免费适配器仍待后续开发；红人板块的“全站关键词发现”也不等同于已知官方账号自采。

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
- 从默认分支开发时使用 `codex/` 前缀创建分支；推送后创建 PR，并向用户提供分支名、提交记录和 PR 链接。对于用户需要在生产页面核对的改动，交付完成标准还包括合并到 `main`、确认 Railway 部署成功，并通过线上 `/api/health` 检查，不能只停留在 Draft PR。
- 如果 GitHub 身份验证、远端权限或分支冲突阻止发布，Codex 应先自行排查和修复；只有确实需要用户完成账号授权时才请求一次必要操作。

### 网页快照任务调度

- 每个网页监控任务都可以单独编辑名称、URL、范围、子页上限、运行状态，以及页面检查/完整截图频率。
- 页面检查只抓取可见文本并判断相对最近快照是否发生变化；完整快照会启动 Chromium，并在同一次渲染中保存整页 PNG 和自包含 HTML 归档，两种频率独立配置。
- 监控列表展示下次检查时间、下次截图时间及倒计时；暂停任务后自动调度停止，仍可手动点击「立即截图」。
- 当前后台调度器默认每小时扫描一次到期任务，因此实际开始时间可能晚于预计时间少量时间。
- 完整截图前会自动逐屏滚动页面，触发懒加载图片、视频、iframe 和背景资源，再回到顶部生成整页 PNG。
- HTML 归档会尽量内嵌页面 DOM、CSS、JavaScript、图片和字体，视频只保留封面；归档回放运行在无同源权限的沙箱中，禁止联网、表单提交和外部跳转，避免归档脚本访问 Monitor 的 Cookie/API。
- 交互归档会在用户查看截图时后台预载，切换后显示明确的加载状态；归档文件使用长期缓存和版本参数，重复打开同一快照时可直接命中浏览器缓存。
- 打开单张快照或历史详情弹窗后，后台时间线会暂停继续下载数 MB 的长截图；切换到「变化对比」时，尚未完成的交互归档预载也会暂停，并将局部对比图提升为最高加载优先级。对比模式会先展示可横向切换的 BEFORE / AFTER 视觉区域，再在下方补充文字变更，避免用户只看到 added / removed 文本却误以为对比图缺失；对比图显示明确的加载动画，加载完成后再渐显，避免大文件并发下载造成长时间空白。
- 为缩短首次打开时间，归档中的大尺寸 PNG/JPEG/WebP 会缩放到回放所需分辨率并重新编码为 WebP，原站脚本移动到正文末尾延迟执行，让页面内容先显示；用于变化分析的原始 PNG 截图不会被压缩。服务启动时会一次性优化 Volume 中的旧归档并同步更新文件大小。
- 普通 HTML 预抓若遇到 HTTP 403/429、超时或其他公开站点访问错误，不会再提前终止快照，而会继续用 Chromium 直接打开真实页面；Chromium 成功后会回传最终 URL、标题和可见文本用于快照及变化分析。Chromium 若返回 `local_rate_limited`、HTTP 429/5xx 等错误占位页，会更换页面并分段重试；直接导航持续受限但预抓 HTML 可用时，则在原站 URL 下重新渲染真实 HTML。所有可信方式都失败时不会写入截图、归档或变化分析，也不会推进上次成功截图时间。任务先按 10、30、60、180 分钟自动补拍，随后每 6 小时恢复重试；连续失败超过 8 次后，完整 Chromium 截图进入 24 小时保护，但系统每小时只做一次轻量 HTML 恢复探测，一旦目标站恢复访问便在下一轮调度立即补拍，无需白等到熔断结束。Railway 生产部署启用 IPv6 出口，双栈目标站可避开被限流的共享 IPv4 路径。网页截图使用独立调度线程每分钟检查，监控卡片会显示失败次数、限流熔断状态、轻量探测时间和下次补拍倒计时。
- 多个监控任务指向同一个规范化 URL 时，30 分钟内会复用最近一次成功抓取的页面内容，并为每个任务复制独立的 PNG 与 HTML 文件；这样既保留各自完整历史和删除能力，也避免 Railway 从同一出口短时间重复访问目标站点而触发 429。
- 归档会为常见注册/营销弹窗的关闭按钮、Cookie 接受/拒绝按钮和 Esc 键提供安全兜底；回放启动及页面变化后都会清除原站遗留的滚动锁，即使克隆 DOM 丢失原事件监听，也能关闭遮挡并继续向下浏览。服务启动时会自动升级 Volume 中已有的 HTML 归档，无需重新截图。
- 单张快照可从卡片右上角或历史详情中删除，系统会二次确认并同步清理 PNG、HTML 与相关分析缓存；「查看更多」提供按年、月和具体抓取时间浏览的完整历史面板。
- 网页快照页复用全局日期范围选择器，支持 24 小时、7/30 天、3/6 个月、1 年和自定义日期；系统统计变化天数、平均变化间隔、严重度、活跃页面，并自动与上一等长周期环比。
- 视觉变化先由本地图像算法计算变化比例和区域；用户点击「生成 AI 分析」后，系统只把最重要的前后截图区域及文本证据交给支持图片输入的大模型，并缓存相同快照集合的分析结果。

### 网页快照存储与模型要求

- Railway Volume 仍需挂载到 `/app/data`。每次完整快照会新增一个原始 PNG 和一个经过回放优化的 HTML 文件；抓取阶段最多处理约 30 MB 资源，写入前会压缩大图，长期运行时仍应配置容量监控和保留策略。
- `Pillow` 用于本地截图差异计算；生产依赖已写入 `requirements.txt`。
- AI 快照分析需要设置页中的模型支持 Anthropic Messages 图片内容块；未配置 Token 时，日期筛选、视觉统计和周期环比仍可正常使用。
- 动态接口、登录态、WebSocket、Canvas、Service Worker、跨域 iframe 和完整视频无法保证离线复原；这类限制会在归档和 AI 分析中标注，不应视为完整的 Web Archive 替代品。

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
