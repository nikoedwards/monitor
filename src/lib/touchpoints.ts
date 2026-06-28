// Shared touchpoint taxonomy: brand config page and AI draft preview render
// from the same catalog. Every touchpoint maps onto the `links` table via
// dimension + channel + platform + url.

export interface TouchpointPlatform {
  platform: string; // stable key stored in links.platform
  label: string;
  channel: string; // links.channel
  placeholder?: string;
  listingSource?: boolean; // configuring this auto-expands into a monitored Listing List
  multi?: boolean; // allow several links under the same platform (e.g. multiple subreddits)
  // Crawl-logic transparency (rendered as an InfoHint next to the platform):
  dataSource?: string; // what data this touchpoint pulls
  method?: string; // how it is crawled
  requires?: string; // prerequisites / limitations
}

// Sales channels that have an automated provider (scrape / SellerSprite).
export const AUTOMATED_SALES_CHANNELS = new Set(["amazon", "dtc", "other_ecom"]);

export interface TouchpointSection {
  key: "sales" | "social" | "community";
  title: string;
  dimension: "sales" | "marketing";
  platforms: TouchpointPlatform[];
}

export const TOUCHPOINTS: TouchpointSection[] = [
  {
    key: "sales",
    title: "销售渠道",
    dimension: "sales",
    platforms: [
      { platform: "amazon", label: "Amazon", channel: "amazon", placeholder: "店铺/品牌页 https://www.amazon.com/s?me=... 或单品 /dp/...", listingSource: true },
      { platform: "dtc", label: "独立站 DTC", channel: "dtc", placeholder: "店铺/系列页 https://brand.com/shop", listingSource: true },
      { platform: "walmart", label: "Walmart", channel: "other_ecom" },
      { platform: "target", label: "Target", channel: "other_ecom" },
      { platform: "ebay", label: "eBay", channel: "other_ecom" },
      { platform: "temu", label: "Temu", channel: "other_ecom" },
      { platform: "tiktok_shop", label: "TikTok Shop", channel: "other_ecom" },
      { platform: "shopee", label: "Shopee", channel: "other_ecom" },
      { platform: "lazada", label: "Lazada", channel: "other_ecom" },
      { platform: "aliexpress", label: "AliExpress", channel: "other_ecom" },
      { platform: "offline", label: "线下门店", channel: "offline" },
    ],
  },
  {
    key: "social",
    title: "社媒",
    dimension: "marketing",
    platforms: [
      { platform: "instagram", label: "Instagram", channel: "social" },
      { platform: "tiktok", label: "TikTok", channel: "social" },
      { platform: "x", label: "X (Twitter)", channel: "social" },
      { platform: "youtube", label: "YouTube", channel: "social" },
      { platform: "facebook", label: "Facebook", channel: "social" },
      { platform: "linkedin", label: "LinkedIn", channel: "social" },
      { platform: "pinterest", label: "Pinterest", channel: "social" },
    ],
  },
  {
    key: "community",
    title: "社群",
    dimension: "marketing",
    platforms: [
      {
        platform: "reddit", label: "Reddit", channel: "community", multi: true,
        placeholder: "subreddit，如 r/anker 或 https://www.reddit.com/r/anker",
        dataSource: "指定 subreddit 的帖子与回复，及全站按品牌关键词的讨论",
        method: "Reddit JSON 端点抓取，失败回退公开 RSS；可同时追踪多个 subreddit",
        requires: "公开免登录即可；配置 REDDIT_BEARER_TOKEN 可显著提升配额、降低被限流",
      },
      {
        platform: "self_hosted", label: "自建社群/论坛", channel: "community", multi: true,
        placeholder: "社群/论坛网址，如 https://community.brand.com 或 https://feedback.brand.com",
        dataSource: "自建论坛 / 反馈站的帖子与回复",
        method: "依次尝试 Discourse API → 页面内嵌 JSON(Frill 等) → RSS → 页面快照",
        requires: "需为公开页面；纯前端渲染且无公开数据接口的站点可能仅能取到有限内容",
      },
      {
        platform: "discord", label: "Discord", channel: "community", multi: true,
        dataSource: "服务器频道消息规模 / 内容（阶段二）",
        method: "Bot Token + 消息内容特权 intent 读取自家服务器；竞品仅能取成员/在线规模",
        requires: "需配置 discord_bot_token，且 Bot 已加入对应服务器",
      },
      {
        platform: "facebook_group", label: "Facebook Group", channel: "community", multi: true,
        dataSource: "群组公开内容（阶段二）",
        method: "Graph 群组接口多已锁权限；公开主页可走 Page API，群组多需人工/CSV 兜底",
        requires: "需配置 facebook_access_token；多数群组接口受限",
      },
      {
        platform: "telegram", label: "Telegram", channel: "community", multi: true,
        dataSource: "公开频道消息（阶段二）",
        method: "公开频道可经 t.me 预览 / Bot API 抓取",
        requires: "私有群需 Bot 加入；阶段二接入",
      },
      {
        platform: "quora", label: "Quora", channel: "community", multi: true,
        dataSource: "公开问答与话题（阶段二）",
        method: "公开问答页抓取",
        requires: "阶段二接入",
      },
    ],
  },
];

export const SECTION_BY_KEY = Object.fromEntries(TOUCHPOINTS.map((s) => [s.key, s]));
