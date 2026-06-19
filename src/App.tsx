import {
  AlertTriangle,
  BarChart3,
  Building2,
  CheckCircle,
  Database,
  ExternalLink,
  FileUp,
  Filter,
  Globe,
  Inbox,
  Link as LinkIcon,
  MessageSquareText,
  Newspaper,
  PlugZap,
  RefreshCw,
  Save,
  Search,
  ShoppingBag,
  SquarePen,
  TrendingUp,
  Trash2,
  Users,
  WandSparkles
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
  data_type: string;
  platform?: string;
  title?: string;
  author?: string;
  body: string;
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

type View = "overview" | "voice" | "brands" | "sources";

const API = "";

const DATA_TYPES = [
  { value: "user_voice", label: "用户之声" },
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
  creator: "红人",
  retail: "电商",
  pr: "PR"
};

const CATEGORY_ICON: Record<string, ReactNode> = {
  creator: <Users size={18} />,
  pr: <Newspaper size={18} />,
  social: <BarChart3 size={18} />,
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

function classNames(...values: Array<string | false | undefined>) {
  return values.filter(Boolean).join(" ");
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

function dataTypeLabel(value: string) {
  return DATA_TYPES.find((item) => item.value === value)?.label ?? value;
}

function topicLabel(value: string) {
  return TOPIC_LABELS[value] ?? value;
}

export default function App() {
  const [activeView, setActiveView] = useState<View>("overview");
  const [overview, setOverview] = useState<Overview>(emptyOverview);
  const [sources, setSources] = useState<Source[]>([]);
  const [brands, setBrands] = useState<BrandProfile[]>([]);
  const [records, setRecords] = useState<RecordItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [brandUrl, setBrandUrl] = useState("");
  const [brandDraft, setBrandDraft] = useState<BrandDraft | null>(null);
  const [analyzingBrand, setAnalyzingBrand] = useState(false);
  const [savingBrand, setSavingBrand] = useState(false);
  const [query, setQuery] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [sentimentFilter, setSentimentFilter] = useState("");
  const [form, setForm] = useState({
    source_id: "manual_csv",
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

  const loadData = async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams();
      if (query) params.set("q", query);
      if (sourceFilter) params.set("source_id", sourceFilter);
      if (typeFilter) params.set("data_type", typeFilter);
      if (sentimentFilter) params.set("sentiment", sentimentFilter);

      const [nextOverview, nextSources, nextBrands, nextRecords] = await Promise.all([
        apiGet<Overview>("/api/overview"),
        apiGet<Source[]>("/api/sources"),
        apiGet<BrandProfile[]>("/api/brands"),
        apiGet<RecordItem[]>(`/api/records?${params.toString()}`)
      ]);
      setOverview(nextOverview);
      setSources(nextSources);
      setBrands(nextBrands);
      setRecords(nextRecords);
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
    const timeout = window.setTimeout(() => {
      loadData();
    }, 240);
    return () => window.clearTimeout(timeout);
  }, [query, sourceFilter, typeFilter, sentimentFilter]);

  const maxTrend = Math.max(1, ...overview.trend.map((item) => item.count));
  const negativeCount = overview.by_sentiment.negative ?? 0;
  const highSignal = overview.recent.filter((record) => record.sentiment === "negative" || record.intent === "purchase_signal");
  const readySources = sources.filter((source) => source.status === "ready").length;

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
          <button className={classNames("nav-item", activeView === "voice" && "active")} onClick={() => setActiveView("voice")}>
            <Inbox size={18} /> 用户之声
          </button>
          <button className={classNames("nav-item", activeView === "brands" && "active")} onClick={() => setActiveView("brands")}>
            <Building2 size={18} /> 品牌建档
          </button>
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
            <h1>{activeView === "overview" ? "经营信号总览" : activeView === "voice" ? "用户之声" : activeView === "brands" ? "品牌建档" : "数据源与模型"}</h1>
          </div>
          <div className="topbar-actions">
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

        {activeView === "voice" && (
          <div className="voice-layout">
            <section className="panel voice-panel">
              <div className="filter-bar">
                <div className="search-box">
                  <Search size={18} />
                  <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索品牌、竞品、评论内容" />
                </div>
                <Select icon={<PlugZap size={16} />} value={sourceFilter} onChange={setSourceFilter} options={sources.map((source) => ({ value: source.id, label: source.name }))} placeholder="全部来源" />
                <Select icon={<Filter size={16} />} value={typeFilter} onChange={setTypeFilter} options={DATA_TYPES} placeholder="全部类型" />
                <Select icon={<AlertTriangle size={16} />} value={sentimentFilter} onChange={setSentimentFilter} options={[
                  { value: "negative", label: "负向" },
                  { value: "neutral", label: "中性" },
                  { value: "positive", label: "正向" }
                ]} placeholder="全部情绪" />
              </div>

              <div className="record-list">
                {records.map((record) => (
                  <RecordRow key={record.id} record={record} />
                ))}
              </div>
            </section>

            <section className="panel input-panel">
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
        )}

        {activeView === "brands" && (
          <div className="brand-layout">
            <section className="panel brand-intake-panel">
              <div className="panel-title">
                <h2>创建监控对象</h2>
                <span>URL first</span>
              </div>
              <form className="brand-url-form" onSubmit={analyzeBrand}>
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

              <div className="onboarding-steps">
                <div>
                  <b>1</b>
                  <span>识别品牌、域名、Amazon 市场、ASIN 和官网平台链接</span>
                </div>
                <div>
                  <b>2</b>
                  <span>从公开元数据、页面链接和 JSON-LD 生成可编辑草稿</span>
                </div>
                <div>
                  <b>3</b>
                  <span>保存前提示疑似重复档案，避免同品牌拆成多份</span>
                </div>
              </div>
            </section>

            <section className="panel brand-review-panel">
              <div className="panel-title">
                <h2>审核与修改</h2>
                <span>{brandDraft?.confidence ? `${Math.round(brandDraft.confidence * 100)}% confidence` : "等待分析"}</span>
              </div>
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
                    <button className="secondary-button" type="button" onClick={() => setBrandDraft(null)}>
                      取消
                    </button>
                    <button className="primary-button" type="button" onClick={saveBrand} disabled={savingBrand}>
                      <Save size={18} /> {savingBrand ? "保存中" : brandDraft.id ? "保存修改" : "保存品牌档案"}
                    </button>
                  </div>
                </div>
              ) : (
                <div className="empty-state">
                  <Globe size={30} />
                  <strong>输入一个品牌官网或 Amazon 商品链接</strong>
                  <span>系统会先生成草稿，你确认后再入库。</span>
                </div>
              )}
            </section>

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

function Metric({ label, value, icon, tone }: { label: string; value: number; icon: ReactNode; tone: string }) {
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

function RecordRow({ record, compact = false }: { record: RecordItem; compact?: boolean }) {
  return (
    <article className={classNames("record-row", compact && "compact")}>
      <div className="record-main">
        <div className="record-meta">
          <span>{record.source_name}</span>
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
