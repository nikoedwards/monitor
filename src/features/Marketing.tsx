import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { TrendChart, Bars } from "../components/charts";
import { RecordList } from "../components/RecordList";
import { Badge, Button, Card, EmptyState, InfoHint, Modal, SectionTitle, SegmentGroup, Spinner, StatCard } from "../components/ui";
import { MonitorStatus } from "../components/MonitorStatus";
import { CreatorSourceStatus } from "../components/CreatorSourceStatus";
import { CreatorCollectionLogic } from "../components/CreatorCollectionLogic";
import { TimeRangePicker } from "../components/TimeRangePicker";
import { ExecutiveMarketingSummary, SmartSummary } from "../components/SmartSummary";
import { useAds, useAdsSummary, useMarketingOverview, useMarketingSummary, useRecords } from "../lib/hooks";
import type { MarketingAd, MarketingAdsSummary } from "../lib/api";
import { useTimeRange, rangeParams } from "../lib/timeRange";
import { CHANNEL_LABEL, fmtDate, fmtNum } from "../lib/format";

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
    <div><span style={{ color: "var(--mute)" }}>浏览量：</span>仅汇总平台公开的帖子累计浏览量（如 Discourse），不代表去重人数或每日新增曝光；Reddit、Frill 未公开时显示「未公开」，不以投票、回复或关注人数推算。</div>
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
  useEffect(() => {
    setTrendMetric("volume");
  }, [activeChannel, brandId]);
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

  const isAds = view === "channel" && channel === "ads";
  const isSocial = view === "channel" && channel === "social";
  const selectedSection = view === "overview" ? "overview" : channel;
  if (view === "overview") {
    return (
      <MarketingOverview
        brandId={brandId}
        range={range}
        onSectionChange={(value) => {
          if (value === "overview") return;
          setChannel(value);
          setView("channel");
        }}
      />
    );
  }
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
  const isCreators = view === "channel" && channel === "creators";
  const hasCommunityViews = summary.community_view_records > 0;
  const canShowReach = isCommunity ? hasCommunityViews : summary.total_reach > 0;
  const visibleTrendMetric = canShowReach ? trendMetric : "volume";
  const reachLabel = isCommunity ? "帖子浏览量" : "预计曝光";
  const subchannelCount = (summary.by_subchannel || []).reduce((acc: number, g: any) => acc + (g.subchannels?.length || 0), 0);
  const shownRecords = isCommunity ? records.filter((r) => !hidden.has(r.platform || "")) : records;

  return (
    <div className="space-y-6">
      <SectionTitle
        title="营销监控"
        subtitle="媒体公关、广告、红人、社群与社交声量"
        action={<div className="flex flex-wrap items-center gap-2"><TimeRangePicker /><MonitorStatus brandId={brandId} dimension="marketing" /></div>}
      />
      {channel === "creators" && <CreatorSourceStatus brandId={brandId} />}
      {channel === "creators" && <CreatorCollectionLogic compact />}

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
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <StatCard label="新增帖子" value={fmtNum(summary.posts)} tone="accent" />
          <StatCard label="新增回复" value={fmtNum(summary.replies)} />
          <StatCard label="子渠道数" value={subchannelCount} hint="如多个 subreddit / 自建站" />
          <StatCard label="数据源" value={summary.by_source?.length || 0} />
          <StatCard label="帖子公开浏览量" value={hasCommunityViews ? fmtNum(summary.total_community_views) : "未公开"} hint={hasCommunityViews ? `覆盖 ${summary.community_view_records} / ${summary.posts} 篇帖子，非去重人数` : "当前帖子未提供公开浏览量"} />
        </div>
      ) : isCreators ? (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatCard label="红人内容" value={fmtNum(summary.total)} />
          <StatCard label="总曝光 / 播放" value={fmtNum(summary.total_views)} tone="accent" />
          <StatCard label="总点赞" value={fmtNum(summary.total_likes)} />
          <StatCard label="总互动" value={fmtNum(summary.total_engagement)} />
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

      {!isCommunity && summary.total_reach > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatCard label="预估总触达" value={fmtNum(summary.total_reach)} tone="accent" hint="按媒体月流量估算，同一媒体仅计一次" />
          <StatCard label="媒体价值 AVE" value={`$${fmtNum(summary.total_ave)}`} hint="等价广告价值(估算)" />
          <StatCard label="媒体层级" value={summary.by_tier?.length || 0} />
          <StatCard label="覆盖国家" value={summary.by_country?.length || 0} />
        </div>
      )}

      {!isCommunity && summary.total_reach > 0 && (
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
            title={visibleTrendMetric === "volume" ? (isCommunity ? "社群声量趋势" : "声量趋势") : `${reachLabel}趋势`}
            subtitle={isCommunity ? (hasCommunityViews
              ? "浏览量为所选期间发布帖子的累计公开读数，非每日新增曝光；未公开的数据留空。"
              : "Reddit、Frill 等公开来源通常不提供曝光量；此处展示已采集的帖子与回复数量。") : undefined}
            action={canShowReach ? (
              <SegmentGroup
                value={visibleTrendMetric}
                options={[{ value: "volume", label: "篇数" }, { value: "reach", label: reachLabel }]}
                onChange={setTrendMetric}
              />
            ) : undefined}
          />
          {visibleTrendMetric === "volume" ? (
            <TrendChart data={summary.trend} keys={[{ key: "total", name: "声量", color: "var(--accent)" }, { key: "negative", name: "负向", color: "var(--danger)" }]} />
          ) : (
            <TrendChart
              data={summary.trend}
              keys={[{ key: isCommunity ? "community_views" : "estimated_reach", name: reachLabel, color: "var(--violet)" }]}
              valueFormatter={fmtNum}
              showPoints={isCommunity}
            />
          )}
        </Card>
        <Card>
          <SectionTitle
            title={isSocial || isCreators ? "平台采集明细" : view === "channel" ? "来源渠道明细" : "平台分布"}
            subtitle={isSocial || isCreators ? "按 YouTube、Instagram、TikTok 等平台拆分采集量" : view === "channel" ? "该渠道下各数据源的采集量" : undefined}
          />
          {view === "channel" ? (
            <SourceBreakdown
              sources={isSocial || isCreators ? summary.by_platform || [] : summary.by_source || []}
              kind={isSocial || isCreators ? "platform" : "source"}
            />
          ) : summary.by_platform?.length ? (
            <Bars data={summary.by_platform.slice(0, 8)} dataKey="total" nameKey="platform" name="声量" color="var(--violet)" />
          ) : (
            <p className="text-[13px]" style={{ color: "var(--mute)" }}>暂无平台数据</p>
          )}
        </Card>
      </div>

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
        <RecordList
          records={shownRecords}
          summarizeCommunity={isCommunity}
          variant={channel === "social" || channel === "creators" ? "social-cards" : "list"}
          emptyHint={channel === "creators"
            ? "红人关键词采集每天搜索公开内容；品牌官方账号内容请看「社交媒体」，手动导入的红人只进入候选池。"
            : "在数据源页发起媒体 / 广告 / 红人 / 社群采集后查看。"}
        />
      </Card>

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

function marketingDeltaLabel(delta: any): string {
  if (!delta || delta.value == null) return "暂无数据";
  if (delta.delta == null || delta.previous == null) return "暂无环比";
  const sign = delta.delta > 0 ? "+" : "";
  const pct = delta.delta_pct == null ? "" : ` · ${sign}${delta.delta_pct}%`;
  return `环比 ${sign}${fmtNum(delta.delta)}${pct}`;
}

function marketingValue(delta: any, formatter: (value: number) => string = fmtNum): string {
  return delta?.value == null ? "暂无数据" : formatter(Number(delta.value));
}

function MarketingOverview({
  brandId,
  range,
  onSectionChange,
}: {
  brandId?: string;
  range: ReturnType<typeof useTimeRange>[0];
  onSectionChange: (value: string) => void;
}) {
  const overview = useMarketingOverview(brandId, range);
  const data = overview.data;
  const modules = data?.modules || {};
  const kpis = data?.kpis || {};
  const moduleMeta: { key: string; label: string; color: string; metrics: [string, (module: any) => string][] }[] = [
    { key: "media", label: "媒体公关", color: "var(--violet)", metrics: [["报道量", (m: any) => fmtNum(m.total)], ["估算触达", (m: any) => m.total_reach ? fmtNum(m.total_reach) : "暂无数据"], ["AVE", (m: any) => m.total_ave ? `$${fmtNum(m.total_ave)}` : "暂无数据"]] },
    { key: "ads", label: "广告投放", color: "var(--accent)", metrics: [["广告总量", (m: any) => fmtNum(m.total_ads)], ["当前活跃", (m: any) => fmtNum(m.active_ads)], ["本期变化", (m: any) => `新 ${fmtNum(m.new_ads)} · 停 ${fmtNum(m.stopped_ads)}`]] },
    { key: "creators", label: "红人达人", color: "var(--warning)", metrics: [["内容量", (m: any) => fmtNum(m.total)], ["达人数", (m: any) => fmtNum(m.creator_count)], ["互动", (m: any) => fmtNum(m.total_engagement)] ] },
    { key: "social", label: "社交媒体", color: "var(--success)", metrics: [["内容量", (m: any) => fmtNum(m.total)], ["播放", (m: any) => fmtNum(m.total_views)], ["互动", (m: any) => fmtNum(m.total_engagement)] ] },
    { key: "community", label: "社群", color: "var(--danger)", metrics: [["帖子", (m: any) => fmtNum(m.posts)], ["回复", (m: any) => fmtNum(m.replies)], ["子渠道", (m: any) => fmtNum(m.subchannel_count)] ] },
  ];

  return (
    <div className="space-y-6">
      <SectionTitle
        title="营销监控"
        subtitle="营销驾驶舱：媒体、公关、广告、红人、社交与社群统一判断"
        action={<div className="flex flex-wrap items-center gap-2"><TimeRangePicker /><MonitorStatus brandId={brandId} dimension="marketing" /></div>}
      />
      <div className="flex flex-wrap items-center gap-3">
        <SegmentGroup value="overview" options={[{ value: "overview", label: "总览" }, ...CHANNELS]} onChange={onSectionChange} />
      </div>

      <Card>
        <SectionTitle title="智能总结" subtitle="自动整理所选周期的营销结论，并单独提示昨日发生的变化" />
        <ExecutiveMarketingSummary brandId={brandId} range={range} />
      </Card>

      {overview.isLoading && <Spinner />}
      {overview.isError && <EmptyState title="营销总览暂时不可用" hint="请稍后刷新；各营销详情页仍可单独查看。" />}
      {data && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
            <MarketingKpi label="营销内容" value={marketingValue(kpis.content_total)} change={marketingDeltaLabel(kpis.content_total)} />
            <MarketingKpi label="总播放" value={marketingValue(kpis.total_views)} change={marketingDeltaLabel(kpis.total_views)} />
            <MarketingKpi label="总互动" value={marketingValue(kpis.total_engagement)} change={marketingDeltaLabel(kpis.total_engagement)} />
            <MarketingKpi label="媒体触达" value={marketingValue(kpis.media_reach)} change={marketingDeltaLabel(kpis.media_reach)} />
            <MarketingKpi label="广告总量" value={marketingValue(kpis.ads_total)} change={marketingDeltaLabel(kpis.ads_total)} />
            <MarketingKpi label="合作达人" value={marketingValue(kpis.creator_count)} change={marketingDeltaLabel(kpis.creator_count)} />
          </div>

          <Card>
            <SectionTitle title="重点营销信号" subtitle="按严重度聚合广告、媒体、红人、社交和社群的变化" />
            {data.signals?.length ? (
              <div className="space-y-2">
                {data.signals.map((signal: any, index: number) => (
                  <button key={`${signal.domain}-${index}`} type="button" className="w-full text-left flex items-start gap-3 p-3 rounded-md cursor-pointer" style={{ background: "var(--bg-soft-2)" }} onClick={() => onSectionChange(signal.domain === "ads" ? "ads" : signal.domain)}>
                    <Badge tone={signal.severity === "high" ? "negative" : signal.severity === "medium" ? "warning" : "neutral"}>{signal.domain}</Badge>
                    <div className="min-w-0"><div className="text-[13px] font-medium" style={{ color: "var(--ink)" }}>{signal.title}</div><div className="text-[12px] mt-0.5" style={{ color: "var(--body)" }}>{signal.detail}</div></div>
                  </button>
                ))}
              </div>
            ) : <EmptyState title="暂无需要重点关注的营销信号" hint="当前周期没有检测到明显异常或变化。" />}
          </Card>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {moduleMeta.map((meta) => {
              const module = modules[meta.key] || {};
              const coverage = data.coverage?.[meta.key] || {};
              const trend = module.trend || [];
              const trendKeys = meta.key === "ads"
                ? [{ key: "active", name: "活跃广告", color: meta.color }, { key: "new", name: "新增", color: "var(--warning)" }]
                : [{ key: "total", name: meta.key === "media" ? "报道量" : "内容量", color: meta.color }];
              return (
                <Card key={meta.key}>
                  <SectionTitle title={meta.label} subtitle={coverage.status === "ready" ? `最近更新 ${fmtDate(coverage.last_data_at)}` : coverage.status === "configured_no_data" ? "已配置，当前周期暂无数据" : "尚未配置或暂无数据"} action={<Button size="sm" onClick={() => onSectionChange(meta.key)}>查看详情</Button>} />
                  <div className="grid grid-cols-3 gap-2 mb-3">
                    {meta.metrics.map(([label, formatter]) => <div key={label as string}><div className="text-[11px]" style={{ color: "var(--mute)" }}>{label}</div><div className="text-[16px] font-semibold mt-1" style={{ color: coverage.status === "ready" ? "var(--ink)" : "var(--mute)" }}>{coverage.status === "ready" ? formatter(module) : "暂无数据"}</div></div>)}
                  </div>
                  {trend.length ? <TrendChart data={trend} keys={trendKeys} valueFormatter={fmtNum} /> : <EmptyState title="暂无趋势数据" hint="采集到新的营销记录后将在此显示。" />}
                </Card>
              );
            })}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card><SectionTitle title="营销内容渠道分布" subtitle="不同营销子板块的内容量" />{moduleMeta.some((meta) => data.coverage?.[meta.key]?.status === "ready") ? <Bars data={moduleMeta.filter((meta) => data.coverage?.[meta.key]?.status === "ready").map((meta) => ({ label: meta.label, total: modules[meta.key]?.total ?? modules[meta.key]?.total_ads ?? 0 }))} dataKey="total" nameKey="label" name="数量" color="var(--accent)" /> : <EmptyState title="暂无可比较的营销数据" hint="采集到营销内容后将在此显示。" />}</Card>
            <Card><SectionTitle title="数据覆盖说明" subtitle="按当前时间范围判断，缺失数据不会被显示为 0" /><div className="space-y-2">{moduleMeta.map((meta) => { const coverage = data.coverage?.[meta.key] || {}; return <div key={meta.key} className="flex items-center justify-between text-[13px]"><span style={{ color: "var(--ink)" }}>{meta.label}</span><Badge tone={coverage.status === "ready" ? "positive" : coverage.status === "configured_no_data" ? "warning" : "neutral"}>{coverage.status === "ready" ? "有数据" : coverage.status === "configured_no_data" ? "已配置待采集" : "暂无数据"}</Badge></div>; })}</div></Card>
          </div>
        </>
      )}
    </div>
  );
}

function MarketingKpi({ label, value, change }: { label: string; value: string; change: string }) {
  return <Card className="p-4"><div className="text-[12px] uppercase tracking-wide" style={{ color: "var(--mute)" }}>{label}</div><div className="text-[24px] font-semibold mt-1 tabular-nums tracking-tight" style={{ color: value === "暂无数据" ? "var(--mute)" : "var(--ink)" }}>{value}</div><div className="text-[11px] mt-1" style={{ color: "var(--mute)" }}>{change}</div></Card>;
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

function SourceBreakdown({ sources, kind = "source" }: { sources: { source_id?: string; platform?: string; total: number }[]; kind?: "source" | "platform" }) {
  if (!sources.length) {
    return <EmptyState title="暂无数据源" hint="该渠道尚未采集到数据，配置链接或手动刷新后查看。" />;
  }
  const max = Math.max(...sources.map((s) => s.total), 1);
  return (
    <div className="space-y-2.5">
      {sources.map((s) => (
        <div key={s.source_id || s.platform}>
          <div className="flex items-center justify-between text-[13px] mb-1">
            <span style={{ color: "var(--ink)" }}>{kind === "platform" ? (s.platform || "unknown") : (SOURCE_LABEL[s.source_id || ""] || s.source_id || "unknown")}</span>
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
  const [longOnly, setLongOnly] = useState(false);
  const [selectedAd, setSelectedAd] = useState<MarketingAd | null>(null);
  const { data: summary, isLoading: summaryLoading } = useAdsSummary(brandId, range);
  const { data: fetchedAds = [], isLoading: adsLoading } = useAds(brandId, range, {
    status: status === "all" ? undefined : status,
    source: source === "all" ? undefined : source,
    sort,
    // Pull the complete candidate pool so the conclusion card is not biased
    // toward the most recently seen 60 ads. Rendering remains capped below.
    limit: 1000,
  });

  const ads = [...fetchedAds].sort((a, b) => {
    if (sort === "recent") return dateValue(b.last_seen || b.delivery_start) - dateValue(a.last_seen || a.delivery_start);
    if (sort === "score") return (b.persistence_score || 0) - (a.persistence_score || 0);
    return (b.duration_days ?? b.active_days ?? 0) - (a.duration_days ?? a.active_days ?? 0);
  });
  const safeSummary: MarketingAdsSummary = summary || {};
  const durationRanked = ads
    .filter((ad) => adDuration(ad) !== undefined)
    .sort((a, b) => (adDuration(b) || 0) - (adDuration(a) || 0));
  const topResearchAds = durationRanked.slice(0, 3);
  const longRunningCount = durationRanked.filter((ad) => (adDuration(ad) || 0) >= 30).length;
  const activeLongRunningCount = durationRanked.filter((ad) => (adDuration(ad) || 0) >= 30 && normalizeAdStatus(ad.status || ad.lifecycle) === "active").length;
  const displayAds = longOnly ? ads.filter((ad) => (adDuration(ad) || 0) >= 30) : ads;
  const visibleAds = displayAds.slice(0, 60);
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
            <StatCard label="长投放候选" value={fmtNum(longRunningCount)} tone="accent" hint="持续 ≥ 30 天，优先研究" />
          </div>

          <Card style={{ borderColor: "rgba(0,112,243,0.28)", background: "linear-gradient(135deg, var(--panel), rgba(0,112,243,0.045))" }}>
            <SectionTitle
              title="先看结论"
              subtitle="把持续时间转成素材研究优先级；它是稳定度代理，不等于真实转化效果。"
              hint="广告库通常没有竞品的转化、成本和利润数据，因此这里用持续时长、当前是否仍活跃和素材变化作为筛选信号。"
            />
            {topResearchAds.length ? (
              <div className="grid grid-cols-1 lg:grid-cols-[1.25fr_1fr] gap-5 items-start">
                <div>
                  <div className="text-[20px] leading-snug font-semibold tracking-tight" style={{ color: "var(--ink)" }}>
                    {topResearchAds[0].advertiser_name || topResearchAds[0].advertiser || topResearchAds[0].page_name || "未知广告主"} 的素材最值得优先研究
                  </div>
                  <p className="text-[13px] mt-2 leading-relaxed" style={{ color: "var(--body)" }}>
                    当前可见的 {fmtNum(durationRanked.length)} 条有时长素材中，它已持续 <strong>{Math.round(adDuration(topResearchAds[0]) || 0)} 天</strong>，{normalizeAdStatus(topResearchAds[0].status || topResearchAds[0].lifecycle) === "active" ? "目前仍在投放" : "目前已停止投放"}。建议先拆解它的开场钩子、核心卖点、画面结构和 CTA，再与自有素材做一轮对照。
                  </p>
                  <div className="flex flex-wrap items-center gap-2 mt-4">
                    <Button size="sm" variant="primary" onClick={() => setSelectedAd(topResearchAds[0])}>查看这条素材</Button>
                    <Button size="sm" onClick={() => { setLongOnly(true); setSort("duration"); }}>只看长投放候选</Button>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <MiniMetric label="≥ 30 天素材" value={`${longRunningCount} 条`} />
                  <MiniMetric label="仍在投放" value={`${activeLongRunningCount} 条`} />
                  <MiniMetric label="最长持续" value={`${Math.round(adDuration(topResearchAds[0]) || 0)} 天`} />
                  <MiniMetric label="平均稳定度" value={`${Math.round(safeSummary.persistence_score ?? averageScore(ads))}/100`} />
                </div>
              </div>
            ) : (
              <EmptyState title="暂时没有足够的持续时长数据" hint="完成多轮广告库采集后，系统才能识别长投放素材并给出优先级。" />
            )}
          </Card>

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
              title={longOnly ? "长投放素材候选" : "广告素材研究清单"}
              subtitle={longOnly ? `已筛选持续 ≥ 30 天的素材，优先用于反向拆解 · ${fmtNum(displayAds.length)} 条` : `优先按持续时间查看公开广告快照，先找值得研究的素材 · ${fmtNum(displayAds.length)} 条`}
              action={<div className="flex flex-wrap items-center gap-2">
                {longOnly && <Button size="sm" variant="ghost" onClick={() => setLongOnly(false)}>清除长投放筛选</Button>}
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
            {visibleAds.length ? (
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
                {visibleAds.map((ad) => <AdCard key={ad.id || ad.source_ad_id} ad={ad} onOpen={() => setSelectedAd(ad)} />)}
              </div>
            ) : (
              <EmptyState title={longOnly ? "暂无 30 天以上的素材" : "暂无广告数据"} hint={longOnly ? "可以先清除筛选，查看全部素材；持续采集几轮后再判断长投放候选。" : "请先在数据源页配置 Meta Ad Library 或 Google Ads Transparency 的广告主/关键词，再发起采集。"} />
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
          <div className="text-[12px] mt-1 line-clamp-2 leading-relaxed" style={{ color: "var(--body)" }}>{ad.body || ad.description || ad.title || "暂无文案"}</div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] mt-2" style={{ color: "var(--mute)" }}>
            <span>{AD_SOURCE_LABEL[ad.source || ""] || ad.source || "广告库"}</span>
            {ad.duplicate_count && ad.duplicate_count > 1 && <span>相同文案 {ad.duplicate_count} 条</span>}
            {ad.source === "google_ads" && ad.source_ad_id && <span>素材 {ad.source_ad_id.split(":").pop()}</span>}
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

function adDuration(ad: MarketingAd) {
  const value = ad.duration_days ?? ad.active_days;
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
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
