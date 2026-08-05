import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { TrendChart, Bars } from "../components/charts";
import { RecordList } from "../components/RecordList";
import { Button, Card, EmptyState, InfoHint, Modal, SectionTitle, SegmentGroup, Spinner, StatCard } from "../components/ui";
import { MonitorStatus } from "../components/MonitorStatus";
import { TimeRangePicker } from "../components/TimeRangePicker";
import { SmartSummary } from "../components/SmartSummary";
import { useMarketingSummary, useRecords } from "../lib/hooks";
import { useTimeRange, rangeParams } from "../lib/timeRange";
import { CHANNEL_LABEL, fmtDate, fmtNum } from "../lib/format";

const CHANNELS = [
  { value: "media", label: "媒体公关" },
  { value: "ads", label: "广告投放" },
  { value: "creators", label: "红人达人" },
  { value: "community", label: "社群" },
  { value: "social", label: "社交媒体" },
];

const SOURCE_LABEL: Record<string, string> = {
  reddit_search: "Reddit",
  community_site: "自建社群/论坛",
  google_news: "Google News",
  google_web_search: "Google 网页搜索补漏",
  meta_ads: "Meta 广告库",
  youtube_search: "YouTube",
  discord_community: "Discord",
  facebook_groups: "Facebook 群组",
  social_accounts: "官方社媒账号",
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
  youtube: "YouTube",
  instagram: "Instagram",
  tiktok: "TikTok",
  x: "X",
  facebook: "Facebook",
  linkedin: "LinkedIn",
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
    <div><span style={{ color: "var(--mute)" }}>Reddit：</span>品牌主名称用于全站搜索；指定 subreddit 作为官方大本营，全部帖子按时间顺序收录且不要求关键词命中。</div>
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
  const [selectedPublication, setSelectedPublication] = useState<PublicationStat | null>(null);
  const [trendMetric, setTrendMetric] = useState<"volume" | "reach">("volume");
  const [socialPlatform, setSocialPlatform] = useState("all");
  const contentStreamRef = useRef<HTMLDivElement>(null);
  const activeChannel = view === "channel" ? channel : undefined;
  const { data: summary, isLoading } = useMarketingSummary(brandId, activeChannel, range);
  const selectedPublicationTotal = selectedPublication
    ? (summary?.by_publication || []).find((item: PublicationStat) =>
        selectedPublication.domain
          ? item.domain === selectedPublication.domain
          : !item.domain && item.name === selectedPublication.name
      )?.total || 0
    : 0;
  const { data: records = [], isLoading: recordsLoading } = useRecords({
    brand_id: brandId,
    dimension: "marketing",
    channel: selectedPublication ? "media" : activeChannel,
    platform: activeChannel === "social" && socialPlatform !== "all" ? socialPlatform : undefined,
    publication_domain: selectedPublication?.domain || undefined,
    publication_name: selectedPublication?.name || undefined,
    ...rangeParams(range),
    limit: selectedPublication || activeChannel === "community" ? 1000 : 60,
  });

  const [hidden, setHidden] = useState<Set<string>>(new Set());
  useEffect(() => {
    try {
      setHidden(new Set(JSON.parse(localStorage.getItem(`monitor.hiddenCommunity.${brandId}`) || "[]")));
    } catch {
      setHidden(new Set());
    }
  }, [brandId]);
  useEffect(() => {
    if (view !== "channel" || channel !== "media") setSelectedPublication(null);
  }, [view, channel]);
  useEffect(() => {
    if (activeChannel !== "social") {
      if (socialPlatform !== "all") setSocialPlatform("all");
      return;
    }
    if (
      summary
      && socialPlatform !== "all"
      && !(summary.by_platform || []).some((item: { platform: string }) => item.platform === socialPlatform)
    ) {
      setSocialPlatform("all");
    }
  }, [activeChannel, socialPlatform, summary]);
  useEffect(() => {
    if (!selectedPublication) return;
    const frame = requestAnimationFrame(() => {
      contentStreamRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    return () => cancelAnimationFrame(frame);
  }, [selectedPublication]);
  const toggleHidden = (platform: string) => {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(platform)) next.delete(platform);
      else next.add(platform);
      localStorage.setItem(`monitor.hiddenCommunity.${brandId}`, JSON.stringify([...next]));
      return next;
    });
  };

  if (isLoading || !summary) return <Spinner />;

  const channelName = CHANNEL_LABEL[channel] || channel;
  const isCommunity = view === "channel" && channel === "community";
  const isSocial = view === "channel" && channel === "social";
  const selectedSection = view === "overview" ? "overview" : channel;
  const socialPlatformOptions = [
    { value: "all", label: "全部" },
    ...(summary.by_platform || []).map((item: { platform: string }) => ({
      value: item.platform,
      label: PLATFORM_LABEL[item.platform] || item.platform,
    })),
  ];
  const selectedSocialPlatformLabel = PLATFORM_LABEL[socialPlatform] || socialPlatform;
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
      ) : isSocial ? (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatCard label="社媒内容" value={fmtNum(summary.total)} />
          <StatCard label="总播放" value={fmtNum(summary.total_views)} tone="accent" />
          <StatCard label="总点赞" value={fmtNum(summary.total_likes)} />
          <StatCard label="总评论" value={fmtNum(summary.total_comments)} />
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
          <StatCard label="预估总触达" value={fmtNum(summary.total_reach)} tone="accent" hint="按媒体月流量估算，同一媒体仅计一次" />
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
            title={isSocial ? "社媒平台明细" : view === "channel" ? "来源渠道明细" : "平台分布"}
            subtitle={isSocial ? "按 YouTube、Instagram、TikTok 等平台拆分采集量" : view === "channel" ? "该渠道下各数据源的采集量" : undefined}
          />
          {view === "channel" ? (
            <SourceBreakdown
              sources={isSocial ? summary.by_platform || [] : summary.by_source || []}
              kind={isSocial ? "platform" : "source"}
            />
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

      <div ref={contentStreamRef}>
        <Card>
          <SectionTitle
            title={selectedPublication ? `${selectedPublication.name} 收录文章` : view === "channel" ? `${channelName}内容流` : "营销内容流"}
            subtitle={selectedPublication
              ? `当前时间范围内共收录 ${fmtNum(selectedPublicationTotal)} 篇文章`
              : isCommunity
                ? "勾选上方来源可在此显示/隐藏对应内容"
                : isSocial && socialPlatform !== "all"
                  ? `当前仅显示 ${selectedSocialPlatformLabel} 的真实采集内容`
                  : "按渠道筛选的真实采集内容"}
            action={selectedPublication
              ? <Button size="sm" onClick={() => setSelectedPublication(null)}>清除筛选</Button>
              : isSocial
                ? <SegmentGroup value={socialPlatform} options={socialPlatformOptions} onChange={setSocialPlatform} />
                : undefined}
          />
          {recordsLoading ? (
            <Spinner />
          ) : (
            <RecordList
              records={shownRecords}
              variant={isSocial ? "social-cards" : "list"}
              emptyHint={selectedPublication
                ? "当前时间范围内暂无该媒体的收录文章。"
                : isSocial && socialPlatform !== "all"
                  ? `当前时间范围内暂无 ${selectedSocialPlatformLabel} 内容。`
                  : "在数据源页发起媒体 / 广告 / 红人 / 社群 / 社媒账号采集后查看。"}
            />
          )}
        </Card>
      </div>

      <PublicationDetailModal
        open={publicationDetailOpen}
        onClose={() => setPublicationDetailOpen(false)}
        publications={summary.by_publication || []}
        onSelectPublication={(publication) => {
          setSelectedPublication(publication);
          setPublicationDetailOpen(false);
        }}
      />
    </div>
  );
}

type PublicationStat = {
  name: string;
  domain: string;
  total: number;
  monthly_traffic: number;
  traffic_lower?: number;
  traffic_upper?: number;
  popularity_rank?: number;
  traffic_source?: string;
  traffic_confidence?: string;
  traffic_as_of?: string;
  authority: number;
  tier: string;
  country: string;
};

const TRAFFIC_SOURCE_LABEL: Record<string, string> = {
  seed: "媒体库基准",
  tranco_model: "Tranco 公开排名估算",
  tranco_no_rank: "Tranco 暂无排名",
  tranco_unranked: "待重新查询 Tranco",
  unavailable: "暂无公开排名",
  historical: "历史采集值",
  manual: "人工校准",
};

const TRAFFIC_CONFIDENCE_LABEL: Record<string, string> = {
  high: "高可信",
  medium: "中可信",
  low: "低可信",
};

function formatTrafficEstimate(item: PublicationStat): string {
  const lower = item.traffic_lower || 0;
  const upper = item.traffic_upper || 0;
  if (lower > 0 && upper > 0 && lower !== upper) return `${fmtNum(lower)}–${fmtNum(upper)}`;
  if (lower > 0 && lower === upper) return fmtNum(lower);
  if (item.monthly_traffic > 0) return fmtNum(item.monthly_traffic);
  return "暂无可靠估值";
}

function trafficEstimateMeta(item: PublicationStat): string {
  const parts: string[] = [];
  if (item.popularity_rank) parts.push(`Tranco #${item.popularity_rank.toLocaleString("en-US")}`);
  else if (item.traffic_source) parts.push(TRAFFIC_SOURCE_LABEL[item.traffic_source] || item.traffic_source);
  if (item.traffic_confidence) parts.push(TRAFFIC_CONFIDENCE_LABEL[item.traffic_confidence] || item.traffic_confidence);
  if (item.traffic_as_of) parts.push(`更新 ${fmtDate(item.traffic_as_of)}`);
  return parts.join(" · ");
}

function PublicationDetailModal({
  open,
  onClose,
  publications,
  onSelectPublication,
}: {
  open: boolean;
  onClose: () => void;
  publications: PublicationStat[];
  onSelectPublication: (publication: PublicationStat) => void;
}) {
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
          {ranking === "frequency" ? "按当前时间范围内收录文章数排序" : "按月访问量估算中值排序；区间、来源和可信度见下方"}
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
                    <button
                      type="button"
                      className="tabular-nums font-medium cursor-pointer hover:underline underline-offset-2"
                      style={{ color: "var(--accent)" }}
                      title={`查看 ${item.name} 的全部收录文章`}
                      onClick={() => onSelectPublication(item)}
                    >
                      {fmtNum(item.total)} 篇
                    </button>
                    {ranking === "reach" && (
                      <div className="text-[12px] font-medium" style={{ color: item.monthly_traffic > 0 ? "var(--violet)" : "var(--mute)" }}>
                        月访问量 {formatTrafficEstimate(item)}
                      </div>
                    )}
                    <div className="text-[11px]" style={{ color: "var(--mute)" }}>
                      {ranking === "reach" && trafficEstimateMeta(item) ? `${trafficEstimateMeta(item)} · ` : ""}
                      {TIER_LABEL[item.tier] || item.tier}{item.authority ? ` · 权威度 ${item.authority}` : ""}
                    </div>
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

type SourceBreakdownItem = { source_id?: string; platform?: string; total: number };

function SourceBreakdown({
  sources,
  kind = "source",
}: {
  sources: SourceBreakdownItem[];
  kind?: "source" | "platform";
}) {
  if (!sources.length) {
    return (
      <EmptyState
        title={kind === "platform" ? "暂无平台数据" : "暂无数据源"}
        hint="该渠道尚未采集到数据，配置链接或手动刷新后查看。"
      />
    );
  }
  const max = Math.max(...sources.map((s) => s.total), 1);
  return (
    <div className="space-y-2.5">
      {sources.map((s) => {
        const key = kind === "platform" ? s.platform || "unknown" : s.source_id || "unknown";
        const label = kind === "platform" ? PLATFORM_LABEL[key] || key : SOURCE_LABEL[key] || key;
        return (
          <div key={key}>
            <div className="flex items-center justify-between text-[13px] mb-1">
              <span style={{ color: "var(--ink)" }}>{label}</span>
              <span className="tabular-nums" style={{ color: "var(--mute)" }}>{fmtNum(s.total)}</span>
            </div>
            <div className="h-1.5 rounded-full overflow-hidden" style={{ background: "var(--bg-soft-2)" }}>
              <div className="h-full rounded-full" style={{ width: `${(s.total / max) * 100}%`, background: "var(--accent)" }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}
