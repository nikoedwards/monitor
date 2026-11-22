import { useState, type ReactNode } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, ChevronRight, ExternalLink } from "lucide-react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { TimeRangePicker } from "../components/TimeRangePicker";
import { Badge, Card, EmptyState, SectionTitle, Select, Spinner } from "../components/ui";
import type { AppMetric, AppObservation, BrandMetric, BrandObservations, MarketShareResponse } from "../lib/api";
import { useBrands, useMarketObservations } from "../lib/hooks";
import { useTimeRange, type TimeRange } from "../lib/timeRange";

const BRAND_METRICS: ReadonlyArray<{ key: BrandMetric; label: string }> = [
  { key: "app_store_ratings", label: "App Store 评分数" },
  { key: "google_play_ratings", label: "Google Play 全球评分数" },
  { key: "app_downloads", label: "实采下载 / 安装量" },
];
const APP_METRICS: ReadonlyArray<{ key: AppMetric; label: string }> = [
  { key: "rating_count", label: "累计评分数" },
  { key: "downloads", label: "实采下载 / 安装量" },
];
const AXIS_STYLE = { fontSize: 11, fill: "var(--mute)" };
const DAY_MS = 86_400_000;
const numberFormat = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 5 });
const regionNames = new Intl.DisplayNames(["zh-CN"], { type: "region", fallback: "none" });

function countryLabel(code: string) {
  const name = /^[a-z]{2}$/i.test(code) ? regionNames.of(code) : undefined;
  return name ? `${name}（${code}）` : code;
}

function numberText(value?: number | null) {
  return value == null ? "—" : numberFormat.format(value);
}

function appScope(app: AppObservation) {
  if (app.platform === "google_play") return "Google Play · 全球";
  return `App Store · ${app.scope ? countryLabel(app.scope) : "地区未记录"}`;
}

function ObservationTable({ headers, rows }: {
  headers: string[];
  rows: Array<{ key: string; values: ReactNode[]; selected?: boolean }>;
}) {
  return (
    <div className="overflow-auto max-h-[28rem]">
      <table className="w-full text-[13px] tabular-nums">
        <thead className="sticky top-0 z-10" style={{ background: "var(--panel)", color: "var(--mute)" }}>
          <tr>
            {headers.map((heading, index) => (
              <th key={heading} scope="col" className={`whitespace-nowrap font-medium px-3 py-2.5 ${index === 0 ? "text-left" : "text-right"}`} style={{ borderBottom: "1px solid var(--hairline)" }}>
                {heading}
              </th>
            ))}
          </tr>
        </thead>
        <tbody style={{ color: "var(--body)" }}>
          {rows.map(({ key, values, selected }) => (
            <tr key={key} style={{ borderBottom: "1px solid var(--hairline)", background: selected ? "var(--bg-soft-2)" : undefined }}>
              {values.map((value, index) => (
                <td key={headers[index]} className={`px-3 py-3 ${index === 0 ? "text-left" : "text-right whitespace-nowrap"}`}>{value}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ObservationTrend({ dates, metrics, hint }: {
  dates: string[];
  metrics: Array<{ key: string; label: string; values: Array<number | null | undefined> }>;
  hint: string;
}) {
  const [metricKey, setMetricKey] = useState("");
  const available = metrics.filter((metric) => metric.values.some((value) => value != null));
  const metric = available.find((item) => item.key === metricKey) ?? available[0];
  if (!metric) return null;
  const observations = dates.map((date, index) => ({
    time: Date.parse(date),
    value: metric.values[index] ?? null,
  })).reverse();
  const points = observations.flatMap((point, index) => {
    const lastDay = observations[index - 1]?.time;
    return point.time - lastDay > DAY_MS ? [{ time: lastDay + DAY_MS, value: null }, point] : [point];
  });
  const dateLabel = (value: number) => new Date(value).toISOString().slice(0, 10);
  return (
    <Card>
      <SectionTitle
        title="每日趋势"
        subtitle={hint}
        action={
          <Select value={metric.key} onChange={(event) => setMetricKey(event.target.value)} aria-label="趋势指标">
            {available.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}
          </Select>
        }
      />
      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={points} margin={{ top: 12, right: 16, left: 18, bottom: 4 }}>
          <CartesianGrid stroke="var(--hairline)" strokeDasharray="3 3" vertical={false} />
          <XAxis type="number" dataKey="time" scale="utc" domain={["dataMin", "dataMax"]} tickFormatter={(value) => dateLabel(value).slice(5)} tick={AXIS_STYLE} />
          <YAxis width={68} tickFormatter={numberText} tick={AXIS_STYLE} />
          <Tooltip labelFormatter={(value) => typeof value === "number" ? dateLabel(value) : value} formatter={(value) => numberText(Number(value))} />
          <Line dataKey="value" name={metric.label} stroke="var(--accent)" strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </Card>
  );
}

function RangeFilters({ countries }: { countries: string[] }) {
  const [params, setParams] = useSearchParams();
  const country = params.get("country") || "all";
  const options = [...new Set([...countries, ...(country !== "all" ? [country] : [])])].sort();
  return (
    <div className="flex flex-wrap items-center justify-end gap-2">
      <Select value={country} aria-label="国家" onChange={(event) => {
        const next = new URLSearchParams(params);
        if (event.target.value === "all") next.delete("country");
        else next.set("country", event.target.value);
        setParams(next, { replace: true });
      }}>
        <option value="all">全部地区</option>
        {options.map((code) => <option key={code} value={code}>{countryLabel(code)}</option>)}
      </Select>
      <TimeRangePicker />
    </div>
  );
}

export function MarketShareBreakdown({ brandIds, country, range, result }: {
  brandIds: string[];
  country: string;
  range: TimeRange;
  result?: MarketShareResponse;
}) {
  const { brandId } = useParams();
  const [params] = useSearchParams();
  const query = useMarketObservations(brandIds, country, range);
  const base = `/brand/${brandId}/market-share`;
  const observations = new Map(query.data?.brands.map((brand) => [brand.brand_id, brand.metrics]));
  const estimates = new Map(result?.brands.map((brand) => [brand.brand_id, brand]));
  const brands = result?.brands ?? query.data?.brands ?? [];
  const weights = result?.model.active_weights;
  const metrics = BRAND_METRICS.filter(({ key }) => {
    if (key === "google_play_ratings") return country === "all";
    if (key === "app_downloads") return Boolean(weights?.app || query.data?.brands.some((brand) => brand.metrics[key] != null));
    return true;
  });
  const components = weights && weights.app > 0 && weights.conversation > 0 ? [
    { key: "conversation", label: `评分份额（${Math.round(weights.conversation * 100)}% 权重）` },
    { key: "app", label: `下载份额（${Math.round(weights.app * 100)}% 权重）` },
  ] as const : [];
  return (
    <Card className="min-w-0">
      <SectionTitle
        title="份额拆解"
        subtitle="点击品牌查看每日统计和 App 明细。"
        hint="原始数据取所选期间内各 App 在对应商店和地区的最新快照。综合份额按当前模型权重计算，横线表示该项暂无数据。"
      />
      {query.isLoading && !brands.length && <Spinner />}
      {query.isError && <p role="status" className="mb-3 text-[13px]" style={{ color: "var(--danger)" }}>原始数据加载失败，{query.error.message}</p>}
      {brands.length > 0 && (
        <ObservationTable
          headers={["品牌", "综合份额", ...metrics.map(({ label }) => label), ...components.map(({ label }) => label), "覆盖度"]}
          rows={brands.map((brand) => {
            const entry = estimates.get(brand.brand_id);
            const raw = observations.get(brand.brand_id);
            return {
              key: brand.brand_id,
              values: [
                <Link to={{ pathname: `${base}/brands/${encodeURIComponent(brand.brand_id)}`, search: params.toString() }} className="inline-flex items-center gap-1 whitespace-nowrap font-semibold hover:underline" style={{ color: "var(--accent)" }}>
                  {brand.name} <ChevronRight size={14} />
                </Link>,
                entry ? <span className="font-semibold" style={{ color: "var(--accent)" }}>{entry.share.toFixed(2)}%</span> : "—",
                ...metrics.map(({ key }) => numberText(raw?.[key])),
                ...components.map(({ key }) => entry ? `${entry.signal_shares[key].toFixed(1)}%` : "—"),
                entry ? <Badge tone={entry.coverage >= 75 ? "positive" : entry.coverage >= 50 ? "warning" : "negative"}>{entry.coverage}%</Badge> : "—",
              ],
            };
          })}
        />
      )}
    </Card>
  );
}

export function MarketShareDetail({ view }: { view: "brand" | "apps" }) {
  const { brandId, detailBrandId } = useParams();
  const [params, setParams] = useSearchParams();
  const [range] = useTimeRange();
  const { data: brands = [], isLoading: brandsLoading, isError: brandsError } = useBrands();
  const country = params.get("country") || "all";
  const requestedBrand = params.get("brand") || brandId;
  const activeBrand = view === "brand" ? detailBrandId : brands.find((brand) => brand.id === requestedBrand)?.id ?? brands[0]?.id;
  const query = useMarketObservations(activeBrand ? [activeBrand] : [], country, range, true);
  const brand = query.data?.brands[0];
  const base = `/brand/${brandId}/market-share`;
  const filter = country === "all" ? "" : new URLSearchParams({ country }).toString();
  const appFilter = new URLSearchParams(filter);
  if (activeBrand) appFilter.set("brand", activeBrand);
  const daily = brand?.daily ?? [];
  const brandMetrics = BRAND_METRICS.filter(({ key }) => key !== "app_downloads" || daily.some(({ metrics }) => metrics[key] != null));

  return (
    <div className="space-y-5">
      <Link to={{ pathname: base, search: filter }} className="inline-flex items-center gap-1.5 text-[13px] hover:underline" style={{ color: "var(--accent)" }}>
        <ArrowLeft size={14} /> 份额拆解
      </Link>
      <SectionTitle
        title={view === "brand" ? (brand ? `${brand.name} · 品牌详情` : "品牌详情") : "App 明细"}
        action={<RangeFilters countries={query.data?.countries ?? []} />}
      />
      {view === "apps" && brands.length > 0 && (
        <Select aria-label="品牌" value={activeBrand} onChange={(event) => {
          const next = new URLSearchParams(params);
          next.set("brand", event.target.value);
          setParams(next);
        }}>
          {brands.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </Select>
      )}
      {(brandsLoading || query.isLoading) && <Spinner />}
      {(brandsError || query.isError) && <EmptyState title="明细加载失败" hint={query.error?.message} />}
      {view === "apps" && !brandsLoading && !brandsError && !activeBrand && <EmptyState title="暂无品牌" />}
      {view === "apps" && brand && <AppObservations key={brand.brand_id} apps={brand.apps ?? []} />}
      {view === "brand" && brand && (
        <>
          <ObservationTrend
            dates={daily.map(({ date }) => date)}
            metrics={brandMetrics.map(({ key, label }) => ({ key, label, values: daily.map(({ metrics }) => metrics[key]) }))}
            hint="仅绘制有采集记录的日期。每天参与汇总的 App 和商店可能不同。"
          />
          <Card>
            <SectionTitle title="每日统计" action={
              <Link to={{ pathname: `${base}/apps`, search: appFilter.toString() }} className="inline-flex items-center gap-1 text-[13px] hover:underline" style={{ color: "var(--accent)" }}>
                查看品牌 App 明细 <ChevronRight size={14} />
              </Link>
            } />
            {daily.length ? <ObservationTable
              headers={["日期", ...brandMetrics.map(({ label }) => label)]}
              rows={daily.map(({ date, metrics }) => ({
                key: date,
                values: [date, ...brandMetrics.map(({ key }) => numberText(metrics[key]))],
              }))}
            /> : <EmptyState title="所选期间暂无采集记录" hint="可调整日期或地区查看已有记录。" />}
          </Card>
        </>
      )}
    </div>
  );
}

function AppObservations({ apps }: { apps: NonNullable<BrandObservations["apps"]> }) {
  const [appKey, setAppKey] = useState("");
  const app = apps.find((item) => item.key === appKey) ?? apps[0];
  if (!app) return <EmptyState title="所选期间暂无 App 商店数据" hint="可切换品牌、调整日期或选择全部地区。" />;
  const latestMetrics = APP_METRICS.filter(({ key }) => key === "rating_count" || apps.some((item) => item[key] != null));
  const historyMetrics = APP_METRICS.filter(({ key }) => key === "rating_count" || app.daily.some((point) => point[key] != null));
  const sourceLink = (item: AppObservation) => item.url && /^https?:\/\//i.test(item.url) ? (
    <a href={item.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 hover:underline" style={{ color: "var(--accent)" }}>
      商店页面 <ExternalLink size={12} />
    </a>
  ) : "—";
  return (
    <>
      <Card>
        <SectionTitle title="应用商店数据" subtitle="每行展示所选期间内该 App 在对应商店口径下的最新采集值。点击应用查看每日记录。" />
        <ObservationTable
          headers={["应用", "商店 / 地区", ...latestMetrics.map(({ label }) => label), "采集日期", "来源"]}
          rows={apps.map((item) => ({
            key: item.key,
            selected: item.key === app.key,
            values: [
              <div className="min-w-40">
                <button type="button" aria-pressed={item.key === app.key} onClick={() => setAppKey(item.key)} className="text-left font-semibold cursor-pointer hover:underline" style={{ color: "var(--accent)" }}>{item.name}</button>
                <div className="text-[12px] break-all" style={{ color: "var(--mute)" }}>{item.app_id}</div>
              </div>,
              appScope(item),
              ...latestMetrics.map(({ key }) => numberText(item[key])),
              item.date,
              sourceLink(item),
            ],
          }))}
        />
      </Card>
      <ObservationTrend
        key={app.key}
        dates={app.daily.map(({ date }) => date)}
        metrics={historyMetrics.map(({ key, label }) => ({ key, label, values: app.daily.map((point) => point[key]) }))}
        hint={`${app.name} · ${appScope(app)}。图中标记对应实际采集日期。`}
      />
      <Card>
        <SectionTitle title="App 每日记录" subtitle={`${app.name} · ${appScope(app)}`} />
        <ObservationTable
          headers={["采集日期", ...historyMetrics.map(({ label }) => label), "来源"]}
          rows={app.daily.map((point) => ({
            key: point.date,
            values: [point.date, ...historyMetrics.map(({ key }) => numberText(point[key])), sourceLink(point)],
          }))}
        />
      </Card>
    </>
  );
}
