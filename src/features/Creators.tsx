import { useState } from "react";
import { useParams } from "react-router-dom";
import { RefreshCw, Sparkles } from "lucide-react";
import { TrendChart, Bars } from "../components/charts";
import { RecordList } from "../components/RecordList";
import { Badge, Button, Card, EmptyState, Input, SectionTitle, SegmentGroup, Spinner, StatCard } from "../components/ui";
import { TimeRangePicker } from "../components/TimeRangePicker";
import { useBrands, useCreatorsReport, useCreatorsRoster, useCreatorsSummary, useCreatorsSync, useRecords } from "../lib/hooks";
import { useTimeRange, rangeParams } from "../lib/timeRange";
import type { CreatorRosterItem } from "../lib/api";
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

export default function Creators() {
  const { brandId } = useParams();
  const [platform, setPlatform] = useState("all");
  const [query, setQuery] = useState("");
  const [report, setReport] = useState("");
  const [reportError, setReportError] = useState("");
  const plat = platform === "all" ? undefined : platform;

  const [range] = useTimeRange();
  const { data: brands = [] } = useBrands();
  const brand = brands.find((b) => b.id === brandId);
  const { data: summary, isLoading } = useCreatorsSummary(brandId, plat, range);
  const { data: roster = [] } = useCreatorsRoster(brandId, plat);
  const { data: records = [] } = useRecords({ brand_id: brandId, dimension: "marketing", channel: "creators", platform: plat, q: query || undefined, ...rangeParams(range), limit: 60 });
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
      setReport(await reportMut.mutateAsync(brandId));
    } catch (e: any) {
      setReportError(e?.message || "生成失败");
    }
  };

  return (
    <div className="space-y-6">
      <SectionTitle
        title="红人达人监控"
        subtitle="跨 Instagram / YouTube / TikTok / X 的合作红人监控、达人库与分析报告"
        action={
          <div className="flex flex-wrap items-center gap-2">
            <TimeRangePicker />
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

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <StatCard label="合作达人" value={fmtNum(t.creators)} tone="accent" />
        <StatCard label="合作内容" value={`${fmtNum(t.collab_posts)}/${fmtNum(t.posts)}`} hint="合作/总采集" />
        <StatCard label="付费内容" value={fmtNum(t.sponsored_posts)} />
        <StatCard label="总触达" value={fmtNum(t.total_views)} />
        <StatCard label="互动总量" value={fmtNum(t.total_engagement)} />
      </div>

      {empty ? (
        <EmptyState
          title="红人达人板块暂无数据"
          hint="YouTube 配置 youtube_api_key 后即可免费采集；Instagram / TikTok / X 需在设置中配置第三方源 token。配置后点「立即同步」。"
          action={<Button variant="primary" onClick={runSync} disabled={sync.isPending}>{sync.isPending ? "采集中…" : "立即同步"}</Button>}
        />
      ) : (
        <>
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
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
          </div>

          <Card>
            <SectionTitle
              title={brand?.is_competitor ? "竞品红人库" : "红人库"}
              subtitle="按合作内容聚合的达人；「重叠品牌」标记同时合作过其他品牌的达人"
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
              title="内容流"
              subtitle="按时间倒序的真实采集内容"
              action={<Input placeholder="搜索关键词 / 达人…" value={query} onChange={(e) => setQuery(e.target.value)} className="w-56" />}
            />
            <RecordList records={records} emptyHint="调整平台 / 关键词，或先发起一次采集。" />
          </Card>
        </>
      )}
    </div>
  );
}
