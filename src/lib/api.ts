const BASE = "";

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
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
  sentiment?: string;
  sentiment_score?: number;
  intent?: string;
  topics: string[];
  metrics: Record<string, unknown>;
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
  last_snapshot_at?: string;
  last_status?: string;
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
  change_score?: number;
  summary?: string;
  changes: { type: string; text?: string; from?: string; to?: string }[];
  page_path: string;
}

export interface TrendPoint {
  date: string;
  total: number;
  negative: number;
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
  ensembledata_configured?: boolean;
  ensembledata_key_hint?: string;
  youtube_configured?: boolean;
  youtube_key_hint?: string;
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
