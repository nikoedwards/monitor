const BASE = "";

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = data.detail || JSON.stringify(data);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: body ? JSON.stringify(body) : undefined }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

export function qs(params: Record<string, unknown>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value));
    }
  }
  const str = search.toString();
  return str ? `?${str}` : "";
}

// ---------------------------------------------------------------------- types
export interface Brand {
  id: string;
  name: string;
  is_competitor: boolean;
  is_primary: boolean;
  official_website?: string;
  amazon_url?: string;
  category?: string;
  description?: string;
  logo_url?: string;
  monitoring_keywords: string[];
  social_links: Record<string, string>;
  ecommerce_links: Record<string, string>;
}

export interface Product {
  id: string;
  brand_id: string;
  name: string;
  sku?: string;
  category?: string;
  notes?: string;
}

export interface Link {
  id: string;
  brand_id: string;
  product_id?: string;
  dimension: string;
  channel: string;
  platform?: string;
  url?: string;
  label?: string;
  region?: string;
  status: string;
  last_collect_at?: string;
  last_status?: string;
  last_error?: string;
  config: Record<string, unknown>;
}

export interface RecordItem {
  id: string;
  brand_id?: string;
  product_id?: string;
  source_id: string;
  data_type: string;
  dimension?: string;
  channel?: string;
  platform?: string;
  title?: string;
  author?: string;
  body: string;
  url?: string;
  occurred_at?: string;
  voice_source?: string;
  sentiment?: string;
  sentiment_score?: number;
  sentiment_explanation?: {
    method: string;
    reason: string;
    positive_terms: string[];
    negative_terms: string[];
    negation_terms: string[];
    evidence: string[];
  };
  intent?: string;
  topics: string[];
  metrics: Record<string, unknown>;
  raw?: Record<string, unknown>;
}

export interface Source {
  id: string;
  name: string;
  category: string;
  tier: number;
  vendor?: string;
  sync_mode: string;
  status: string;
  needs_credentials: boolean;
  credential_key?: string;
  cadence: string;
  last_collect_at?: string;
  last_status?: string;
  last_error?: string;
  item_count: number;
  notes?: string;
}

export interface SalesMetric {
  id: string;
  brand_id: string;
  product_id?: string;
  link_id?: string;
  snapshot_date: string;
  channel: string;
  platform?: string;
  price?: number;
  currency?: string;
  review_count?: number;
  rating?: number;
  rank?: number;
  units_est?: number;
  revenue_est?: number;
  in_stock?: boolean | null;
  asin?: string;
  bsr?: number;
  title?: string;
  image_url?: string;
  change_score?: number;
  changes?: { field: string; from?: unknown; to?: unknown }[];
  source: string;
}

export interface SalesListing {
  id: string;
  brand_id: string;
  product_id?: string;
  link_id?: string;
  channel: string;
  platform?: string;
  asin?: string;
  url?: string;
  marketplace?: string;
  title?: string;
  sku?: string;
  image_url?: string;
  status: string;
  monitor: boolean;
  last_seen?: string;
  last_change_at?: string;
  last_status?: string;
  last_error?: string;
  has_change: boolean;
  data_points: number;
  latest?: SalesMetric | null;
}

export interface JobSnapshotItem {
  id: string;
  posting_id: string;
  snapshot_date: string;
  platform?: string;
  status?: string;
  is_open?: boolean | null;
  title?: string;
  department?: string;
  city?: string;
  posted_at?: string;
  applicant_signal?: string;
  change_score?: number;
  changes?: { field: string; from?: unknown; to?: unknown }[];
}

export interface JobPosting {
  id: string;
  brand_id: string;
  link_id?: string;
  platform: string;
  external_id?: string;
  url?: string;
  title?: string;
  department?: string;
  city?: string;
  jd_text?: string;
  status: string;
  posted_at?: string;
  refreshed_at?: string;
  first_seen?: string;
  last_seen?: string;
  closed_at?: string;
  last_change_at?: string;
  last_status?: string;
  last_error?: string;
  business_tags: string[];
  has_change: boolean;
  data_points: number;
  latest?: JobSnapshotItem | null;
}

export interface LinkedInEmployee {
  id: string;
  brand_id: string;
  external_id?: string;
  source_type?: "company" | "manual";
  name?: string;
  headline?: string;
  title?: string;
  notes?: string;
  profile_url?: string;
  avatar_url?: string;
  status: string;
  monitor: boolean;
  last_activity_at?: string;
  last_profile_change_at?: string;
  last_seen?: string;
  last_status?: string;
  last_error?: string;
  activity_count: number;
  snapshot_count: number;
  change_count: number;
  latest_snapshot?: LinkedInProfileSnapshot | null;
}

export interface LinkedInEmployeeImportResult {
  links: number;
  profiles: number;
  errors: number;
  candidates: LinkedInEmployee[];
}

export interface LinkedInProfileSnapshot {
  id: string;
  profile_id: string;
  brand_id: string;
  snapshot_date: string;
  name?: string;
  headline?: string;
  title?: string;
  status?: string;
  changes: { field: string; from?: unknown; to?: unknown }[];
}

export interface LinkedInActivity {
  id: string;
  profile_id: string;
  brand_id: string;
  activity_type: "post" | "comment" | "reaction" | "job_change" | "profile_change" | string;
  text?: string;
  url?: string;
  posted_at?: string;
  created_at: string;
  profile_name?: string;
  profile_title?: string;
  profile_url?: string;
}

export interface VocAction {
  id: string;
  brand_id?: string;
  record_id?: string;
  title: string;
  description?: string;
  owner_team?: string;
  priority: string;
  status: string;
  product?: string;
  topic?: string;
  due_at?: string;
  closed_at?: string;
}

export interface WebMonitor {
  id: string;
  brand_id?: string;
  name: string;
  url: string;
  scope: string;
  crawl_limit: number;
  status: string;
  check_interval_minutes: number;
  snapshot_interval_minutes: number;
  last_check_at?: string;
  last_snapshot_at?: string;
  snapshot_retry_count?: number;
  next_snapshot_retry_at?: string;
  last_snapshot_attempt_at?: string;
  next_check_at?: string;
  next_snapshot_at?: string;
  seconds_until_check?: number;
  seconds_until_snapshot?: number;
  last_change_score?: number;
  last_change_summary?: string;
  last_status?: string;
  last_error?: string;
  snapshot_count: number;
  latest_snapshot_date?: string;
}

export interface WebSnapshot {
  id: string;
  monitor_id: string;
  snapshot_date: string;
  url: string;
  final_url?: string;
  title?: string;
  screenshot_url: string;
  archive_url?: string;
  archive_size?: number;
  archive_self_contained?: boolean;
  capture_method?: string;
  change_score?: number;
  visual_change_score?: number;
  visual_change_ratio?: number;
  effective_change_score?: number;
  has_meaningful_change?: boolean;
  visual_regions: { x: number; y: number; width: number; height: number; change_ratio: number }[];
  previous_snapshot_id?: string;
  previous_created_at?: string;
  comparison_url?: string;
  summary?: string;
  changes: { type: string; text?: string; from?: string; to?: string }[];
  page_path: string;
  created_at: string;
  screenshot_path?: string;
}

export interface WebPeriodStats {
  start_date: string;
  end_date: string;
  range_days: number;
  total_snapshots: number;
  changed: number;
  changed_days: number;
  change_day_rate: number;
  capture_change_rate: number;
  average_interval_days?: number | null;
  average_severity: number;
  major_changes: number;
  daily: { date: string; captures: number; changed: number; severity: number }[];
  page_activity: { page: string; captures: number; changed: number; average_severity: number }[];
  highlights: WebSnapshot[];
}

export interface WebSummary extends WebPeriodStats {
  previous_period: WebPeriodStats;
  comparison: {
    trend: "more_active" | "more_stable" | "flat";
    changed_delta: number;
    changed_days_delta: number;
    frequency_delta_pct?: number | null;
    severity_delta: number;
  };
  ai_configured: boolean;
}

export interface WebAiAnalysis {
  summary: string;
  highlights: string[];
  change_categories: { category: string; count: number; evidence: string }[];
  major_events: { date: string; page: string; change: string; impact?: string; persistence?: string }[];
  frequency_assessment: string;
  business_signals: string[];
  caveats: string[];
  model?: string;
  cached?: boolean;
  created_at?: string;
  analysis_id?: string;
}

export interface TrendPoint {
  date: string;
  total: number;
  negative: number;
}

// Public ad-library monitoring models.  The backend keeps source-specific
// fields in `raw`; these normalized fields are enough for the dashboard.
export interface MarketingAd {
  id: string;
  source?: string;
  source_ad_id?: string;
  advertiser?: string;
  advertiser_name?: string;
  page_name?: string;
  country?: string;
  countries?: string[];
  platform?: string;
  platforms?: string[];
  status?: string;
  lifecycle?: string;
  title?: string;
  body?: string;
  description?: string;
  creative_url?: string;
  media_url?: string;
  thumbnail_url?: string;
  landing_url?: string;
  snapshot_url?: string;
  first_seen?: string;
  last_seen?: string;
  delivery_start?: string;
  delivery_stop?: string;
  duration_days?: number;
  active_days?: number;
  persistence_score?: number;
  evidence_level?: string;
  spend?: number | null;
  impressions?: number | null;
  reach?: number | null;
  creative_changes?: number;
  variants?: number;
  raw?: Record<string, unknown>;
}

export interface MarketingAdsSummary {
  total_ads?: number;
  active_ads?: number;
  new_ads?: number;
  stopped_ads?: number;
  changed_ads?: number;
  avg_duration_days?: number;
  avg_lifetime_days?: number;
  persistence_score?: number;
  evidence_level?: string;
  last_sync_at?: string;
  trend?: { date: string; active?: number; new?: number; stopped?: number; total?: number }[];
  by_source?: { source?: string; source_id?: string; total: number }[];
  by_status?: { status: string; total: number }[];
  alerts?: { id?: string; type: string; title: string; detail?: string; detected_at?: string; ad_id?: string }[];
}

export type MarketShareModelKey = "balanced" | "commerce" | "attention";

export interface MarketShareBrand {
  brand_id: string;
  name: string;
  category?: string;
  is_primary: boolean;
  is_competitor: boolean;
  rank: number;
  share: number;
  coverage: number;
  signals: Record<"sales" | "app" | "conversation" | "engagement", number>;
  signal_shares: Record<"sales" | "app" | "conversation" | "engagement", number>;
  raw: {
    mentions: number;
    voc_records: number;
    app_reviews: number;
    app_rating?: number | null;
    app_store_apps: number;
    app_rating_count_observed: boolean;
    comments: number;
    engagement: number;
    views: number;
    app_downloads_est: number;
    app_downloads_low: number;
    app_downloads_high: number;
    app_download_basis: "observed" | "review_proxy" | "unavailable";
    sales_revenue: number;
    sales_units: number;
    product_reviews: number;
    sales_data_points: number;
  };
  gaps: string[];
}

export interface MarketShareResponse {
  range: { start: string; end: string };
  country: string;
  countries: string[];
  model: {
    key: MarketShareModelKey;
    label: string;
    description: string;
    sales_basis: "revenue" | "units" | "unavailable";
    base_weights: Record<"sales" | "app" | "conversation" | "engagement", number>;
    active_weights: Record<"sales" | "app" | "conversation" | "engagement", number>;
  };
  confidence: {
    score: number;
    label: "高" | "中" | "低";
    available_weight: number;
    coverage_fairness: number;
    evidence_total: number;
    download_proxy_ratio: number;
  };
  brands: MarketShareBrand[];
  warnings: string[];
}

export interface MarketShareTrendPoint {
  date: string;
  shares: Record<string, number>;
  public_metrics: Record<string, { rating_count: number; average_rating?: number | null; app_count: number }>;
  fresh_brand_count: number;
  is_carried_forward: boolean;
  data_as_of?: string | null;
}

export interface MarketShareTrendSummary {
  brand_id: string;
  name: string;
  start_share: number;
  latest_share: number;
  change_pp: number;
  start_rating_count: number;
  latest_rating_count: number;
  rating_count_change: number;
  latest_average_rating?: number | null;
}

export interface MarketShareTrendResponse {
  range: { start: string; end: string };
  country: string;
  model: MarketShareModelKey;
  cadence: "daily";
  latest_date?: string | null;
  last_snapshot_at?: string | null;
  brands: Array<Pick<MarketShareBrand, "brand_id" | "name" | "category" | "is_primary" | "is_competitor">>;
  points: MarketShareTrendPoint[];
  summary: MarketShareTrendSummary[];
}

export interface LlmSettings {
  configured: boolean;
  key_hint?: string;
  base_url?: string;
  model?: string;
  app_title?: string;
  max_tokens?: string;
  sellersprite_configured?: boolean;
  sellersprite_key_hint?: string;
  youtube_configured?: boolean;
  youtube_key_hint?: string;
  boss_configured?: boolean;
  boss_key_hint?: string;
  linkedin_configured?: boolean;
  linkedin_key_hint?: string;
}

export interface CreatorRosterItem {
  id: string;
  brand_id: string;
  platform: string;
  handle?: string;
  name?: string;
  url?: string;
  avatar_url?: string;
  follower_count: number;
  post_count: number;
  collab_count: number;
  sponsored_count: number;
  total_views: number;
  total_engagement: number;
  first_seen?: string;
  last_seen?: string;
  last_collab_at?: string;
  avg_engagement: number;
  engagement_rate?: number | null;
  shared_brands?: string[];
}

export interface CreatorMapPoint {
  id: string;
  name: string;
  handle?: string;
  platform: string;
  url?: string;
  x: number;
  y: number;
  size: number;
  quadrant: "core" | "potential" | "scale" | "observe";
  collab_count: number;
  post_count: number;
  total_views: number;
  total_engagement: number;
  engagement_rate?: number | null;
  review_status?: CreatorReviewStatus;
}

export type CreatorReviewStatus = "pending" | "approved" | "priority" | "rejected";
export type CreatorRelationshipStatus = "potential" | "contacted" | "collaborating" | "past";

export interface CreatorCandidate {
  id: string;
  brand_id: string;
  platform: "youtube" | "instagram" | "tiktok";
  identity_key: string;
  handle?: string;
  name?: string;
  url?: string;
  avatar_url?: string;
  review_status: CreatorReviewStatus;
  relationship_status: CreatorRelationshipStatus;
  discovery_source: "collected" | "manual" | "manual+collected";
  relevance_score: number;
  follower_count: number;
  post_count: number;
  collab_count: number;
  sponsored_count: number;
  total_views: number;
  total_engagement: number;
  evidence_count: number;
  avg_engagement: number;
  engagement_rate?: number | null;
  first_seen?: string;
  last_seen?: string;
  last_collab_at?: string;
  notes?: string;
  reviewed_at?: string;
  products: { id: string; name: string }[];
}

export interface CreatorCandidateEvidence {
  id: string;
  candidate_id: string;
  record_id?: string;
  product_id?: string;
  product_name?: string;
  evidence_type: string;
  query?: string;
  title?: string;
  excerpt?: string;
  url?: string;
  confidence: number;
  occurred_at?: string;
  evidence: {
    match_type?: string;
    signals?: { kind: string; signal: string }[];
    mentions?: string[];
    collab_type?: string;
  };
}

export interface CreatorCandidatesResponse {
  candidates: CreatorCandidate[];
  totals: Record<"all" | "pending" | "approved" | "priority" | "rejected" | "curated", number>;
  curated_map: {
    points: CreatorMapPoint[];
    quadrants: { key: "core" | "potential" | "scale" | "observe"; label: string; total: number }[];
  };
}

export interface CreatorMapSnapshot {
  id: string;
  brand_id: string;
  product_id?: string;
  platform: string;
  snapshot_date: string;
  title?: string;
  created_at: string;
  map: CreatorCandidatesResponse["curated_map"];
}

export interface BrandDraft {
  name: string;
  category?: string;
  description?: string;
  official_website?: string;
  is_competitor?: boolean;
  monitoring_keywords: string[];
  products: { name: string; category?: string; sku?: string }[];
  sales: { platform: string; url: string }[];
  social: { platform: string; url: string }[];
  community: { platform: string; url: string }[];
}
