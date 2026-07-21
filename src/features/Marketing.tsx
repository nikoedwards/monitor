import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { TrendChart, Bars } from "../components/charts";
import { RecordList } from "../components/RecordList";
import { Button, Card, EmptyState, InfoHint, Modal, SectionTitle, SegmentGroup, Spinner, StatCard } from "../components/ui";
import { MonitorStatus } from "../components/MonitorStatus";
import { TimeRangePicker } from "../components/TimeRangePicker";
import { SmartSummary } from "../components/SmartSummary";
import { useMarketingSummary, useRecords } from "../lib/hooks";
import { useTimeRange, rangeParams } from "../lib/timeRange";
import { CHANNEL_LABEL, fmtNum } from "../lib/format";

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

  if (isLoading || !summary) return <Spinner />;

  const channelName = CHANNEL_LABEL[channel] || channel;
  const isCommunity = view === "channel" && channel === "community";
  const selectedSection = view === "overview" ? "overview" : channel;
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
        <RecordList records={shownRecords} emptyHint="在数据源页发起媒体 / 广告 / 红人 / 社群采集后查看。" />
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
