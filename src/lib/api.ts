const BASE = "";
const REQUEST_TIMEOUT_MS = 120_000;

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      ...options,
      signal: options.signal || controller.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error("请求超时，请稍后重试");
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = data.detail || JSON.stringify(data);
    } catch {
      /* ignore */
    }
    throw new ApiError(detail || `HTTP ${res.status}`, res.status);
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
  slug?: string;
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

export interface CommunityPostSummary {
  status: "ready" | "unavailable" | "unconfigured" | "error";
  summary: string | null;
  headline?: string;
}

export interface VoiceEvidence {
  record_id: string;
  title: string;
  text: string;
  excerpt: string;
  highlights: string[];
  source_key?: string;
  platform?: string;
  author?: string;
  occurred_at?: string;
  rating?: number | string | null;
  url?: string;
  url_kind?: "source" | "review" | "store" | "thread";
}

export interface VoiceIssuePreview {
  key: string;
  title: string;
  summary: string;
  evidence: VoiceEvidence;
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
  voice_issue?: VoiceIssuePreview;
  community_summary?: CommunityPostSummary | null;
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
  feedback_summary?: string;
  problem_summary?: string;
  highlight_terms?: string[];
  interaction_count?: number;
  interaction_velocity?: number;
  age_hours?: number | null;
  hot_risk?: boolean;
  relevance_status?: "confirmed" | "pending" | "rejected";
  media_candidate_id?: string;
  retry_at?: string;
  media_relevance?: {
    status: "confirmed" | "pending" | "rejected";
    reason: string;
    mention_type?: "dedicated" | "roundup" | "comparison" | "incidental";
    evidence: { quote: string; field?: string; links?: string[] }[];
    manual?: boolean;
    model?: string;
    rule_version?: string;
  };
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
  next_collect_at?: string;
  due?: boolean;
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
  /** Amazon category ranks. `rank`/`bsr` remain as legacy fallbacks. */
  category_rank?: number;
  subcategory_rank?: number;
  category_name?: string;
  subcategory_name?: string;
  /** Compatibility aliases used by some collectors/imports. */
  main_category_rank?: number;
  sub_category_rank?: number;
  main_category_name?: string;
  sub_category_name?: string;
  units_est?: number;
  revenue_est?: number;
  estimate_method?: string;
  estimate_confidence?: "high" | "medium" | "low" | string;
  estimate_period_days?: number;
  estimate_basis?: Record<string, unknown>;
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

export interface SalesChangeLog {
  range: { start: string; end: string };
  events: {
    date: string;
    listing_id?: string;
    listing_title?: string;
    asin?: string;
    product_id?: string;
    product_name?: string;
    channel?: string;
    platform?: string;
    event_type?: "listing_added" | "metric_change";
    change_score: number;
    changes: { field: string; from?: unknown; to?: unknown }[];
  }[];
  daily: { date: string; listing_count: number; delta: number }[];
  total_events: number;
  changed_listings: number;
  field_counts: Record<string, number>;
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

export interface JobPostingsPage {
  postings: JobPosting[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface HiringWindowPosting {
  id: string;
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
  last_status?: string;
  last_error?: string;
  business_tags: string[];
}

export interface HiringSummary {
  window?: { start: string; end: string };
  trend: { date: string; released: number; closed: number; active: number }[];
  by_department: { department: string; count: number }[];
  by_platform: { platform: string; open: number; closed: number }[];
  total_postings: number;
  total_open: number;
  total_closed: number;
  avg_open_days?: number | null;
  released_in_window: number;
  closed_in_window: number;
  released_postings: HiringWindowPosting[];
  closed_postings: HiringWindowPosting[];
}

export interface HiringOrgRadar {
  window: { start: string; end: string };
  headcount_reference?: { count: number; source: string; as_of: string } | null;
  overview: { open_roles: number; known_people: number; city_count: number; department_count: number; confidence: string };
  departments: HiringOrgRadarGroup[];
  locations: (HiringOrgRadarGroup & { city: string; department_details: (HiringOrgRadarGroup & { department: string })[] })[];
  role_families: (HiringOrgRadarGroup & { role_family: string })[];
  trend: { date: string; released: number; closed: number; active: number }[];
  data_quality: { postings_total: number; with_city: number; with_department: number; people_total: number; limitations: string[] };
}

export interface HiringOrgRadarGroup {
  department?: string;
  open_roles: number;
  postings_total: number;
  released: number;
  closed: number;
  net_change: number;
  known_people: number;
  top_roles: { title: string; count: number }[];
  confidence: string;
  evidence: { id: string; title: string; url?: string; first_seen?: string; closed_at?: string; city?: string; city_source?: "linkedin_title" | null; status?: string }[];
}

export interface HiringOrgEstimateGroup {
  department?: string;
  city?: string;
  role_family?: string;
  low: number;
  high: number;
  confidence: string;
  rationale: string;
  evidence_ids: string[];
}

export interface HiringOrgEstimate {
  summary: string;
  overall: { low: number; high: number; confidence: string; basis: string[] } | null;
  departments: HiringOrgEstimateGroup[];
  locations: (HiringOrgEstimateGroup & { departments: HiringOrgEstimateGroup[] })[];
  role_families: HiringOrgEstimateGroup[];
  assumptions: string[];
  limitations: string[];
  cached?: boolean;
  model?: string;
  generated_at?: string;
}

export interface HiringOrgEstimateState {
  estimate: HiringOrgEstimate | null;
  generation: { status: "running" | "completed" | "failed"; error?: string } | null;
}

export interface HiringStrategicSignalEvidence {
  id: string;
  domain: string;
  kind: string;
  at?: string;
  title?: string;
  detail?: string;
  url?: string;
  independence_group?: string;
  metrics?: Record<string, unknown>;
}

export interface HiringStrategicSignal {
  id: string;
  title: string;
  direction: string;
  priority: "high" | "medium" | "low";
  confidence: string;
  observation: string;
  inference: string;
  business_impact: string;
  alternative: string;
  next_signal: string;
  time_horizon: string;
  domains: string[];
  evidence_ids: string[];
  evidence: HiringStrategicSignalEvidence[];
}

export interface HiringStrategicSignals {
  generation?: { status: "running" | "completed" | "failed"; error?: string };
  analysis_status?: "complete" | "fallback";
  previous_result?: boolean;
  generated_at?: string;
  schema_version: string;
  brand: string;
  window: { scope: string; start: string; end: string };
  headline: { posture: string; summary: string; confidence: string };
  signals: HiringStrategicSignal[];
  coverage: Record<string, any>;
  source_coverage: Record<string, any>;
  timeline: { date: string; events: { domain: string; event_type: string; label: string; evidence_ids: string[] }[] }[];
  details: { organization_estimate?: { status: string; href: string; note: string }; evidence_chain?: any[]; method?: Record<string, any> };
  caveats: string[];
  cached?: boolean;
  refresh?: boolean;
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
  configured?: boolean;
  error_messages?: string[];
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
  duplicate_count?: number;
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
    app_reviews: number;
    app_downloads: number;
    app_rating_count_observed: boolean;
    app_evidence_count: number;
    app_data_updated_at?: string | null;
    excluded_app_records: number;
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
    ratings_only_ratio: number;
  };
  brands: MarketShareBrand[];
  warnings: string[];
}

export interface MarketShareTrendPoint {
  date: string;
  shares: Record<string, number>;
  public_metrics: Record<string, { rating_count: number }>;
  fresh_brand_count: number;
  is_carried_forward: boolean;
  data_as_of?: string | null;
}

export interface RatingGrowthBrand {
  brand_id: string;
  name: string;
  net: number | null;
  daily_average: number | null;
  share: number | null;
  comparable_share: number | null;
  observed_window: {
    end: string;
    end_count: number;
    app_count: number;
    pair: { start: string; app_count: number; net: number } | null;
  } | null;
  missing_dates: string[];
  apps: Array<Pick<AppObservation, "key" | "name" | "platform" | "scope"> & {
    baseline_count: number | null;
    end_count: number | null;
    in_baseline_sample: boolean;
  }>;
}

export interface MarketShareGrowthResponse {
  range: { start: string; end: string; baseline: string; days: number };
  countries: string[];
  total_net: number | null;
  comparable_total_net: number | null;
  brands: RatingGrowthBrand[];
  points: Array<{ date: string; net: Record<string, number | null> }>;
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

export type BrandMetric = "app_store_ratings" | "google_play_ratings" | "app_downloads";
export type AppMetric = "rating_count" | "downloads";

export interface AppObservation extends Record<AppMetric, number | null> {
  key: string;
  app_id: string;
  name: string;
  platform: "app_store" | "google_play";
  scope: string;
  date: string;
  observed_at: string;
  url?: string | null;
}

export interface BrandObservations {
  brand_id: string;
  name: string;
  metrics: Partial<Record<BrandMetric, number>>;
  daily?: Array<{ date: string; metrics: Partial<Record<BrandMetric, number>> }>;
  apps?: Array<AppObservation & { daily: AppObservation[] }>;
}

export interface MarketObservationsResponse {
  countries: string[];
  brands: BrandObservations[];
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
  x_configured?: boolean;
  x_key_hint?: string;
  ensembledata_configured?: boolean;
  ensembledata_key_hint?: string;
  meta_configured?: boolean;
  meta_key_hint?: string;
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
  platform: "youtube" | "instagram" | "tiktok" | "x";
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

interface CompareEvidence {
  id: string;
  brand_id: string;
  brand: string;
  title: string;
  detail: string;
  at: string;
  url: string;
  platform?: string;
  period: "current" | "previous" | "active_in_window";
}

export interface CompareFinding {
  title: string;
  observation: string;
  implication: string;
  next_signal: string;
  confidence: "low" | "medium" | "high";
  evidence: CompareEvidence[];
  brand_id?: string;
}

export interface StrategySummary {
  mode: "ai" | "empty";
  cached: boolean;
  summary: string;
  range: { start: string; end: string; previous_start: string; previous_end: string };
  generated_at: string;
  evidence_count: number;
  brands: Array<{
    brand_id: string;
    name: string;
    is_primary: boolean;
    coverage: Array<{ label: string; evidence_count: number }>;
  }>;
  industry_changes: CompareFinding[];
  brand_moves: CompareFinding[];
  opportunities: CompareFinding[];
  risks: CompareFinding[];
  caveats: string[];
}
