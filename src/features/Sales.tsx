import { useState } from "react";
import { useParams } from "react-router-dom";
import { SimpleLine, Bars } from "../components/charts";
import { Badge, Button, Card, EmptyState, Field, Input, Modal, SectionTitle, SegmentGroup, Select, Spinner, StatCard } from "../components/ui";
import { MonitorStatus } from "../components/MonitorStatus";
import { TimeRangePicker } from "../components/TimeRangePicker";
import { useListingAutomap, useListingHistory, useListingMutations, useProducts, useSalesListings, useSalesMetrics, useSalesMutations, useSalesSummary, useSalesSync, useSettings } from "../lib/hooks";
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
  const { data: summary, isLoading } = useSalesSummary(brandId, productId || undefined, range);
  const sync = useSalesSync();

  if (isLoading || !summary) return <Spinner />;

  return (
    <div className="space-y-6">
      <SectionTitle
        title="销售监控"
        subtitle="配置店铺链接后自动展开 Listing 并每日采集；线下/竞品可人工录入"
        action={
          <div className="flex flex-wrap items-center gap-2">
            <TimeRangePicker />
            <MonitorStatus brandId={brandId} dimension="sales" />
            <Button onClick={() => brandId && sync.mutate({ brandId })} disabled={sync.isPending}>{sync.isPending ? "同步中…" : "立即同步"}</Button>
            <Button variant="primary" onClick={() => setEntryOpen(true)}>+ 录入销售数据</Button>
          </div>
        }
      />

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label="累计销售额(估)" value={fmtNum(summary.total_revenue)} />
        <StatCard label="累计销量(估)" value={fmtNum(summary.total_units)} />
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
        <GlobalView summary={summary} brandId={brandId!} productId={productId} />
      ) : (
        <ChannelView brandId={brandId!} channel={channel} setChannel={setChannel} productId={productId} products={products} onDetail={setDetailId} />
      )}

      <EntryModal open={entryOpen} onClose={() => setEntryOpen(false)} brandId={brandId!} products={products} />
      <ListingDetailModal listingId={detailId} onClose={() => setDetailId(undefined)} />
    </div>
  );
}

// --------------------------------------------------------------------- global
function GlobalView({ summary, brandId, productId }: { summary: any; brandId: string; productId: string }) {
  const channelBars = (summary.channels || []).filter((c: any) => c.revenue > 0).map((c: any) => ({ ...c, label: CHANNEL_LABEL[c.channel] || c.channel }));
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card className="lg:col-span-2">
          <SectionTitle title="销售额趋势" />
          {summary.trend?.length ? (
            <SimpleLine data={summary.trend} dataKey="revenue" name="销售额" color="var(--accent)" />
          ) : (
            <EmptyState title="暂无销售时序" hint="配置销售渠道链接或录入数据后展示。" />
          )}
        </Card>
        <Card>
          <SectionTitle title="渠道分布" />
          {channelBars.length ? (
            <Bars data={channelBars} dataKey="revenue" nameKey="label" name="销售额" color="var(--violet)" />
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
            {["产品", "销量(估)", "销售额(估)"].map((h) => (
              <th key={h} className="text-left font-medium py-2 px-2">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.product_id || "unmapped"} style={{ borderBottom: "1px solid var(--hairline)", color: "var(--body)" }}>
              <td className="py-2 px-2" style={{ color: r.product_id ? "var(--ink)" : "var(--mute)" }}>{r.product_name}</td>
              <td className="py-2 px-2 tabular-nums">{fmtNum(r.units)}</td>
              <td className="py-2 px-2 tabular-nums">{fmtNum(r.revenue)}</td>
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
  const { data: listings = [], isLoading } = useSalesListings(brandId, channel, productId || undefined);
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
      {isLoading ? <Spinner /> : <ListingTable listings={listings} products={products} onDetail={onDetail} />}
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
  const { data, isLoading } = useListingHistory(listingId);
  if (!listingId) return null;
  const listing: SalesListing | undefined = data?.listing;
  const metrics: any[] = data?.metrics || [];
  const changes: any[] = data?.changes || [];
  const series = metrics.map((m) => ({ date: m.snapshot_date, price: m.price, rank: m.bsr ?? m.rank, units: m.units_est }));
  const has = (k: string) => series.some((s) => s[k as keyof typeof s] != null);

  return (
    <Modal open={!!listingId} onClose={onClose} title={listing?.title || "Listing 详情"} width={720}>
      {isLoading || !data ? (
        <Spinner />
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
                        <span className="font-medium">{ch.field}</span>：<span style={{ color: "var(--mute)" }}>{String(ch.from ?? "—")}</span> → {String(ch.to ?? "—")}
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
