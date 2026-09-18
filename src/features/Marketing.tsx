import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { TrendChart, Bars } from "../components/charts";
import { RecordList } from "../components/RecordList";
import { Badge, Button, Card, EmptyState, InfoHint, Modal, SectionTitle, SegmentGroup, Spinner, StatCard } from "../components/ui";
import { MonitorStatus } from "../components/MonitorStatus";
import { TimeRangePicker } from "../components/TimeRangePicker";
import { SmartSummary } from "../components/SmartSummary";
import { useAds, useAdsSummary, useMarketingSummary, useRecords } from "../lib/hooks";
import type { MarketingAd, MarketingAdsSummary } from "../lib/api";
import { useTimeRange, rangeParams } from "../lib/timeRange";
import { CHANNEL_LABEL, fmtNum } from "../lib/format";

const CHANNELS = [
  { value: "ads", label: "广告投放" },
  { value: "creators", label: "红人达人" },
  { value: "media", label: "媒体公关" },
  { value: "social", label: "社交媒体" },
  { value: "community", label: "社群" },
];

const SOURCE_LABEL: Record<string, string> = {
  reddit_search: "Reddit",
  community_site: "自建社群/论坛",
  google_news: "Google News",
  meta_ads: "Meta 广告库",
  youtube_search: "YouTube",
  discord_community: "Discord",
  facebook_groups: "Facebook 群组",
  manual_csv: "手动录入",
};

const TIER_LABEL: Record<string, string> = {
  tier_1: "一线媒体",
  tier_2: "二线媒体",
  tier_3: "三线媒体",
  tier_4: "长尾媒体",
  wire: "通讯社/发稿",
  unknown: "未知",
};

const PLATFORM_LABEL: Record<string, string> = {
  reddit: "Reddit",
  discourse: "论坛 (Discourse)",
  frill: "反馈站 (Frill)",
  forum: "论坛 / RSS",
  discord: "Discord",
  facebook_group: "Facebook 群组",
  telegram: "Telegram",
  quora: "Quora",
};

const COMMUNITY_CRAWL_NOTE = (
  <div className="space-y-1.5">
    <div className="font-medium" style={{ color: "var(--ink)" }}>社群数据来源与采集逻辑</div>
    <div><span style={{ color: "var(--mute)" }}>Reddit：</span>按品牌关键词全站搜索 + 指定 subreddit 抓帖子，JSON 失败自动回退公开 RSS。</div>
    <div><span style={{ color: "var(--mute)" }}>自建社群：</span>Discourse 取帖子与回复；Frill 等反馈站从页面内嵌数据取功能建议（帖子）；其余尝试 RSS，最后才退化为页面快照。</div>
    <div><span style={{ color: "var(--mute)" }}>Discord / FB 群 / Telegram：</span>多需登录或特权令牌，列为阶段二。</div>
    <div style={{ color: "var(--mute)" }}>「回复」以加粗左边框标记，可在内容流中区分帖子与回复。</div>
  </div>
);

export default function Marketing() {
  const { brandId } = useParams();
  const [range] = useTimeRange();
  const [view, setView] = useState<"overview" | "channel">("overview");
  const [channel, setChannel] = useState("community");
  const [publicationDetailOpen, setPublicationDetailOpen] = useState(false);
  const [trendMetric, setTrendMetric] = useState<"volume" | "reach">("volume");
  const activeChannel = view === "channel" ? channel : undefined;
  const { data: summary, isLoading } = useMarketingSummary(brandId, activeChannel, range);
  const { data: records = [] } = useRecords({ brand_id: brandId, dimension: "marketing", channel: activeChannel, ...rangeParams(range), limit: 60 });

  const [hidden, setHidden] = useState<Set<string>>(new Set());
  useEffect(() => {
    try {
      setHidden(new Set(JSON.parse(localStorage.getItem(`monitor.hiddenCommunity.${brandId}`) || "[]")));
    } catch {
      setHidden(new Set());
    }
  }, [brandId]);
  const toggleHidden = (platform: string) => {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(platform)) next.delete(platform);
      else next.add(platform);
      localStorage.setItem(`monitor.hiddenCommunity.${brandId}`, JSON.stringify([...next]));
      return next;
    });
  };

  const isAds = view === "channel" && channel === "ads";
  const selectedSection = view === "overview" ? "overview" : channel;
  if (isAds) {
    return <AdsChannel brandId={brandId} range={range} selectedSection={selectedSection} onSectionChange={(value) => {
      if (value === "overview") {
        setView("overview");
      } else {
        setChannel(value);
        setView("channel");
      }
    }} />;
  }
  if (isLoading || !summary) return <Spinner />;

  const channelName = CHANNEL_LABEL[channel] || channel;
  const isCommunity = view === "channel" && channel === "community";
  const subchannelCount = (summary.by_subchannel || []).reduce((acc: number, g: any) => acc + (g.subchannels?.length || 0), 0);
  const shownRecords = isCommunity ? records.filter((r) => !hidden.has(r.platform || "")) : records;

  return (
    <div className="space-y-6">
      <SectionTitle
        title="营销监控"
        subtitle="媒体公关、广告、红人、社群与社交声量"
        action={<div className="flex flex-wrap items-center gap-2"><TimeRangePicker /><MonitorStatus brandId={brandId} dimension="marketing" /></div>}
      />

      <div className="flex flex-wrap items-center gap-3">
        <SegmentGroup
          value={selectedSection}
          options={[{ value: "overview", label: "总览" }, ...CHANNELS]}
          onChange={(value) => {
            if (value === "overview") {
              setView("overview");
            } else {
              setChannel(value);
              setView("channel");
            }
          }}
        />
      </div>

      {isCommunity ? (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatCard label="新增帖子" value={fmtNum(summary.posts)} tone="accent" />
          <StatCard label="新增回复" value={fmtNum(summary.replies)} />
          <StatCard label="子渠道数" value={subchannelCount} hint="如多个 subreddit / 自建站" />
          <StatCard label="数据源" value={summary.by_source?.length || 0} />
        </div>
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatCard label={view === "channel" ? `${channelName}声量` : "营销声量"} value={fmtNum(summary.total)} />
          <StatCard label="覆盖渠道" value={summary.by_channel?.length || 0} tone="accent" />
          <StatCard label="覆盖平台" value={summary.by_platform?.length || 0} />
          <StatCard label="数据源" value={summary.by_source?.length || 0} />
        </div>
      )}

      {summary.total_reach > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatCard label="预估总触达" value={fmtNum(summary.total_reach)} tone="accent" hint="按媒体月流量估算" />
          <StatCard label="媒体价值 AVE" value={`$${fmtNum(summary.total_ave)}`} hint="等价广告价值(估算)" />
          <StatCard label="媒体层级" value={summary.by_tier?.length || 0} />
          <StatCard label="覆盖国家" value={summary.by_country?.length || 0} />
        </div>
      )}

      {summary.total_reach > 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Card>
            <SectionTitle title="媒体层级分布" subtitle="按预估影响力分级的报道数" />
            {summary.by_tier?.length ? (
              <Bars
                data={summary.by_tier.map((t: any) => ({ ...t, label: TIER_LABEL[t.tier] || t.tier }))}
                dataKey="total"
                nameKey="label"
                name="报道数"
                color="var(--violet)"
              />
            ) : (
              <p className="text-[13px]" style={{ color: "var(--mute)" }}>暂无层级数据</p>
            )}
          </Card>
          <Card>
            <SectionTitle
              title="声量占比 SOV"
              subtitle="各媒体声量份额(Top 8)"
              hint="SOV（Share of Voice，声量占比）表示某个媒体的报道声量占全部媒体报道声量的比例。"
              action={summary.by_publication?.length ? <Button size="sm" onClick={() => setPublicationDetailOpen(true)}>查看明细</Button> : undefined}
            />
            <ShareOfVoice items={summary.share_of_voice || []} />
          </Card>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card className="lg:col-span-2">
          <SectionTitle
            title={trendMetric === "volume" ? "声量趋势" : "预计曝光趋势"}
            action={summary.total_reach > 0 ? (
              <SegmentGroup
                value={trendMetric}
                options={[{ value: "volume", label: "篇数" }, { value: "reach", label: "预计曝光" }]}
                onChange={setTrendMetric}
              />
            ) : undefined}
          />
          {trendMetric === "volume" ? (
            <TrendChart data={summary.trend} keys={[{ key: "total", name: "声量", color: "var(--accent)" }, { key: "negative", name: "负向", color: "var(--danger)" }]} />
          ) : (
            <TrendChart
              data={summary.trend}
              keys={[{ key: "estimated_reach", name: "预计曝光", color: "var(--violet)" }]}
              valueFormatter={fmtNum}
            />
          )}
        </Card>
        <Card>
          <SectionTitle
            title={view === "channel" ? "来源渠道明细" : "平台分布"}
            subtitle={view === "channel" ? "该渠道下各数据源的采集量" : undefined}
          />
          {view === "channel" ? (
            <SourceBreakdown sources={summary.by_source || []} />
          ) : summary.by_platform?.length ? (
            <Bars data={summary.by_platform.slice(0, 8)} dataKey="total" nameKey="platform" name="声量" color="var(--violet)" />
          ) : (
            <p className="text-[13px]" style={{ color: "var(--mute)" }}>暂无平台数据</p>
          )}
        </Card>
      </div>

      {view === "overview" && (
        <Card>
          <SectionTitle title="渠道分布" subtitle="各营销渠道声量占比" />
          {summary.by_channel?.length ? (
            <Bars
              data={summary.by_channel.map((c: any) => ({ ...c, label: CHANNEL_LABEL[c.channel] || c.channel }))}
              dataKey="total"
              nameKey="label"
              name="声量"
              color="var(--accent)"
            />
          ) : (
            <p className="text-[13px]" style={{ color: "var(--mute)" }}>暂无渠道数据</p>
          )}
        </Card>
      )}

      {isCommunity && (
        <Card>
          <SectionTitle
            title="社群子渠道明细"
            subtitle="按平台与具体来源（subreddit / 自建站）拆分的帖子与回复"
            action={<InfoHint text={COMMUNITY_CRAWL_NOTE} />}
          />
          <CommunityBreakdown groups={summary.by_subchannel || []} hidden={hidden} onToggle={toggleHidden} />
        </Card>
      )}

      <Card>
        <SectionTitle title="智能总结" subtitle="基于所选时间范围与渠道的记录，由大模型生成要点 / 情绪 / 代表内容" />
        <SmartSummary brandId={brandId} dimension="marketing" channel={activeChannel} range={range} />
      </Card>

      <Card>
        <SectionTitle title={view === "channel" ? `${channelName}内容流` : "营销内容流"} subtitle={isCommunity ? "勾选上方来源可在此显示/隐藏对应内容" : "按渠道筛选的真实采集内容"} />
        <RecordList records={shownRecords} variant={channel === "social" || channel === "creators" ? "social-cards" : "list"} emptyHint="在数据源页发起媒体 / 广告 / 红人 / 社群采集后查看。" />
      </Card>

      <PublicationDetailModal
        open={publicationDetailOpen}
        onClose={() => setPublicationDetailOpen(false)}
        publications={summary.by_publication || []}
      />
    </div>
  );
}

type PublicationStat = {
  name: string;
  domain: string;
  total: number;
  monthly_traffic: number;
  authority: number;
  tier: string;
  country: string;
};

function PublicationDetailModal({ open, onClose, publications }: { open: boolean; onClose: () => void; publications: PublicationStat[] }) {
  const [ranking, setRanking] = useState<"frequency" | "reach">("frequency");
  const ranked = [...publications].sort((a, b) => ranking === "frequency" ? b.total - a.total : b.monthly_traffic - a.monthly_traffic);
  const max = Math.max(...ranked.map((item) => ranking === "frequency" ? item.total : item.monthly_traffic), 1);
  return (
    <Modal open={open} onClose={onClose} title="媒体来源排行" width={760}>
      <div className="flex items-center justify-between gap-3 flex-wrap mb-4">
        <SegmentGroup
          value={ranking}
          options={[{ value: "frequency", label: "发文频率排行" }, { value: "reach", label: "媒体体量排行" }]}
          onChange={setRanking}
        />
        <span className="text-[12px]" style={{ color: "var(--mute)" }}>
          {ranking === "frequency" ? "按当前时间范围内收录文章数排序" : "按预估月访问量排序，数据为媒体库估算值"}
        </span>
      </div>
      {ranked.length ? (
        <div className="space-y-3 max-h-[60vh] overflow-y-auto pr-1">
          {ranked.map((item, index) => {
            const value = ranking === "frequency" ? item.total : item.monthly_traffic;
            return (
              <div key={item.domain || item.name}>
                <div className="flex items-center justify-between gap-4 text-[13px] mb-1.5">
                  <div className="min-w-0 flex items-center gap-2">
                    <span className="w-5 text-right tabular-nums shrink-0" style={{ color: "var(--mute)" }}>{index + 1}</span>
                    {item.domain && <img src={`https://www.google.com/s2/favicons?domain=${item.domain}&sz=32`} alt="" className="w-4 h-4 rounded-sm shrink-0" />}
                    <div className="min-w-0">
                      <div className="truncate font-medium" style={{ color: "var(--ink)" }}>{item.name}</div>
                      {item.domain && <div className="truncate text-[11px]" style={{ color: "var(--mute)" }}>{item.domain}</div>}
                    </div>
                  </div>
                  <div className="text-right shrink-0">
                    <div className="tabular-nums font-medium" style={{ color: "var(--ink)" }}>{ranking === "frequency" ? `${fmtNum(value)} 篇` : fmtNum(value)}</div>
                    <div className="text-[11px]" style={{ color: "var(--mute)" }}>{TIER_LABEL[item.tier] || item.tier}{item.authority ? ` · 权威度 ${item.authority}` : ""}</div>
                  </div>
                </div>
                <div className="ml-7 h-1.5 rounded-full overflow-hidden" style={{ background: "var(--bg-soft-2)" }}>
                  <div className="h-full rounded-full" style={{ width: `${(value / max) * 100}%`, background: ranking === "frequency" ? "var(--accent)" : "var(--violet)" }} />
                </div>
              </div>
            );
          })}
        </div>
      ) : <EmptyState title="暂无媒体数据" hint="当前时间范围内尚未收录媒体报道。" />}
    </Modal>
  );
}

function ShareOfVoice({ items }: { items: { platform: string; total: number; share: number }[] }) {
  if (!items.length) {
    return <p className="text-[13px]" style={{ color: "var(--mute)" }}>暂无声量数据</p>;
  }
  return (
    <div className="space-y-2.5">
      {items.map((s) => (
        <div key={s.platform}>
          <div className="flex items-center justify-between text-[13px] mb-1">
            <span className="truncate pr-2" style={{ color: "var(--ink)" }}>{s.platform}</span>
            <span className="tabular-nums shrink-0" style={{ color: "var(--mute)" }}>{(s.share * 100).toFixed(1)}% · {fmtNum(s.total)}</span>
          </div>
          <div className="h-1.5 rounded-full overflow-hidden" style={{ background: "var(--bg-soft-2)" }}>
            <div className="h-full rounded-full" style={{ width: `${s.share * 100}%`, background: "var(--violet)" }} />
          </div>
        </div>
      ))}
    </div>
  );
}

type SubChannel = { key: string; total: number; posts: number; replies: number };
type SubGroup = { platform: string; total: number; posts: number; replies: number; subchannels: SubChannel[] };

function CommunityBreakdown({ groups, hidden, onToggle }: { groups: SubGroup[]; hidden: Set<string>; onToggle: (platform: string) => void }) {
  if (!groups.length) {
    return <EmptyState title="暂无社群数据" hint="在品牌管理配置 Reddit / 自建社群链接后，发起一次采集即可查看。" />;
  }
  return (
    <div className="space-y-4">
      {groups.map((g) => {
        const max = Math.max(...g.subchannels.map((s) => s.total), 1);
        const isHidden = hidden.has(g.platform);
        return (
          <div key={g.platform} style={isHidden ? { opacity: 0.45 } : undefined}>
            <div className="flex items-center justify-between text-[13px] mb-2">
              <label className="flex items-center gap-2 cursor-pointer select-none">
                <input type="checkbox" checked={!isHidden} onChange={() => onToggle(g.platform)} className="cursor-pointer" />
                <span className="font-medium" style={{ color: "var(--ink)" }}>{PLATFORM_LABEL[g.platform] || g.platform}</span>
              </label>
              <span className="tabular-nums" style={{ color: "var(--mute)" }}>帖子 {fmtNum(g.posts)} · 回复 {fmtNum(g.replies)} · 共 {fmtNum(g.total)}</span>
            </div>
            <div className="space-y-2 pl-3" style={{ borderLeft: "1px solid var(--hairline)" }}>
              {g.subchannels.map((s) => (
                <div key={s.key}>
                  <div className="flex items-center justify-between text-[12px] mb-1">
                    <span className="truncate pr-2" style={{ color: "var(--body)" }}>{s.key}</span>
                    <span className="tabular-nums shrink-0" style={{ color: "var(--mute)" }}>帖 {fmtNum(s.posts)} · 回 {fmtNum(s.replies)}</span>
                  </div>
                  <div className="h-1.5 rounded-full overflow-hidden" style={{ background: "var(--bg-soft-2)" }}>
                    <div className="h-full rounded-full" style={{ width: `${(s.total / max) * 100}%`, background: "var(--accent)" }} />
                  </div>
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function SourceBreakdown({ sources }: { sources: { source_id: string; total: number }[] }) {
  if (!sources.length) {
    return <EmptyState title="暂无数据源" hint="该渠道尚未采集到数据，配置链接或手动刷新后查看。" />;
  }
  const max = Math.max(...sources.map((s) => s.total), 1);
  return (
    <div className="space-y-2.5">
      {sources.map((s) => (
        <div key={s.source_id}>
          <div className="flex items-center justify-between text-[13px] mb-1">
            <span style={{ color: "var(--ink)" }}>{SOURCE_LABEL[s.source_id] || s.source_id}</span>
            <span className="tabular-nums" style={{ color: "var(--mute)" }}>{fmtNum(s.total)}</span>
          </div>
          <div className="h-1.5 rounded-full overflow-hidden" style={{ background: "var(--bg-soft-2)" }}>
            <div className="h-full rounded-full" style={{ width: `${(s.total / max) * 100}%`, background: "var(--accent)" }} />
          </div>
        </div>
      ))}
    </div>
  );
}

type AdsChannelProps = {
  brandId?: string;
  range: ReturnType<typeof useTimeRange>[0];
  selectedSection: string;
  onSectionChange: (value: string) => void;
};

const AD_STATUS_OPTIONS = [
  { value: "all", label: "全部" },
  { value: "active", label: "活跃" },
  { value: "new", label: "新发现" },
  { value: "stopped", label: "已停止" },
];

const AD_SOURCE_LABEL: Record<string, string> = {
  meta: "Meta Ad Library",
  meta_ads: "Meta Ad Library",
  google: "Google Ads Transparency",
  google_ads: "Google Ads Transparency",
  manual: "手动导入",
};

function AdsChannel({ brandId, range, selectedSection, onSectionChange }: AdsChannelProps) {
  const [status, setStatus] = useState("all");
  const [source, setSource] = useState("all");
  const [sort, setSort] = useState<"duration" | "recent" | "score">("duration");
  const [selectedAd, setSelectedAd] = useState<MarketingAd | null>(null);
  const { data: summary, isLoading: summaryLoading } = useAdsSummary(brandId, range);
  const { data: fetchedAds = [], isLoading: adsLoading } = useAds(brandId, range, {
    status: status === "all" ? undefined : status,
    source: source === "all" ? undefined : source,
    sort,
    limit: 60,
  });

  const ads = [...fetchedAds].sort((a, b) => {
    if (sort === "recent") return dateValue(b.last_seen || b.delivery_start) - dateValue(a.last_seen || a.delivery_start);
    if (sort === "score") return (b.persistence_score || 0) - (a.persistence_score || 0);
    return (b.duration_days ?? b.active_days ?? 0) - (a.duration_days ?? a.active_days ?? 0);
  });
  const safeSummary: MarketingAdsSummary = summary || {};
  const sourceOptions = (safeSummary.by_source || []).map((item) => {
    const value = item.source || item.source_id || "unknown";
    return { value, label: AD_SOURCE_LABEL[value] || value };
  });
  const trend = (safeSummary.trend || []).map((point) => ({
    date: point.date,
    active: point.active || 0,
    total: point.total || 0,
    new: point.new || 0,
    stopped: point.stopped || 0,
  }));
  const hasLifecycleTrend = (safeSummary.trend || []).some((point) => point.new !== undefined || point.stopped !== undefined);
  const isLoading = summaryLoading || adsLoading;

  return (
    <div className="space-y-6">
      <SectionTitle
        title="广告投放监控"
        subtitle="跟踪公开广告库中的创意、投放周期与变化信号；持续时长仅作为投放稳定度代理"
        action={<div className="flex flex-wrap items-center gap-2"><TimeRangePicker /><MonitorStatus brandId={brandId} dimension="marketing" /></div>}
      />

      <div className="flex flex-wrap items-center gap-3">
        <SegmentGroup
          value={selectedSection as any}
          options={[{ value: "overview", label: "总览" }, ...CHANNELS]}
          onChange={onSectionChange}
        />
      </div>

      {isLoading && !summary && !ads.length ? <Spinner /> : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <StatCard label="活跃广告" value={fmtNum(safeSummary.active_ads ?? countAds(ads, "active"))} tone="accent" hint="最近一次快照仍可见" />
            <StatCard label="新发现" value={fmtNum(safeSummary.new_ads ?? countAds(ads, "new"))} />
            <StatCard label="平均持续" value={`${Math.round(safeSummary.avg_duration_days ?? safeSummary.avg_lifetime_days ?? averageDuration(ads))} 天`} hint="同一广告的首次/最近发现" />
            <StatCard label="投放稳定度" value={`${Math.round(safeSummary.persistence_score ?? averageScore(ads))}`} hint="0–100，持续性弱代理" />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <Card className="lg:col-span-2">
              <SectionTitle title="广告活跃趋势" subtitle="按采集快照记录的活跃、新发现与停止" hint="广告库通常不提供竞品转化数据，趋势用于判断投放节奏，不代表 ROI。" />
              {trend.length ? (
                <TrendChart data={trend} keys={hasLifecycleTrend ? [
                  { key: "active", name: "活跃", color: "var(--accent)" },
                  { key: "new", name: "新发现", color: "var(--violet)" },
                  { key: "stopped", name: "停止", color: "var(--danger)" },
                ] : [
                  { key: "active", name: "活跃", color: "var(--accent)" },
                  { key: "total", name: "快照总量", color: "var(--violet)" },
                ]} />
              ) : <EmptyState title="暂无趋势数据" hint="完成至少一次广告库采集后，系统会在这里记录变化。" />}
            </Card>
            <Card>
              <SectionTitle title="变化提醒" subtitle="需要优先查看的广告事件" />
              {safeSummary.alerts?.length ? (
                <div className="space-y-3">
                  {safeSummary.alerts.slice(0, 5).map((alert, index) => (
                    <div key={alert.id || `${alert.type}-${index}`} className="rounded-md p-3" style={{ background: "var(--bg-soft)", border: "1px solid var(--hairline)" }}>
                      <div className="flex items-start justify-between gap-2">
                        <Badge tone={alert.type.toLowerCase().includes("stop") ? "warning" : "accent"}>{alert.type}</Badge>
                        {alert.detected_at && <span className="text-[11px] shrink-0" style={{ color: "var(--mute)" }}>{formatDate(alert.detected_at)}</span>}
                      </div>
                      <div className="text-[13px] font-medium mt-2" style={{ color: "var(--ink)" }}>{alert.title}</div>
                      {alert.detail && <div className="text-[12px] mt-1 leading-relaxed" style={{ color: "var(--mute)" }}>{alert.detail}</div>}
                    </div>
                  ))}
                </div>
              ) : <EmptyState title="暂无异常变化" hint="新广告、停止投放或素材变化会在这里提示。" />}
            </Card>
          </div>

          <Card>
            <SectionTitle
              title="广告素材"
              subtitle="按广告主、素材版本和投放周期查看公开广告快照"
              action={<div className="flex flex-wrap items-center gap-2">
                <SegmentGroup value={status as any} options={AD_STATUS_OPTIONS as any} onChange={setStatus} />
                <select value={source} onChange={(event) => setSource(event.target.value)} className="h-7 px-2 text-[12px] rounded-md" style={{ background: "var(--bg-soft)", color: "var(--body)", border: "1px solid var(--hairline-strong)" }}>
                  <option value="all">全部来源</option>
                  {sourceOptions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                </select>
                <select value={sort} onChange={(event) => setSort(event.target.value as typeof sort)} className="h-7 px-2 text-[12px] rounded-md" style={{ background: "var(--bg-soft)", color: "var(--body)", border: "1px solid var(--hairline-strong)" }}>
                  <option value="duration">按持续时间</option>
                  <option value="recent">按最近发现</option>
                  <option value="score">按稳定度</option>
                </select>
              </div>}
            />
            {ads.length ? (
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
                {ads.map((ad) => <AdCard key={ad.id || ad.source_ad_id} ad={ad} onOpen={() => setSelectedAd(ad)} />)}
              </div>
            ) : (
              <EmptyState title="暂无广告数据" hint="请先在数据源页配置 Meta Ad Library 或 Google Ads Transparency 的广告主/关键词，再发起采集。" />
            )}
          </Card>
        </>
      )}

      <AdDetailModal ad={selectedAd} onClose={() => setSelectedAd(null)} />
    </div>
  );
}

function AdCard({ ad, onOpen }: { ad: MarketingAd; onOpen: () => void }) {
  const advertiser = ad.advertiser_name || ad.advertiser || ad.page_name || "未知广告主";
  const status = normalizeAdStatus(ad.status || ad.lifecycle);
  const duration = ad.duration_days ?? ad.active_days;
  const score = ad.persistence_score;
  // Snapshot links are webpages on Meta/Google, not image URLs; only use a
  // media/thumbnail URL in an <img> and keep the source link below.
  const preview = ad.thumbnail_url || ad.media_url;
  return (
    <button onClick={onOpen} className="text-left rounded-lg p-3 transition-colors cursor-pointer w-full" style={{ background: "var(--bg-soft)", border: "1px solid var(--hairline)" }}>
      <div className="flex gap-3">
        <div className="w-24 h-20 rounded-md shrink-0 overflow-hidden flex items-center justify-center" style={{ background: "var(--bg-soft-2)", border: "1px solid var(--hairline)" }}>
          {preview ? <img src={preview} alt="" className="w-full h-full object-cover" loading="lazy" /> : <span className="text-[11px]" style={{ color: "var(--mute)" }}>无预览</span>}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <div className="truncate text-[13px] font-medium" style={{ color: "var(--ink)" }}>{advertiser}</div>
            <Badge tone={status === "active" ? "positive" : status === "stopped" ? "warning" : "neutral"}>{adStatusLabel(status)}</Badge>
          </div>
          <div className="text-[12px] mt-1 line-clamp-2 leading-relaxed" style={{ color: "var(--body)" }}>{ad.title || ad.body || ad.description || "暂无文案"}</div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] mt-2" style={{ color: "var(--mute)" }}>
            <span>{AD_SOURCE_LABEL[ad.source || ""] || ad.source || "广告库"}</span>
            {duration !== undefined && <span>持续 {duration} 天</span>}
            {score !== undefined && <span>稳定度 {Math.round(score)}</span>}
            {(ad.countries?.length || ad.country) && <span>{ad.countries?.length ? `${ad.countries.length} 个地区` : ad.country}</span>}
          </div>
        </div>
      </div>
      <div className="flex items-center justify-between gap-2 mt-3 pt-2 text-[11px]" style={{ borderTop: "1px solid var(--hairline)", color: "var(--mute)" }}>
        <span>{ad.first_seen ? `首次 ${formatDate(ad.first_seen)}` : "首次发现待确认"}</span>
        <span>{ad.last_seen ? `最近 ${formatDate(ad.last_seen)}` : "暂无最近时间"}</span>
      </div>
    </button>
  );
}

function AdDetailModal({ ad, onClose }: { ad: MarketingAd | null; onClose: () => void }) {
  if (!ad) return null;
  const advertiser = ad.advertiser_name || ad.advertiser || ad.page_name || "未知广告主";
  const score = ad.persistence_score;
  const duration = ad.duration_days ?? ad.active_days;
  return (
    <Modal open={!!ad} onClose={onClose} title="广告详情" width={760}>
      <div className="space-y-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-[16px] font-semibold" style={{ color: "var(--ink)" }}>{advertiser}</div>
            <div className="text-[12px] mt-1" style={{ color: "var(--mute)" }}>{AD_SOURCE_LABEL[ad.source || ""] || ad.source || "公开广告库"} · {ad.source_ad_id || ad.id}</div>
          </div>
          <Badge tone={normalizeAdStatus(ad.status || ad.lifecycle) === "active" ? "positive" : "warning"}>{adStatusLabel(normalizeAdStatus(ad.status || ad.lifecycle))}</Badge>
        </div>
        {(ad.thumbnail_url || ad.media_url) && <img src={ad.thumbnail_url || ad.media_url} alt="广告素材预览" className="w-full max-h-64 object-contain rounded-md" style={{ background: "var(--bg-soft)", border: "1px solid var(--hairline)" }} />}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <MiniMetric label="持续天数" value={duration !== undefined ? `${duration} 天` : "—"} />
          <MiniMetric label="投放稳定度" value={score !== undefined ? `${Math.round(score)}/100` : "—"} />
          <MiniMetric label="创意变体" value={ad.variants !== undefined ? String(ad.variants) : "—"} />
          <MiniMetric label="覆盖平台" value={ad.platforms?.length ? String(ad.platforms.length) : ad.platform || "—"} />
        </div>
        <div className="rounded-md p-3 text-[13px] leading-relaxed" style={{ background: "var(--bg-soft)", color: "var(--body)" }}>
          {ad.body || ad.description || ad.title || "暂无广告文案"}
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-[12px]" style={{ color: "var(--mute)" }}>
          <div><span style={{ color: "var(--body)" }}>首次发现：</span>{formatDate(ad.first_seen)}</div>
          <div><span style={{ color: "var(--body)" }}>最近发现：</span>{formatDate(ad.last_seen)}</div>
          <div><span style={{ color: "var(--body)" }}>投放开始：</span>{formatDate(ad.delivery_start)}</div>
          <div><span style={{ color: "var(--body)" }}>投放结束：</span>{formatDate(ad.delivery_stop)}</div>
        </div>
        {ad.landing_url && <a href={ad.landing_url} target="_blank" rel="noreferrer" className="text-[13px] underline break-all" style={{ color: "var(--accent)" }}>{ad.landing_url}</a>}
        {(ad.snapshot_url || ad.creative_url) && <div><a href={ad.snapshot_url || ad.creative_url} target="_blank" rel="noreferrer" className="text-[13px] underline" style={{ color: "var(--accent)" }}>打开广告库原始快照 ↗</a></div>}
      </div>
    </Modal>
  );
}

function MiniMetric({ label, value }: { label: string; value: string }) {
  return <div className="rounded-md p-2.5" style={{ background: "var(--bg-soft)", border: "1px solid var(--hairline)" }}><div className="text-[11px]" style={{ color: "var(--mute)" }}>{label}</div><div className="text-[15px] font-semibold mt-0.5" style={{ color: "var(--ink)" }}>{value}</div></div>;
}

function countAds(ads: MarketingAd[], status: string) {
  return ads.filter((ad) => normalizeAdStatus(ad.status || ad.lifecycle) === status).length;
}

function averageDuration(ads: MarketingAd[]) {
  const values = ads.map((ad) => ad.duration_days ?? ad.active_days).filter((value): value is number => typeof value === "number");
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0;
}

function averageScore(ads: MarketingAd[]) {
  const values = ads.map((ad) => ad.persistence_score).filter((value): value is number => typeof value === "number");
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0;
}

function normalizeAdStatus(value?: string) {
  const normalized = (value || "active").toLowerCase();
  if (normalized.includes("stop") || normalized.includes("pause") || normalized.includes("inactive")) return "stopped";
  if (normalized.includes("new")) return "new";
  return "active";
}

function adStatusLabel(value: string) {
  return value === "stopped" ? "已停止" : value === "new" ? "新发现" : "活跃";
}

function dateValue(value?: string) {
  if (!value) return 0;
  const date = new Date(value).getTime();
  return Number.isFinite(date) ? date : 0;
}

function formatDate(value?: string) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value.slice(0, 10) : date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}
