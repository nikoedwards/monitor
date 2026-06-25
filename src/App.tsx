import {
  AlertTriangle,
  BarChart3,
  Building2,
  CalendarDays,
  Camera,
  CheckCircle,
  Clock,
  Database,
  ExternalLink,
  FileUp,
  Filter,
  Globe,
  History,
  Inbox,
  Link as LinkIcon,
  MessageSquareText,
  Newspaper,
  PauseCircle,
  Play,
  Plus,
  PlugZap,
  RefreshCw,
  Save,
  Search,
  ShoppingBag,
  SlidersHorizontal,
  SquarePen,
  TrendingUp,
  Trash2,
  Users,
  WandSparkles,
  X
} from "lucide-react";
import { ChangeEvent, FormEvent, ReactNode, useEffect, useRef, useState } from "react";

type Sentiment = "positive" | "neutral" | "negative";

type Source = {
  id: string;
  name: string;
  category: string;
  vendor: string;
  sync_mode: string;
  status: "ready" | "planned";
  notes: string;
};

type BrandProfile = {
  id: string;
  name: string;
  source_url: string;
  source_kind: string;
  official_website?: string;
  amazon_url?: string;
  marketplace?: string;
  asin?: string;
  category?: string;
  description?: string;
  logo_url?: string;
  social_links: Record<string, string>;
  ecommerce_links: Record<string, string>;
  monitoring_keywords: string[];
  updated_at?: string;
};

type BrandDraft = Omit<BrandProfile, "id" | "updated_at"> & {
  id?: string;
  final_url?: string;
  confidence?: number;
  evidence?: string[];
  duplicate_candidates?: Array<{
    id: string;
    name: string;
    source_kind: string;
    source_url: string;
    reasons: string[];
  }>;
  raw?: Record<string, unknown>;
};

type RecordItem = {
  id: string;
  source_id: string;
  source_name: string;
  source_category?: string;
  data_type: string;
  platform?: string;
  community_source_name?: string;
  community_platform?: string;
  community_brand_id?: string;
  community_brand_name?: string;
  title?: string;
  author?: string;
  body: string;
  url?: string;
  monitor_id?: string;
  brand?: string;
  competitor?: string;
  product?: string;
  region?: string;
  language?: string;
  occurred_at: string;
  sentiment: Sentiment;
  sentiment_score: number;
  intent: string;
  topics: string[];
};

type Overview = {
  total_records: number;
  total_sources: number;
  total_brands: number;
  by_sentiment: Partial<Record<Sentiment, number>>;
  by_type: Array<{ data_type: string; count: number }>;
  by_source: Array<{ name: string; source_id: string; count: number }>;
  trend: Array<{ date: string; count: number }>;
  top_topics: Array<{ topic: string; count: number }>;
  recent: RecordItem[];
};

type SnapshotChange = {
  type: "added" | "removed" | "title";
  text?: string;
  from?: string;
  to?: string;
};

type WebSnapshot = {
  id: string;
  monitor_id: string;
  monitor_name?: string;
  snapshot_date: string;
  url: string;
  page_key?: string;
  page_path?: string;
  final_url?: string;
  title?: string;
  status: "ready" | "error";
  screenshot_url: string;
  html_url: string;
  text_hash?: string;
  text_excerpt?: string;
  content_length: number;
  change_score: number;
  summary?: string;
  changes: SnapshotChange[];
  created_at: string;
};

type WebMonitor = {
  id: string;
  brand_id?: string;
  name: string;
  url: string;
  scope: "single_page" | "domain";
  crawl_limit: number;
  icon_url?: string;
  status: "active" | "paused";
  cadence: "daily";
  last_snapshot_at?: string;
  last_status: "pending" | "running" | "ready" | "error";
  last_error?: string;
  created_at: string;
  updated_at: string;
  snapshots: number;
  page_count: number;
  changed_snapshots: number;
  latest_snapshot?: WebSnapshot | null;
};

type CaptureJob = {
  id: string;
  monitor_id: string;
  status: "queued" | "running" | "complete" | "error";
  progress: number;
  phase: string;
  message: string;
  current_url: string;
  total_pages: number;
  completed_pages: number;
  snapshot_ids: string[];
  error: string;
  created_at: string;
  started_at?: string;
  finished_at?: string;
};

type WebSummary = {
  range_days: number;
  generated_at: string;
  active_monitors: number;
  total_snapshots: number;
  changed_snapshots: number;
  daily: Array<{
    date: string;
    snapshots: number;
    changed: number;
    summaries: Array<{
      monitor?: string;
      page?: string;
      url?: string;
      summary: string;
      score: number;
      snapshot_id: string;
    }>;
  }>;
  highlights: Array<{
    monitor?: string;
    page?: string;
    url?: string;
    date: string;
    summary: string;
    score: number;
    snapshot_id: string;
    screenshot_url: string;
  }>;
};

type CoverageType = "earned" | "paid_pr" | string;

type MediaMention = RecordItem & {
  publication: string;
  coverage_type: CoverageType;
  coverage_confidence: number;
  coverage_reason: string;
  estimated_reach: number;
  media_tier: string;
  source_domain?: string;
  pr_themes: string[];
};

type MediaMonitor = {
  id: string;
  brand_id?: string;
  brand_name: string;
  query: string;
  region: string;
  language: string;
  status: "active" | "paused";
  cadence: "daily";
  last_scan_at?: string;
  last_status: "pending" | "running" | "ready" | "error";
  last_error?: string;
  created_at: string;
  updated_at: string;
  mentions: number;
  estimated_reach: number;
  earned_mentions: number;
  paid_mentions: number;
  latest_mention?: MediaMention | null;
};

type MediaSummary = {
  range_days: number;
  generated_at: string;
  active_monitors: number;
  total_mentions: number;
  estimated_reach: number;
  earned_mentions: number;
  paid_mentions: number;
  coverage_mix: Record<string, number>;
  sentiment: Partial<Record<Sentiment, number>>;
  daily: Array<{ date: string; mentions: number; estimated_reach: number }>;
  top_publications: Array<{ publication: string; mentions: number; estimated_reach: number }>;
  pr_directions: Array<{ theme: string; count: number }>;
  share_of_voice: Array<{ brand_name: string; monitor_id: string; mentions: number; share: number }>;
  recent: MediaMention[];
};

type MarketingMonitorType = "social" | "creator" | "ads";

type MarketingLink = {
  id: string;
  brand_id?: string;
  brand_name: string;
  monitor_type: MarketingMonitorType;
  platform: string;
  platform_label: string;
  name: string;
  url: string;
  status: "active" | "paused";
  cadence: "daily" | string;
  last_collect_at?: string;
  last_status: "pending" | "ready" | "error";
  last_error?: string;
  metrics: Record<string, unknown> & {
    title?: string;
    author?: string;
    provider?: string;
    thumbnail_url?: string;
    final_url?: string;
    method?: string;
  };
  record_count: number;
  latest_record_at?: string;
  latest_record?: RecordItem | null;
};

type MarketingLinkSummary = {
  generated_at: string;
  monitor_type: MarketingMonitorType;
  total_links: number;
  active_links: number;
  ready_links: number;
  error_links: number;
  total_records: number;
  by_brand: Array<{ brand_name: string; links: number; records: number }>;
  by_platform: Array<{ platform: string; platform_label: string; links: number; records: number }>;
  recent: RecordItem[];
  links: MarketingLink[];
};

type MarketingLinkForm = {
  monitor_type: MarketingMonitorType;
  brand_id: string;
  brand_name: string;
  platform: string;
  name: string;
  url: string;
  status: "active" | "paused";
};

type SalesChannel = {
  id: string;
  brand_name: string;
  product_name: string;
  platform: string;
  store_name: string;
  store_type: "自营店" | "渠道店" | string;
  channel_url?: string;
  region: string;
  sales_units: number;
  previous_sales_units: number;
  review_count: number;
  rating: number;
  revenue: number;
  currency: string;
  snapshot_date: string;
  status: "增长" | "稳定" | "下滑" | string;
  updated_at?: string;
};

type SalesChannelLink = {
  id: string;
  sales_brand_id: string;
  platform: string;
  platform_label: string;
  name: string;
  url: string;
  canonical_url: string;
  store_type: "自营店" | "渠道店" | string;
  region: string;
  status: "active" | "paused";
  cadence: string;
  discovery_source?: string;
  confidence: number;
  notes?: string;
  last_checked_at?: string;
  last_status: "pending" | "ready" | "error";
  last_error?: string;
  created_at: string;
  updated_at: string;
};

type SalesChannelBrand = {
  id: string;
  brand_profile_id?: string;
  name: string;
  source_url?: string;
  status: "active" | "paused";
  notes?: string;
  created_at: string;
  updated_at: string;
  links: SalesChannelLink[];
  link_count: number;
  active_link_count: number;
  platforms: string[];
};

type SalesChannelDiscovery = {
  brand: {
    name: string;
    source_url: string;
    analysis?: Record<string, unknown>;
  };
  candidates: Array<{
    platform: string;
    platform_label: string;
    name: string;
    url: string;
    canonical_url: string;
    store_type: string;
    region: string;
    discovery_source: string;
    confidence: number;
  }>;
  evidence: string[];
};

type SalesProductSummary = {
  id: string;
  brandName: string;
  productName: string;
  salesUnits: number;
  previousSalesUnits: number;
  revenue: number;
  reviews: number;
  rating: number;
  channels: number;
  changeRate: number;
};

type SalesDashboard = {
  channels: SalesChannel[];
  products: SalesProductSummary[];
  totalUnits: number;
  previousUnits: number;
  totalRevenue: number;
  totalReviews: number;
  averageRating: number;
  changeRate: number;
  activeChannels: number;
  ownedChannels: number;
  partnerChannels: number;
  decliningChannels: number;
  topBrandName: string;
};

type CommunitySource = {
  id: string;
  brand_id: string;
  platform: "reddit" | "discord" | "facebook" | "owned";
  platform_label: string;
  name: string;
  url: string;
  status: "active" | "paused";
  cadence: string;
  notes?: string;
  last_collect_at?: string;
  last_status: "pending" | "running" | "ready" | "error";
  last_error?: string;
  record_count: number;
  negative_count: number;
  negative_rate: number;
  latest_record_at?: string;
  top_topics: Array<{ topic: string; count: number }>;
};

type CommunityBrand = {
  id: string;
  brand_profile_id?: string;
  name: string;
  description?: string;
  status: "active" | "paused";
  created_at: string;
  updated_at: string;
  sources: CommunitySource[];
  source_count: number;
  record_count: number;
  negative_count: number;
  negative_rate: number;
  top_topics: Array<{ topic: string; count: number }>;
};

type CommunitySummary = {
  generated_at: string;
  brands: CommunityBrand[];
  records: RecordItem[];
  total_brands: number;
  total_sources: number;
  total_records: number;
  negative_records: number;
  negative_rate: number;
  top_topics: Array<{ topic: string; count: number }>;
  by_platform: Array<{
    platform: string;
    platform_label: string;
    count: number;
    negative: number;
    negative_rate: number;
  }>;
};

type OwnerTeam = "product_team" | "support_team" | "experience_team" | "marketing_team";
type VocActionStatus = "open" | "assigned" | "in_progress" | "resolved" | "closed";
type VocPriority = "low" | "medium" | "high" | "urgent";

type VocTopic = {
  topic: string;
  count: number;
  negative: number;
  negative_rate: number;
  owner_team: OwnerTeam;
};

type VocChannel = {
  source_id: string;
  name: string;
  category: string;
  count: number;
  negative: number;
  negative_rate: number;
  top_topics: VocTopic[];
};

type VocProduct = {
  product: string;
  value: string;
  count: number;
  negative: number;
  negative_rate: number;
  top_topics: VocTopic[];
};

type VocConclusion = {
  title: string;
  detail: string;
  tone: "neutral" | "medium" | "high";
};

type VocAlert = {
  id: string;
  title: string;
  description: string;
  level: "medium" | "high";
  priority: VocPriority;
  owner_team: OwnerTeam;
  count: number;
  previous_count: number;
  change_rate: number;
  topic?: string;
  product?: string;
  source_id?: string;
  record_ids: string[];
};

type VocAction = {
  id: string;
  record_id?: string;
  source_id?: string;
  title: string;
  description?: string;
  owner_team: OwnerTeam;
  priority: VocPriority;
  status: VocActionStatus;
  product?: string;
  topic?: string;
  due_at?: string;
  closed_at?: string;
  created_at: string;
  updated_at: string;
  record?: {
    title?: string;
    body?: string;
    sentiment?: Sentiment;
    intent?: string;
    occurred_at?: string;
    source_name?: string;
  };
};

type VocSummary = {
  range_days: number;
  generated_at: string;
  total_records: number;
  negative_records: number;
  negative_rate: number;
  open_actions: number;
  closed_actions: number;
  closure_rate: number;
  trend: Array<{ date: string; count: number; negative: number }>;
  channels: VocChannel[];
  products: VocProduct[];
  topics: VocTopic[];
  conclusions: VocConclusion[];
  alerts: VocAlert[];
  actions: VocAction[];
  recent: RecordItem[];
};

type View = "overview" | "communities" | "voice" | "brands" | "channel-sales" | "web" | "sources" | "marketing-media" | "marketing-social" | "marketing-ads" | "marketing-creators";

const API = "";
const VIEW_VALUES: View[] = ["overview", "communities", "voice", "brands", "channel-sales", "web", "sources", "marketing-media", "marketing-social", "marketing-ads", "marketing-creators"];
const MARKETING_VIEWS: View[] = ["marketing-media", "marketing-social", "marketing-ads", "marketing-creators"];

const VIEW_TITLES: Record<View, string> = {
  overview: "经营信号总览",
  communities: "社群分析",
  voice: "用户之声",
  brands: "品牌监控",
  "channel-sales": "渠道销售监控",
  web: "网页快照监控",
  sources: "数据源与模型",
  "marketing-media": "媒体监控",
  "marketing-social": "社媒监控",
  "marketing-ads": "广告监控",
  "marketing-creators": "红人监控"
};

const COMMUNITY_PLATFORM_OPTIONS: Array<{ value: CommunitySource["platform"]; label: string }> = [
  { value: "reddit", label: "Reddit" },
  { value: "discord", label: "Discord" },
  { value: "facebook", label: "Facebook Group" },
  { value: "owned", label: "自建社群" }
];

const COMMUNITY_SOURCE_ID_BY_PLATFORM: Record<CommunitySource["platform"], string> = {
  reddit: "reddit_search",
  discord: "discord_community",
  facebook: "facebook_groups",
  owned: "owned_community"
};

const DATA_TYPES = [
  { value: "user_voice", label: "用户之声" },
  { value: "social_comment", label: "社媒评论" },
  { value: "ad_comment", label: "广告评论" },
  { value: "customer_email", label: "客户邮件" },
  { value: "app_review", label: "APP 反馈" },
  { value: "ecommerce_review", label: "电商评论" },
  { value: "support_ticket", label: "客服工单" },
  { value: "community_post", label: "社区讨论" },
  { value: "media_mention", label: "媒体报道" },
  { value: "creator_signal", label: "红人信号" }
];

const SENTIMENT_LABELS: Record<Sentiment, string> = {
  positive: "正向",
  neutral: "中性",
  negative: "负向"
};

const TOPIC_LABELS: Record<string, string> = {
  price: "价格",
  quality: "质量",
  delivery: "物流",
  feature: "功能",
  support: "客服",
  experience: "体验",
  ads: "广告",
  email: "邮件",
  creator: "红人",
  retail: "电商",
  pr: "PR",
  other: "其他"
};

const OWNER_TEAM_LABELS: Record<OwnerTeam, string> = {
  product_team: "产品团队",
  support_team: "客服同学",
  experience_team: "体验/APP 团队",
  marketing_team: "品牌/市场团队"
};

const ACTION_STATUS_LABELS: Record<VocActionStatus, string> = {
  open: "待分派",
  assigned: "已分派",
  in_progress: "处理中",
  resolved: "已解决",
  closed: "已闭环"
};

const PRIORITY_LABELS: Record<VocPriority, string> = {
  low: "低",
  medium: "中",
  high: "高",
  urgent: "紧急"
};

const PR_THEME_LABELS: Record<string, string> = {
  product_launch: "新品发布",
  partnership: "合作联名",
  funding: "融资投资",
  retail_expansion: "渠道扩张",
  creator_campaign: "红人活动",
  leadership: "高管动态",
  sustainability: "可持续",
  reputation_risk: "风险舆情",
  general_coverage: "常规报道"
};

const COVERAGE_LABELS: Record<string, string> = {
  earned: "自发报道",
  paid_pr: "疑似付费 PR"
};

const CATEGORY_ICON: Record<string, ReactNode> = {
  creator: <Users size={18} />,
  pr: <Newspaper size={18} />,
  social: <BarChart3 size={18} />,
  ads: <ShoppingBag size={18} />,
  email: <Inbox size={18} />,
  app: <Globe size={18} />,
  commerce: <ShoppingBag size={18} />,
  community: <MessageSquareText size={18} />,
  manual: <FileUp size={18} />
};

const emptyOverview: Overview = {
  total_records: 0,
  total_sources: 0,
  total_brands: 0,
  by_sentiment: {},
  by_type: [],
  by_source: [],
  trend: [],
  top_topics: [],
  recent: []
};

const emptyWebSummary: WebSummary = {
  range_days: 7,
  generated_at: "",
  active_monitors: 0,
  total_snapshots: 0,
  changed_snapshots: 0,
  daily: [],
  highlights: []
};

const emptyCommunitySummary: CommunitySummary = {
  generated_at: "",
  brands: [],
  records: [],
  total_brands: 0,
  total_sources: 0,
  total_records: 0,
  negative_records: 0,
  negative_rate: 0,
  top_topics: [],
  by_platform: []
};

const emptyMediaSummary: MediaSummary = {
  range_days: 30,
  generated_at: "",
  active_monitors: 0,
  total_mentions: 0,
  estimated_reach: 0,
  earned_mentions: 0,
  paid_mentions: 0,
  coverage_mix: {},
  sentiment: {},
  daily: [],
  top_publications: [],
  pr_directions: [],
  share_of_voice: [],
  recent: []
};

const emptySocialSummary: MarketingLinkSummary = {
  generated_at: "",
  monitor_type: "social",
  total_links: 0,
  active_links: 0,
  ready_links: 0,
  error_links: 0,
  total_records: 0,
  by_brand: [],
  by_platform: [],
  recent: [],
  links: []
};

const emptyCreatorSummary: MarketingLinkSummary = {
  ...emptySocialSummary,
  monitor_type: "creator"
};

const emptyAdsSummary: MarketingLinkSummary = {
  ...emptySocialSummary,
  monitor_type: "ads"
};

const emptyVocSummary: VocSummary = {
  range_days: 30,
  generated_at: "",
  total_records: 0,
  negative_records: 0,
  negative_rate: 0,
  open_actions: 0,
  closed_actions: 0,
  closure_rate: 0,
  trend: [],
  channels: [],
  products: [],
  topics: [],
  conclusions: [],
  alerts: [],
  actions: [],
  recent: []
};

function classNames(...values: Array<string | false | undefined>) {
  return values.filter(Boolean).join(" ");
}

function communitySourceId(platform: CommunitySource["platform"]) {
  return COMMUNITY_SOURCE_ID_BY_PLATFORM[platform] || "owned_community";
}

function communityCollectStatusLabel(source: CommunitySource) {
  if (source.last_status === "ready") return "已采集";
  if (source.last_status === "error") return "需授权/检查";
  return "待采集";
}

function communityRiskFromValues(total: number, negative: number, negativeRate: number) {
  if (!total) return "待采样";
  if (negativeRate >= 0.35 || negative >= 5) return "高关注";
  if (negativeRate >= 0.18 || negative >= 2) return "需跟进";
  return "稳定";
}

function communityAccessNote(platform: CommunitySource["platform"]) {
  if (platform === "reddit") return "Reddit 支持 subreddit、帖子或搜索链接采集；公共 JSON 受限时可配置官方 OAuth 授权。";
  if (platform === "discord") return "Discord invite 链接可采公开服务器元数据；频道消息需要 Bot 加入服务器并获得读取权限。";
  if (platform === "facebook") return "Facebook Group 需要 Graph API、群组管理员授权和合规权限后，才能采集帖子与评论。";
  return "自建社群通过网页可读文本抓取，适合论坛、会员社区、问答区和站内评论页。";
}

async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${API}${path}`);
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

async function apiPost<T>(path: string, payload: unknown): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

async function apiPut<T>(path: string, payload: unknown): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

async function apiDelete<T>(path: string): Promise<T> {
  const response = await fetch(`${API}${path}`, { method: "DELETE" });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

function linksToText(links: Record<string, string> = {}) {
  return Object.entries(links)
    .map(([platform, url]) => `${platform}=${url}`)
    .join("\n");
}

function textToLinks(value: string) {
  return value.split("\n").reduce<Record<string, string>>((result, line) => {
    const trimmed = line.trim();
    if (!trimmed) return result;
    const separator = trimmed.includes("=") ? "=" : ":";
    const [platform, ...urlParts] = trimmed.split(separator);
    const url = urlParts.join(separator).trim();
    if (platform.trim() && url) {
      result[platform.trim().toLowerCase()] = url;
    }
    return result;
  }, {});
}

function parseCsv(text: string) {
  const rows: string[][] = [];
  let cell = "";
  let row: string[] = [];
  let quote = false;

  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    const next = text[index + 1];

    if (char === '"' && quote && next === '"') {
      cell += '"';
      index += 1;
    } else if (char === '"') {
      quote = !quote;
    } else if (char === "," && !quote) {
      row.push(cell.trim());
      cell = "";
    } else if ((char === "\n" || char === "\r") && !quote) {
      if (char === "\r" && next === "\n") index += 1;
      row.push(cell.trim());
      if (row.some(Boolean)) rows.push(row);
      row = [];
      cell = "";
    } else {
      cell += char;
    }
  }

  row.push(cell.trim());
  if (row.some(Boolean)) rows.push(row);
  const [headers = [], ...bodyRows] = rows;

  return bodyRows.map((bodyRow) =>
    headers.reduce<Record<string, string>>((record, header, index) => {
      record[header] = bodyRow[index] ?? "";
      return record;
    }, {})
  );
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit" }).format(new Date(value));
}

function formatDateTime(value?: string) {
  if (!value) return "未生成";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(value));
}

function formatScore(value: number) {
  return `${Math.round((value || 0) * 100)}%`;
}

function dataTypeLabel(value: string) {
  return DATA_TYPES.find((item) => item.value === value)?.label ?? value;
}

function topicLabel(value: string) {
  return TOPIC_LABELS[value] ?? value;
}

function prThemeLabel(value: string) {
  return PR_THEME_LABELS[value] ?? value;
}

function coverageLabel(value: string) {
  return COVERAGE_LABELS[value] ?? value;
}

function formatCompactNumber(value: number) {
  return new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 }).format(value || 0);
}

function formatCurrency(value: number, currency = "USD") {
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency,
    notation: "compact",
    maximumFractionDigits: 1
  }).format(value || 0);
}

function formatSignedPercent(value: number) {
  const rounded = Math.round((value || 0) * 100);
  return `${rounded > 0 ? "+" : ""}${rounded}%`;
}

function salesChangeRate(channel: SalesChannel) {
  if (!channel.previous_sales_units) return channel.sales_units ? 1 : 0;
  return (channel.sales_units - channel.previous_sales_units) / channel.previous_sales_units;
}

function salesStatusClass(status: string) {
  if (status === "下滑") return "down";
  if (status === "增长") return "up";
  return "stable";
}

function buildSalesDashboard(channels: SalesChannel[]): SalesDashboard {
  const productMap = new Map<string, SalesProductSummary & { ratingWeight: number }>();

  channels.forEach((channel) => {
    const key = `${channel.brand_name}::${channel.product_name}`;
    const current = productMap.get(key) ?? {
      id: key,
      brandName: channel.brand_name,
      productName: channel.product_name,
      salesUnits: 0,
      previousSalesUnits: 0,
      revenue: 0,
      reviews: 0,
      rating: 0,
      ratingWeight: 0,
      channels: 0,
      changeRate: 0
    };
    current.salesUnits += channel.sales_units;
    current.previousSalesUnits += channel.previous_sales_units;
    current.revenue += channel.revenue;
    current.reviews += channel.review_count;
    current.ratingWeight += channel.rating * Math.max(channel.review_count, 1);
    current.channels += 1;
    current.rating = current.ratingWeight / Math.max(current.reviews, current.channels);
    current.changeRate = current.previousSalesUnits ? (current.salesUnits - current.previousSalesUnits) / current.previousSalesUnits : 0;
    productMap.set(key, current);
  });

  const totalUnits = channels.reduce((sum, channel) => sum + channel.sales_units, 0);
  const previousUnits = channels.reduce((sum, channel) => sum + channel.previous_sales_units, 0);
  const totalRevenue = channels.reduce((sum, channel) => sum + channel.revenue, 0);
  const totalReviews = channels.reduce((sum, channel) => sum + channel.review_count, 0);
  const ratingWeight = channels.reduce((sum, channel) => sum + channel.rating * Math.max(channel.review_count, 1), 0);
  const products = Array.from(productMap.values())
    .map(({ ratingWeight: _ratingWeight, ...product }) => product)
    .sort((a, b) => b.revenue - a.revenue);

  return {
    channels,
    products,
    totalUnits,
    previousUnits,
    totalRevenue,
    totalReviews,
    averageRating: ratingWeight / Math.max(totalReviews, channels.length, 1),
    changeRate: previousUnits ? (totalUnits - previousUnits) / previousUnits : 0,
    activeChannels: channels.length,
    ownedChannels: channels.filter((channel) => channel.store_type === "自营店").length,
    partnerChannels: channels.filter((channel) => channel.store_type === "渠道店").length,
    decliningChannels: channels.filter((channel) => channel.status === "下滑").length,
    topBrandName: channels[0]?.brand_name || "全部品牌"
  };
}

function filterSalesDashboard(dashboard: SalesDashboard, brandName: string, productName: string) {
  const filtered = dashboard.channels.filter((channel) => {
    return (!brandName || channel.brand_name === brandName) && (!productName || channel.product_name === productName);
  });
  return buildSalesDashboard(filtered);
}

function rangeLabel(value: string) {
  return value === "1" ? "今天" : value === "7" ? "近 7 天" : value === "30" ? "近 30 天" : value === "90" ? "近 90 天" : `近 ${value} 天`;
}

function ownerTeamLabel(value?: OwnerTeam) {
  return value ? OWNER_TEAM_LABELS[value] : "待确认团队";
}

function actionStatusLabel(value: VocActionStatus) {
  return ACTION_STATUS_LABELS[value] ?? value;
}

function priorityLabel(value: VocPriority) {
  return PRIORITY_LABELS[value] ?? value;
}

function nextActionStatus(status: VocActionStatus): VocActionStatus {
  if (status === "open") return "assigned";
  if (status === "assigned") return "in_progress";
  if (status === "in_progress") return "resolved";
  if (status === "resolved") return "closed";
  return "closed";
}

function formatChangeRate(value: number) {
  const percent = Math.round((value || 0) * 100);
  return `${percent >= 0 ? "+" : ""}${percent}%`;
}

function percentage(value: number) {
  return `${Math.round(value * 100)}%`;
}

function nameKey(value?: string) {
  return (value || "").trim().toLowerCase().replace(/\s+/g, "");
}

function primaryBrandUrl(brand?: BrandProfile) {
  return brand?.official_website || brand?.source_url || brand?.amazon_url || "";
}

function primaryBrandKeyword(brand?: BrandProfile) {
  return brand?.monitoring_keywords?.[0] || brand?.name || "";
}

function marketingTypeLabel(value: MarketingMonitorType) {
  if (value === "creator") return "红人";
  if (value === "ads") return "广告";
  return "社媒";
}

function initialView(): View {
  const value = new URLSearchParams(window.location.search).get("view") as View | null;
  return value && VIEW_VALUES.includes(value) ? value : "overview";
}

export default function App() {
  const [activeView, setActiveView] = useState<View>(initialView);
  const [overview, setOverview] = useState<Overview>(emptyOverview);
  const [sources, setSources] = useState<Source[]>([]);
  const [brands, setBrands] = useState<BrandProfile[]>([]);
  const [records, setRecords] = useState<RecordItem[]>([]);
  const [salesChannels, setSalesChannels] = useState<SalesChannel[]>([]);
  const [salesChannelBrands, setSalesChannelBrands] = useState<SalesChannelBrand[]>([]);
  const [communityRecords, setCommunityRecords] = useState<RecordItem[]>([]);
  const [communitySummary, setCommunitySummary] = useState<CommunitySummary>(emptyCommunitySummary);
  const [webMonitors, setWebMonitors] = useState<WebMonitor[]>([]);
  const [webSnapshots, setWebSnapshots] = useState<WebSnapshot[]>([]);
  const [webSummary, setWebSummary] = useState<WebSummary>(emptyWebSummary);
  const [mediaMonitors, setMediaMonitors] = useState<MediaMonitor[]>([]);
  const [mediaMentions, setMediaMentions] = useState<MediaMention[]>([]);
  const [mediaSummary, setMediaSummary] = useState<MediaSummary>(emptyMediaSummary);
  const [socialLinks, setSocialLinks] = useState<MarketingLink[]>([]);
  const [creatorLinks, setCreatorLinks] = useState<MarketingLink[]>([]);
  const [adsLinks, setAdsLinks] = useState<MarketingLink[]>([]);
  const [socialSummary, setSocialSummary] = useState<MarketingLinkSummary>(emptySocialSummary);
  const [creatorSummary, setCreatorSummary] = useState<MarketingLinkSummary>(emptyCreatorSummary);
  const [adsSummary, setAdsSummary] = useState<MarketingLinkSummary>(emptyAdsSummary);
  const [vocSummary, setVocSummary] = useState<VocSummary>(emptyVocSummary);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [brandUrl, setBrandUrl] = useState("");
  const [brandDraft, setBrandDraft] = useState<BrandDraft | null>(null);
  const [brandCreateOpen, setBrandCreateOpen] = useState(false);
  const [brandMenuOpen, setBrandMenuOpen] = useState(false);
  const [analyzingBrand, setAnalyzingBrand] = useState(false);
  const [savingBrand, setSavingBrand] = useState(false);
  const [query, setQuery] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [sentimentFilter, setSentimentFilter] = useState("");
  const [voiceRange, setVoiceRange] = useState("30");
  const [productFilter, setProductFilter] = useState("");
  const [savingActionId, setSavingActionId] = useState("");
  const [salesBrandFilter, setSalesBrandFilter] = useState("");
  const [salesProductFilter, setSalesProductFilter] = useState("");
  const [selectedSalesBrandId, setSelectedSalesBrandId] = useState("");
  const [editingSalesBrandId, setEditingSalesBrandId] = useState("");
  const [editingSalesLinkId, setEditingSalesLinkId] = useState("");
  const [savingSalesBrand, setSavingSalesBrand] = useState(false);
  const [savingSalesLink, setSavingSalesLink] = useState(false);
  const [discoveringSalesChannels, setDiscoveringSalesChannels] = useState(false);
  const [salesDiscovery, setSalesDiscovery] = useState<SalesChannelDiscovery | null>(null);
  const [selectedCommunityBrandId, setSelectedCommunityBrandId] = useState("");
  const [selectedCommunityId, setSelectedCommunityId] = useState("all");
  const [savingCommunityBrand, setSavingCommunityBrand] = useState(false);
  const [savingCommunitySource, setSavingCommunitySource] = useState(false);
  const [collectingCommunitySource, setCollectingCommunitySource] = useState("");
  const [editingCommunityBrandId, setEditingCommunityBrandId] = useState("");
  const [editingCommunitySourceId, setEditingCommunitySourceId] = useState("");
  const [communitySettingsOpen, setCommunitySettingsOpen] = useState(false);
  const [communityMenuOpen, setCommunityMenuOpen] = useState(false);
  const [selectedBrandConfigId, setSelectedBrandConfigId] = useState("");
  const [mediaRange, setMediaRange] = useState("30");
  const [selectedMediaMonitorId, setSelectedMediaMonitorId] = useState("");
  const [mediaCreateOpen, setMediaCreateOpen] = useState(false);
  const [mediaMenuOpen, setMediaMenuOpen] = useState(false);
  const [editingMediaMonitorId, setEditingMediaMonitorId] = useState("");
  const [savingMediaMonitor, setSavingMediaMonitor] = useState(false);
  const [scanningMediaMonitor, setScanningMediaMonitor] = useState("");
  const [marketingSavingLink, setMarketingSavingLink] = useState("");
  const [marketingCollectingLink, setMarketingCollectingLink] = useState("");
  const [marketingEditingLinkId, setMarketingEditingLinkId] = useState("");
  const [brandMarketingSettingsOpen, setBrandMarketingSettingsOpen] = useState(false);
  const [marketingLinkForm, setMarketingLinkForm] = useState<MarketingLinkForm>({
    monitor_type: "social" as MarketingMonitorType,
    brand_id: "",
    brand_name: "",
    platform: "",
    name: "",
    url: "",
    status: "active" as "active" | "paused"
  });
  const [webRange, setWebRange] = useState("7");
  const [selectedMonitorId, setSelectedMonitorId] = useState("");
  const [savingMonitor, setSavingMonitor] = useState(false);
  const [monitorMenuOpen, setMonitorMenuOpen] = useState(false);
  const [monitorSettingsOpen, setMonitorSettingsOpen] = useState(false);
  const [editingMonitorId, setEditingMonitorId] = useState("");
  const [salesMenuOpen, setSalesMenuOpen] = useState(false);
  const [salesSettingsMode, setSalesSettingsMode] = useState<"" | "brand" | "link">("");
  const [captureJobs, setCaptureJobs] = useState<Record<string, CaptureJob>>({});
  const [mediaForm, setMediaForm] = useState({
    brand_id: "",
    brand_name: "",
    query: "",
    region: "US",
    language: "en-US"
  });
  const [monitorForm, setMonitorForm] = useState({
    brand_id: "",
    name: "",
    url: "",
    scope: "domain" as "single_page" | "domain",
    crawl_limit: 20
  });
  const [communityBrandForm, setCommunityBrandForm] = useState({
    name: "",
    description: ""
  });
  const [communitySourceForm, setCommunitySourceForm] = useState({
    brand_id: "",
    platform: "reddit" as CommunitySource["platform"],
    name: "",
    url: "",
    notes: ""
  });
  const [salesBrandForm, setSalesBrandForm] = useState({
    brand_profile_id: "",
    name: "",
    source_url: "",
    status: "active" as "active" | "paused",
    notes: ""
  });
  const [salesLinkForm, setSalesLinkForm] = useState({
    platform: "amazon",
    name: "",
    url: "",
    store_type: "自营店",
    region: "US",
    status: "active" as "active" | "paused",
    cadence: "manual",
    notes: ""
  });
  const [form, setForm] = useState({
    source_id: "manual_csv",
    monitor_id: "",
    data_type: "user_voice",
    platform: "TikTok",
    brand: "Our Brand",
    competitor: "",
    product: "",
    region: "US",
    language: "en",
    title: "",
    author: "",
    body: ""
  });
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const captureTimersRef = useRef<Record<string, number>>({});

  const loadData = async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams();
      if (query) params.set("q", query);
      if (sourceFilter) params.set("source_id", sourceFilter);
      if (typeFilter) params.set("data_type", typeFilter);
      if (sentimentFilter) params.set("sentiment", sentimentFilter);
      if (productFilter) params.set("product", productFilter);
      params.set("days", voiceRange);
      const webParams = new URLSearchParams();
      webParams.set("days", webRange);
      if (selectedMonitorId) webParams.set("monitor_id", selectedMonitorId);
      const mediaParams = new URLSearchParams();
      mediaParams.set("days", mediaRange);
      if (selectedMediaMonitorId) mediaParams.set("monitor_id", selectedMediaMonitorId);
      const communityParams = new URLSearchParams();
      communityParams.set("limit", "200");

      const [
        nextOverview,
        nextSources,
        nextBrands,
        nextRecords,
        nextVocSummary,
        nextSalesChannels,
        nextSalesChannelBrands,
        nextCommunitySummary,
        nextCommunityRecords,
        nextWebMonitors,
        nextWebSnapshots,
        nextWebSummary,
        nextMediaMonitors,
        nextMediaMentions,
        nextMediaSummary,
        nextSocialLinks,
        nextCreatorLinks,
        nextAdsLinks,
        nextSocialSummary,
        nextCreatorSummary,
        nextAdsSummary
      ] = await Promise.all([
        apiGet<Overview>("/api/overview"),
        apiGet<Source[]>("/api/sources"),
        apiGet<BrandProfile[]>("/api/brands"),
        apiGet<RecordItem[]>(`/api/records?${params.toString()}`),
        apiGet<VocSummary>(`/api/voc-summary?${params.toString()}`),
        apiGet<SalesChannel[]>("/api/sales-channels"),
        apiGet<SalesChannelBrand[]>("/api/sales-channel-brands"),
        apiGet<CommunitySummary>("/api/community-summary"),
        apiGet<RecordItem[]>(`/api/community-records?${communityParams.toString()}`),
        apiGet<WebMonitor[]>(`/api/web-monitors?days=${webRange}`),
        apiGet<WebSnapshot[]>(`/api/web-snapshots?${webParams.toString()}`),
        apiGet<WebSummary>(`/api/web-monitor-summary?${webParams.toString()}`),
        apiGet<MediaMonitor[]>(`/api/media-monitors?days=${mediaRange}`),
        apiGet<MediaMention[]>(`/api/media-mentions?${mediaParams.toString()}`),
        apiGet<MediaSummary>(`/api/media-summary?${mediaParams.toString()}`),
        apiGet<MarketingLink[]>("/api/marketing-links?monitor_type=social"),
        apiGet<MarketingLink[]>("/api/marketing-links?monitor_type=creator"),
        apiGet<MarketingLink[]>("/api/marketing-links?monitor_type=ads"),
        apiGet<MarketingLinkSummary>("/api/marketing-link-summary?monitor_type=social"),
        apiGet<MarketingLinkSummary>("/api/marketing-link-summary?monitor_type=creator"),
        apiGet<MarketingLinkSummary>("/api/marketing-link-summary?monitor_type=ads")
      ]);
      setOverview(nextOverview);
      setSources(nextSources);
      setBrands(nextBrands);
      setRecords(nextRecords);
      setVocSummary(nextVocSummary);
      setSalesChannels(nextSalesChannels);
      setSalesChannelBrands(nextSalesChannelBrands);
      setCommunitySummary(nextCommunitySummary);
      setCommunityRecords(nextCommunityRecords);
      setWebMonitors(nextWebMonitors);
      setWebSnapshots(nextWebSnapshots);
      setWebSummary(nextWebSummary);
      setMediaMonitors(nextMediaMonitors);
      setMediaMentions(nextMediaMentions);
      setMediaSummary(nextMediaSummary);
      setSocialLinks(nextSocialLinks);
      setCreatorLinks(nextCreatorLinks);
      setAdsLinks(nextAdsLinks);
      setSocialSummary(nextSocialSummary);
      setCreatorSummary(nextCreatorSummary);
      setAdsSummary(nextAdsSummary);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  useEffect(() => {
    return () => {
      Object.values(captureTimersRef.current).forEach((timer) => window.clearTimeout(timer));
    };
  }, []);

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      loadData();
    }, 240);
    return () => window.clearTimeout(timeout);
  }, [query, sourceFilter, typeFilter, sentimentFilter, voiceRange, productFilter, webRange, selectedMonitorId, mediaRange, selectedMediaMonitorId]);

  useEffect(() => {
    if (marketingEditingLinkId) return;
    if (activeView === "marketing-social") {
      setMarketingLinkForm((current) => ({ ...current, monitor_type: "social" }));
    } else if (activeView === "marketing-creators") {
      setMarketingLinkForm((current) => ({ ...current, monitor_type: "creator" }));
    } else if (activeView === "marketing-ads") {
      setMarketingLinkForm((current) => ({ ...current, monitor_type: "ads" }));
    }
  }, [activeView, marketingEditingLinkId]);

  const maxTrend = Math.max(1, ...overview.trend.map((item) => item.count));
  const negativeCount = overview.by_sentiment.negative ?? 0;
  const highSignal = overview.recent.filter((record) => record.sentiment === "negative" || record.intent === "purchase_signal");
  const readySources = sources.filter((source) => source.status === "ready").length;
  const selectedMonitor = webMonitors.find((monitor) => monitor.id === selectedMonitorId);
  const webRangeLabel = webRange === "1" ? "今天" : webRange === "7" ? "近 7 天" : webRange === "15" ? "近半个月" : `近 ${webRange} 天`;
  const captureJobList = Object.values(captureJobs).sort((a, b) => b.created_at.localeCompare(a.created_at));
  const activeCaptureJobs = captureJobList.filter((job) => job.status === "queued" || job.status === "running");
  const selectedCaptureJob = captureJobList.find((job) => job.monitor_id === selectedMonitorId) || activeCaptureJobs[0];
  const selectedMediaMonitor = mediaMonitors.find((monitor) => monitor.id === selectedMediaMonitorId);
  const mediaRangeLabel = rangeLabel(mediaRange);
  const maxMediaDaily = Math.max(1, ...mediaSummary.daily.map((item) => item.mentions));
  const paidMediaRate = mediaSummary.total_mentions ? mediaSummary.paid_mentions / mediaSummary.total_mentions : 0;
  const earnedMediaRate = mediaSummary.total_mentions ? mediaSummary.earned_mentions / mediaSummary.total_mentions : 0;
  const mediaMonitorStatus = (monitor: MediaMonitor) => {
    if (scanningMediaMonitor === monitor.id) return { label: "扫描中", tone: "running" };
    if (monitor.status === "paused") return { label: "已暂停", tone: "paused" };
    if (monitor.last_status === "error") return { label: "扫描失败", tone: "error" };
    if (monitor.last_status === "pending") return { label: "待首次扫描", tone: "pending" };
    return { label: "监控中", tone: "active" };
  };
  const allSalesDashboard = buildSalesDashboard(salesChannels);
  const salesBrandOptions = Array.from(new Set(allSalesDashboard.channels.map((channel) => channel.brand_name)))
    .sort()
    .map((brandName) => ({ value: brandName, label: brandName }));
  const salesProductOptions = Array.from(new Set(
    allSalesDashboard.channels
      .filter((channel) => !salesBrandFilter || channel.brand_name === salesBrandFilter)
      .map((channel) => channel.product_name)
  ))
    .sort()
    .map((productName) => ({ value: productName, label: productName }));
  const salesDashboard = filterSalesDashboard(allSalesDashboard, salesBrandFilter, salesProductFilter);
  const maxSalesUnits = Math.max(1, ...salesDashboard.channels.map((channel) => channel.sales_units));
  const selectedSalesBrand = salesChannelBrands.find((brand) => brand.id === selectedSalesBrandId) || salesChannelBrands[0];
  const salesLinkTotal = salesChannelBrands.reduce((sum, brand) => sum + brand.link_count, 0);
  const salesActiveLinkTotal = salesChannelBrands.reduce((sum, brand) => sum + brand.active_link_count, 0);
  const salesPlatformTotal = new Set(salesChannelBrands.flatMap((brand) => brand.platforms)).size;
  const voiceRangeLabel = rangeLabel(voiceRange);
  const maxVocTrend = Math.max(1, ...vocSummary.trend.map((item) => item.count));
  const vocProductOptions = vocSummary.products
    .filter((product) => product.value)
    .map((product) => ({ value: product.value, label: product.product }));
  const activeVocActions = vocSummary.actions.filter((action) => action.status !== "closed");
  const communityBrands = communitySummary.brands;
  const selectedCommunityBrand = selectedCommunityBrandId
    ? communityBrands.find((brand) => brand.id === selectedCommunityBrandId)
    : undefined;
  const communitySourceBrandId = selectedCommunityBrand?.id || communitySourceForm.brand_id || communityBrands[0]?.id || "";
  const communitySourceSubmitDisabled = !communityBrands.length || savingCommunitySource || !communitySourceForm.url.trim();
  const communitySourceSubmitTitle = !communityBrands.length
    ? "请先创建品牌"
    : !communitySourceForm.url.trim()
      ? "请填写社群链接"
      : undefined;
  const communitySourceSubmitLabel = !communityBrands.length
    ? "先创建品牌"
    : editingCommunitySourceId
      ? "保存来源"
      : "新增来源";
  const scopedCommunitySources = selectedCommunityBrand
    ? selectedCommunityBrand.sources
    : communityBrands.flatMap((brand) => brand.sources);
  const selectedCommunitySource = scopedCommunitySources.find((source) => source.id === selectedCommunityId);
  const selectedCommunityRecords = communityRecords.filter((record) => {
    const brandMatch = !selectedCommunityBrand?.id || record.community_brand_id === selectedCommunityBrand.id;
    const sourceMatch = !selectedCommunitySource || record.monitor_id === selectedCommunitySource.id;
    return brandMatch && sourceMatch;
  });
  const selectedCommunityTitle = selectedCommunitySource?.name || selectedCommunityBrand?.name || "全部社群";
  const selectedCommunityRecordCount = selectedCommunitySource
    ? selectedCommunitySource.record_count
    : selectedCommunityBrand
      ? selectedCommunityBrand.record_count
      : communitySummary.total_records;
  const selectedCommunityRecordListLabel = selectedCommunityRecords.length === selectedCommunityRecordCount
    ? `${selectedCommunityRecordCount}`
    : `${selectedCommunityRecords.length}/${selectedCommunityRecordCount}`;
  const communityNegative = selectedCommunitySource
    ? selectedCommunitySource.negative_count
    : selectedCommunityBrand
      ? selectedCommunityBrand.negative_count
      : communitySummary.negative_records;
  const communityNegativeRate = selectedCommunitySource
    ? selectedCommunitySource.negative_rate
    : selectedCommunityBrand
      ? selectedCommunityBrand.negative_rate
      : communitySummary.negative_rate;
  const communityTopTopics = selectedCommunitySource
    ? selectedCommunitySource.top_topics
    : selectedCommunityBrand
      ? selectedCommunityBrand.top_topics
      : communitySummary.top_topics;
  const communityAlerts = scopedCommunitySources.filter((source) => source.record_count > 0 && (source.negative_rate >= 0.18 || source.negative_count >= 2));
  const communityPlatformRows = selectedCommunityBrand
    ? selectedCommunityBrand.sources.map((source) => ({
      platform: source.platform,
      platform_label: source.platform_label,
      count: source.record_count,
      negative: source.negative_count,
      negative_rate: source.negative_rate
    }))
    : communitySummary.by_platform;
  const selectedBrandConfig = brands.find((brand) => brand.id === selectedBrandConfigId) || brands[0];
  const findCommunityBrandForProfile = (brand?: BrandProfile) => brand
    ? communityBrands.find((item) => item.brand_profile_id === brand.id)
      || communityBrands.find((item) => !item.brand_profile_id && nameKey(item.name) === nameKey(brand.name))
    : undefined;
  const findSalesBrandForProfile = (brand?: BrandProfile) => brand
    ? salesChannelBrands.find((item) => item.brand_profile_id === brand.id)
      || salesChannelBrands.find((item) => !item.brand_profile_id && nameKey(item.name) === nameKey(brand.name))
    : undefined;
  const brandConfigCommunity = findCommunityBrandForProfile(selectedBrandConfig);
  const brandConfigSales = findSalesBrandForProfile(selectedBrandConfig);
  const brandConfigWeb = selectedBrandConfig ? webMonitors.filter((monitor) => monitor.brand_id === selectedBrandConfig.id) : [];
  const brandConfigMedia = selectedBrandConfig
    ? mediaMonitors.filter((monitor) => monitor.brand_id === selectedBrandConfig.id || (!monitor.brand_id && nameKey(monitor.brand_name) === nameKey(selectedBrandConfig.name)))
    : [];
  const brandConfigMarketing = selectedBrandConfig
    ? [...socialLinks, ...creatorLinks, ...adsLinks].filter((link) => link.brand_id === selectedBrandConfig.id || (!link.brand_id && nameKey(link.brand_name) === nameKey(selectedBrandConfig.name)))
    : [];
  const brandConfigTotals = {
    community: brandConfigCommunity?.source_count || 0,
    sales: brandConfigSales?.link_count || 0,
    web: brandConfigWeb.length,
    marketing: brandConfigMedia.length + brandConfigMarketing.length
  };

  const openCommunityRecordForm = (source?: CommunitySource) => {
    setForm((current) => ({
      ...current,
      source_id: source ? communitySourceId(source.platform) : "reddit_search",
      monitor_id: source?.id || "",
      data_type: "community_post",
      platform: source?.platform_label || "Reddit",
      brand: selectedCommunityBrand?.name || current.brand
    }));
    setActiveView("voice");
  };

  const openBrandCommunityConfig = async (brand: BrandProfile) => {
    setError("");
    let target = findCommunityBrandForProfile(brand);
    try {
      if (!target) {
        target = await apiPost<CommunityBrand>("/api/community-brands", {
          brand_profile_id: brand.id,
          name: brand.name,
          description: brand.description || "",
          status: "active"
        });
        await loadData();
      }
      setSelectedCommunityBrandId(target.id);
      setSelectedCommunityId("all");
      setCommunityBrandForm({ name: "", description: "" });
      setEditingCommunityBrandId("");
      setCommunitySourceForm({ brand_id: target.id, platform: "reddit", name: "", url: "", notes: "" });
      setCommunitySettingsOpen(true);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "社群配置打开失败");
    }
  };

  const openBrandSalesConfig = (brand: BrandProfile) => {
    const salesBrand = findSalesBrandForProfile(brand);
    setSalesMenuOpen(false);
    if (salesBrand) {
      setSelectedSalesBrandId(salesBrand.id);
      resetSalesLinkForm();
      setSalesSettingsMode("link");
      return;
    }
    resetSalesBrandForm();
    setSalesBrandForm({
      brand_profile_id: brand.id,
      name: brand.name,
      source_url: brand.amazon_url || primaryBrandUrl(brand),
      status: "active",
      notes: ""
    });
    setSalesSettingsMode("brand");
  };

  const openBrandWebConfig = (brand: BrandProfile, monitor?: WebMonitor) => {
    if (monitor) {
      setSelectedMonitorId(monitor.id);
      setMonitorForm({
        brand_id: monitor.brand_id || brand.id,
        name: monitor.name,
        url: monitor.url,
        scope: monitor.scope,
        crawl_limit: monitor.crawl_limit || 20
      });
      setEditingMonitorId(monitor.id);
    } else {
      setMonitorForm({
        brand_id: brand.id,
        name: `${brand.name} 官网`,
        url: primaryBrandUrl(brand),
        scope: "domain",
        crawl_limit: 20
      });
      setEditingMonitorId("");
    }
    setMonitorMenuOpen(false);
    setMonitorSettingsOpen(true);
  };

  const openBrandMediaConfig = (brand: BrandProfile, monitor?: MediaMonitor) => {
    setMediaForm({
      brand_id: brand.id,
      brand_name: monitor?.brand_name || brand.name,
      query: monitor?.query || `"${primaryBrandKeyword(brand)}"`,
      region: monitor?.region || "US",
      language: monitor?.language || "en-US"
    });
    setEditingMediaMonitorId(monitor?.id || "");
    setMediaCreateOpen(true);
  };

  const openBrandMarketingConfig = (brand: BrandProfile, monitorType: MarketingMonitorType, link?: MarketingLink) => {
    setMarketingEditingLinkId(link?.id || "");
    setMarketingLinkForm({
      monitor_type: monitorType,
      brand_id: brand.id,
      brand_name: brand.name,
      platform: link?.platform || "",
      name: link?.name || "",
      url: link?.url || "",
      status: link?.status || "active"
    });
    setBrandMarketingSettingsOpen(true);
  };

  const saveCommunityBrand = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!communityBrandForm.name.trim()) return;
    setSavingCommunityBrand(true);
    setError("");
    try {
      const payload = { ...communityBrandForm, status: "active" };
      const saved = editingCommunityBrandId
        ? await apiPut<CommunityBrand>(`/api/community-brands/${editingCommunityBrandId}`, payload)
        : await apiPost<CommunityBrand>("/api/community-brands", payload);
      setSelectedCommunityBrandId(saved.id);
      setCommunitySourceForm((current) => ({ ...current, brand_id: saved.id }));
      setCommunityBrandForm({ name: "", description: "" });
      setEditingCommunityBrandId("");
      await loadData();
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "社群品牌保存失败");
    } finally {
      setSavingCommunityBrand(false);
    }
  };

  const editCommunityBrand = (brand: CommunityBrand) => {
    setEditingCommunityBrandId(brand.id);
    setSelectedCommunityBrandId(brand.id);
    setCommunityBrandForm({ name: brand.name, description: brand.description || "" });
  };

  const deleteCommunityBrand = async (brand: CommunityBrand) => {
    const confirmed = window.confirm(`删除社群品牌「${brand.name}」及其来源和记录？`);
    if (!confirmed) return;
    await apiDelete<{ deleted: number }>(`/api/community-brands/${brand.id}`);
    if (selectedCommunityBrandId === brand.id) {
      setSelectedCommunityBrandId("");
      setSelectedCommunityId("all");
    }
    await loadData();
  };

  const saveCommunitySource = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const brandId = communitySourceForm.brand_id || selectedCommunityBrand?.id || communityBrands[0]?.id;
    if (!brandId) {
      setError("请先创建品牌，再新增社群来源");
      return;
    }
    if (!communitySourceForm.url.trim()) return;
    setSavingCommunitySource(true);
    setError("");
    try {
      const payload = { ...communitySourceForm, brand_id: brandId, status: "active", cadence: "manual" };
      const saved = editingCommunitySourceId
        ? await apiPut<CommunitySource>(`/api/community-sources/${editingCommunitySourceId}`, payload)
        : await apiPost<CommunitySource>("/api/community-sources", payload);
      setSelectedCommunityBrandId(saved.brand_id);
      setSelectedCommunityId(saved.id);
      setCommunitySourceForm({ brand_id: saved.brand_id, platform: "reddit", name: "", url: "", notes: "" });
      setEditingCommunitySourceId("");
      await loadData();
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "社群来源保存失败");
    } finally {
      setSavingCommunitySource(false);
    }
  };

  const editCommunitySource = (source: CommunitySource) => {
    setEditingCommunitySourceId(source.id);
    setSelectedCommunityBrandId(source.brand_id);
    setSelectedCommunityId(source.id);
    setCommunitySourceForm({
      brand_id: source.brand_id,
      platform: source.platform,
      name: source.name,
      url: source.url,
      notes: source.notes || ""
    });
  };

  const deleteCommunitySource = async (source: CommunitySource) => {
    const confirmed = window.confirm(`删除社群来源「${source.name}」及其记录？`);
    if (!confirmed) return;
    await apiDelete<{ deleted: number }>(`/api/community-sources/${source.id}`);
    if (selectedCommunityId === source.id) {
      setSelectedCommunityId("all");
    }
    await loadData();
  };

  const collectCommunitySource = async (source: CommunitySource) => {
    setCollectingCommunitySource(source.id);
    setError("");
    try {
      await apiPost<{ source: CommunitySource; created: number; scanned: number }>(`/api/community-sources/${source.id}/collect`, {});
      await loadData();
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "社群采集失败");
      await loadData();
    } finally {
      setCollectingCommunitySource("");
    }
  };

  const resetSalesBrandForm = () => {
    setSalesBrandForm({ brand_profile_id: "", name: "", source_url: "", status: "active", notes: "" });
    setEditingSalesBrandId("");
    setSalesDiscovery(null);
  };

  const resetSalesLinkForm = () => {
    setSalesLinkForm({ platform: "amazon", name: "", url: "", store_type: "自营店", region: "US", status: "active", cadence: "manual", notes: "" });
    setEditingSalesLinkId("");
  };

  const openNewSalesBrand = () => {
    resetSalesBrandForm();
    setSalesMenuOpen(false);
    setSalesSettingsMode("brand");
  };

  const openNewSalesLink = () => {
    resetSalesLinkForm();
    if (!selectedSalesBrandId && salesChannelBrands[0]) {
      setSelectedSalesBrandId(salesChannelBrands[0].id);
    }
    setSalesMenuOpen(false);
    setSalesSettingsMode("link");
  };

  const closeSalesSettings = () => {
    setSalesSettingsMode("");
    resetSalesBrandForm();
    resetSalesLinkForm();
  };

  const applySalesBrandProfile = (brandId: string) => {
    const brand = brands.find((item) => item.id === brandId);
    setSalesBrandForm((current) => ({
      ...current,
      brand_profile_id: brandId,
      name: brand?.name || current.name,
      source_url: brand?.official_website || brand?.amazon_url || brand?.source_url || current.source_url
    }));
  };

  const saveSalesBrand = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!salesBrandForm.name.trim() && !salesBrandForm.source_url.trim() && !salesBrandForm.brand_profile_id) return;
    setSavingSalesBrand(true);
    setError("");
    try {
      const saved = editingSalesBrandId
        ? await apiPut<SalesChannelBrand>(`/api/sales-channel-brands/${editingSalesBrandId}`, salesBrandForm)
        : await apiPost<SalesChannelBrand>("/api/sales-channel-brands", { ...salesBrandForm, auto_analyze: true });
      setSelectedSalesBrandId(saved.id);
      resetSalesBrandForm();
      setSalesSettingsMode("");
      await loadData();
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "渠道销售品牌保存失败");
    } finally {
      setSavingSalesBrand(false);
    }
  };

  const discoverSalesChannels = async () => {
    if (!salesBrandForm.source_url.trim()) return;
    setDiscoveringSalesChannels(true);
    setError("");
    try {
      const discovery = await apiPost<SalesChannelDiscovery>("/api/sales-channel-discovery", {
        url: salesBrandForm.source_url,
        brand_name: salesBrandForm.name
      });
      setSalesDiscovery(discovery);
      setSalesBrandForm((current) => ({
        ...current,
        name: current.name || discovery.brand.name,
        source_url: discovery.brand.source_url || current.source_url
      }));
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "渠道链接识别失败");
    } finally {
      setDiscoveringSalesChannels(false);
    }
  };

  const createSalesBrandFromDiscovery = async () => {
    if (!salesBrandForm.source_url.trim()) return;
    setSavingSalesBrand(true);
    setError("");
    try {
      const saved = await apiPost<SalesChannelBrand>("/api/sales-channel-brands/discover", {
        url: salesBrandForm.source_url,
        brand_name: salesBrandForm.name
      });
      setSelectedSalesBrandId(saved.id);
      resetSalesBrandForm();
      setSalesSettingsMode("");
      await loadData();
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "渠道监控创建失败");
    } finally {
      setSavingSalesBrand(false);
    }
  };

  const editSalesBrand = (brand: SalesChannelBrand) => {
    setEditingSalesBrandId(brand.id);
    setSelectedSalesBrandId(brand.id);
    setSalesBrandForm({
      brand_profile_id: brand.brand_profile_id || "",
      name: brand.name,
      source_url: brand.source_url || "",
      status: brand.status,
      notes: brand.notes || ""
    });
    setSalesDiscovery(null);
    setSalesMenuOpen(false);
    setSalesSettingsMode("brand");
  };

  const deleteSalesBrand = async (brand: SalesChannelBrand) => {
    const confirmed = window.confirm(`删除渠道销售品牌「${brand.name}」及其渠道链接？`);
    if (!confirmed) return;
    await apiDelete<{ deleted: number }>(`/api/sales-channel-brands/${brand.id}`);
    if (selectedSalesBrandId === brand.id) {
      setSelectedSalesBrandId("");
    }
    await loadData();
  };

  const saveSalesLink = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const brandId = selectedSalesBrand?.id;
    if (!brandId || !salesLinkForm.url.trim()) return;
    setSavingSalesLink(true);
    setError("");
    try {
      const payload = { ...salesLinkForm, sales_brand_id: brandId };
      await (editingSalesLinkId
        ? apiPut<SalesChannelLink>(`/api/sales-channel-links/${editingSalesLinkId}`, payload)
        : apiPost<SalesChannelLink>("/api/sales-channel-links", payload));
      resetSalesLinkForm();
      setSalesSettingsMode("");
      await loadData();
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "渠道链接保存失败");
    } finally {
      setSavingSalesLink(false);
    }
  };

  const addSalesDiscoveryCandidate = async (candidate: SalesChannelDiscovery["candidates"][number]) => {
    const brandId = selectedSalesBrand?.id;
    if (!brandId) return;
    setSavingSalesLink(true);
    setError("");
    try {
      await apiPost<SalesChannelLink>("/api/sales-channel-links", {
        ...candidate,
        sales_brand_id: brandId,
        discovery_source: candidate.discovery_source || "discovery"
      });
      resetSalesBrandForm();
      setSalesSettingsMode("");
      await loadData();
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "候选渠道保存失败");
    } finally {
      setSavingSalesLink(false);
    }
  };

  const editSalesLink = (link: SalesChannelLink) => {
    setSelectedSalesBrandId(link.sales_brand_id);
    setEditingSalesLinkId(link.id);
    setSalesLinkForm({
      platform: link.platform,
      name: link.name,
      url: link.url,
      store_type: link.store_type,
      region: link.region,
      status: link.status,
      cadence: link.cadence,
      notes: link.notes || ""
    });
    setSalesMenuOpen(false);
    setSalesSettingsMode("link");
  };

  const deleteSalesLink = async (link: SalesChannelLink) => {
    const confirmed = window.confirm(`删除渠道链接「${link.name}」？`);
    if (!confirmed) return;
    await apiDelete<{ deleted: number }>(`/api/sales-channel-links/${link.id}`);
    if (editingSalesLinkId === link.id) {
      resetSalesLinkForm();
    }
    await loadData();
  };

  const applyMediaBrand = (brandId: string) => {
    const brand = brands.find((item) => item.id === brandId);
    setMediaForm((current) => ({
      ...current,
      brand_id: brandId,
      brand_name: brand?.name || current.brand_name,
      query: brand ? `"${brand.monitoring_keywords[0] || brand.name}"` : current.query
    }));
  };

  const createMediaMonitor = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!mediaForm.brand_name.trim() && !mediaForm.query.trim() && !mediaForm.brand_id) return;
    setSavingMediaMonitor(true);
    setError("");
    try {
      const monitor = editingMediaMonitorId
        ? await apiPut<MediaMonitor>(`/api/media-monitors/${editingMediaMonitorId}`, mediaForm)
        : await apiPost<MediaMonitor>("/api/media-monitors", {
          ...mediaForm,
          scan_now: true
        });
      setMediaForm({ brand_id: "", brand_name: "", query: "", region: "US", language: "en-US" });
      setEditingMediaMonitorId("");
      setSelectedMediaMonitorId(monitor.id);
      setMediaCreateOpen(false);
      await loadData();
      if (activeView === "marketing-media") {
        setActiveView("marketing-media");
      }
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "媒体监控保存失败");
    } finally {
      setSavingMediaMonitor(false);
    }
  };

  const scanMediaMonitor = async (monitorId: string) => {
    setScanningMediaMonitor(monitorId);
    setError("");
    try {
      await apiPost<{ monitor: MediaMonitor; created: number; scanned: number }>(`/api/media-monitors/${monitorId}/scan`, {});
      await loadData();
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "媒体扫描失败");
    } finally {
      setScanningMediaMonitor("");
    }
  };

  const toggleMediaMonitor = async (monitor: MediaMonitor) => {
    const status = monitor.status === "active" ? "paused" : "active";
    await apiPut<MediaMonitor>(`/api/media-monitors/${monitor.id}`, { ...monitor, status });
    await loadData();
  };

  const deleteMediaMonitor = async (monitor: MediaMonitor) => {
    const confirmed = window.confirm(`删除媒体监控「${monitor.brand_name}」？`);
    if (!confirmed) return;
    await apiDelete<{ deleted: number }>(`/api/media-monitors/${monitor.id}`);
    if (selectedMediaMonitorId === monitor.id) {
      setSelectedMediaMonitorId("");
    }
    await loadData();
  };

  const resetMarketingLinkForm = (monitorType: MarketingMonitorType = marketingLinkForm.monitor_type) => {
    setMarketingEditingLinkId("");
    setMarketingLinkForm({
      monitor_type: monitorType,
      brand_id: "",
      brand_name: "",
      platform: "",
      name: "",
      url: "",
      status: "active"
    });
  };

  const applyMarketingBrand = (brandId: string) => {
    const brand = brands.find((item) => item.id === brandId);
    setMarketingLinkForm((current) => ({
      ...current,
      brand_id: brandId,
      brand_name: brand?.name || current.brand_name
    }));
  };

  const editMarketingLink = (link: MarketingLink) => {
    setMarketingEditingLinkId(link.id);
    setMarketingLinkForm({
      monitor_type: link.monitor_type,
      brand_id: link.brand_id || "",
      brand_name: link.brand_name,
      platform: link.platform,
      name: link.name,
      url: link.url,
      status: link.status
    });
  };

  const saveMarketingLink = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!marketingLinkForm.brand_name.trim() || !marketingLinkForm.url.trim()) return;
    const savingKey = marketingEditingLinkId || `${marketingLinkForm.monitor_type}:new`;
    setMarketingSavingLink(savingKey);
    setError("");
    try {
      if (marketingEditingLinkId) {
        await apiPut<MarketingLink>(`/api/marketing-links/${marketingEditingLinkId}`, marketingLinkForm);
      } else {
        await apiPost<MarketingLink>("/api/marketing-links", { ...marketingLinkForm, collect_now: true });
      }
      const nextType = marketingLinkForm.monitor_type;
      resetMarketingLinkForm(nextType);
      setBrandMarketingSettingsOpen(false);
      await loadData();
      if (activeView === "marketing-social" || activeView === "marketing-creators" || activeView === "marketing-ads") {
        setActiveView(nextType === "creator" ? "marketing-creators" : nextType === "ads" ? "marketing-ads" : "marketing-social");
      }
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "营销链接保存失败");
    } finally {
      setMarketingSavingLink("");
    }
  };

  const collectMarketingLink = async (link: MarketingLink) => {
    setMarketingCollectingLink(link.id);
    setError("");
    try {
      await apiPost<{ link: MarketingLink; created: number; scanned: number }>(`/api/marketing-links/${link.id}/collect`, {});
      await loadData();
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "链接采集失败");
    } finally {
      setMarketingCollectingLink("");
    }
  };

  const toggleMarketingLink = async (link: MarketingLink) => {
    const status = link.status === "active" ? "paused" : "active";
    await apiPut<MarketingLink>(`/api/marketing-links/${link.id}`, { ...link, status });
    await loadData();
  };

  const deleteMarketingLink = async (link: MarketingLink) => {
    const confirmed = window.confirm(`删除「${link.brand_name} / ${link.name}」？`);
    if (!confirmed) return;
    await apiDelete<{ deleted: number }>(`/api/marketing-links/${link.id}`);
    if (marketingEditingLinkId === link.id) {
      resetMarketingLinkForm(link.monitor_type);
    }
    await loadData();
  };

  const createVocAction = async (payload: Partial<VocAction> & { record_ids?: string[] }, savingKey: string) => {
    setSavingActionId(savingKey);
    setError("");
    try {
      await apiPost<VocAction>("/api/voc-actions", payload);
      await loadData();
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "闭环任务创建失败");
    } finally {
      setSavingActionId("");
    }
  };

  const createActionFromAlert = async (alert: VocAlert) => {
    await createVocAction({
      title: alert.title,
      description: alert.description,
      owner_team: alert.owner_team,
      priority: alert.priority,
      topic: alert.topic,
      product: alert.product,
      source_id: alert.source_id,
      record_ids: alert.record_ids
    }, alert.id);
  };

  const createActionFromRecord = async (record: RecordItem) => {
    await createVocAction({
      record_id: record.id,
      title: record.title || record.body.slice(0, 54),
      description: record.body,
      product: record.product,
      source_id: record.source_id,
      topic: record.topics[0]
    }, record.id);
  };

  const updateVocActionStatus = async (action: VocAction, status: VocActionStatus) => {
    setSavingActionId(action.id);
    setError("");
    try {
      await apiPut<VocAction>(`/api/voc-actions/${action.id}`, { status });
      await loadData();
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "闭环状态更新失败");
    } finally {
      setSavingActionId("");
    }
  };

  const deleteVocAction = async (action: VocAction) => {
    const confirmed = window.confirm(`删除闭环任务「${action.title}」？`);
    if (!confirmed) return;
    setSavingActionId(action.id);
    try {
      await apiDelete<{ deleted: number }>(`/api/voc-actions/${action.id}`);
      await loadData();
    } finally {
      setSavingActionId("");
    }
  };

  const submitRecord = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!form.body.trim()) return;
    await apiPost<RecordItem>("/api/records", form);
    setForm((current) => ({ ...current, body: "", title: "", author: "", competitor: "", product: "" }));
    await loadData();
    setActiveView("voice");
  };

  const handleCsv = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    const text = await file.text();
    const rows = parseCsv(text);
    await apiPost<{ created: number }>("/api/import", {
      source_id: form.source_id || "manual_csv",
      rows
    });
    event.target.value = "";
    await loadData();
    setActiveView("voice");
  };

  const resetMonitorForm = () => {
    setMonitorForm({ brand_id: "", name: "", url: "", scope: "domain", crawl_limit: 20 });
    setEditingMonitorId("");
  };

  const openNewMonitor = () => {
    resetMonitorForm();
    setMonitorMenuOpen(false);
    setMonitorSettingsOpen(true);
  };

  const openMonitorSettings = (monitor?: WebMonitor) => {
    const target = monitor || selectedMonitor;
    if (!target) {
      openNewMonitor();
      return;
    }
    setMonitorForm({
      brand_id: target.brand_id || "",
      name: target.name,
      url: target.url,
      scope: target.scope,
      crawl_limit: target.crawl_limit || 20
    });
    setEditingMonitorId(target.id);
    setMonitorMenuOpen(false);
    setMonitorSettingsOpen(true);
  };

  const closeMonitorSettings = () => {
    setMonitorSettingsOpen(false);
    resetMonitorForm();
  };

  const saveMonitorSettings = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!monitorForm.url.trim()) return;
    setSavingMonitor(true);
    setError("");
    try {
      const monitor = editingMonitorId
        ? await apiPut<WebMonitor>(`/api/web-monitors/${editingMonitorId}`, monitorForm)
        : await apiPost<WebMonitor>("/api/web-monitors", {
            ...monitorForm,
            capture_now: false
          });
      setSelectedMonitorId(monitor.id);
      closeMonitorSettings();
      await loadData();
      if (!editingMonitorId) {
        await startCaptureJob(monitor.id);
      }
      setActiveView("web");
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "网页监控保存失败");
    } finally {
      setSavingMonitor(false);
    }
  };

  const pollCaptureJob = async (jobId: string) => {
    try {
      const job = await apiGet<CaptureJob>(`/api/capture-jobs/${jobId}`);
      setCaptureJobs((current) => ({ ...current, [job.id]: job }));
      if (job.status === "queued" || job.status === "running") {
        captureTimersRef.current[job.id] = window.setTimeout(() => {
          pollCaptureJob(job.id);
        }, 800);
      } else {
        delete captureTimersRef.current[job.id];
        await loadData();
      }
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "进度更新失败");
    }
  };

  const startCaptureJob = async (monitorId: string) => {
    setError("");
    try {
      const job = await apiPost<CaptureJob>(`/api/web-monitors/${monitorId}/capture-job`, {});
      setCaptureJobs((current) => ({ ...current, [job.id]: job }));
      await pollCaptureJob(job.id);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "快照生成失败");
    }
  };

  const toggleMonitor = async (monitor: WebMonitor) => {
    const status = monitor.status === "active" ? "paused" : "active";
    await apiPut<WebMonitor>(`/api/web-monitors/${monitor.id}`, { ...monitor, status });
    await loadData();
  };

  const deleteMonitor = async (monitor: WebMonitor) => {
    const confirmed = window.confirm(`删除网页监控「${monitor.name}」？`);
    if (!confirmed) return;
    await apiDelete<{ deleted: number }>(`/api/web-monitors/${monitor.id}`);
    if (selectedMonitorId === monitor.id) {
      setSelectedMonitorId("");
    }
    await loadData();
  };

  const openBrandCreate = () => {
    setBrandMenuOpen(false);
    setBrandDraft(null);
    setBrandUrl("");
    setBrandCreateOpen(true);
  };

  const closeBrandCreate = () => {
    setBrandCreateOpen(false);
    setBrandDraft(null);
    setBrandUrl("");
  };

  const analyzeBrand = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!brandUrl.trim()) return;
    setAnalyzingBrand(true);
    setError("");
    try {
      const draft = await apiPost<BrandDraft>("/api/brands/analyze", { url: brandUrl });
      setBrandDraft(draft);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "品牌分析失败");
    } finally {
      setAnalyzingBrand(false);
    }
  };

  const updateBrandDraft = (key: keyof BrandDraft, value: string | string[] | Record<string, string>) => {
    setBrandDraft((current) => (current ? { ...current, [key]: value } : current));
  };

  const saveBrand = async () => {
    if (!brandDraft) return;
    setSavingBrand(true);
    setError("");
    try {
      const saved = brandDraft.id
        ? await apiPut<BrandProfile>(`/api/brands/${brandDraft.id}`, brandDraft)
        : await apiPost<BrandProfile>("/api/brands", brandDraft);
      setBrands((current) => [saved, ...current.filter((brand) => brand.id !== saved.id)]);
      setBrandDraft(null);
      setBrandUrl("");
      setBrandCreateOpen(false);
      await loadData();
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "品牌保存失败");
    } finally {
      setSavingBrand(false);
    }
  };

  const editBrand = (brand: BrandProfile) => {
    setBrandDraft({
      ...brand,
      confidence: 1,
      evidence: ["Loaded existing profile for editing"],
      duplicate_candidates: []
    });
    setBrandUrl(brand.source_url);
    setBrandCreateOpen(true);
    setBrandMenuOpen(false);
    setActiveView("brands");
  };

  const mergeIntoExistingBrand = (brandId: string) => {
    const existing = brands.find((brand) => brand.id === brandId);
    if (!existing || !brandDraft) return;
    const mergedKeywords = Array.from(new Set([
      ...existing.monitoring_keywords,
      ...(brandDraft.monitoring_keywords || [])
    ]));
    setBrandDraft({
      ...existing,
      source_url: existing.source_url || brandDraft.source_url,
      official_website: existing.official_website || brandDraft.official_website,
      amazon_url: existing.amazon_url || brandDraft.amazon_url,
      marketplace: existing.marketplace || brandDraft.marketplace,
      asin: existing.asin || brandDraft.asin,
      category: existing.category || brandDraft.category,
      description: existing.description || brandDraft.description,
      logo_url: existing.logo_url || brandDraft.logo_url,
      social_links: { ...(existing.social_links || {}), ...(brandDraft.social_links || {}) },
      ecommerce_links: { ...(existing.ecommerce_links || {}), ...(brandDraft.ecommerce_links || {}) },
      monitoring_keywords: mergedKeywords,
      confidence: 1,
      evidence: ["Merged current analysis into existing profile"],
      duplicate_candidates: []
    });
  };

  const deleteBrand = async (brand: BrandProfile) => {
    const confirmed = window.confirm(`删除品牌档案「${brand.name}」？`);
    if (!confirmed) return;
    await apiDelete<{ deleted: number }>(`/api/brands/${brand.id}`);
    if (brandDraft?.id === brand.id) {
      setBrandDraft(null);
      setBrandCreateOpen(false);
    }
    await loadData();
  };

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup">
          <div className="brand-mark"><Database size={22} /></div>
          <div>
            <strong>Monitor</strong>
            <span>Intelligence Hub</span>
          </div>
        </div>

        <nav className="nav-stack">
          <button className={classNames("nav-item", activeView === "overview" && "active")} onClick={() => setActiveView("overview")}>
            <TrendingUp size={18} /> 总览
          </button>
          <button className={classNames("nav-item", activeView === "communities" && "active")} onClick={() => setActiveView("communities")}>
            <MessageSquareText size={18} /> 社群分析
          </button>
          <button className={classNames("nav-item", activeView === "voice" && "active")} onClick={() => setActiveView("voice")}>
            <Inbox size={18} /> 用户之声
          </button>
          <button className={classNames("nav-item", activeView === "brands" && "active")} onClick={() => setActiveView("brands")}>
            <Building2 size={18} /> 品牌监控
          </button>
          <button className={classNames("nav-item", activeView === "channel-sales" && "active")} onClick={() => setActiveView("channel-sales")}>
            <ShoppingBag size={18} /> 渠道销售
          </button>
          <button className={classNames("nav-item", activeView === "web" && "active")} onClick={() => setActiveView("web")}>
            <Camera size={18} /> 网页监控
          </button>
          <button className={classNames("nav-item", MARKETING_VIEWS.includes(activeView) && "active")} onClick={() => setActiveView("marketing-media")}>
            <Newspaper size={18} /> 营销监控
          </button>
          <div className="nav-sub-stack">
            <button className={classNames("nav-item nav-sub-item", activeView === "marketing-media" && "active")} onClick={() => setActiveView("marketing-media")}>
              <Newspaper size={18} /> 媒体
            </button>
            <button className={classNames("nav-item nav-sub-item", activeView === "marketing-social" && "active")} onClick={() => setActiveView("marketing-social")}>
              <BarChart3 size={18} /> 社媒
            </button>
            <button className={classNames("nav-item nav-sub-item", activeView === "marketing-ads" && "active")} onClick={() => setActiveView("marketing-ads")}>
              <ShoppingBag size={18} /> 广告
            </button>
            <button className={classNames("nav-item nav-sub-item", activeView === "marketing-creators" && "active")} onClick={() => setActiveView("marketing-creators")}>
              <Users size={18} /> 红人
            </button>
          </div>
          <button className={classNames("nav-item", activeView === "sources" && "active")} onClick={() => setActiveView("sources")}>
            <PlugZap size={18} /> 数据源
          </button>
        </nav>

        <div className="sync-panel">
          <span>已接入</span>
          <strong>{readySources}/{sources.length || 1}</strong>
          <small>SQLite 本地库</small>
        </div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">Marketing intelligence</p>
            <h1>{VIEW_TITLES[activeView]}</h1>
          </div>
          <div className="topbar-actions">
            {activeView === "brands" && (
              <div className="topbar-menu-shell">
                <button className="icon-button" type="button" onClick={() => setBrandMenuOpen((current) => !current)} aria-label="新增品牌设置" title="新增">
                  <Plus size={18} />
                </button>
                {brandMenuOpen && (
                  <div className="web-menu-popover">
                    <button type="button" onClick={openBrandCreate}>
                      <Building2 size={16} /> 新增品牌档案
                    </button>
                  </div>
                )}
              </div>
            )}
            {activeView === "communities" && (
              <div className="topbar-menu-shell">
                <button className="icon-button" type="button" onClick={() => setCommunityMenuOpen((current) => !current)} aria-label="新增社群设置" title="新增">
                  <Plus size={18} />
                </button>
                {communityMenuOpen && (
                  <div className="web-menu-popover">
                    <button type="button" onClick={() => {
                      setCommunityMenuOpen(false);
                      setCommunitySettingsOpen(true);
                    }}>
                      <Users size={16} /> 管理社群来源
                    </button>
                    <button type="button" onClick={() => {
                      setCommunityMenuOpen(false);
                      setActiveView("brands");
                    }}>
                      <SlidersHorizontal size={16} /> 配置总管理
                    </button>
                  </div>
                )}
              </div>
            )}
            {activeView === "marketing-media" && (
              <div className="topbar-menu-shell">
                <button className="icon-button" type="button" onClick={() => setMediaMenuOpen((current) => !current)} aria-label="新增媒体设置" title="新增">
                  <Plus size={18} />
                </button>
                {mediaMenuOpen && (
                  <div className="web-menu-popover">
                    <button type="button" onClick={() => {
                      setMediaMenuOpen(false);
                      setEditingMediaMonitorId("");
                      setMediaCreateOpen(true);
                    }}>
                      <Newspaper size={16} /> 新增媒体监控
                    </button>
                    <button type="button" onClick={() => {
                      setMediaMenuOpen(false);
                      setActiveView("brands");
                    }}>
                      <SlidersHorizontal size={16} /> 配置总管理
                    </button>
                  </div>
                )}
              </div>
            )}
            {activeView === "channel-sales" && (
              <div className="topbar-menu-shell">
                <button className="icon-button" type="button" onClick={() => setSalesMenuOpen((current) => !current)} aria-label="新增渠道销售设置" title="新增">
                  <Plus size={18} />
                </button>
                {salesMenuOpen && (
                  <div className="web-menu-popover">
                    <button type="button" onClick={openNewSalesBrand}>
                      <Building2 size={16} /> 新增监控品牌
                    </button>
                    <button type="button" onClick={openNewSalesLink} disabled={!selectedSalesBrand}>
                      <LinkIcon size={16} /> 新增渠道链接
                    </button>
                  </div>
                )}
              </div>
            )}
            <button className="icon-button" onClick={loadData} aria-label="刷新">
              <RefreshCw size={18} />
            </button>
            <button className="primary-button" onClick={() => fileInputRef.current?.click()}>
              <FileUp size={18} /> 导入 CSV
            </button>
            <input ref={fileInputRef} className="hidden-input" type="file" accept=".csv,text/csv" onChange={handleCsv} />
          </div>
        </header>

        {error && <div className="error-strip">{error}</div>}
        {loading && <div className="loading-strip">Loading</div>}

        {activeView === "overview" && (
          <div className="view-grid">
            <section className="metric-row">
              <Metric label="记录总量" value={overview.total_records} icon={<Database size={18} />} tone="green" />
              <Metric label="负向信号" value={negativeCount} icon={<AlertTriangle size={18} />} tone="red" />
              <Metric label="品牌档案" value={overview.total_brands} icon={<Building2 size={18} />} tone="amber" />
              <Metric label="数据源" value={overview.total_sources} icon={<PlugZap size={18} />} tone="violet" />
            </section>

            <section className="panel wide-panel">
              <div className="panel-title">
                <h2>近 14 天入库</h2>
                <span>{overview.trend.reduce((sum, item) => sum + item.count, 0)} 条</span>
              </div>
              <div className="bar-chart">
                {overview.trend.map((item) => (
                  <div className="bar-column" key={item.date}>
                    <div className="bar-track">
                      <div className="bar-fill" style={{ height: `${Math.max(8, (item.count / maxTrend) * 100)}%` }} />
                    </div>
                    <span>{formatDate(item.date)}</span>
                  </div>
                ))}
              </div>
            </section>

            <section className="panel">
              <div className="panel-title">
                <h2>主题</h2>
                <span>{overview.top_topics.length}</span>
              </div>
              <div className="topic-cloud">
                {overview.top_topics.map((item) => (
                  <span className="topic-pill" key={item.topic}>
                    {topicLabel(item.topic)} <b>{item.count}</b>
                  </span>
                ))}
              </div>
            </section>

            <section className="panel">
              <div className="panel-title">
                <h2>高信号</h2>
                <span>{highSignal.length}</span>
              </div>
              <div className="signal-list">
                {(highSignal.length ? highSignal : overview.recent).slice(0, 5).map((record) => (
                  <RecordRow key={record.id} record={record} compact />
                ))}
              </div>
            </section>

          </div>
        )}

        {activeView === "communities" && (
          <div className="community-layout">
            <section className="metric-row community-metrics">
              <Metric label="监控品牌" value={selectedCommunityBrand ? 1 : communitySummary.total_brands} icon={<Building2 size={18} />} tone="green" />
              <Metric label="社群来源" value={scopedCommunitySources.length} icon={<Users size={18} />} tone="amber" />
              <Metric label="入库内容" value={selectedCommunityRecordCount} icon={<MessageSquareText size={18} />} tone="violet" />
              <Metric label="负向预警" value={communityNegative} icon={<AlertTriangle size={18} />} tone="red" />
            </section>

            <section className="panel community-scope-panel">
              <div className="panel-title">
                <h2>分析范围</h2>
                <span>{selectedCommunityTitle}</span>
              </div>
              <div className="community-brand-filter">
                <button
                  className={classNames("community-filter-chip", !selectedCommunityBrandId && "active")}
                  type="button"
                  onClick={() => {
                    setSelectedCommunityBrandId("");
                    setSelectedCommunityId("all");
                  }}
                >
                  全部品牌
                </button>
                {communityBrands.map((brand) => (
                  <button
                    className={classNames("community-filter-chip", selectedCommunityBrandId === brand.id && "active")}
                    type="button"
                    key={brand.id}
                    onClick={() => {
                      setSelectedCommunityBrandId(brand.id);
                      setSelectedCommunityId("all");
                    }}
                  >
                    {brand.name}
                  </button>
                ))}
              </div>
              <div className="community-selector-grid">
                <button
                  className={classNames("community-selector-card", selectedCommunityId === "all" && "active")}
                  type="button"
                  onClick={() => setSelectedCommunityId("all")}
                >
                  <span className="community-platform">All</span>
                  <strong>全部社群</strong>
                  <small>{selectedCommunityRecordCount} 条内容 · {communityAlerts.length ? `${communityAlerts.length} 个来源需跟进` : "汇总正常"}</small>
                  <b className={classNames("risk-badge", communityAlerts.length > 0 && "medium")}>{communityAlerts.length ? "需跟进" : "稳定"}</b>
                </button>
                {scopedCommunitySources.map((source) => (
                  <button
                    className={classNames("community-selector-card", selectedCommunityId === source.id && "active")}
                    type="button"
                    key={source.id}
                    onClick={() => setSelectedCommunityId(source.id)}
                  >
                    <PlatformLogo platform={source.platform} compact />
                    <strong>{source.name}</strong>
                    <small>{source.record_count} 条内容 · {communityCollectStatusLabel(source)}</small>
                    <b className={classNames(
                      "risk-badge",
                      communityRiskFromValues(source.record_count, source.negative_count, source.negative_rate) === "高关注" && "high",
                      communityRiskFromValues(source.record_count, source.negative_count, source.negative_rate) === "需跟进" && "medium"
                    )}>
                      {communityRiskFromValues(source.record_count, source.negative_count, source.negative_rate)}
                    </b>
                  </button>
                ))}
              </div>
            </section>

            <section className="panel community-focus-panel">
              <div className="panel-title">
                <h2>{selectedCommunityTitle}分析</h2>
                <span>{selectedCommunitySource ? communityCollectStatusLabel(selectedCommunitySource) : "全局汇总"}</span>
              </div>

              {selectedCommunitySource ? (
                <div className="community-profile">
                  <div className="community-profile-main">
                    <PlatformLogo platform={selectedCommunitySource.platform} />
                    <h3>{selectedCommunitySource.name}</h3>
                    <p>{selectedCommunitySource.notes || selectedCommunitySource.url}</p>
                  </div>
                  <div className="community-score-grid">
                    <div>
                      <span>内容量</span>
                      <strong>{selectedCommunitySource.record_count}</strong>
                    </div>
                    <div>
                      <span>负向比例</span>
                      <strong>{percentage(selectedCommunitySource.negative_rate)}</strong>
                    </div>
                    <div>
                      <span>风险</span>
                      <strong>{communityRiskFromValues(selectedCommunitySource.record_count, selectedCommunitySource.negative_count, selectedCommunitySource.negative_rate)}</strong>
                    </div>
                    <div>
                      <span>最近更新</span>
                      <strong>{selectedCommunitySource.last_collect_at ? formatDateTime(selectedCommunitySource.last_collect_at) : "未采集"}</strong>
                    </div>
                  </div>
                  <div className="analysis-note">
                    <BarChart3 size={18} />
                    <p>{communityAccessNote(selectedCommunitySource.platform)}</p>
                  </div>
                  <button className="primary-button" type="button" onClick={() => openCommunityRecordForm(selectedCommunitySource)}>
                    <Save size={18} /> 录入该社群内容
                  </button>
                </div>
              ) : (
                <div className="community-profile">
                  <div className="community-profile-main">
                    <span className="community-platform">All communities</span>
                    <h3>跨社群汇总</h3>
                    <p>按品牌聚合 Reddit、Discord、Facebook Group 和自建社群，统一分析声量、情绪、主题、意图和竞品提及。</p>
                  </div>
                  <div className="community-score-grid">
                    <div>
                      <span>内容量</span>
                      <strong>{selectedCommunityRecordCount}</strong>
                    </div>
                    <div>
                      <span>负向比例</span>
                      <strong>{percentage(communityNegativeRate)}</strong>
                    </div>
                    <div>
                      <span>需跟进</span>
                      <strong>{communityAlerts.length}</strong>
                    </div>
                    <div>
                      <span>来源数</span>
                      <strong>{scopedCommunitySources.length}</strong>
                    </div>
                  </div>
                  <button className="primary-button" type="button" onClick={() => openCommunityRecordForm()}>
                    <Save size={18} /> 录入社群内容
                  </button>
                </div>
              )}
            </section>

            <section className="panel community-summary-panel">
              <div className="panel-title">
                <h2>全局汇总数据分析</h2>
                <span>{selectedCommunityRecordCount} 条</span>
              </div>
              <div className="community-breakdown">
                {communityPlatformRows.length ? communityPlatformRows.map((item) => (
                  <article className="community-breakdown-row" key={`${item.platform}-${item.count}`}>
                    <div>
                      <strong>{item.platform_label}</strong>
                      <span>{item.count} 条 · 负向 {percentage(item.negative_rate)}</span>
                    </div>
                    <div className="community-bar-track">
                      <span style={{ width: `${Math.max(6, selectedCommunityRecordCount ? (item.count / selectedCommunityRecordCount) * 100 : 0)}%` }} />
                    </div>
                  </article>
                )) : (
                  <div className="empty-state compact-empty">
                    <BarChart3 size={26} />
                    <strong>等待分析数据</strong>
                    <span>添加社群来源并完成采集后，这里会显示平台分布和风险主题。</span>
                  </div>
                )}
              </div>

              <div className="topic-cloud community-topic-cloud">
                {communityTopTopics.length ? communityTopTopics.map((item) => (
                  <span className="topic-pill" key={item.topic}>
                    {topicLabel(item.topic)} <b>{item.count}</b>
                  </span>
                )) : (
                  <span className="topic-pill">等待社群内容入库</span>
                )}
              </div>
            </section>

            <section className="panel community-record-panel">
              <div className="panel-title">
                <h2>{selectedCommunityTitle}内容流</h2>
                <span>{selectedCommunityRecordListLabel}</span>
              </div>
              <div className="record-list">
                {selectedCommunityRecords.length ? selectedCommunityRecords.slice(0, 8).map((record) => (
                  <RecordRow key={record.id} record={record} />
                )) : (
                  <div className="empty-state compact-empty">
                    <MessageSquareText size={26} />
                    <strong>还没有社群内容</strong>
                    <span>添加来源后点击采集，或手动录入帖子、评论、聊天摘要。</span>
                  </div>
                )}
              </div>
            </section>

            <section className="panel community-plan-panel">
              <div className="panel-title">
                <h2>平台接入方式</h2>
                <span>真实采集优先</span>
              </div>
              <div className="community-plan-list">
                {COMMUNITY_PLATFORM_OPTIONS.map((platform) => (
                  <article className="community-plan-card" key={platform.value}>
                    <div>
                      <PlatformLogo platform={platform.value} compact />
                      <strong>{platform.label}</strong>
                    </div>
                    <p>{communityAccessNote(platform.value)}</p>
                    <small>{platform.value === "reddit" || platform.value === "owned" ? "已支持链接采集" : "已支持授权采集"}</small>
                  </article>
                ))}
              </div>
            </section>

            {communitySettingsOpen && (
              <div className="monitor-modal-backdrop" role="presentation" onMouseDown={(event) => {
                if (event.target === event.currentTarget) setCommunitySettingsOpen(false);
              }}>
                <section className="monitor-settings-modal community-settings-modal" role="dialog" aria-modal="true" aria-label="管理社群来源">
                  <div className="modal-title-row">
                    <div>
                      <h2>管理品牌与社群来源</h2>
                      <span>低频配置收在这里，主页面只保留分析。</span>
                    </div>
                    <button className="icon-button" type="button" onClick={() => setCommunitySettingsOpen(false)} aria-label="关闭">
                      <X size={18} />
                    </button>
                  </div>

                  <div className="community-settings-grid">
                    <section>
                      <div className="panel-title compact-title">
                        <h2>品牌</h2>
                        <span>{communityBrands.length} 个</span>
                      </div>
                      <form className="community-form" onSubmit={saveCommunityBrand}>
                        <label>
                          品牌名称
                          <input value={communityBrandForm.name} onChange={(event) => setCommunityBrandForm({ ...communityBrandForm, name: event.target.value })} placeholder="例如 Acme" />
                        </label>
                        <label>
                          分析范围
                          <textarea value={communityBrandForm.description} onChange={(event) => setCommunityBrandForm({ ...communityBrandForm, description: event.target.value })} placeholder="产品线、市场、竞品或社群边界" />
                        </label>
                        <div className="community-form-actions">
                          <button className="primary-button" type="submit" disabled={savingCommunityBrand || !communityBrandForm.name.trim()}>
                            <Save size={17} /> {editingCommunityBrandId ? "保存品牌" : "新增品牌"}
                          </button>
                          {editingCommunityBrandId && (
                            <button className="icon-button" type="button" onClick={() => {
                              setEditingCommunityBrandId("");
                              setCommunityBrandForm({ name: "", description: "" });
                            }} title="取消编辑">
                              <X size={17} />
                            </button>
                          )}
                        </div>
                      </form>

                      <div className="community-brand-list">
                        {communityBrands.map((brand) => (
                          <article className={classNames("community-brand-card", selectedCommunityBrand?.id === brand.id && "active")} key={brand.id}>
                            <button type="button" className="community-card-main" onClick={() => {
                              setSelectedCommunityBrandId(brand.id);
                              setSelectedCommunityId("all");
                              setCommunitySourceForm((current) => ({ ...current, brand_id: brand.id }));
                            }}>
                              <strong>{brand.name}</strong>
                              <span>{brand.source_count} 个来源 · {brand.record_count} 条内容 · 负向 {percentage(brand.negative_rate)}</span>
                            </button>
                            <div className="community-card-actions">
                              <button className="icon-button" type="button" onClick={() => editCommunityBrand(brand)} title="编辑品牌"><SquarePen size={16} /></button>
                              <button className="icon-button danger" type="button" onClick={() => deleteCommunityBrand(brand)} title="删除品牌"><Trash2 size={16} /></button>
                            </div>
                          </article>
                        ))}
                      </div>
                    </section>

                    <section>
                      <div className="panel-title compact-title">
                        <h2>社群来源</h2>
                        <span>{selectedCommunityBrand?.name || "全部品牌"}</span>
                      </div>
                      <form className="community-source-form" onSubmit={saveCommunitySource}>
                        <label>
                          所属品牌
                          <select value={communitySourceForm.brand_id || communitySourceBrandId} onChange={(event) => setCommunitySourceForm({ ...communitySourceForm, brand_id: event.target.value })}>
                            {!communityBrands.length && <option value="">先创建品牌</option>}
                            {communityBrands.map((brand) => <option key={brand.id} value={brand.id}>{brand.name}</option>)}
                          </select>
                        </label>
                        <label>
                          平台
                          <select value={communitySourceForm.platform} onChange={(event) => setCommunitySourceForm({ ...communitySourceForm, platform: event.target.value as CommunitySource["platform"] })}>
                            {COMMUNITY_PLATFORM_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                          </select>
                        </label>
                        {!communityBrands.length && (
                          <div className="community-inline-alert full-field" id="community-source-brand-help" role="status">
                            先在左侧创建品牌，再新增 Reddit、Discord、Facebook Group 等社群来源。
                          </div>
                        )}
                        <label>
                          社群名称
                          <input value={communitySourceForm.name} onChange={(event) => setCommunitySourceForm({ ...communitySourceForm, name: event.target.value })} placeholder="例如 r/OpenAI 或官方 Discord" />
                        </label>
                        <label className="full-field">
                          社群链接
                          <input value={communitySourceForm.url} onChange={(event) => setCommunitySourceForm({ ...communitySourceForm, url: event.target.value })} placeholder="https://www.reddit.com/r/..." />
                        </label>
                        <label className="full-field">
                          备注
                          <textarea value={communitySourceForm.notes} onChange={(event) => setCommunitySourceForm({ ...communitySourceForm, notes: event.target.value })} placeholder="频道、采样规则、授权信息或抓取限制" />
                        </label>
                        <div className="community-form-actions full-field">
                          <button
                            className="primary-button"
                            type="submit"
                            disabled={communitySourceSubmitDisabled}
                            title={communitySourceSubmitTitle}
                            aria-describedby={!communityBrands.length ? "community-source-brand-help" : undefined}
                          >
                            <Plus size={17} /> {communitySourceSubmitLabel}
                          </button>
                          {editingCommunitySourceId && (
                            <button className="icon-button" type="button" onClick={() => {
                              setEditingCommunitySourceId("");
                              setCommunitySourceForm({ brand_id: communitySourceBrandId, platform: "reddit", name: "", url: "", notes: "" });
                            }} title="取消编辑">
                              <X size={17} />
                            </button>
                          )}
                        </div>
                      </form>

                      <div className="community-source-list">
                        {scopedCommunitySources.length ? scopedCommunitySources.map((source) => (
                          <article className={classNames("community-source-card", selectedCommunityId === source.id && "active")} key={source.id}>
                            <div className="community-source-head">
                              <PlatformLogo platform={source.platform} />
                              <button type="button" onClick={() => setSelectedCommunityId(source.id)}>
                                <strong>{source.name}</strong>
                                <span>{source.platform_label} · {source.record_count} 条内容</span>
                              </button>
                              <b className={classNames("collect-status", source.last_status)}>{communityCollectStatusLabel(source)}</b>
                            </div>
                            <a className="community-source-link" href={source.url} target="_blank" rel="noreferrer">
                              <ExternalLink size={14} /> {source.url}
                            </a>
                            <div className="community-source-stats">
                              <span>负向 {percentage(source.negative_rate)}</span>
                              <span>最近 {source.last_collect_at ? formatDateTime(source.last_collect_at) : "未采集"}</span>
                            </div>
                            {source.last_error && <p className="community-source-error">{source.last_error}</p>}
                            <div className="community-source-actions">
                              <button className="primary-button" type="button" onClick={() => collectCommunitySource(source)} disabled={collectingCommunitySource === source.id}>
                                <RefreshCw size={16} /> {collectingCommunitySource === source.id ? "采集中" : "采集"}
                              </button>
                              <button className="icon-button" type="button" onClick={() => editCommunitySource(source)} title="编辑来源"><SquarePen size={16} /></button>
                              <button className="icon-button danger" type="button" onClick={() => deleteCommunitySource(source)} title="删除来源"><Trash2 size={16} /></button>
                            </div>
                          </article>
                        )) : (
                          <div className="empty-state compact-empty">
                            <Users size={26} />
                            <strong>还没有社群来源</strong>
                            <span>先创建品牌，再添加 Reddit、Discord、Facebook Group 或自建社群链接。</span>
                          </div>
                        )}
                      </div>
                    </section>
                  </div>
                </section>
              </div>
            )}
          </div>
        )}

        {activeView === "voice" && (
          <div className="voice-layout">
            <section className="metric-row voice-metrics">
              <Metric label="反馈总量" value={vocSummary.total_records} icon={<Inbox size={18} />} tone="green" />
              <Metric label="负向反馈" value={vocSummary.negative_records} icon={<AlertTriangle size={18} />} tone="red" />
              <Metric label="待处理" value={vocSummary.open_actions} icon={<Clock size={18} />} tone="amber" />
              <Metric label="已闭环" value={vocSummary.closed_actions} icon={<CheckCircle size={18} />} tone="violet" />
            </section>

            <section className="panel voice-control-panel">
              <div className="panel-title">
                <h2>数据汇总与维度分析</h2>
                <span>{voiceRangeLabel} · 负向 {percentage(vocSummary.negative_rate)}</span>
              </div>
              <div className="voice-filter-grid">
                <div className="search-box">
                  <Search size={18} />
                  <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索品牌、竞品、评论内容" />
                </div>
                <Select icon={<PlugZap size={16} />} value={sourceFilter} onChange={setSourceFilter} options={sources.map((source) => ({ value: source.id, label: source.name }))} placeholder="全部来源" />
                <Select icon={<Filter size={16} />} value={typeFilter} onChange={setTypeFilter} options={DATA_TYPES} placeholder="全部类型" />
                <Select icon={<ShoppingBag size={16} />} value={productFilter} onChange={setProductFilter} options={vocProductOptions} placeholder="全部产品" />
                <Select icon={<AlertTriangle size={16} />} value={sentimentFilter} onChange={setSentimentFilter} options={[
                  { value: "negative", label: "负向" },
                  { value: "neutral", label: "中性" },
                  { value: "positive", label: "正向" }
                ]} placeholder="全部情绪" />
                <div className="segment-group voice-range-group" aria-label="用户之声时间范围">
                  {[
                    { value: "7", label: "近 7 天" },
                    { value: "30", label: "近 30 天" },
                    { value: "90", label: "近 90 天" }
                  ].map((range) => (
                    <button key={range.value} className={classNames("segment-button", voiceRange === range.value && "active")} type="button" onClick={() => setVoiceRange(range.value)}>
                      {range.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="conclusion-list">
                {vocSummary.conclusions.map((item) => (
                  <article className={classNames("conclusion-row", item.tone)} key={item.title}>
                    <strong>{item.title}</strong>
                    <span>{item.detail}</span>
                  </article>
                ))}
              </div>
            </section>

            <section className="panel voice-dimension-panel">
              <div className="panel-title">
                <h2>时间、渠道、产品维度</h2>
                <span>{vocSummary.channels.length} 个渠道 · {vocSummary.products.length} 条产品线</span>
              </div>
              <div className="voice-trend">
                {vocSummary.trend.map((item) => (
                  <div className="voice-trend-column" key={item.date}>
                    <div className="voice-trend-track">
                      <span className="voice-trend-fill" style={{ height: `${Math.max(7, (item.count / maxVocTrend) * 100)}%` }} />
                      <span className="voice-trend-negative" style={{ height: `${Math.max(0, (item.negative / maxVocTrend) * 100)}%` }} />
                    </div>
                    <small>{formatDate(item.date)}</small>
                  </div>
                ))}
              </div>
              <div className="dimension-grid">
                <div className="dimension-block">
                  <h3>渠道对比</h3>
                  <div className="dimension-list">
                    {vocSummary.channels.slice(0, 6).map((channel) => (
                      <article className="dimension-row" key={channel.source_id}>
                        <div>
                          <strong>{channel.name}</strong>
                          <span>{channel.count} 条 · 负向 {percentage(channel.negative_rate)}</span>
                        </div>
                        <div className="dimension-meter">
                          <span style={{ width: `${Math.max(6, vocSummary.total_records ? (channel.count / vocSummary.total_records) * 100 : 0)}%` }} />
                        </div>
                      </article>
                    ))}
                  </div>
                </div>
                <div className="dimension-block">
                  <h3>产品线问题</h3>
                  <div className="dimension-list">
                    {vocSummary.products.slice(0, 6).map((product) => (
                      <article className="dimension-row" key={product.product}>
                        <div>
                          <strong>{product.product}</strong>
                          <span>{product.count} 条 · 负向 {product.negative}</span>
                        </div>
                        <div className="topic-cloud compact-topic-cloud">
                          {product.top_topics.slice(0, 3).map((topic) => <span className="topic-pill" key={`${product.product}-${topic.topic}`}>{topicLabel(topic.topic)} <b>{topic.count}</b></span>)}
                        </div>
                      </article>
                    ))}
                  </div>
                </div>
              </div>
            </section>

            <section className="panel voice-monitor-panel">
              <div className="panel-title">
                <h2>监控与预警</h2>
                <span>{vocSummary.alerts.length ? `${vocSummary.alerts.length} 个异常` : "实时监测中"}</span>
              </div>
              <div className="alert-list">
                {vocSummary.alerts.length ? vocSummary.alerts.map((alert) => (
                  <article className={classNames("alert-row", alert.level)} key={alert.id}>
                    <div>
                      <strong>{alert.title}</strong>
                      <span>{alert.count} 条 · 较上期 {formatChangeRate(alert.change_rate)} · {ownerTeamLabel(alert.owner_team)}</span>
                    </div>
                    <p>{alert.description}</p>
                    <button className="ghost-button" type="button" onClick={() => createActionFromAlert(alert)} disabled={savingActionId === alert.id}>
                      <SquarePen size={16} /> {savingActionId === alert.id ? "创建中" : "生成处理项"}
                    </button>
                  </article>
                )) : (
                  <div className="empty-state compact-empty">
                    <CheckCircle size={26} />
                    <strong>暂无异常波动</strong>
                    <span>当前筛选范围内没有明显负向聚集或声量突增。</span>
                  </div>
                )}
              </div>
            </section>

            <section className="panel voice-record-panel">
              <div className="panel-title">
                <h2>反馈内容流</h2>
                <span>{records.length}</span>
              </div>
              <div className="record-list">
                {records.length ? records.map((record) => (
                  <RecordRow key={record.id} record={record} onCreateAction={createActionFromRecord} />
                )) : (
                  <div className="empty-state compact-empty">
                    <Inbox size={26} />
                    <strong>暂无反馈</strong>
                    <span>调整筛选条件，或通过 CSV/手动录入补充用户声音。</span>
                  </div>
                )}
              </div>
            </section>

            <div className="voice-side-stack">
              <section className="panel voice-action-panel">
                <div className="panel-title">
                  <h2>协同处理与闭环</h2>
                  <span>闭环率 {percentage(vocSummary.closure_rate)}</span>
                </div>
                <div className="voc-action-list">
                  {activeVocActions.length ? activeVocActions.slice(0, 8).map((action) => (
                    <article className={classNames("voc-action-card", `priority-${action.priority}`)} key={action.id}>
                      <div className="voc-action-head">
                        <div>
                          <strong>{action.title}</strong>
                          <span>{ownerTeamLabel(action.owner_team)} · {priorityLabel(action.priority)}优先级 · {action.due_at || "未设截止"}</span>
                        </div>
                        <b className={classNames("action-status", action.status)}>{actionStatusLabel(action.status)}</b>
                      </div>
                      {action.description && <p>{action.description}</p>}
                      {action.record?.body && <small>原声：{action.record.body.slice(0, 92)}</small>}
                      <div className="voc-action-buttons">
                        {action.status !== "closed" && (
                          <button type="button" onClick={() => updateVocActionStatus(action, nextActionStatus(action.status))} disabled={savingActionId === action.id}>
                            <CheckCircle size={15} /> {action.status === "resolved" ? "确认闭环" : "推进"}
                          </button>
                        )}
                        <button type="button" className="danger-action" onClick={() => deleteVocAction(action)} disabled={savingActionId === action.id}>
                          <Trash2 size={15} /> 删除
                        </button>
                      </div>
                    </article>
                  )) : (
                    <div className="empty-state compact-empty">
                      <Clock size={26} />
                      <strong>暂无待处理项</strong>
                      <span>可从预警或原始反馈生成处理项并分派团队。</span>
                    </div>
                  )}
                </div>
              </section>

            <section className="panel input-panel voice-input-panel">
              <div className="panel-title">
                <h2>新增记录</h2>
                <span>{form.source_id}</span>
              </div>
              <form onSubmit={submitRecord} className="record-form">
                <label>
                  来源
                  <select value={form.source_id} onChange={(event) => setForm({ ...form, source_id: event.target.value })}>
                    {sources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}
                  </select>
                </label>
                <label>
                  类型
                  <select value={form.data_type} onChange={(event) => setForm({ ...form, data_type: event.target.value })}>
                    {DATA_TYPES.map((type) => <option key={type.value} value={type.value}>{type.label}</option>)}
                  </select>
                </label>
                <label>
                  平台
                  <input value={form.platform} onChange={(event) => setForm({ ...form, platform: event.target.value })} />
                </label>
                <label>
                  品牌
                  <input value={form.brand} onChange={(event) => setForm({ ...form, brand: event.target.value })} />
                </label>
                <label>
                  竞品
                  <input value={form.competitor} onChange={(event) => setForm({ ...form, competitor: event.target.value })} />
                </label>
                <label>
                  产品
                  <input value={form.product} onChange={(event) => setForm({ ...form, product: event.target.value })} />
                </label>
                <label className="full-field">
                  标题
                  <input value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} />
                </label>
                <label className="full-field">
                  内容
                  <textarea value={form.body} onChange={(event) => setForm({ ...form, body: event.target.value })} />
                </label>
                <button className="primary-button full-field" type="submit">
                  <Save size={18} /> 入库
                </button>
              </form>
            </section>
            </div>
          </div>
        )}

        {activeView === "brands" && (
          <div className="brand-layout">
            <section className="panel brand-list-panel">
              <div className="panel-title">
                <h2>已建档品牌</h2>
                <span>{brands.length}</span>
              </div>
              <div className="brand-list">
                {brands.length ? brands.map((brand) => (
                  <article className="brand-card" key={brand.id}>
                    <div className="brand-card-main">
                      <div className="brand-avatar">
                        {brand.logo_url ? <img src={brand.logo_url} alt="" /> : <Building2 size={20} />}
                      </div>
                      <div>
                        <strong>{brand.name}</strong>
                        <span>{brand.source_kind} · {brand.marketplace || brand.category || "global"}</span>
                      </div>
                    </div>
                    <p>{brand.description || brand.source_url}</p>
                    <div className="record-tags">
                      {brand.asin && <span>ASIN {brand.asin}</span>}
                      {brand.monitoring_keywords.slice(0, 5).map((keyword) => <span key={keyword}>{keyword}</span>)}
                      {Object.keys(brand.social_links || {}).map((platform) => <span key={platform}>{platform}</span>)}
                    </div>
                    <div className="brand-actions">
                      {(brand.official_website || brand.amazon_url) && (
                        <a href={brand.official_website || brand.amazon_url} target="_blank" rel="noreferrer">
                          <ExternalLink size={16} /> 打开
                        </a>
                      )}
                      <button type="button" onClick={() => editBrand(brand)}>
                        <SquarePen size={16} /> 编辑
                      </button>
                      <button type="button" className="danger-action" onClick={() => deleteBrand(brand)}>
                        <Trash2 size={16} /> 删除
                      </button>
                    </div>
                  </article>
                )) : (
                  <div className="empty-state compact-empty">
                    <Building2 size={26} />
                    <strong>还没有品牌档案</strong>
                    <span>先用一个官网或 Amazon 链接试一下。</span>
                  </div>
                )}
              </div>
            </section>

            <section className="panel brand-config-panel">
              <div className="panel-title">
                <h2>配置总管理</h2>
                <span>{selectedBrandConfig?.name || "选择品牌"}</span>
              </div>
              {brands.length ? (
                <>
                  <div className="brand-config-selector" aria-label="品牌配置选择">
                    {brands.map((brand) => (
                      <button
                        className={classNames("brand-config-chip", selectedBrandConfig?.id === brand.id && "active")}
                        type="button"
                        key={brand.id}
                        onClick={() => setSelectedBrandConfigId(brand.id)}
                      >
                        {brand.logo_url ? <img src={brand.logo_url} alt="" /> : <Building2 size={15} />}
                        <span>{brand.name}</span>
                      </button>
                    ))}
                  </div>

                  {selectedBrandConfig && (
                    <div className="brand-config-grid">
                      <article className="brand-config-block">
                        <div className="brand-config-head">
                          <div>
                            <strong>社群</strong>
                            <span>{brandConfigTotals.community} 个来源</span>
                          </div>
                          <button type="button" onClick={() => openBrandCommunityConfig(selectedBrandConfig)}>
                            <Plus size={15} /> 配置
                          </button>
                        </div>
                        <div className="brand-config-list">
                          {brandConfigCommunity?.sources.length ? brandConfigCommunity.sources.map((source) => (
                            <div className="brand-config-row" key={source.id}>
                              <span>{source.platform_label}</span>
                              <b>{source.name}</b>
                              <small>{source.record_count} 条 · {communityCollectStatusLabel(source)}</small>
                              <div>
                                <button type="button" onClick={() => {
                                  setSelectedCommunityBrandId(brandConfigCommunity.id);
                                  editCommunitySource(source);
                                  setCommunitySettingsOpen(true);
                                }}>编辑</button>
                                <button type="button" onClick={() => deleteCommunitySource(source)}>删除</button>
                              </div>
                            </div>
                          )) : <small className="brand-config-empty">还没有社群来源</small>}
                        </div>
                      </article>

                      <article className="brand-config-block">
                        <div className="brand-config-head">
                          <div>
                            <strong>渠道销售</strong>
                            <span>{brandConfigTotals.sales} 个链接</span>
                          </div>
                          <button type="button" onClick={() => openBrandSalesConfig(selectedBrandConfig)}>
                            <Plus size={15} /> 配置
                          </button>
                        </div>
                        <div className="brand-config-list">
                          {brandConfigSales?.links.length ? brandConfigSales.links.map((link) => (
                            <div className="brand-config-row" key={link.id}>
                              <span>{link.platform_label}</span>
                              <b>{link.name}</b>
                              <small>{link.region} · {link.status === "active" ? "启用" : "暂停"}</small>
                              <div>
                                <button type="button" onClick={() => editSalesLink(link)}>编辑</button>
                                <button type="button" onClick={() => deleteSalesLink(link)}>删除</button>
                              </div>
                            </div>
                          )) : <small className="brand-config-empty">还没有渠道链接</small>}
                        </div>
                      </article>

                      <article className="brand-config-block">
                        <div className="brand-config-head">
                          <div>
                            <strong>网页监控</strong>
                            <span>{brandConfigTotals.web} 个页面</span>
                          </div>
                          <button type="button" onClick={() => openBrandWebConfig(selectedBrandConfig)}>
                            <Plus size={15} /> 配置
                          </button>
                        </div>
                        <div className="brand-config-list">
                          {brandConfigWeb.length ? brandConfigWeb.map((monitor) => (
                            <div className="brand-config-row" key={monitor.id}>
                              <span>{monitor.scope === "domain" ? "主域名" : "单页"}</span>
                              <b>{monitor.name}</b>
                              <small>{monitor.snapshots} 张快照 · {monitor.status === "active" ? "启用" : "暂停"}</small>
                              <div>
                                <button type="button" onClick={() => openBrandWebConfig(selectedBrandConfig, monitor)}>编辑</button>
                                <button type="button" onClick={() => deleteMonitor(monitor)}>删除</button>
                              </div>
                            </div>
                          )) : <small className="brand-config-empty">还没有网页监控</small>}
                        </div>
                      </article>

                      <article className="brand-config-block">
                        <div className="brand-config-head">
                          <div>
                            <strong>营销监控</strong>
                            <span>{brandConfigTotals.marketing} 个配置</span>
                          </div>
                          <div className="brand-config-actions">
                            <button type="button" onClick={() => openBrandMediaConfig(selectedBrandConfig)}>媒体</button>
                            <button type="button" onClick={() => openBrandMarketingConfig(selectedBrandConfig, "social")}>社媒</button>
                            <button type="button" onClick={() => openBrandMarketingConfig(selectedBrandConfig, "ads")}>广告</button>
                            <button type="button" onClick={() => openBrandMarketingConfig(selectedBrandConfig, "creator")}>红人</button>
                          </div>
                        </div>
                        <div className="brand-config-list">
                          {brandConfigMedia.map((monitor) => (
                            <div className="brand-config-row" key={monitor.id}>
                              <span>媒体</span>
                              <b>{monitor.query}</b>
                              <small>{monitor.mentions} 篇 · {monitor.status === "active" ? "启用" : "暂停"}</small>
                              <div>
                                <button type="button" onClick={() => openBrandMediaConfig(selectedBrandConfig, monitor)}>编辑</button>
                                <button type="button" onClick={() => deleteMediaMonitor(monitor)}>删除</button>
                              </div>
                            </div>
                          ))}
                          {brandConfigMarketing.map((link) => (
                            <div className="brand-config-row" key={link.id}>
                              <span>{marketingTypeLabel(link.monitor_type)}</span>
                              <b>{link.name || link.platform_label}</b>
                              <small>{link.platform_label} · {link.status === "active" ? "启用" : "暂停"}</small>
                              <div>
                                <button type="button" onClick={() => openBrandMarketingConfig(selectedBrandConfig, link.monitor_type, link)}>编辑</button>
                                <button type="button" onClick={() => deleteMarketingLink(link)}>删除</button>
                              </div>
                            </div>
                          ))}
                          {!brandConfigMedia.length && !brandConfigMarketing.length && <small className="brand-config-empty">还没有营销配置</small>}
                        </div>
                      </article>
                    </div>
                  )}
                </>
              ) : (
                <div className="empty-state compact-empty">
                  <Building2 size={26} />
                  <strong>先创建品牌档案</strong>
                  <span>保存品牌后，这里会统一管理社群、渠道销售、网页和营销配置。</span>
                </div>
              )}
            </section>

            {brandCreateOpen && (
              <div className="monitor-modal-backdrop" role="presentation" onMouseDown={(event) => {
                if (event.target === event.currentTarget) closeBrandCreate();
              }}>
                <section className="monitor-settings-modal brand-profile-modal" role="dialog" aria-modal="true" aria-label={brandDraft?.id ? "编辑品牌档案" : "新增品牌档案"}>
                  <div className="modal-title-row">
                    <div>
                      <h2>{brandDraft?.id ? "编辑品牌档案" : "新增品牌档案"}</h2>
                      <span>{brandDraft ? "确认后入库" : "URL first"}</span>
                    </div>
                    <button className="icon-button" type="button" onClick={closeBrandCreate} aria-label="关闭">
                      <X size={18} />
                    </button>
                  </div>

                  {!brandDraft && (
                    <form className="brand-url-form modal-monitor-form" onSubmit={analyzeBrand}>
                      <label>
                        官网或 Amazon 链接
                        <div className="url-input-row">
                          <LinkIcon size={18} />
                          <input
                            value={brandUrl}
                            onChange={(event) => setBrandUrl(event.target.value)}
                            placeholder="https://brand.com 或 https://www.amazon.com/dp/ASIN"
                          />
                        </div>
                      </label>
                      <button className="primary-button" type="submit" disabled={analyzingBrand}>
                        <WandSparkles size={18} /> {analyzingBrand ? "分析中" : "自动分析"}
                      </button>
                    </form>
                  )}

                  {brandDraft ? (
                    <div className="brand-review-grid">
                      {brandDraft.logo_url && (
                        <div className="brand-logo-preview">
                          <img src={brandDraft.logo_url} alt="" />
                        </div>
                      )}
                      <label>
                        品牌名
                        <input value={brandDraft.name || ""} onChange={(event) => updateBrandDraft("name", event.target.value)} />
                      </label>
                      <label>
                        来源类型
                        <select value={brandDraft.source_kind || "website"} onChange={(event) => updateBrandDraft("source_kind", event.target.value)}>
                          <option value="website">官网</option>
                          <option value="amazon">Amazon</option>
                        </select>
                      </label>
                      <label>
                        官网
                        <input value={brandDraft.official_website || ""} onChange={(event) => updateBrandDraft("official_website", event.target.value)} />
                      </label>
                      <label>
                        Amazon 链接
                        <input value={brandDraft.amazon_url || ""} onChange={(event) => updateBrandDraft("amazon_url", event.target.value)} />
                      </label>
                      <label>
                        市场
                        <input value={brandDraft.marketplace || ""} onChange={(event) => updateBrandDraft("marketplace", event.target.value)} />
                      </label>
                      <label>
                        ASIN
                        <input value={brandDraft.asin || ""} onChange={(event) => updateBrandDraft("asin", event.target.value)} />
                      </label>
                      <label className="full-field">
                        描述
                        <textarea value={brandDraft.description || ""} onChange={(event) => updateBrandDraft("description", event.target.value)} />
                      </label>
                      <label className="full-field">
                        监控关键词
                        <textarea
                          value={(brandDraft.monitoring_keywords || []).join("\n")}
                          onChange={(event) => updateBrandDraft("monitoring_keywords", event.target.value.split("\n").map((item) => item.trim()).filter(Boolean))}
                        />
                      </label>
                      <label className="full-field">
                        社媒链接
                        <textarea
                          value={linksToText(brandDraft.social_links)}
                          onChange={(event) => updateBrandDraft("social_links", textToLinks(event.target.value))}
                          placeholder="instagram=https://instagram.com/brand&#10;tiktok=https://tiktok.com/@brand"
                        />
                      </label>
                      <label className="full-field">
                        电商链接
                        <textarea
                          value={linksToText(brandDraft.ecommerce_links)}
                          onChange={(event) => updateBrandDraft("ecommerce_links", textToLinks(event.target.value))}
                          placeholder="amazon=https://www.amazon.com/dp/ASIN"
                        />
                      </label>
                      {!!brandDraft.duplicate_candidates?.length && (
                        <div className="duplicate-panel full-field">
                          <strong>可能已存在</strong>
                          {brandDraft.duplicate_candidates.map((candidate) => (
                            <div className="duplicate-row" key={candidate.id}>
                              <div>
                                <b>{candidate.name}</b>
                                <span>{candidate.reasons.join(", ")}</span>
                              </div>
                              <button type="button" className="ghost-button" onClick={() => {
                                mergeIntoExistingBrand(candidate.id);
                              }}>
                                <SquarePen size={16} /> 编辑已有
                              </button>
                            </div>
                          ))}
                        </div>
                      )}
                      <div className="evidence-list full-field">
                        {(brandDraft.evidence || []).map((item) => (
                          <span key={item}><CheckCircle size={14} /> {item}</span>
                        ))}
                      </div>
                      <div className="form-actions full-field">
                        <button className="secondary-button" type="button" onClick={closeBrandCreate}>
                          取消
                        </button>
                        <button className="primary-button" type="button" onClick={saveBrand} disabled={savingBrand}>
                          <Save size={18} /> {savingBrand ? "保存中" : brandDraft.id ? "保存修改" : "保存品牌档案"}
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="empty-state compact-empty brand-create-empty">
                      <Globe size={28} />
                      <strong>输入链接后生成可编辑草稿</strong>
                      <span>保存前会提示疑似重复档案。</span>
                    </div>
                  )}
                </section>
              </div>
            )}

            {mediaCreateOpen && activeView === "brands" && (
              <div className="monitor-modal-backdrop" role="presentation" onMouseDown={(event) => {
                if (event.target === event.currentTarget) {
                  setMediaCreateOpen(false);
                  setEditingMediaMonitorId("");
                }
              }}>
                <section className="monitor-settings-modal" role="dialog" aria-modal="true" aria-label="媒体监控配置">
                  <div className="modal-title-row">
                    <div>
                      <h2>{editingMediaMonitorId ? "编辑媒体监控" : "新增媒体监控"}</h2>
                      <span>{mediaForm.brand_name || selectedBrandConfig?.name || "品牌媒体"}</span>
                    </div>
                    <button className="icon-button" type="button" onClick={() => {
                      setMediaCreateOpen(false);
                      setEditingMediaMonitorId("");
                    }} aria-label="关闭">
                      <X size={18} />
                    </button>
                  </div>
                  <form className="media-monitor-form modal-monitor-form" onSubmit={createMediaMonitor}>
                    <label>
                      品牌
                      <input value={mediaForm.brand_name} onChange={(event) => setMediaForm({ ...mediaForm, brand_name: event.target.value })} placeholder="Brand name" />
                    </label>
                    <label>
                      市场
                      <input value={mediaForm.region} onChange={(event) => setMediaForm({ ...mediaForm, region: event.target.value.toUpperCase() })} />
                    </label>
                    <label className="full-field">
                      检索式
                      <div className="url-input-row">
                        <Search size={18} />
                        <input value={mediaForm.query} onChange={(event) => setMediaForm({ ...mediaForm, query: event.target.value })} placeholder="&quot;Brand&quot; OR &quot;Product&quot;" />
                      </div>
                    </label>
                    <label>
                      语言
                      <input value={mediaForm.language} onChange={(event) => setMediaForm({ ...mediaForm, language: event.target.value })} />
                    </label>
                    <div className="form-actions full-field">
                      <button className="secondary-button" type="button" onClick={() => {
                        setMediaCreateOpen(false);
                        setEditingMediaMonitorId("");
                      }}>取消</button>
                      <button className="primary-button" type="submit" disabled={savingMediaMonitor}>
                        <Newspaper size={18} /> {savingMediaMonitor ? "保存中" : editingMediaMonitorId ? "保存修改" : "新增并扫描"}
                      </button>
                    </div>
                  </form>
                </section>
              </div>
            )}

            {brandMarketingSettingsOpen && selectedBrandConfig && (
              <div className="monitor-modal-backdrop" role="presentation" onMouseDown={(event) => {
                if (event.target === event.currentTarget) setBrandMarketingSettingsOpen(false);
              }}>
                <section className="monitor-settings-modal" role="dialog" aria-modal="true" aria-label="营销链接配置">
                  <div className="modal-title-row">
                    <div>
                      <h2>{marketingEditingLinkId ? `编辑${marketingTypeLabel(marketingLinkForm.monitor_type)}配置` : `新增${marketingTypeLabel(marketingLinkForm.monitor_type)}配置`}</h2>
                      <span>{selectedBrandConfig.name}</span>
                    </div>
                    <button className="icon-button" type="button" onClick={() => setBrandMarketingSettingsOpen(false)} aria-label="关闭">
                      <X size={18} />
                    </button>
                  </div>
                  <form className="marketing-link-form modal-monitor-form" onSubmit={saveMarketingLink}>
                    <label>
                      类型
                      <select value={marketingLinkForm.monitor_type} onChange={(event) => setMarketingLinkForm({ ...marketingLinkForm, monitor_type: event.target.value as MarketingMonitorType })}>
                        <option value="social">社媒</option>
                        <option value="ads">广告</option>
                        <option value="creator">红人</option>
                      </select>
                    </label>
                    <label>
                      平台
                      <select value={marketingLinkForm.platform} onChange={(event) => setMarketingLinkForm({ ...marketingLinkForm, platform: event.target.value })}>
                        <option value="">按链接识别</option>
                        <option value="youtube">YouTube</option>
                        <option value="tiktok">TikTok</option>
                        <option value="instagram">Instagram</option>
                        <option value="x">X / Twitter</option>
                        <option value="facebook">Facebook</option>
                        <option value="website">Website</option>
                      </select>
                    </label>
                    <label>
                      名称
                      <input value={marketingLinkForm.name} onChange={(event) => setMarketingLinkForm({ ...marketingLinkForm, name: event.target.value })} placeholder="账号、帖子、广告库或红人名称" />
                    </label>
                    <label>
                      状态
                      <select value={marketingLinkForm.status} onChange={(event) => setMarketingLinkForm({ ...marketingLinkForm, status: event.target.value as "active" | "paused" })}>
                        <option value="active">启用</option>
                        <option value="paused">暂停</option>
                      </select>
                    </label>
                    <label className="full-field">
                      链接
                      <div className="url-input-row">
                        <LinkIcon size={18} />
                        <input value={marketingLinkForm.url} onChange={(event) => setMarketingLinkForm({ ...marketingLinkForm, url: event.target.value })} placeholder="https://..." />
                      </div>
                    </label>
                    <div className="form-actions full-field">
                      <button className="secondary-button" type="button" onClick={() => setBrandMarketingSettingsOpen(false)}>取消</button>
                      <button className="primary-button" type="submit" disabled={!!marketingSavingLink || !marketingLinkForm.url.trim()}>
                        <Save size={18} /> {marketingSavingLink ? "保存中" : marketingEditingLinkId ? "保存修改" : "保存配置"}
                      </button>
                    </div>
                  </form>
                </section>
              </div>
            )}
          </div>
        )}

        {activeView === "marketing-media" && (
          <div className="media-layout">
            <section className="metric-row media-metrics">
              <Metric label="监控品牌" value={mediaMonitors.length} icon={<Building2 size={18} />} tone="green" />
              <Metric label={mediaRangeLabel} value={mediaSummary.total_mentions} icon={<Newspaper size={18} />} tone="amber" />
              <Metric label="预估曝光" value={formatCompactNumber(mediaSummary.estimated_reach)} icon={<TrendingUp size={18} />} tone="violet" />
              <Metric label="疑似付费 PR" value={mediaSummary.paid_mentions} icon={<AlertTriangle size={18} />} tone="red" />
            </section>

            <section className="panel media-control-panel">
              <div className="panel-title">
                <h2>媒体监控对象</h2>
                <span>Google News RSS · 真实扫描</span>
              </div>

              <div className="media-source-note">
                <div>
                  <strong>监控逻辑</strong>
                  <span>每天自动用品牌检索式拉取公开新闻 RSS；“立即扫描”会马上刷新一次。</span>
                </div>
                <div>
                  <strong>数据口径</strong>
                  <span>报道数来自真实扫描结果；曝光为按媒体域名估算的参考值，不是广告后台真实曝光。</span>
                </div>
              </div>

              <div className="web-filter-row">
                <div className="segment-group" aria-label="媒体报道时间范围">
                  {[
                    { value: "7", label: "近 7 天" },
                    { value: "30", label: "近 30 天" },
                    { value: "90", label: "近 90 天" },
                    { value: "180", label: "近半年" }
                  ].map((range) => (
                    <button key={range.value} className={classNames("segment-button", mediaRange === range.value && "active")} type="button" onClick={() => setMediaRange(range.value)}>
                      {range.label}
                    </button>
                  ))}
                </div>
                <Select
                  icon={<Filter size={16} />}
                  value={selectedMediaMonitorId}
                  onChange={setSelectedMediaMonitorId}
                  options={mediaMonitors.map((monitor) => ({ value: monitor.id, label: monitor.brand_name }))}
                  placeholder="全部品牌"
                />
              </div>

              <div className="monitor-list">
                {mediaMonitors.length ? mediaMonitors.map((monitor) => {
                  const status = mediaMonitorStatus(monitor);
                  return (
                    <article className={classNames("monitor-row", selectedMediaMonitorId === monitor.id && "selected")} key={monitor.id}>
                      <button className="monitor-main" type="button" onClick={() => setSelectedMediaMonitorId(monitor.id)}>
                        <span className={classNames("monitor-dot", monitor.last_status)} />
                        <span>
                          <strong>{monitor.brand_name}</strong>
                          <small>{monitor.query}</small>
                        </span>
                      </button>
                      <div className="monitor-meta">
                        <b className={classNames("media-status-badge", status.tone)}>{status.label}</b>
                        <span><Clock size={14} /> 上次扫描 {formatDateTime(monitor.last_scan_at)}</span>
                        <span>{monitor.mentions} 篇 · {formatCompactNumber(monitor.estimated_reach)} 曝光</span>
                      </div>
                      {monitor.last_error && <p className="monitor-error">{monitor.last_error}</p>}
                      <div className="monitor-actions">
                        <button type="button" onClick={() => scanMediaMonitor(monitor.id)} disabled={scanningMediaMonitor === monitor.id}>
                          <RefreshCw size={16} /> {scanningMediaMonitor === monitor.id ? "扫描中" : "立即扫描"}
                        </button>
                        <button type="button" onClick={() => toggleMediaMonitor(monitor)}>
                          {monitor.status === "active" ? <PauseCircle size={16} /> : <Play size={16} />} {monitor.status === "active" ? "暂停" : "启用"}
                        </button>
                        <button type="button" className="danger-action" onClick={() => deleteMediaMonitor(monitor)}>
                          <Trash2 size={16} /> 删除
                        </button>
                      </div>
                    </article>
                  );
                }) : (
                  <div className="empty-state compact-empty">
                    <Newspaper size={26} />
                    <strong>暂无媒体监控</strong>
                    <span>点击右上角加号新增品牌，系统会拉取公开新闻报道并入库。</span>
                  </div>
                )}
              </div>
            </section>

            <section className="panel media-analysis-panel">
              <div className="panel-title">
                <h2>{selectedMediaMonitor ? selectedMediaMonitor.brand_name : "媒体分析"}</h2>
                <span>{mediaRangeLabel}</span>
              </div>

              <div className="media-chart">
                {mediaSummary.daily.slice(-14).map((item) => (
                  <div className="bar-column" key={item.date}>
                    <div className="bar-track">
                      <div className="bar-fill" style={{ height: `${Math.max(8, (item.mentions / maxMediaDaily) * 100)}%` }} />
                    </div>
                    <span>{formatDate(item.date)}</span>
                  </div>
                ))}
              </div>

              <div className="media-insight-grid">
                <article>
                  <span>内容方向</span>
                  <div className="topic-cloud">
                    {mediaSummary.pr_directions.length ? mediaSummary.pr_directions.map((item) => (
                      <span className="topic-pill" key={item.theme}>{prThemeLabel(item.theme)} <b>{item.count}</b></span>
                    )) : <span className="topic-pill">等待报道入库</span>}
                  </div>
                </article>
                <article>
                  <span>媒体体量</span>
                  <div className="publication-list">
                    {mediaSummary.top_publications.length ? mediaSummary.top_publications.slice(0, 5).map((item) => (
                      <div key={item.publication}>
                        <strong>{item.publication}</strong>
                        <small>{item.mentions} 篇 · {formatCompactNumber(item.estimated_reach)}</small>
                      </div>
                    )) : <small>暂无媒体来源</small>}
                  </div>
                </article>
                <article>
                  <span>属性识别</span>
                  <div className="coverage-bars">
                    <div>
                      <b>{coverageLabel("earned")}</b>
                      <span>{percentage(earnedMediaRate)}</span>
                    </div>
                    <div className="coverage-track"><i style={{ width: `${Math.max(4, earnedMediaRate * 100)}%` }} /></div>
                    <div>
                      <b>{coverageLabel("paid_pr")}</b>
                      <span>{percentage(paidMediaRate)}</span>
                    </div>
                    <div className="coverage-track paid"><i style={{ width: `${Math.max(4, paidMediaRate * 100)}%` }} /></div>
                  </div>
                </article>
                <article>
                  <span>声量占比</span>
                  <div className="publication-list">
                    {mediaSummary.share_of_voice.length ? mediaSummary.share_of_voice.slice(0, 5).map((item) => (
                      <div key={item.monitor_id}>
                        <strong>{item.brand_name}</strong>
                        <small>{percentage(item.share)} · {item.mentions} 篇</small>
                      </div>
                    )) : <small>暂无可比品牌</small>}
                  </div>
                </article>
              </div>
            </section>

            <section className="panel media-mentions-panel">
              <div className="panel-title">
                <h2>媒体报道</h2>
                <span>{mediaMentions.length}</span>
              </div>
              <div className="media-mention-list">
                {mediaMentions.length ? mediaMentions.map((mention) => (
                  <MediaMentionRow mention={mention} key={mention.id} />
                )) : (
                  <div className="empty-state compact-empty">
                    <Search size={26} />
                    <strong>还没有报道记录</strong>
                    <span>新增品牌监控后会自动生成第一批媒体报道。</span>
                  </div>
                )}
              </div>
            </section>

            {mediaCreateOpen && (
              <div className="monitor-modal-backdrop" role="presentation" onMouseDown={(event) => {
                if (event.target === event.currentTarget) setMediaCreateOpen(false);
              }}>
                <section className="monitor-settings-modal" role="dialog" aria-modal="true" aria-label="新增媒体监控">
                  <div className="modal-title-row">
                    <div>
                      <h2>新增媒体监控</h2>
                      <span>新增后会立即扫描一次公开新闻结果，并进入每日监控。</span>
                    </div>
                    <button className="icon-button" type="button" onClick={() => setMediaCreateOpen(false)} aria-label="关闭">
                      <X size={18} />
                    </button>
                  </div>
                  <form className="media-monitor-form modal-monitor-form" onSubmit={createMediaMonitor}>
                    <label>
                      已建档品牌
                      <select value={mediaForm.brand_id} onChange={(event) => applyMediaBrand(event.target.value)}>
                        <option value="">手动输入品牌</option>
                        {brands.map((brand) => <option key={brand.id} value={brand.id}>{brand.name}</option>)}
                      </select>
                    </label>
                    <label>
                      品牌
                      <input value={mediaForm.brand_name} onChange={(event) => setMediaForm({ ...mediaForm, brand_name: event.target.value })} placeholder="Brand name" />
                    </label>
                    <label className="full-field">
                      检索式
                      <div className="url-input-row">
                        <Search size={18} />
                        <input value={mediaForm.query} onChange={(event) => setMediaForm({ ...mediaForm, query: event.target.value })} placeholder="&quot;Brand&quot; OR &quot;Product&quot;" />
                      </div>
                    </label>
                    <label>
                      市场
                      <input value={mediaForm.region} onChange={(event) => setMediaForm({ ...mediaForm, region: event.target.value.toUpperCase() })} />
                    </label>
                    <label>
                      语言
                      <input value={mediaForm.language} onChange={(event) => setMediaForm({ ...mediaForm, language: event.target.value })} />
                    </label>
                    <div className="form-actions full-field">
                      <button className="secondary-button" type="button" onClick={() => setMediaCreateOpen(false)}>
                        取消
                      </button>
                      <button className="primary-button" type="submit" disabled={savingMediaMonitor}>
                        <Newspaper size={18} /> {savingMediaMonitor ? "扫描中" : "新增并扫描"}
                      </button>
                    </div>
                  </form>
                </section>
              </div>
            )}
          </div>
        )}

        {activeView === "marketing-social" && (
          <MarketingLinkMonitorPanel
            monitorType="social"
            title="社媒监控"
            icon={<BarChart3 size={18} />}
            links={socialLinks}
            summary={socialSummary}
            brands={brands}
            collectingLink={marketingCollectingLink}
            onCollect={collectMarketingLink}
            onToggle={toggleMarketingLink}
            onConfigure={() => setActiveView("brands")}
          />
        )}

        {activeView === "marketing-ads" && (
          <MarketingLinkMonitorPanel
            monitorType="ads"
            title="广告监控"
            icon={<ShoppingBag size={18} />}
            links={adsLinks}
            summary={adsSummary}
            brands={brands}
            collectingLink={marketingCollectingLink}
            onCollect={collectMarketingLink}
            onToggle={toggleMarketingLink}
            onConfigure={() => setActiveView("brands")}
          />
        )}

        {activeView === "marketing-creators" && (
          <MarketingLinkMonitorPanel
            monitorType="creator"
            title="红人监控"
            icon={<Users size={18} />}
            links={creatorLinks}
            summary={creatorSummary}
            brands={brands}
            collectingLink={marketingCollectingLink}
            onCollect={collectMarketingLink}
            onToggle={toggleMarketingLink}
            onConfigure={() => setActiveView("brands")}
          />
        )}

        {activeView === "channel-sales" && (
          <div className="sales-layout">
            <section className="metric-row sales-metrics">
              <Metric label="监控品牌" value={salesChannelBrands.length} icon={<Building2 size={18} />} tone="green" />
              <Metric label="渠道链接" value={salesLinkTotal} icon={<ShoppingBag size={18} />} tone="amber" />
              <Metric label="启用链接" value={salesActiveLinkTotal} icon={<CheckCircle size={18} />} tone="violet" />
              <Metric label="平台类型" value={salesPlatformTotal} icon={<Globe size={18} />} tone="red" />
            </section>

            <section className="panel sales-overview-panel">
              <div className="panel-title">
                <h2>监控概览</h2>
                <span>实时配置</span>
              </div>
              <div className="sales-summary-grid">
                <div>
                  <span>品牌覆盖</span>
                  <strong>{salesChannelBrands.length}</strong>
                  <small>{salesChannelBrands.length ? "已进入渠道监控池" : "右上角 + 新增品牌"}</small>
                </div>
                <div>
                  <span>渠道覆盖</span>
                  <strong>{salesLinkTotal}</strong>
                  <small>{salesPlatformTotal} 类平台</small>
                </div>
                <div>
                  <span>启用比例</span>
                  <strong>{salesLinkTotal ? formatScore(salesActiveLinkTotal / salesLinkTotal) : "0%"}</strong>
                  <small>{salesActiveLinkTotal}/{salesLinkTotal} 个链接启用</small>
                </div>
                <div>
                  <span>数据采集</span>
                  <strong>待接入</strong>
                  <small>不展示种子销量</small>
                </div>
              </div>
            </section>

            <section className="panel sales-brand-panel">
              <div className="panel-title">
                <h2>监控品牌</h2>
                <span>{salesChannelBrands.length}</span>
              </div>
              <div className="sales-brand-list">
                {salesChannelBrands.length ? salesChannelBrands.map((brand) => (
                  <article className={classNames("sales-brand-card", selectedSalesBrand?.id === brand.id && "selected")} key={brand.id}>
                    <button type="button" onClick={() => setSelectedSalesBrandId(brand.id)}>
                      <strong>{brand.name}</strong>
                      <span>{brand.link_count} 个链接 · {brand.platforms.join(" / ") || "待添加"}</span>
                    </button>
                    <div className="brand-actions">
                      <button type="button" onClick={() => editSalesBrand(brand)}><SquarePen size={16} /> 编辑</button>
                      <button type="button" className="danger-action" onClick={() => deleteSalesBrand(brand)}><Trash2 size={16} /> 删除</button>
                    </div>
                  </article>
                )) : (
                  <div className="empty-state compact-empty">
                    <ShoppingBag size={26} />
                    <strong>暂无渠道销售品牌</strong>
                    <span>右上角 + 添加第一个品牌。</span>
                  </div>
                )}
              </div>
            </section>

            <section className="panel sales-link-panel">
              <div className="panel-title">
                <h2>{selectedSalesBrand ? `${selectedSalesBrand.name} 渠道状态` : "渠道状态"}</h2>
                <span>{selectedSalesBrand?.link_count || 0}</span>
              </div>
              <div className="sales-link-list">
                {selectedSalesBrand?.links.length ? selectedSalesBrand.links.map((link) => (
                  <article className="sales-link-row" key={link.id}>
                    <div>
                      <div className="record-meta">
                        <span>{link.platform_label}</span>
                        <span>{link.store_type}</span>
                        <span>{link.region}</span>
                        <span>{link.status === "active" ? "启用" : "暂停"}</span>
                      </div>
                      <h3>{link.name}</h3>
                      <p>{link.url}</p>
                    </div>
                    <div className="monitor-actions">
                      <button type="button" onClick={() => editSalesLink(link)}><SquarePen size={16} /> 编辑</button>
                      {link.url && <a href={link.url} target="_blank" rel="noreferrer"><ExternalLink size={16} /> 打开</a>}
                      <button type="button" className="danger-action" onClick={() => deleteSalesLink(link)}><Trash2 size={16} /> 删除</button>
                    </div>
                  </article>
                )) : (
                  <div className="empty-state compact-empty">
                    <LinkIcon size={26} />
                    <strong>暂无渠道链接</strong>
                    <span>右上角 + 添加 Amazon、国际站或独立站链接。</span>
                  </div>
                )}
              </div>
            </section>

            <section className="panel sales-data-panel">
              <div className="panel-title">
                <h2>数据面板</h2>
                <span>真实采集</span>
              </div>
              <div className="sales-data-empty">
                <Database size={26} />
                <strong>等待销售数据采集器</strong>
                <span>当前页面只展示已保存的品牌和渠道链接，不展示种子销量。</span>
              </div>
            </section>

            {salesSettingsMode && (
              <div className="monitor-modal-backdrop" role="presentation" onMouseDown={(event) => {
                if (event.target === event.currentTarget) closeSalesSettings();
              }}>
                <section className="monitor-settings-modal sales-settings-modal" role="dialog" aria-modal="true" aria-label={salesSettingsMode === "brand" ? "监控品牌设置" : "渠道链接设置"}>
                  <div className="modal-title-row">
                    <div>
                      <h2>{salesSettingsMode === "brand" ? editingSalesBrandId ? "编辑监控品牌" : "新增监控品牌" : editingSalesLinkId ? "编辑渠道链接" : "新增渠道链接"}</h2>
                      <span>{salesSettingsMode === "brand" ? "品牌与渠道" : selectedSalesBrand ? selectedSalesBrand.name : "先选择品牌"}</span>
                    </div>
                    <button className="icon-button" type="button" onClick={closeSalesSettings} aria-label="关闭">
                      <X size={18} />
                    </button>
                  </div>

                  {salesSettingsMode === "brand" ? (
                    <>
                      <form className="sales-config-form modal-monitor-form" onSubmit={saveSalesBrand}>
                        <label>
                          已建档品牌
                          <select value={salesBrandForm.brand_profile_id} onChange={(event) => applySalesBrandProfile(event.target.value)}>
                            <option value="">手动输入</option>
                            {brands.map((brand) => <option key={brand.id} value={brand.id}>{brand.name}</option>)}
                          </select>
                        </label>
                        <label>
                          品牌
                          <input value={salesBrandForm.name} onChange={(event) => setSalesBrandForm({ ...salesBrandForm, name: event.target.value })} placeholder="A Brand" />
                        </label>
                        <label className="full-field">
                          初始渠道链接
                          <div className="url-input-row">
                            <LinkIcon size={18} />
                            <input value={salesBrandForm.source_url} onChange={(event) => setSalesBrandForm({ ...salesBrandForm, source_url: event.target.value })} placeholder="https://www.amazon.com/dp/ASIN" />
                          </div>
                        </label>
                        <label>
                          状态
                          <select value={salesBrandForm.status} onChange={(event) => setSalesBrandForm({ ...salesBrandForm, status: event.target.value as "active" | "paused" })}>
                            <option value="active">启用</option>
                            <option value="paused">暂停</option>
                          </select>
                        </label>
                        <label>
                          备注
                          <input value={salesBrandForm.notes} onChange={(event) => setSalesBrandForm({ ...salesBrandForm, notes: event.target.value })} />
                        </label>
                        <div className="form-actions full-field">
                          <button className="secondary-button" type="button" onClick={discoverSalesChannels} disabled={discoveringSalesChannels || !salesBrandForm.source_url.trim()}>
                            <Search size={18} /> {discoveringSalesChannels ? "识别中" : "智能识别"}
                          </button>
                          <button className="secondary-button" type="button" onClick={closeSalesSettings}>取消</button>
                          <button className="primary-button" type="submit" disabled={savingSalesBrand}>
                            <Save size={18} /> {savingSalesBrand ? "保存中" : editingSalesBrandId ? "保存修改" : "保存品牌"}
                          </button>
                        </div>
                      </form>

                      {salesDiscovery && (
                        <div className="sales-discovery-panel">
                          <div className="panel-title">
                            <h2>识别候选</h2>
                            <span>{salesDiscovery.candidates.length}</span>
                          </div>
                          <div className="sales-discovery-list">
                            {salesDiscovery.candidates.map((candidate) => (
                              <article className="sales-discovery-row" key={candidate.canonical_url}>
                                <div>
                                  <strong>{candidate.platform_label}</strong>
                                  <span>{candidate.name} · {candidate.region} · {formatScore(candidate.confidence)}</span>
                                  <small>{candidate.url}</small>
                                </div>
                                {selectedSalesBrand ? (
                                  <button type="button" onClick={() => addSalesDiscoveryCandidate(candidate)} disabled={savingSalesLink}>
                                    <Save size={15} /> 加入
                                  </button>
                                ) : (
                                  <button type="button" onClick={createSalesBrandFromDiscovery} disabled={savingSalesBrand}>
                                    <Save size={15} /> 创建
                                  </button>
                                )}
                              </article>
                            ))}
                          </div>
                        </div>
                      )}
                    </>
                  ) : (
                    <form className="sales-link-form modal-monitor-form" onSubmit={saveSalesLink}>
                      <label>
                        平台
                        <select value={salesLinkForm.platform} onChange={(event) => setSalesLinkForm({ ...salesLinkForm, platform: event.target.value })}>
                          <option value="amazon">Amazon</option>
                          <option value="alibaba">Alibaba 国际站</option>
                          <option value="owned_site">独立站</option>
                          <option value="tiktok_shop">TikTok Shop</option>
                          <option value="walmart">Walmart</option>
                          <option value="shopify">Shopify</option>
                          <option value="temu">Temu</option>
                          <option value="other">其他渠道</option>
                        </select>
                      </label>
                      <label>
                        名称
                        <input value={salesLinkForm.name} onChange={(event) => setSalesLinkForm({ ...salesLinkForm, name: event.target.value })} placeholder="Amazon 商品页" />
                      </label>
                      <label className="full-field">
                        URL
                        <div className="url-input-row">
                          <LinkIcon size={18} />
                          <input value={salesLinkForm.url} onChange={(event) => setSalesLinkForm({ ...salesLinkForm, url: event.target.value })} placeholder="https://..." />
                        </div>
                      </label>
                      <label>
                        店铺类型
                        <select value={salesLinkForm.store_type} onChange={(event) => setSalesLinkForm({ ...salesLinkForm, store_type: event.target.value })}>
                          <option value="自营店">自营店</option>
                          <option value="渠道店">渠道店</option>
                        </select>
                      </label>
                      <label>
                        区域
                        <input value={salesLinkForm.region} onChange={(event) => setSalesLinkForm({ ...salesLinkForm, region: event.target.value.toUpperCase() })} />
                      </label>
                      <label>
                        状态
                        <select value={salesLinkForm.status} onChange={(event) => setSalesLinkForm({ ...salesLinkForm, status: event.target.value as "active" | "paused" })}>
                          <option value="active">启用</option>
                          <option value="paused">暂停</option>
                        </select>
                      </label>
                      <label>
                        备注
                        <input value={salesLinkForm.notes} onChange={(event) => setSalesLinkForm({ ...salesLinkForm, notes: event.target.value })} />
                      </label>
                      <div className="form-actions full-field">
                        <button className="secondary-button" type="button" onClick={closeSalesSettings}>取消</button>
                        <button className="primary-button" type="submit" disabled={savingSalesLink || !selectedSalesBrand}>
                          <Save size={18} /> {savingSalesLink ? "保存中" : editingSalesLinkId ? "保存修改" : "新增链接"}
                        </button>
                      </div>
                    </form>
                  )}
                </section>
              </div>
            )}
          </div>
        )}

        {activeView === "web" && (
          <div className="web-layout">
            <section className="web-toolbar">
              <div className="site-switcher" aria-label="站点切换">
                <button className={classNames("site-switch-button", !selectedMonitorId && "active")} type="button" onClick={() => setSelectedMonitorId("")} title="全部网页">
                  <Globe size={20} />
                </button>
                {webMonitors.map((monitor) => (
                  <button
                    className={classNames("site-switch-button", selectedMonitorId === monitor.id && "active")}
                    type="button"
                    key={monitor.id}
                    onClick={() => setSelectedMonitorId(monitor.id)}
                    title={monitor.name}
                  >
                    <span className="site-icon-fallback"><Globe size={20} /></span>
                    {monitor.icon_url && <img src={monitor.icon_url} alt="" onError={(event) => { event.currentTarget.style.display = "none"; }} />}
                  </button>
                ))}
              </div>
              <div className="web-toolbar-actions">
                <button className="icon-button" type="button" onClick={() => setMonitorMenuOpen((current) => !current)} aria-label="监控设置">
                  <Plus size={18} />
                </button>
                {monitorMenuOpen && (
                  <div className="web-menu-popover">
                    <button type="button" onClick={openNewMonitor}>
                      <Plus size={16} /> 新增
                    </button>
                    <button type="button" onClick={() => openMonitorSettings()} disabled={!selectedMonitor}>
                      <SlidersHorizontal size={16} /> 重新设置
                    </button>
                  </div>
                )}
              </div>
            </section>

            <section className="metric-row web-metrics">
              <Metric label="监控链接" value={webMonitors.length} icon={<Camera size={18} />} tone="green" />
              <Metric label={webRangeLabel} value={webSummary.total_snapshots} icon={<CalendarDays size={18} />} tone="amber" />
              <Metric label="发生变化" value={webSummary.changed_snapshots} icon={<History size={18} />} tone="red" />
              <Metric label="启用中" value={webSummary.active_monitors} icon={<Play size={18} />} tone="violet" />
            </section>

            <section className="panel web-control-panel">
              <div className="panel-title">
                <h2>监控状态</h2>
                <span>{selectedMonitor ? selectedMonitor.name : "全部站点"}</span>
              </div>

              {selectedCaptureJob && (
                <div className={classNames("capture-progress", selectedCaptureJob.status)}>
                  <div className="capture-progress-head">
                    <strong>{selectedCaptureJob.status === "error" ? "生成失败" : selectedCaptureJob.status === "complete" ? "生成完成" : "正在生成快照"}</strong>
                    <span>{Math.max(0, Math.min(100, selectedCaptureJob.progress))}%</span>
                  </div>
                  <div className="progress-track">
                    <div className="progress-fill" style={{ width: `${Math.max(4, Math.min(100, selectedCaptureJob.progress))}%` }} />
                  </div>
                  <p>{selectedCaptureJob.message}</p>
                  {!!selectedCaptureJob.total_pages && (
                    <small>{selectedCaptureJob.completed_pages}/{selectedCaptureJob.total_pages} 页 · {selectedCaptureJob.current_url}</small>
                  )}
                  {selectedCaptureJob.error && <small className="monitor-error">{selectedCaptureJob.error}</small>}
                </div>
              )}

              <div className="web-filter-row">
                <div className="segment-group" aria-label="快照时间范围">
                  {[
                    { value: "1", label: "今天" },
                    { value: "7", label: "近 7 天" },
                    { value: "15", label: "近半个月" },
                    { value: "30", label: "近 30 天" }
                  ].map((range) => (
                    <button key={range.value} className={classNames("segment-button", webRange === range.value && "active")} type="button" onClick={() => setWebRange(range.value)}>
                      {range.label}
                    </button>
                  ))}
                </div>
                <Select
                  icon={<Filter size={16} />}
                  value={selectedMonitorId}
                  onChange={setSelectedMonitorId}
                  options={webMonitors.map((monitor) => ({ value: monitor.id, label: monitor.name }))}
                  placeholder="全部网页"
                />
              </div>

              <div className="monitor-list">
                {webMonitors.length ? webMonitors.map((monitor) => (
                  <article className={classNames("monitor-row", selectedMonitorId === monitor.id && "selected")} key={monitor.id}>
                    <button className="monitor-main" type="button" onClick={() => setSelectedMonitorId(monitor.id)}>
                      <span className={classNames("monitor-dot", monitor.last_status)} />
                      <span>
                        <strong>{monitor.name}</strong>
                        <small>{monitor.url}</small>
                      </span>
                    </button>
                    <div className="monitor-meta">
                      <span><Clock size={14} /> {formatDateTime(monitor.last_snapshot_at)}</span>
                      <span>{monitor.scope === "domain" ? "主域名" : "当前页"}</span>
                      <span>{monitor.page_count || 0} 页 · {monitor.snapshots} 张快照</span>
                    </div>
                    {monitor.last_error && <p className="monitor-error">{monitor.last_error}</p>}
                    <div className="monitor-actions">
                      <button type="button" onClick={() => startCaptureJob(monitor.id)} disabled={activeCaptureJobs.some((job) => job.monitor_id === monitor.id)}>
                        <Camera size={16} /> {activeCaptureJobs.some((job) => job.monitor_id === monitor.id) ? "生成中" : "立即快照"}
                      </button>
                      <button type="button" onClick={() => toggleMonitor(monitor)}>
                        {monitor.status === "active" ? <PauseCircle size={16} /> : <Play size={16} />} {monitor.status === "active" ? "暂停" : "启用"}
                      </button>
                      <button type="button" className="danger-action" onClick={() => deleteMonitor(monitor)}>
                        <Trash2 size={16} /> 删除
                      </button>
                    </div>
                  </article>
                )) : (
                  <div className="empty-state compact-empty">
                    <Camera size={26} />
                    <strong>暂无网页监控</strong>
                    <span>添加 URL 后会生成第一张快照。</span>
                  </div>
                )}
              </div>
            </section>

            <section className="panel web-summary-panel">
              <div className="panel-title">
                <h2>{selectedMonitor ? selectedMonitor.name : "变化总结"}</h2>
                <span>{webRangeLabel}</span>
              </div>
              <div className="summary-highlight-list">
                {webSummary.highlights.length ? webSummary.highlights.map((item) => (
                  <article className="summary-highlight" key={item.snapshot_id}>
                    <div>
                      <strong>{item.monitor || "网页"}</strong>
                      <span>{item.date} · {formatScore(item.score)}</span>
                    </div>
                    {item.page && <small>{item.page}</small>}
                    <p>{item.summary}</p>
                    {item.screenshot_url && <a href={item.screenshot_url} target="_blank" rel="noreferrer"><ExternalLink size={15} /> 快照</a>}
                  </article>
                )) : (
                  <div className="empty-state compact-empty">
                    <History size={26} />
                    <strong>没有变化摘要</strong>
                    <span>{webRangeLabel}内暂无快照记录。</span>
                  </div>
                )}
              </div>

              <div className="daily-summary-list">
                {webSummary.daily.map((day) => (
                  <article className="daily-summary" key={day.date}>
                    <div>
                      <b>{day.date}</b>
                      <span>{day.snapshots} 张 · {day.changed} 处变化</span>
                    </div>
                    {day.summaries.slice(0, 3).map((item) => (
                      <p key={item.snapshot_id}>{item.monitor ? `${item.monitor} · ` : ""}{item.page ? `${item.page}: ` : ""}{item.summary}</p>
                    ))}
                  </article>
                ))}
              </div>
            </section>

            <section className="panel web-snapshot-panel">
              <div className="panel-title">
                <h2>快照回溯</h2>
                <span>{webSnapshots.length}</span>
              </div>
              <div className="snapshot-list">
                {webSnapshots.length ? webSnapshots.map((snapshot) => (
                  <article className="snapshot-row" key={snapshot.id}>
                    {snapshot.screenshot_url && (
                      <a className="snapshot-thumb" href={snapshot.screenshot_url} target="_blank" rel="noreferrer">
                        <img src={snapshot.screenshot_url} alt="" />
                      </a>
                    )}
                    <div className="snapshot-main">
                      <div className="record-meta">
                        <span>{snapshot.monitor_name || "网页"}</span>
                        <span>{snapshot.page_path || "/"}</span>
                        <span>{snapshot.snapshot_date}</span>
                        <span>{formatDateTime(snapshot.created_at)}</span>
                        <span>{formatScore(snapshot.change_score)}</span>
                      </div>
                      <h3>{snapshot.title || snapshot.url}</h3>
                      {snapshot.summary && <p>{snapshot.summary}</p>}
                      <div className="change-list">
                        {snapshot.changes.slice(0, 5).map((change, index) => (
                          <span className={classNames("change-pill", change.type)} key={`${snapshot.id}-${index}`}>
                            {change.type === "title" ? "标题" : change.type === "added" ? "新增" : "移除"} {change.text || change.to || change.from}
                          </span>
                        ))}
                      </div>
                      <div className="snapshot-actions">
                        {snapshot.screenshot_url && <a href={snapshot.screenshot_url} target="_blank" rel="noreferrer"><ExternalLink size={16} /> 截图</a>}
                        {snapshot.html_url && <a href={snapshot.html_url} target="_blank" rel="noreferrer"><Globe size={16} /> HTML</a>}
                        {snapshot.final_url && <a href={snapshot.final_url} target="_blank" rel="noreferrer"><LinkIcon size={16} /> 当前网页</a>}
                      </div>
                    </div>
                  </article>
                )) : (
                  <div className="empty-state compact-empty">
                    <CalendarDays size={26} />
                    <strong>暂无快照</strong>
                    <span>{webRangeLabel}内没有可回溯记录。</span>
                  </div>
                )}
              </div>
            </section>

            {monitorSettingsOpen && (
              <div className="monitor-modal-backdrop" role="presentation" onMouseDown={(event) => {
                if (event.target === event.currentTarget) closeMonitorSettings();
              }}>
                <section className="monitor-settings-modal" role="dialog" aria-modal="true" aria-label={editingMonitorId ? "重新设置监控" : "新增监控"}>
                  <div className="modal-title-row">
                    <div>
                      <h2>{editingMonitorId ? "重新设置监控" : "新增监控"}</h2>
                      <span>{editingMonitorId ? "调整站点范围和页面上限" : "新增后会立即开始生成首批快照"}</span>
                    </div>
                    <button className="icon-button" type="button" onClick={closeMonitorSettings} aria-label="关闭">
                      <X size={18} />
                    </button>
                  </div>
                  <form className="web-monitor-form modal-monitor-form" onSubmit={saveMonitorSettings}>
                    <label>
                      名称
                      <input value={monitorForm.name} onChange={(event) => setMonitorForm({ ...monitorForm, name: event.target.value })} placeholder="官网首页" />
                    </label>
                    <label className="full-field">
                      URL
                      <div className="url-input-row">
                        <LinkIcon size={18} />
                        <input value={monitorForm.url} onChange={(event) => setMonitorForm({ ...monitorForm, url: event.target.value })} placeholder="https://example.com" />
                      </div>
                    </label>
                    <label className="full-field">
                      监控范围
                      <div className="scope-row">
                        <button className={classNames("scope-button", monitorForm.scope === "domain" && "active")} type="button" onClick={() => setMonitorForm({ ...monitorForm, scope: "domain" })}>
                          主域名子页面
                        </button>
                        <button className={classNames("scope-button", monitorForm.scope === "single_page" && "active")} type="button" onClick={() => setMonitorForm({ ...monitorForm, scope: "single_page" })}>
                          当前页面
                        </button>
                      </div>
                    </label>
                    {monitorForm.scope === "domain" && (
                      <label className="full-field">
                        最多抓取页面数
                        <input
                          type="number"
                          min={1}
                          max={200}
                          value={monitorForm.crawl_limit}
                          onChange={(event) => setMonitorForm({ ...monitorForm, crawl_limit: Number(event.target.value) || 20 })}
                        />
                      </label>
                    )}
                    <div className="form-actions full-field">
                      <button className="secondary-button" type="button" onClick={closeMonitorSettings}>
                        取消
                      </button>
                      <button className="primary-button" type="submit" disabled={savingMonitor}>
                        <Save size={18} /> {savingMonitor ? "保存中" : editingMonitorId ? "保存设置" : "新增并开始监控"}
                      </button>
                    </div>
                  </form>
                </section>
              </div>
            )}
          </div>
        )}

        {activeView === "sources" && (
          <div className="sources-layout">
            <section className="panel source-list-panel">
              <div className="panel-title">
                <h2>Connector</h2>
                <span>{sources.length}</span>
              </div>
              <div className="source-list">
                {sources.map((source) => (
                  <article className="source-row" key={source.id}>
                    <div className="source-icon">{CATEGORY_ICON[source.category] ?? <Database size={18} />}</div>
                    <div>
                      <strong>{source.name}</strong>
                      <span>{source.vendor} · {source.sync_mode}</span>
                    </div>
                    <b className={classNames("status-badge", source.status)}>{source.status === "ready" ? "Ready" : "Planned"}</b>
                  </article>
                ))}
              </div>
            </section>

            <section className="panel model-panel">
              <div className="panel-title">
                <h2>标准化模型</h2>
                <span>v0.1</span>
              </div>
              <div className="model-flow">
                <ModelNode title="Source" fields={["vendor", "category", "sync_mode", "status"]} />
                <ModelNode title="Record" fields={["data_type", "body", "brand", "competitor", "occurred_at"]} />
                <ModelNode title="Analysis" fields={["sentiment", "intent", "topics", "score"]} />
                <ModelNode title="Insight" fields={["share_of_voice", "issue trend", "creator spike"]} />
              </div>
            </section>
          </div>
        )}
      </section>
    </main>
  );
}

function Metric({ label, value, icon, tone }: { label: string; value: number | string; icon: ReactNode; tone: string }) {
  return (
    <article className={classNames("metric-card", `tone-${tone}`)}>
      <span>{icon}</span>
      <div>
        <strong>{value}</strong>
        <small>{label}</small>
      </div>
    </article>
  );
}

function PlatformLogo({ platform, compact = false }: { platform: CommunitySource["platform"] | string; compact?: boolean }) {
  const normalized = (platform || "owned") as CommunitySource["platform"];
  const label = {
    reddit: "r/",
    discord: "D",
    facebook: "f",
    owned: "www"
  }[normalized] || "C";

  return (
    <span className={classNames("platform-logo", `platform-${normalized}`, compact && "compact")} title={platform}>
      {label}
    </span>
  );
}

function Select({
  icon,
  value,
  onChange,
  options,
  placeholder
}: {
  icon: ReactNode;
  value: string;
  onChange: (value: string) => void;
  options: Array<{ value: string; label: string }>;
  placeholder: string;
}) {
  return (
    <label className="select-shell">
      {icon}
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">{placeholder}</option>
        {options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
    </label>
  );
}

function MediaMentionRow({ mention }: { mention: MediaMention }) {
  return (
    <article className="media-mention-row">
      <div className="media-mention-main">
        <div className="record-meta">
          <span>{mention.publication}</span>
          <span>{formatDate(mention.occurred_at)}</span>
          <span>{formatCompactNumber(mention.estimated_reach)} 曝光</span>
          <span>{mention.media_tier}</span>
        </div>
        <h3>{mention.title || mention.body.slice(0, 72)}</h3>
        <p>{mention.body}</p>
        <div className="record-tags">
          {mention.brand && <span>{mention.brand}</span>}
          {(mention.pr_themes || []).map((theme) => <span key={theme}>{prThemeLabel(theme)}</span>)}
          {mention.source_domain && <span>{mention.source_domain}</span>}
        </div>
      </div>
      <div className="media-mention-side">
        <b className={classNames("coverage-badge", mention.coverage_type !== "earned" && "paid")}>{coverageLabel(mention.coverage_type)}</b>
        <span>{formatScore(mention.coverage_confidence)}</span>
        {mention.url && <a href={mention.url} target="_blank" rel="noreferrer"><ExternalLink size={15} /> 原文</a>}
      </div>
    </article>
  );
}

function marketingLinkStatus(link: MarketingLink, collectingLink: string) {
  if (collectingLink === link.id) return { label: "采集中", tone: "running" };
  if (link.status === "paused") return { label: "已暂停", tone: "paused" };
  if (link.last_status === "error") return { label: "采集失败", tone: "error" };
  if (link.last_status === "pending") return { label: "待首次采集", tone: "pending" };
  return { label: "监控中", tone: "active" };
}

function MarketingLinkMonitorPanel({
  monitorType,
  title,
  icon,
  links,
  summary,
  brands,
  collectingLink,
  onCollect,
  onToggle,
  onConfigure
}: {
  monitorType: MarketingMonitorType;
  title: string;
  icon: ReactNode;
  links: MarketingLink[];
  summary: MarketingLinkSummary;
  brands: BrandProfile[];
  collectingLink: string;
  onCollect: (link: MarketingLink) => void;
  onToggle: (link: MarketingLink) => void;
  onConfigure: () => void;
}) {
  const [selectedBrandName, setSelectedBrandName] = useState("");
  const [selectedLinkId, setSelectedLinkId] = useState("");
  const brandOptions = summary.by_brand.map((brand) => ({ value: brand.brand_name, label: brand.brand_name }));
  const filteredLinks = links.filter((link) => !selectedBrandName || link.brand_name === selectedBrandName);
  const selectedLink = filteredLinks.find((link) => link.id === selectedLinkId) || filteredLinks[0];
  const brandGroups = summary.by_brand.map((brand) => ({
    ...brand,
    links: links.filter((link) => link.brand_name === brand.brand_name)
  })).filter((group) => !selectedBrandName || group.brand_name === selectedBrandName);
  const selectedBrandProfile = brands.find((brand) => selectedLink?.brand_id === brand.id || nameKey(brand.name) === nameKey(selectedBrandName || selectedLink?.brand_name));

  return (
    <div className="marketing-link-layout">
      <section className="metric-row marketing-link-metrics">
        <Metric label="品牌数" value={summary.by_brand.length} icon={<Building2 size={18} />} tone="green" />
        <Metric label="监控链接" value={summary.total_links} icon={icon} tone="amber" />
        <Metric label="启用中" value={summary.active_links} icon={<Play size={18} />} tone="violet" />
        <Metric label="采集记录" value={summary.total_records} icon={<Database size={18} />} tone="red" />
      </section>

      <section className="panel marketing-link-selector-panel">
        <div className="panel-title">
          <h2>选择监控配置</h2>
          <span>{monitorType === "ads" ? "广告" : monitorType === "creator" ? "红人" : "社媒"}</span>
        </div>
        <div className="marketing-selector-grid">
          <Select
            icon={<Building2 size={16} />}
            value={selectedBrandName}
            onChange={(value) => {
              setSelectedBrandName(value);
              setSelectedLinkId("");
            }}
            options={brandOptions}
            placeholder="全部品牌"
          />
          <label className="select-shell">
            <LinkIcon size={16} />
            <select value={selectedLink?.id || ""} onChange={(event) => setSelectedLinkId(event.target.value)}>
              {filteredLinks.length ? filteredLinks.map((link) => <option key={link.id} value={link.id}>{link.name || link.platform_label}</option>) : <option value="">暂无配置</option>}
            </select>
          </label>
          <button className="secondary-button" type="button" onClick={onConfigure}>
            <SlidersHorizontal size={16} /> 配置总管理
          </button>
        </div>

        {selectedLink ? (
          <article className="marketing-selected-card">
            <div>
              <strong>{selectedLink.metrics.title || selectedLink.name}</strong>
              <span>{selectedLink.brand_name} · {selectedLink.platform_label} · {marketingLinkStatus(selectedLink, collectingLink).label}</span>
              <small>{selectedLink.url}</small>
            </div>
            <button type="button" onClick={() => onCollect(selectedLink)} disabled={collectingLink === selectedLink.id}>
              <RefreshCw size={15} /> {collectingLink === selectedLink.id ? "采集中" : "立即采集"}
            </button>
          </article>
        ) : (
          <div className="empty-state compact-empty">
            {icon}
            <strong>还没有可选配置</strong>
            <span>请先在品牌监控的配置总管理里维护。</span>
          </div>
        )}

        <div className="media-source-note marketing-link-note">
          <div>
            <strong>当前品牌</strong>
            <span>{selectedBrandProfile?.name || selectedBrandName || selectedLink?.brand_name || "全部品牌"}</span>
          </div>
          <div>
            <strong>配置来源</strong>
            <span>品牌监控 / 配置总管理</span>
          </div>
        </div>
      </section>

      <section className="panel marketing-link-data-panel">
        <div className="panel-title">
          <h2>{title}链接</h2>
          <span>{summary.ready_links} 条已采集 · {summary.error_links} 条异常</span>
        </div>
        <div className="marketing-brand-groups">
          {brandGroups.length ? brandGroups.map((group) => (
            <article className="marketing-brand-group" key={group.brand_name}>
              <div className="marketing-brand-head">
                <div>
                  <strong>{group.brand_name}</strong>
                  <span>{group.links.length} 个链接 · {group.records} 条记录</span>
                </div>
              </div>
              <div className="marketing-link-list">
                {group.links.map((link) => {
                  const status = marketingLinkStatus(link, collectingLink);
                  return (
                    <article className="marketing-link-row" key={link.id}>
                      <div className="marketing-link-main">
                        <div className="record-meta">
                          <span>{link.platform_label}</span>
                          <span>{link.cadence}</span>
                          <span>上次采集 {formatDateTime(link.last_collect_at)}</span>
                        </div>
                        <h3>{link.metrics.title || link.name}</h3>
                        <p>{link.url}</p>
                        {link.last_error && <small className="monitor-error">{link.last_error}</small>}
                      </div>
                      <div className="marketing-link-actions">
                        <b className={classNames("media-status-badge", status.tone)}>{status.label}</b>
                        <button type="button" onClick={() => onCollect(link)} disabled={collectingLink === link.id}>
                          <RefreshCw size={15} /> {collectingLink === link.id ? "采集中" : "立即采集"}
                        </button>
                        <button type="button" onClick={() => onToggle(link)}>
                          {link.status === "active" ? <PauseCircle size={15} /> : <Play size={15} />} {link.status === "active" ? "暂停" : "启用"}
                        </button>
                      </div>
                    </article>
                  );
                })}
              </div>
            </article>
          )) : (
            <div className="empty-state compact-empty">
              {icon}
              <strong>还没有{title}链接</strong>
              <span>请先在品牌监控的配置总管理里维护。</span>
            </div>
          )}
        </div>
      </section>

      <section className="panel marketing-link-recent-panel">
        <div className="panel-title">
          <h2>最近采集</h2>
          <span>{summary.recent.length}</span>
        </div>
        <div className="record-list">
          {summary.recent.length ? summary.recent.map((record) => (
            <RecordRow key={record.id} record={record} compact />
          )) : (
            <div className="empty-state compact-empty">
              <Search size={26} />
              <strong>暂无采集记录</strong>
              <span>录入真实链接后会生成第一条公开页面采集记录。</span>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

function MarketingStagePanel({
  icon,
  title,
  items,
  metrics
}: {
  icon: ReactNode;
  title: string;
  items: string[];
  metrics: string[];
}) {
  return (
    <div className="marketing-stage-layout">
      <section className="panel marketing-stage-panel">
        <div className="stage-title">
          <span>{icon}</span>
          <div>
            <h2>{title}</h2>
            <small>待接入真实数据</small>
          </div>
        </div>
        <div className="stage-grid">
          {items.map((item) => (
            <article key={item}>
              <strong>{item}</strong>
              <span>待接入</span>
            </article>
          ))}
        </div>
      </section>
      <section className="panel marketing-stage-panel">
        <div className="panel-title">
          <h2>指标模型</h2>
          <span>v0.1</span>
        </div>
        <div className="stage-metric-list">
          {metrics.map((metric) => (
            <span key={metric}>{metric}</span>
          ))}
        </div>
      </section>
    </div>
  );
}

function RecordRow({ record, compact = false, onCreateAction }: { record: RecordItem; compact?: boolean; onCreateAction?: (record: RecordItem) => void }) {
  return (
    <article className={classNames("record-row", compact && "compact")}>
      <div className="record-main">
        <div className="record-meta">
          <span>{record.community_source_name || record.source_name}</span>
          <span>{dataTypeLabel(record.data_type)}</span>
          {record.platform && <span>{record.platform}</span>}
          <span>{formatDate(record.occurred_at)}</span>
        </div>
        <h3>{record.title || record.body.slice(0, 48)}</h3>
        {!compact && <p>{record.body}</p>}
        <div className="record-tags">
          {record.brand && <span>{record.brand}</span>}
          {record.competitor && <span>{record.competitor}</span>}
          {record.product && <span>{record.product}</span>}
          {record.topics.map((topic) => <span key={topic}>{topicLabel(topic)}</span>)}
        </div>
      </div>
      <div className="record-side">
        <b className={classNames("sentiment", record.sentiment)}>{SENTIMENT_LABELS[record.sentiment]}</b>
        <span>{record.intent}</span>
        {onCreateAction && !compact && (
          <button className="record-action-button" type="button" onClick={() => onCreateAction(record)}>
            <SquarePen size={14} /> 分派
          </button>
        )}
      </div>
    </article>
  );
}

function ModelNode({ title, fields }: { title: string; fields: string[] }) {
  return (
    <article className="model-node">
      <strong>{title}</strong>
      {fields.map((field) => <span key={field}>{field}</span>)}
    </article>
  );
}
