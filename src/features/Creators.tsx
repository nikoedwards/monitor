import { useState } from "react";
import { useParams } from "react-router-dom";
import { RefreshCw, Sparkles } from "lucide-react";
import { TrendChart, Bars } from "../components/charts";
import { RecordList } from "../components/RecordList";
import { Badge, Button, Card, EmptyState, Input, SectionTitle, SegmentGroup, Select, Spinner, StatCard } from "../components/ui";
import { TimeRangePicker } from "../components/TimeRangePicker";
import { useBrands, useCreatorsReport, useCreatorsRoster, useCreatorsSummary, useCreatorsSync, useProducts, useRecords } from "../lib/hooks";
import { useTimeRange, rangeParams } from "../lib/timeRange";
import type { CreatorMapPoint, CreatorRosterItem } from "../lib/api";
import { fmtDateTime, fmtNum } from "../lib/format";

const PLATFORMS = [
  { value: "all", label: "全部" },
  { value: "youtube", label: "YouTube" },
  { value: "instagram", label: "Instagram" },
  { value: "tiktok", label: "TikTok" },
  { value: "x", label: "X" },
];

const COLLAB_LABEL: Record<string, string> = {
  tag: "@官方账号",
  mention: "文案提及",
  hashtag: "广告标签",
  none: "未识别",
};

function CollaborationCell({ item }: { item: CreatorRosterItem }) {
  return (
    <div className="flex items-center gap-2 min-w-0">
      <div className="h-7 w-7 rounded-full grid place-items-center text-[11px] font-bold shrink-0" style={{ background: "var(--ink)", color: "var(--bg)" }}>
        {(item.name || item.handle || "?").slice(0, 1).toUpperCase()}
      </div>
      <div className="min-w-0">
        <div className="text-[13px] font-medium truncate" style={{ color: "var(--ink)" }}>
          {item.url ? <a href={item.url} target="_blank" rel="noreferrer" className="hover:underline">{item.name || item.handle}</a> : item.name || item.handle}
        </div>
        <div className="text-[11px] truncate" style={{ color: "var(--mute)" }}>{item.platform}</div>
      </div>
    </div>
  );
}

function RosterTable({ roster }: { roster: CreatorRosterItem[] }) {
  if (!roster.length) {
    return <EmptyState title="暂无红人" hint="发起采集后，按合作内容聚合的达人会出现在这里。" />;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[13px]" style={{ borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ color: "var(--mute)", borderBottom: "1px solid var(--hairline)" }}>
            <th className="text-left font-medium py-2 pr-3">达人</th>
            <th className="text-right font-medium py-2 px-3">粉丝</th>
            <th className="text-right font-medium py-2 px-3">合作内容</th>
            <th className="text-right font-medium py-2 px-3">付费</th>
            <th className="text-right font-medium py-2 px-3">互动总量</th>
            <th className="text-left font-medium py-2 px-3">最近合作</th>
            <th className="text-left font-medium py-2 pl-3">重叠品牌</th>
          </tr>
        </thead>
        <tbody>
          {roster.map((c) => (
            <tr key={c.id} style={{ borderBottom: "1px solid var(--hairline)" }}>
              <td className="py-2 pr-3 max-w-[220px]"><CollaborationCell item={c} /></td>
              <td className="text-right py-2 px-3 tabular-nums" style={{ color: "var(--body)" }}>{fmtNum(c.follower_count)}</td>
              <td className="text-right py-2 px-3 tabular-nums" style={{ color: "var(--ink)" }}>{c.collab_count}/{c.post_count}</td>
              <td className="text-right py-2 px-3 tabular-nums" style={{ color: "var(--body)" }}>{c.sponsored_count}</td>
              <td className="text-right py-2 px-3 tabular-nums" style={{ color: "var(--body)" }}>{fmtNum(c.total_engagement)}</td>
              <td className="py-2 px-3 whitespace-nowrap" style={{ color: "var(--mute)" }}>{c.last_collab_at ? fmtDateTime(c.last_collab_at) : "—"}</td>
              <td className="py-2 pl-3">
                <div className="flex gap-1 flex-wrap">
                  {(c.shared_brands || []).length ? (c.shared_brands || []).map((b) => <Badge key={b} tone="warning">{b}</Badge>) : <span style={{ color: "var(--mute)" }}>—</span>}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const QUADRANT_META = {
  core: { label: "核心伙伴", color: "var(--accent)" },
  potential: { label: "潜力黑马", color: "var(--violet)" },
  scale: { label: "铺量达人", color: "var(--warning)" },
  observe: { label: "观察池", color: "var(--mute)" },
} as const;

function CreatorQuadrantMap({ points, quadrants }: { points: CreatorMapPoint[]; quadrants: { key: keyof typeof QUADRANT_META; label: string; total: number }[] }) {
  if (!points.length) {
    return <EmptyState title="暂无可绘制达人" hint="当前范围内有红人内容后，会按合作深度和内容效果自动生成四象限。" />;
  }
  return (
    <div>
      <div className="relative h-[420px] ml-8 mb-8 rounded-lg" style={{ border: "1px solid var(--hairline-strong)", background: "var(--bg-soft)" }}>
        <div className="absolute inset-0 grid grid-cols-2 grid-rows-2 pointer-events-none">
          <div className="p-3 text-[12px] font-medium" style={{ color: "var(--violet)", borderRight: "1px dashed var(--hairline-strong)", borderBottom: "1px dashed var(--hairline-strong)" }}>潜力黑马</div>
          <div className="p-3 text-right text-[12px] font-medium" style={{ color: "var(--accent)", borderBottom: "1px dashed var(--hairline-strong)" }}>核心伙伴</div>
          <div className="p-3 self-end text-[12px] font-medium" style={{ color: "var(--mute)", borderRight: "1px dashed var(--hairline-strong)" }}>观察池</div>
          <div className="p-3 self-end text-right text-[12px] font-medium" style={{ color: "var(--warning)" }}>铺量达人</div>
        </div>
        {points.map((point) => {
          const meta = QUADRANT_META[point.quadrant];
          const bubbleStyle = {
            width: point.size,
            height: point.size,
            background: meta.color,
            color: "white",
            boxShadow: "0 0 0 3px var(--panel)",
          };
          const title = `${point.name} · ${point.platform}\n合作 ${point.collab_count}/${point.post_count} · 互动 ${fmtNum(point.total_engagement)} · 触达 ${fmtNum(point.total_views)}`;
          const node = (
            <span className="grid h-full w-full place-items-center rounded-full text-[10px] font-semibold" style={bubbleStyle}>
              {point.size >= 18 ? point.name.slice(0, 1).toUpperCase() : ""}
            </span>
          );
          return point.url ? (
            <a key={point.id} href={point.url} target="_blank" rel="noreferrer" aria-label={point.name} title={title} className="absolute -translate-x-1/2 translate-y-1/2 transition-transform hover:scale-125 focus:scale-125" style={{ left: `${point.x}%`, bottom: `${point.y}%` }}>
              {node}
            </a>
          ) : (
            <span key={point.id} title={title} className="absolute -translate-x-1/2 translate-y-1/2" style={{ left: `${point.x}%`, bottom: `${point.y}%` }}>
              <span className="grid place-items-center rounded-full text-[10px] font-semibold" style={bubbleStyle}>
                {point.size >= 18 ? point.name.slice(0, 1).toUpperCase() : ""}
              </span>
            </span>
          );
        })}
        <div className="absolute -bottom-7 left-1/2 -translate-x-1/2 whitespace-nowrap text-[12px]" style={{ color: "var(--mute)" }}>合作深度 →</div>
        <div className="absolute top-1/2 -left-16 -translate-y-1/2 -rotate-90 whitespace-nowrap text-[12px]" style={{ color: "var(--mute)" }}>内容效果 →</div>
      </div>
      <div className="flex flex-wrap gap-3 text-[12px]" style={{ color: "var(--body)" }}>
        {quadrants.map((item) => (
          <div key={item.key} className="flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-full" style={{ background: QUADRANT_META[item.key].color }} />
            {item.label} {item.total}
          </div>
        ))}
        <span style={{ color: "var(--mute)" }}>气泡越大，达人累计触达越高；悬停查看明细。</span>
      </div>
    </div>
  );
}

export default function Creators() {
  const { brandId } = useParams();
  const [platform, setPlatform] = useState("all");
  const [productId, setProductId] = useState("all");
  const [query, setQuery] = useState("");
  const [report, setReport] = useState("");
  const [reportError, setReportError] = useState("");
  const plat = platform === "all" ? undefined : platform;
  const selectedProductId = productId === "all" ? undefined : productId;

  const [range] = useTimeRange();
  const { data: brands = [] } = useBrands();
  const { data: products = [] } = useProducts(brandId);
  const brand = brands.find((b) => b.id === brandId);
  const selectedProduct = products.find((product) => product.id === selectedProductId);
  const { data: summary, isLoading } = useCreatorsSummary(brandId, plat, selectedProductId, range);
  const { data: roster = [] } = useCreatorsRoster(brandId, plat, selectedProductId);
  const { data: records = [] } = useRecords({ brand_id: brandId, product_id: selectedProductId, dimension: "marketing", channel: "creators", platform: plat, q: query || undefined, ...rangeParams(range), limit: 60 });
  const sync = useCreatorsSync();
  const reportMut = useCreatorsReport();

  if (isLoading || !summary) return <Spinner />;
  const t = summary.totals || {};
  const empty = (t.posts || 0) === 0;

  const runSync = async () => {
    if (!brandId) return;
    await sync.mutateAsync({ brandId, platform: plat });
  };

  const runReport = async () => {
    if (!brandId) return;
    setReportError("");
    try {
      setReport(await reportMut.mutateAsync({ brandId, productId: selectedProductId }));
    } catch (e: any) {
      setReportError(e?.message || "生成失败");
    }
  };

  return (
    <div className="space-y-6">
      <SectionTitle
        title="红人达人监控"
        subtitle={selectedProduct ? `当前查看：${brand?.name || "品牌"} / ${selectedProduct.name}` : "品牌整体红人盘点，并可下钻到单个产品"}
        action={
          <div className="flex flex-wrap items-center gap-2">
            <TimeRangePicker />
            <Select
              value={productId}
              onChange={(event) => {
                setProductId(event.target.value);
                setReport("");
                setReportError("");
              }}
              className="max-w-[190px]"
              aria-label="红人数据范围"
            >
              <option value="all">品牌整体</option>
              {products.map((product) => <option key={product.id} value={product.id}>{product.name}</option>)}
            </Select>
            <SegmentGroup value={platform} options={PLATFORMS} onChange={setPlatform} />
            <Button size="sm" onClick={runSync} disabled={sync.isPending}>
              <RefreshCw size={14} className={sync.isPending ? "animate-spin" : ""} /> {sync.isPending ? "采集中…" : "立即同步"}
            </Button>
          </div>
        }
      />

      {sync.data && (
        <div className="text-[13px] p-2 rounded-md" style={{ background: "var(--bg-soft-2)", color: "var(--body)" }}>
          {(sync.data.results || []).map((r: any) => `${r.platform}: ${r.status === "ok" ? `新增 ${r.created}` : r.error || r.status}`).join("　·　")}
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        <StatCard label="合作达人" value={fmtNum(t.creators)} tone="accent" />
        <StatCard label="合作内容" value={`${fmtNum(t.collab_posts)}/${fmtNum(t.posts)}`} hint="合作/总采集" />
        <StatCard label="付费内容" value={fmtNum(t.sponsored_posts)} />
        <StatCard label="总触达" value={fmtNum(t.total_views)} />
        <StatCard label="互动总量" value={fmtNum(t.total_engagement)} />
        <StatCard label="产品归因" value={`${Math.round(Number(t.product_match_coverage || 0) * 100)}%`} hint={selectedProduct ? "当前内容均命中该产品" : `未归因 ${fmtNum(t.unmapped_posts)}`} />
      </div>

      {empty ? (
        <EmptyState
          title={selectedProduct ? `${selectedProduct.name} 暂无红人数据` : "红人达人板块暂无数据"}
          hint={selectedProduct ? "先同步品牌数据；若已有内容仍未命中，请在产品备注中补充别名、型号或常用 Hashtag。" : "YouTube 配置免费 Data API key 后可做全站关键词发现；Instagram / TikTok 的免费自采目前用于品牌管理中已配置的公开官方账号。"}
          action={<Button variant="primary" onClick={runSync} disabled={sync.isPending}>{sync.isPending ? "采集中…" : "立即同步"}</Button>}
        />
      ) : (
        <>
          <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
            <Card className="lg:col-span-2">
              <SectionTitle title="声量趋势" />
              <TrendChart data={summary.trend} keys={[{ key: "total", name: "内容数", color: "var(--accent)" }, { key: "negative", name: "负向", color: "var(--danger)" }]} />
            </Card>
            <Card>
              <SectionTitle title="平台分布" />
              {summary.by_platform?.length ? (
                <Bars data={summary.by_platform} dataKey="total" nameKey="label" name="内容数" color="var(--violet)" />
              ) : (
                <p className="text-[13px]" style={{ color: "var(--mute)" }}>暂无平台数据</p>
              )}
            </Card>
            <Card>
              <SectionTitle title="产品分布" subtitle={selectedProduct ? "同一内容可能同时命中多个产品" : "品牌红人内容的产品归因"} />
              {summary.by_product?.length ? (
                <Bars data={summary.by_product.slice(0, 8)} dataKey="total" nameKey="name" name="内容数" color="var(--accent)" />
              ) : (
                <p className="text-[13px]" style={{ color: "var(--mute)" }}>暂无产品归因；可在品牌管理中补充产品、SKU 和别名。</p>
              )}
            </Card>
          </div>

          <Card>
            <SectionTitle
              title={selectedProduct ? `${selectedProduct.name} 红人四象限` : "品牌红人四象限"}
              subtitle="横轴为合作深度，纵轴为内容效果；按当前时间范围和平台实时计算"
            />
            <CreatorQuadrantMap points={summary.creator_map?.points || []} quadrants={summary.creator_map?.quadrants || []} />
          </Card>

          <Card>
            <SectionTitle
              title={selectedProduct ? `${selectedProduct.name} 红人库` : brand?.is_competitor ? "竞品红人库" : "红人库"}
              subtitle={selectedProduct ? "仅聚合明确归因到该产品的内容；「重叠品牌」标记达人也合作过其他品牌" : "按合作内容聚合的达人；「重叠品牌」标记同时合作过其他品牌的达人"}
              action={
                <Button size="sm" variant="primary" onClick={runReport} disabled={reportMut.isPending}>
                  <Sparkles size={14} /> {reportMut.isPending ? "分析中…" : "生成分析报告"}
                </Button>
              }
            />
            {reportError && <div className="text-[13px] p-2 mb-3 rounded-md" style={{ background: "var(--danger-soft)", color: "var(--danger)" }}>{reportError}</div>}
            {report && (
              <div className="text-[13px] p-3 mb-3 rounded-md whitespace-pre-wrap" style={{ background: "var(--bg-soft)", color: "var(--body)", border: "1px solid var(--hairline)" }}>
                {report}
              </div>
            )}
            <RosterTable roster={roster} />
          </Card>

          <Card>
            <SectionTitle
              title={selectedProduct ? `${selectedProduct.name} 内容流` : "内容流"}
              subtitle={selectedProduct ? "按产品归因筛选后的真实采集内容" : "按时间倒序的真实采集内容"}
              action={<Input placeholder="搜索关键词 / 达人…" value={query} onChange={(e) => setQuery(e.target.value)} className="w-56" />}
            />
            <RecordList records={records} emptyHint="调整平台 / 关键词，或先发起一次采集。" />
          </Card>
        </>
      )}
    </div>
  );
}
