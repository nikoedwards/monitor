import { useState } from "react";
import { useParams } from "react-router-dom";
import { SimpleLine, Bars } from "../components/charts";
import { Badge, Button, Card, EmptyState, Field, Input, Modal, SectionTitle, SegmentGroup, Select, Spinner, StatCard } from "../components/ui";
import { MonitorStatus } from "../components/MonitorStatus";
import { TimeRangePicker } from "../components/TimeRangePicker";
import { useListingAutomap, useListingHistory, useListingMutations, useProducts, useSalesChanges, useSalesListings, useSalesMetrics, useSalesMutations, useSalesSummary, useSettings } from "../lib/hooks";
import { useTimeRange } from "../lib/timeRange";
import type { Product, SalesListing } from "../lib/api";
import { CHANNEL_LABEL, fmtDate, fmtNum } from "../lib/format";

const CHANNELS = [
  { value: "all", label: "全部渠道" },
  { value: "amazon", label: "Amazon" },
  { value: "dtc", label: "独立站" },
  { value: "other_ecom", label: "其他电商" },
  { value: "offline", label: "线下" },
];

export default function Sales() {
  const { brandId } = useParams();
  const [view, setView] = useState<"global" | "channel">("global");
  const [channel, setChannel] = useState("all");
  const [productId, setProductId] = useState("");
  const [entryOpen, setEntryOpen] = useState(false);
  const [detailId, setDetailId] = useState<string | undefined>();

  const [range] = useTimeRange();
  const { data: products = [] } = useProducts(brandId);
  const { data: summary, isLoading, isError, error, refetch } = useSalesSummary(brandId, productId || undefined, range);

  if (isLoading) return <Spinner />;
  if (isError || !summary) {
    return (
      <Card>
        <EmptyState
          title="销售数据加载失败"
          hint={error instanceof Error ? error.message : "请稍后重试。"}
          action={<Button onClick={() => refetch()}>重新加载</Button>}
        />
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <SectionTitle
        title="销售监控"
        subtitle="配置店铺链接后自动展开 Listing 并每日采集；线下/竞品可人工录入"
        action={
          <div className="flex flex-wrap items-center gap-2">
            <TimeRangePicker />
            <MonitorStatus brandId={brandId} dimension="sales" />
            <Button variant="primary" onClick={() => setEntryOpen(true)}>+ 录入销售数据</Button>
          </div>
        }
      />

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label="累计销售额(估)" value={summary.revenue_points ? fmtNum(summary.total_revenue) : "—"} hint={!summary.revenue_points ? "渠道暂无销售额估算" : undefined} />
        <StatCard label="累计销量(估)" value={summary.units_points ? fmtNum(summary.total_units) : "—"} hint={!summary.units_points ? "渠道暂无销量估算" : undefined} />
        <StatCard label="监控 Listing" value={`${summary.monitored_listings} / ${summary.listing_total}`} tone="accent" />
        <StatCard label="数据点" value={summary.data_points} />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <SegmentGroup
          value={view}
          options={[{ value: "global", label: "全局维度" }, { value: "channel", label: "渠道维度" }]}
          onChange={setView}
        />
        <div className="flex items-center gap-2">
          <span className="text-[13px]" style={{ color: "var(--mute)" }}>按产品筛选</span>
          <Select value={productId} onChange={(e) => setProductId(e.target.value)}>
            <option value="">全部产品</option>
            {products.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </Select>
        </div>
      </div>

      {view === "global" ? (
        <GlobalView
          summary={summary}
          brandId={brandId!}
          productId={productId}
          onOpenListings={() => setView("channel")}
          onEntry={() => setEntryOpen(true)}
        />
      ) : (
        <ChannelView brandId={brandId!} channel={channel} setChannel={setChannel} productId={productId} products={products} onDetail={setDetailId} />
      )}

      <EntryModal open={entryOpen} onClose={() => setEntryOpen(false)} brandId={brandId!} products={products} />
      <ListingDetailModal listingId={detailId} onClose={() => setDetailId(undefined)} />
    </div>
  );
}

// --------------------------------------------------------------------- global
function GlobalView({
  summary, brandId, productId, onOpenListings, onEntry,
}: {
  summary: any;
  brandId: string;
  productId: string;
  onOpenListings: () => void;
  onEntry: () => void;
}) {
  const global = summary.global_metrics || {};
  const trend = summary.trend || [];
  const hasRevenue = Number(summary.revenue_points || 0) > 0;
  const hasUnits = Number(summary.units_points || 0) > 0;
  const hasRank = trend.some((p: any) => p.rank_avg != null);
  const hasRating = trend.some((p: any) => p.rating_avg != null);
  const hasReviews = trend.some((p: any) => p.review_count != null);
  // A scrape can produce rank/rating/review snapshots without a revenue
  // estimate. Keep the channel panel useful in that case by falling back to
  // snapshot counts instead of filtering every channel out.
  const channelBars = (summary.channels || [])
    .filter((c: any) => c.data_points > 0 || c.latest_listings > 0)
    .map((c: any) => ({ ...c, label: CHANNEL_LABEL[c.channel] || c.channel }));
  const channelDataKey = hasRevenue ? "revenue" : hasUnits ? "units" : "data_points";
  const channelDataName = hasRevenue ? "销售额" : hasUnits ? "销量" : "数据点";
  const salesTrendKey = hasRevenue ? "revenue" : "units";
  const salesTrendName = hasRevenue ? "销售额" : "销量";

  const delta = (value: number | null | undefined, suffix = "") => {
    if (value == null || value === 0) return "区间内暂无变化";
    return `${value > 0 ? "+" : ""}${fmtNum(value)}${suffix}`;
  };
  const rankDelta = (value: number | null | undefined) => {
    if (value == null) return "暂无可对比排名快照";
    if (value === 0) return "较区间起点暂无变化";
    return `较区间起点${value > 0 ? "提升" : "下降"} ${fmtNum(Math.abs(value))} 位`;
  };

  if (!summary.data_points) {
    const listings = Number(summary.listing_total || 0);
    const emptyTitle = productId
      ? "所选产品暂无销售数据"
      : listings ? "Listing 已就绪，等待首个销售快照" : "还没有销售数据";
    const emptyHint = productId
      ? listings
        ? `该产品已映射 ${fmtNum(listings)} 个 Listing，但所选时间范围内还没有快照。可检查监控状态或录入数据。`
        : "该产品还没有映射 Listing。可前往渠道维度完成映射，或直接录入数据。"
      : listings
        ? `已发现 ${fmtNum(listings)} 个 Listing。可前往渠道维度检查监控状态，或先录入线下/竞品数据。`
        : "在品牌管理配置店铺或商品链接以自动采集，也可以先录入线下/竞品数据。";
    return (
      <div className="space-y-4">
        <Card>
          <EmptyState
            title={emptyTitle}
            hint={emptyHint}
            action={(
              <div className="flex flex-wrap justify-center gap-2">
                <Button onClick={onOpenListings}>{listings ? "查看 Listing" : "前往渠道维度"}</Button>
                <Button variant="primary" onClick={onEntry}>录入销售数据</Button>
              </div>
            )}
          />
        </Card>
        <SalesChangeLog brandId={brandId} productId={productId} />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard
          label="最新平均排名"
          value={global.rank_avg != null ? `#${fmtNum(global.rank_avg)}` : "—"}
          hint={global.rank_avg != null ? rankDelta(global.rank_change) : "暂无排名快照"}
        />
        <StatCard
          label="最新平均评分"
          value={global.rating_avg != null ? global.rating_avg.toFixed(2) : "—"}
          hint={global.rating_change != null ? `较区间起点 ${delta(global.rating_change)}` : "暂无评分快照"}
        />
        <StatCard
          label="最新评论数"
          value={global.review_count != null ? fmtNum(global.review_count) : "—"}
          hint={global.review_count == null
            ? "暂无评论快照"
            : global.review_change > 0
              ? `区间新增 ${fmtNum(global.review_change)}`
              : global.review_change < 0
                ? `较区间起点减少 ${fmtNum(Math.abs(global.review_change))}`
                : "区间内暂无新增"}
        />
        <StatCard
          label="发生变更 Listing"
          value={fmtNum(global.changed_listings || 0)}
          hint={`共 ${fmtNum(global.changes || 0)} 项字段变化`}
          tone="accent"
        />
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card className="lg:col-span-2">
          <SectionTitle
            title={hasRevenue || hasUnits ? `${salesTrendName}趋势` : "商品指标趋势"}
            subtitle={!hasRevenue && !hasUnits ? "当前采集以排名、评分和评论为主，销量估算待渠道返回后展示" : undefined}
          />
          {trend.length && (hasRevenue || hasUnits) ? (
            <SimpleLine data={trend} dataKey={salesTrendKey} name={salesTrendName} color="var(--accent)" />
          ) : trend.length && (hasRank || hasRating || hasReviews) ? (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              {hasRank && <TrendMiniCard title="平均排名" data={trend} dataKey="rank_avg" color="var(--violet)" formatter={(v) => `#${fmtNum(v)}`} />}
              {hasRating && <TrendMiniCard title="平均评分" data={trend} dataKey="rating_avg" color="var(--accent)" formatter={(v) => Number(v).toFixed(2)} />}
              {hasReviews && <TrendMiniCard title="评论数" data={trend} dataKey="review_count" color="var(--warning)" formatter={(v) => fmtNum(v)} />}
            </div>
          ) : (
            <EmptyState title="暂无销售时序" hint="配置销售渠道链接或录入数据后展示。" />
          )}
        </Card>
        <Card>
          <SectionTitle title="渠道分布" />
          {channelBars.length ? (
            <Bars data={channelBars} dataKey={channelDataKey} nameKey="label" name={channelDataName} color="var(--violet)" />
          ) : (
            <p className="text-[13px]" style={{ color: "var(--mute)" }}>暂无渠道销售数据</p>
          )}
        </Card>
      </div>

      <Card>
        <SectionTitle title="产品维度总销量" subtitle="综合各渠道数据按产品聚合（链接需映射到产品）" />
        <ProductTable rows={summary.by_product || []} />
      </Card>

      <ManualPointsCard brandId={brandId} productId={productId} />
      <SalesChangeLog brandId={brandId} productId={productId} />
    </div>
  );
}

const CHANGE_LABELS: Record<string, string> = {
  rank: "排名", bsr: "BSR 排名", rating: "评分", review_count: "评论数", price: "价格",
  units_est: "销量估算", revenue_est: "销售额估算", in_stock: "库存状态", title: "标题",
  image_url: "主图", sku: "SKU", currency: "币种", listing_count: "Listing 数量",
};

function SalesChangeLog({ brandId, productId }: { brandId: string; productId: string }) {
  const [range] = useTimeRange();
  const { data, isLoading, isError, error, refetch } = useSalesChanges(brandId, productId || undefined, undefined, range);
  const events = data?.events || [];
  const daily = data?.daily || [];
  const fieldCounts = Object.entries(data?.field_counts || {});
  const eventListingCount = new Set(events.map((event) => event.listing_id).filter(Boolean)).size;
  return (
    <Card>
      <SectionTitle title="销售变化日志" subtitle={`${fmtDate(data?.range?.start || range.start_date)} ~ ${fmtDate(data?.range?.end || range.end_date)} · 记录 Listing、排名、评分、评论等字段变化`} />
      {isLoading ? <Spinner /> : isError ? (
        <EmptyState
          title="变化日志加载失败"
          hint={error instanceof Error ? error.message : "请稍后重试。"}
          action={<Button onClick={() => refetch()}>重新加载</Button>}
        />
      ) : <div className="space-y-4">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          <LogStat label="变化事件" value={fmtNum(data?.total_events || 0)} />
          <LogStat label="涉及 Listing" value={fmtNum(Math.max(data?.changed_listings || 0, eventListingCount))} />
          <LogStat label="采集天数" value={fmtNum(daily.length)} />
          <LogStat label="主要变化" value={fieldCounts.length ? `${CHANGE_LABELS[fieldCounts[0][0]] || fieldCounts[0][0]} ${fieldCounts[0][1]}` : "—"} />
        </div>
        {events.length ? <div className="space-y-2 max-h-80 overflow-y-auto pr-1">
          {events.map((event, index) => <div key={`${event.listing_id || "event"}-${event.date}-${index}`} className="rounded-md p-3" style={{ background: "var(--bg-soft-2)", border: "1px solid var(--hairline)" }}>
            <div className="flex flex-wrap items-center justify-between gap-2 mb-1">
              <div className="min-w-0 flex flex-wrap items-center gap-2"><span className="text-[12px]" style={{ color: "var(--mute)" }}>{fmtDate(event.date)}</span><span style={{ color: "var(--hairline-strong)" }}>·</span><span className="text-[13px] font-medium" style={{ color: "var(--ink)" }}>{event.listing_title || event.product_name || event.asin || "未命名 Listing"}</span>{event.event_type === "listing_added" && <Badge tone="positive">新增 Listing</Badge>}</div>
              <span className="text-[11px]" style={{ color: "var(--mute)" }}>{CHANNEL_LABEL[event.channel || ""] || event.channel || "—"}</span>
            </div>
            {event.event_type === "listing_added" ? (
              <span className="text-[12px]" style={{ color: "var(--body)" }}>已加入 Listing 监控记录</span>
            ) : (
              <div className="flex flex-wrap gap-x-4 gap-y-1">{event.changes.map((change, changeIndex) => <span key={`${change.field}-${changeIndex}`} className="text-[12px]" style={{ color: "var(--body)" }}><span className="font-medium">{CHANGE_LABELS[change.field] || change.field}</span>：<span style={{ color: "var(--mute)" }}>{formatChangeValue(change.from)}</span> → {formatChangeValue(change.to)}</span>)}</div>
            )}
          </div>)}
        </div> : <EmptyState title="该时间段暂无字段变化" hint="选择其他时间范围，或等待下一次采集形成对比快照。" />}
        {daily.length ? <div><div className="text-[12px] mb-2" style={{ color: "var(--mute)" }}>每日 Listing 采集量</div><div className="flex flex-wrap gap-2">{daily.slice(-14).map((point) => <div key={point.date} className="rounded px-2 py-1 text-[11px]" style={{ border: "1px solid var(--hairline)", color: "var(--body)" }}>{fmtDate(point.date)} <span className="tabular-nums">{point.listing_count}</span>{point.delta !== 0 && <span style={{ color: point.delta > 0 ? "var(--success, #2fa36b)" : "var(--error, #e05a5a)" }}> ({point.delta > 0 ? "+" : ""}{point.delta})</span>}</div>)}</div></div> : null}
      </div>}
    </Card>
  );
}

function LogStat({ label, value }: { label: string; value: string }) {
  return <div className="rounded-md px-3 py-2" style={{ background: "var(--bg-soft-2)" }}><div className="text-[11px]" style={{ color: "var(--mute)" }}>{label}</div><div className="text-[16px] tabular-nums" style={{ color: "var(--ink)" }}>{value}</div></div>;
}

function formatChangeValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "在售" : "缺货";
  if (typeof value === "number") return fmtNum(value);
  return String(value);
}

function TrendMiniCard({ title, data, dataKey, color, formatter }: { title: string; data: any[]; dataKey: string; color: string; formatter: (v: number) => string }) {
  return (
    <div className="rounded-md p-2" style={{ border: "1px solid var(--hairline)" }}>
      <div className="text-[12px] mb-1" style={{ color: "var(--mute)" }}>{title}</div>
      <SimpleLine data={data} dataKey={dataKey} name={title} color={color} />
      <div className="text-right text-[11px] tabular-nums" style={{ color: "var(--mute)" }}>
        {data[data.length - 1]?.[dataKey] != null ? formatter(data[data.length - 1][dataKey]) : "—"}
      </div>
    </div>
  );
}

function ProductTable({ rows }: { rows: any[] }) {
  if (!rows.length) return <EmptyState title="暂无产品销量" hint="采集 Listing 数据并将其映射到产品后在此聚合。" />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[13px]">
        <thead>
          <tr style={{ color: "var(--mute)", borderBottom: "1px solid var(--hairline)" }}>
            {["产品", "销量(估)", "销售额(估)", "平均排名", "平均评分", "评论数"].map((h) => (
              <th key={h} className="text-left font-medium py-2 px-2">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.product_id || "unmapped"} style={{ borderBottom: "1px solid var(--hairline)", color: "var(--body)" }}>
              <td className="py-2 px-2" style={{ color: r.product_id ? "var(--ink)" : "var(--mute)" }}>{r.product_name}</td>
              <td className="py-2 px-2 tabular-nums">{r.units_points ? fmtNum(r.units) : "—"}</td>
              <td className="py-2 px-2 tabular-nums">{r.revenue_points ? fmtNum(r.revenue) : "—"}</td>
              <td className="py-2 px-2 tabular-nums">{r.rank_avg != null ? `#${fmtNum(r.rank_avg)}` : "—"}</td>
              <td className="py-2 px-2 tabular-nums">{r.rating_avg != null ? Number(r.rating_avg).toFixed(2) : "—"}</td>
              <td className="py-2 px-2 tabular-nums">{r.review_count != null ? fmtNum(r.review_count) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ManualPointsCard({ brandId, productId }: { brandId: string; productId: string }) {
  const { data: metrics = [] } = useSalesMetrics(brandId, undefined, productId || undefined);
  const manual = metrics.filter((m) => m.source === "manual");
  const { remove } = useSalesMutations();
  if (!manual.length) return null;
  return (
    <Card>
      <SectionTitle title="人工录入数据点" subtitle="线下渠道与竞品销量的手动记录" />
      <div className="overflow-x-auto">
        <table className="w-full text-[13px]">
          <thead>
            <tr style={{ color: "var(--mute)", borderBottom: "1px solid var(--hairline)" }}>
              {["日期", "渠道", "平台", "价格", "销量", "销售额", ""].map((h) => (
                <th key={h} className="text-left font-medium py-2 px-2">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {manual.map((m) => (
              <tr key={m.id} style={{ borderBottom: "1px solid var(--hairline)", color: "var(--body)" }}>
                <td className="py-2 px-2">{fmtDate(m.snapshot_date)}</td>
                <td className="py-2 px-2">{CHANNEL_LABEL[m.channel] || m.channel}</td>
                <td className="py-2 px-2">{m.platform || "—"}</td>
                <td className="py-2 px-2 tabular-nums">{m.price ? `${m.currency || ""} ${m.price}` : "—"}</td>
                <td className="py-2 px-2 tabular-nums">{fmtNum(m.units_est)}</td>
                <td className="py-2 px-2 tabular-nums">{fmtNum(m.revenue_est)}</td>
                <td className="py-2 px-2"><button onClick={() => remove.mutate(m.id)} className="text-[12px] cursor-pointer" style={{ color: "var(--mute)" }}>删除</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

// -------------------------------------------------------------------- channel
function ChannelView({
  brandId, channel, setChannel, productId, products, onDetail,
}: {
  brandId: string; channel: string; setChannel: (v: string) => void; productId: string; products: Product[]; onDetail: (id: string) => void;
}) {
  const { data: listings = [], isLoading, isError, error, refetch } = useSalesListings(brandId, channel, productId || undefined);
  const { data: settings } = useSettings();
  const automap = useListingAutomap();
  const [mapMsg, setMapMsg] = useState<{ tone: "ok" | "err"; text: string } | null>(null);
  const unmapped = listings.filter((l) => !l.product_id).length;

  const runAutomap = async () => {
    setMapMsg(null);
    try {
      const res = await automap.mutateAsync({ brandId, channel });
      setMapMsg(
        res.candidates === 0
          ? { tone: "ok", text: "没有待映射的 Listing" }
          : { tone: "ok", text: `AI 已映射 ${res.mapped} / ${res.candidates} 个 Listing` },
      );
    } catch (e: any) {
      setMapMsg({ tone: "err", text: e?.message || "自动映射失败" });
    }
  };

  return (
    <Card>
      <SectionTitle
        title="Listing 列表"
        subtitle="每个链接的在售商品，可单独开关监控并映射到产品"
        action={
          <div className="flex flex-wrap items-center gap-2">
            {settings?.configured && (
              <Button onClick={runAutomap} disabled={automap.isPending || !products.length} title={!products.length ? "请先在品牌管理添加产品" : "用大模型按标题/ASIN 自动映射未映射的 Listing"}>
                {automap.isPending ? "AI 映射中…" : `AI 自动映射${unmapped ? ` (${unmapped})` : ""}`}
              </Button>
            )}
            <SegmentGroup value={channel} options={CHANNELS} onChange={setChannel} />
          </div>
        }
      />
      {mapMsg && (
        <p className="text-[12px] mb-2" style={{ color: mapMsg.tone === "err" ? "var(--error, #ee0000)" : "var(--mute)" }}>{mapMsg.text}</p>
      )}
      {isLoading ? <Spinner /> : isError ? (
        <EmptyState
          title="Listing 加载失败"
          hint={error instanceof Error ? error.message : "请稍后重试。"}
          action={<Button onClick={() => refetch()}>重新加载</Button>}
        />
      ) : <ListingTable listings={listings} products={products} onDetail={onDetail} />}
    </Card>
  );
}

function ListingTable({ listings, products, onDetail }: { listings: SalesListing[]; products: Product[]; onDetail: (id: string) => void }) {
  const { update, remove } = useListingMutations();
  if (!listings.length) {
    return <EmptyState title="暂无 Listing" hint="在品牌管理为该渠道配置店铺/单品链接，保存后会自动展开。" />;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[13px]">
        <thead>
          <tr style={{ color: "var(--mute)", borderBottom: "1px solid var(--hairline)" }}>
            {["商品", "渠道", "价格", "排名", "评分", "评论", "变更", "映射产品", "监控", ""].map((h) => (
              <th key={h} className="text-left font-medium py-2 px-2 whitespace-nowrap">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {listings.map((l) => {
            const m = l.latest;
            const changedToday = (m?.changes?.length || 0) > 0;
            return (
              <tr key={l.id} style={{ borderBottom: "1px solid var(--hairline)", color: "var(--body)" }}>
                <td className="py-2 px-2">
                  <div className="flex items-center gap-2 w-[280px]">
                    {l.image_url ? <img src={l.image_url} alt="" loading="lazy" onError={(e) => { e.currentTarget.style.visibility = "hidden"; }} className="h-8 w-8 rounded object-cover shrink-0" style={{ border: "1px solid var(--hairline)" }} /> : null}
                    <div className="min-w-0 flex-1">
                      <button onClick={() => onDetail(l.id)} className="text-left text-[13px] font-medium truncate block w-full cursor-pointer hover:underline" style={{ color: "var(--ink)" }}>
                        {l.title || l.asin || l.url || "(未抓取标题)"}
                      </button>
                      <div className="text-[11px] truncate" style={{ color: "var(--mute)" }}>{l.asin || l.sku || ""} {l.last_status && l.last_status !== "ok" ? `· ${l.last_status}` : ""}</div>
                    </div>
                  </div>
                </td>
                <td className="py-2 px-2 whitespace-nowrap">{CHANNEL_LABEL[l.channel] || l.channel}</td>
                <td className="py-2 px-2 tabular-nums whitespace-nowrap">{m?.price != null ? `${m.currency || ""} ${m.price}` : "—"}</td>
                <td className="py-2 px-2 tabular-nums">{m?.bsr != null ? `#${fmtNum(m.bsr)}` : (m?.rank != null ? `#${fmtNum(m.rank)}` : "—")}</td>
                <td className="py-2 px-2 tabular-nums">{m?.rating ?? "—"}</td>
                <td className="py-2 px-2 tabular-nums">{fmtNum(m?.review_count)}</td>
                <td className="py-2 px-2">{changedToday ? <Badge tone="warning">变更 {m!.changes!.length}</Badge> : l.has_change ? <span className="text-[11px]" style={{ color: "var(--mute)" }}>曾变更</span> : "—"}</td>
                <td className="py-2 px-2">
                  <Select value={l.product_id || ""} onChange={(e) => update.mutate({ id: l.id, product_id: e.target.value || null })} className="max-w-[140px]">
                    <option value="">未映射</option>
                    {products.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                  </Select>
                </td>
                <td className="py-2 px-2">
                  <button
                    onClick={() => update.mutate({ id: l.id, monitor: !l.monitor })}
                    className="text-[12px] px-2 py-1 rounded-md cursor-pointer whitespace-nowrap"
                    style={l.monitor ? { background: "rgba(0,112,243,0.12)", color: "var(--accent)" } : { color: "var(--mute)", border: "1px solid var(--hairline-strong)" }}
                  >
                    {l.monitor ? "监控中" : "已暂停"}
                  </button>
                </td>
                <td className="py-2 px-2 whitespace-nowrap">
                  <button onClick={() => onDetail(l.id)} className="text-[12px] cursor-pointer mr-2" style={{ color: "var(--accent)" }}>详情</button>
                  <button onClick={() => remove.mutate(l.id)} className="text-[12px] cursor-pointer" style={{ color: "var(--mute)" }}>删除</button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// --------------------------------------------------------------- detail modal
function ListingDetailModal({ listingId, onClose }: { listingId?: string; onClose: () => void }) {
  const { data, isLoading, isError, error, refetch } = useListingHistory(listingId);
  if (!listingId) return null;
  const listing: SalesListing | undefined = data?.listing;
  const metrics: any[] = data?.metrics || [];
  const changes: any[] = data?.changes || [];
  const series = metrics.map((m) => ({ date: m.snapshot_date, price: m.price, rank: m.bsr ?? m.rank, units: m.units_est }));
  const has = (k: string) => series.some((s) => s[k as keyof typeof s] != null);

  return (
    <Modal open={!!listingId} onClose={onClose} title={listing?.title || "Listing 详情"} width={720}>
      {isLoading ? (
        <Spinner />
      ) : isError || !data ? (
        <EmptyState
          title="Listing 详情加载失败"
          hint={error instanceof Error ? error.message : "请稍后重试。"}
          action={<Button onClick={() => refetch()}>重新加载</Button>}
        />
      ) : (
        <div className="space-y-4">
          <div className="flex items-center gap-3">
            {listing?.image_url ? <img src={listing.image_url} alt="" onError={(e) => { e.currentTarget.style.visibility = "hidden"; }} className="h-14 w-14 rounded object-cover" style={{ border: "1px solid var(--hairline)" }} /> : null}
            <div className="min-w-0">
              {listing?.url && <a href={listing.url} target="_blank" rel="noreferrer" className="text-[13px] hover:underline break-all" style={{ color: "var(--accent)" }}>{listing.url}</a>}
              <div className="text-[12px] mt-0.5" style={{ color: "var(--mute)" }}>
                {listing?.asin ? `ASIN ${listing.asin} · ` : ""}{CHANNEL_LABEL[listing?.channel || ""] || listing?.channel} · 数据点 {listing?.data_points}
                {listing?.last_status && listing.last_status !== "ok" ? ` · ${listing.last_status}` : ""}
              </div>
            </div>
          </div>

          {!metrics.length ? (
            <EmptyState title="暂无历史快照" hint="该 Listing 尚未采集到数据，或被反爬拦截。" />
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {has("price") && <DetailChart title="价格" data={series} dataKey="price" color="var(--accent)" />}
              {has("rank") && <DetailChart title="排名 (BSR)" data={series} dataKey="rank" color="var(--violet)" />}
              {has("units") && <DetailChart title="销量(估)" data={series} dataKey="units" color="var(--warning)" />}
            </div>
          )}

          <div>
            <div className="text-[13px] font-medium mb-2" style={{ color: "var(--ink)" }}>变更记录</div>
            {changes.length ? (
              <div className="space-y-2 max-h-48 overflow-y-auto">
                {changes.map((c, i) => (
                  <div key={i} className="text-[12px] p-2 rounded-md" style={{ background: "var(--bg-soft-2)" }}>
                    <span style={{ color: "var(--mute)" }}>{fmtDate(c.date)}</span>
                    {(c.changes || []).map((ch: any, j: number) => (
                      <div key={j} style={{ color: "var(--body)" }}>
                        <span className="font-medium">{CHANGE_LABELS[ch.field] || ch.field}</span>：<span style={{ color: "var(--mute)" }}>{formatChangeValue(ch.from)}</span> → {formatChangeValue(ch.to)}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-[12px]" style={{ color: "var(--mute)" }}>暂无变更记录</p>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}

function DetailChart({ title, data, dataKey, color }: { title: string; data: any[]; dataKey: string; color: string }) {
  return (
    <div className="rounded-md p-2" style={{ border: "1px solid var(--hairline)" }}>
      <div className="text-[12px] mb-1" style={{ color: "var(--mute)" }}>{title}</div>
      <SimpleLine data={data} dataKey={dataKey} name={title} color={color} />
    </div>
  );
}

// ---------------------------------------------------------------- entry modal
function EntryModal({ open, onClose, brandId, products }: { open: boolean; onClose: () => void; brandId: string; products: Product[] }) {
  const { add } = useSalesMutations();
  const [form, setForm] = useState<any>({ channel: "offline", currency: "USD" });
  const set = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }));
  return (
    <Modal open={open} onClose={onClose} title="录入销售数据">
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <Field label="日期"><Input type="date" value={form.snapshot_date || ""} onChange={(e) => set("snapshot_date", e.target.value)} /></Field>
          <Field label="渠道">
            <Select value={form.channel} onChange={(e) => set("channel", e.target.value)} className="w-full">
              {["amazon", "dtc", "other_ecom", "offline"].map((c) => <option key={c} value={c}>{CHANNEL_LABEL[c]}</option>)}
            </Select>
          </Field>
          <Field label="产品(可选)">
            <Select value={form.product_id || ""} onChange={(e) => set("product_id", e.target.value || undefined)} className="w-full">
              <option value="">未映射</option>
              {products.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </Select>
          </Field>
          <Field label="平台/门店"><Input value={form.platform || ""} onChange={(e) => set("platform", e.target.value)} /></Field>
          <Field label="价格"><Input type="number" value={form.price || ""} onChange={(e) => set("price", Number(e.target.value))} /></Field>
          <Field label="销量"><Input type="number" value={form.units_est || ""} onChange={(e) => set("units_est", Number(e.target.value))} /></Field>
          <Field label="销售额"><Input type="number" value={form.revenue_est || ""} onChange={(e) => set("revenue_est", Number(e.target.value))} /></Field>
        </div>
        <div className="flex justify-end gap-2 pt-2">
          <Button onClick={onClose}>取消</Button>
          <Button
            variant="primary"
            disabled={add.isPending}
            onClick={async () => {
              await add.mutateAsync({ ...form, brand_id: brandId, source: "manual" });
              setForm({ channel: "offline", currency: "USD" });
              onClose();
            }}
          >
            保存
          </Button>
        </div>
      </div>
    </Modal>
  );
}
