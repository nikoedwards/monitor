# Monitor 招聘采集助手

这个 Chrome / Edge 扩展从用户当前正常打开、已登录的 BOSS 直聘或 LinkedIn 职位页面读取可见内容，然后调用 Monitor 的 `/api/hiring/browser-capture` 接口入库。

它不会打开开发者工具、复制 Cookie、自动登录、处理验证码或绕过安全验证。职位列表页用于发现岗位；职位详情页能补充完整 JD，并能记录明确显示的“职位已下线”。

## 安装

1. Chrome 打开 `chrome://extensions/`；Edge 打开 `edge://extensions/`。
2. 开启“开发者模式”。
3. 点击“加载已解压的扩展程序”，选择本目录 `browser_extensions/hiring_capture`。
4. 固定“Monitor 招聘采集助手”到浏览器工具栏。

## 使用

1. 正常登录 BOSS 直聘或 LinkedIn，并打开职位列表/详情页。
2. 点击扩展图标，填写 Monitor 地址并点击“连接”。
3. 选择归属品牌，点击“采集当前页面”。
4. 如果页面显示登录或安全验证，先人工完成，再重新点击采集。

列表页只记录当前页面实际可见的职位，不会因为某个岗位暂时没出现在当前页就直接判定下线；岗位下线以详情页明确状态或后续可靠来源为准。
