import { useState } from "react";
import {
  AlertTriangle,
  Check,
  Database,
  FileUp,
  MessageSquareText,
  MessagesSquare,
  RefreshCw,
  ShoppingBag,
  ShieldCheck,
  Smartphone,
  UserRound,
  Video,
} from "lucide-react";
import { useParams } from "react-router-dom";
import { TrendChart } from "../components/charts";
import { SentimentBadge } from "../components/RecordList";
import { Badge, Button, Card, EmptyState, Field, Input, Modal, SectionTitle, Spinner, StatCard, Textarea } from "../components/ui";
import { TimeRangePicker } from "../components/TimeRangePicker";
import { useSourceMutations, useVocMutations, useVocRecords, useVocSummary, useVoiceFeedbackSummaries } from "../lib/hooks";
import type { CommunityPostSummary, RecordItem, VoiceEvidence, VoiceIssuePreview } from "../lib/api";
import { useTimeRange } from "../lib/timeRange";
import { fmtDateTime, fmtNum, TEAM_LABEL } from "../lib/format";
import { SmartSummary } from "../components/SmartSummary";

type SourceKey =
  | "sales_reviews"
  | "marketing_videos"
  | "social_posts_comments"
  | "creator_comments"
  | "app_reviews"
  | "manual_feedback";

type BadgeTone = "neutral" | "positive" | "negative" | "warning" | "accent";
type FeedbackSummaries = Record<string, CommunityPostSummary>;

interface VoiceSourceSummary {
  key: string;
  label: string;
  count: number;
  positive: number;
  neutral: number;
  negative: number;
  hot_risk_count: number;
  examples: (VoiceIssuePreview & { count: number })[];
}

interface IssueCard {
  id: string;
  title: string;
  topics: string[];
  total: number;
  negative: number;
  negative_rate: number;
  priority: string;
  priority_reason: string;
  impact: Record<string, unknown>;
  suggested_teams: string[];
  suggested_team: string;
  problem_summary: string;
  evidence: VoiceEvidence[];
}

const VOICE_SOURCES: { key: SourceKey; label: string; description: string; icon: typeof Database }[] = [
  { key: "sales_reviews", label: "销售渠道评论", description: "Amazon、独立站等销售平台的商品评论", icon: ShoppingBag },
  { key: "marketing_videos", label: "营销视频", description: "品牌官方账号发布的视频内容", icon: Video },
  { key: "social_posts_comments", label: "社交帖子与评论", description: "社交媒体、社区和论坛中的帖子、回复", icon: MessagesSquare },
  { key: "creator_comments", label: "红人视频与评论", description: "红人达人内容及其评论反馈", icon: UserRound },
  { key: "app_reviews", label: "应用商店评论", description: "App Store 等应用市场的用户评价", icon: Smartphone },
  { key: "manual_feedback", label: "手动反馈", description: "人工录入、访谈或 CSV 导入的反馈", icon: FileUp },
];

const ALL_SOURCE_KEYS = VOICE_SOURCES.map((source) => source.key);
const SOURCE_LABEL = Object.fromEntries(VOICE_SOURCES.map((source) => [source.key, source.label])) as Record<string, string>;

function toggleSourceSelection(selectedSources: SourceKey[], key: SourceKey): SourceKey[] {
  return selectedSources.length === 1 && selectedSources[0] === key ? ALL_SOURCE_KEYS : [key];
}

const TOPIC_LABEL: Record<string, string> = {
  ads: "广告体验",
  creator: "达人内容",
  delivery: "物流交付",
  experience: "使用体验",
  feature: "功能需求",
  price: "价格与订阅",
  pr: "品牌口碑",
  quality: "产品质量",
  retail: "购买渠道",
  support: "客服售后",
};

function priorityView(priority?: string): { level: string; label: string; tone: BadgeTone } {
  if (priority === "urgent") return { level: "P0", label: "立即响应", tone: "negative" };
  if (priority === "high") return { level: "P1", label: "高优先级", tone: "warning" };
  if (priority === "medium") return { level: "P2", label: "持续观察", tone: "accent" };
  return { level: "P3", label: "常规观察", tone: "neutral" };
}

export default function Voice() {
  const { brandId } = useParams();
  const [range] = useTimeRange();
  const [sourceOpen, setSourceOpen] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [selectedSources, setSelectedSources] = useState<SourceKey[]>(ALL_SOURCE_KEYS);
  const [sourceDraft, setSourceDraft] = useState<SourceKey[]>(ALL_SOURCE_KEYS);
  const [sentimentFilter, setSentimentFilter] = useState<"all" | "positive" | "neutral" | "negative">("all");
  const [collectMessage, setCollectMessage] = useState("");
  const [openEvidence, setOpenEvidence] = useState<VoiceEvidence | null>(null);
  const { collect } = useSourceMutations();
  const summaryQuery = useVocSummary(brandId, range, selectedSources);
  const recordsQuery = useVocRecords(brandId, range, selectedSources, 40, sentimentFilter === "all" ? undefined : sentimentFilter);
  const { data: summary, isLoading, isError, error, refetch } = summaryQuery;
  const { data: records = [], isLoading: recordsLoading, isError: recordsError, refetch: refetchRecords } = recordsQuery;
  const topics: IssueCard[] = summary?.topics || [];
  const sourceCatalog: VoiceSourceSummary[] = summary?.source_catalog || [];
  const feedbackSummaries = useVoiceFeedbackSummaries([
    ...topics.flatMap((topic) => !topic.problem_summary ? (topic.evidence || []).slice(0, 1) : []),
    ...sourceCatalog.flatMap((source) => (source.examples || []).filter((example) => !example.summary).map((example) => example.evidence)),
    ...records.flatMap((record) => record.voice_issue && !record.voice_issue.summary ? [record.voice_issue.evidence] : []),
  ].filter((evidence) => evidence.record_id && evidence.text));

  if (isLoading && !summary) return <Spinner />;
  if (isError && !summary) {
    const message = error instanceof Error ? error.message : "用户之声接口暂时不可用";
    return (
      <Card>
        <EmptyState
          title="用户之声暂时加载失败"
          hint={`${message}。请确认数据服务正常后重试。`}
          action={<Button onClick={() => { void refetch(); void refetchRecords(); }}>重新加载</Button>}
        />
      </Card>
    );
  }
  if (!summary) return <Spinner />;
  const totals = summary.totals;
  const p0 = summary.p0 || { total: 0, issue_count: 0, issues: [] };
  const sourceCounts = Object.fromEntries(sourceCatalog.map((source) => [source.key, source.count]));
  const filteredRecords = records;

  const openSourcePicker = () => {
    setSourceDraft(selectedSources);
    setSourceOpen(true);
  };

  const collectAppReviews = async () => {
    if (!brandId || collect.isPending) return;
    setCollectMessage("正在抓取 App Store 和 Google Play 最新评论…");
    try {
      const results = [];
      for (const sourceId of ["app_store_reviews", "google_play_reviews"]) {
        results.push(await collect.mutateAsync({ sourceId, brandId }));
      }
      const created = results.reduce((total, result) => total + Number(result.created || 0), 0);
      const failed = results.filter((result) => result.status !== "ok");
      setCollectMessage(failed.length ? failed.map((result) => result.error || result.status).join("；") : `已完成，新增 ${created} 条`);
    } catch (error: any) {
      setCollectMessage(error?.message || "应用商店采集失败");
    }
  };

  return (
    <div className="voice-page space-y-6">
      <SectionTitle
        title="用户之声"
        subtitle="聚合各业务板块的真实用户反馈，识别情绪、问题标签、责任团队与高优先级风险"
        action={
          <div className="flex flex-wrap items-center gap-2">
            <TimeRangePicker />
            <Button size="sm" onClick={collectAppReviews} disabled={collect.isPending} title="从 App Store 和 Google Play 公开接口抓取最新评论">
              <RefreshCw size={14} className={collect.isPending ? "animate-spin" : undefined} />
              {collect.isPending ? "采集中…" : "抓取应用商店评论"}
            </Button>
            <Button variant="primary" onClick={openSourcePicker}>
              <Database size={15} />
              数据来源
              <span className="text-[12px] opacity-70">{selectedSources.length}/{VOICE_SOURCES.length}</span>
            </Button>
          </div>
        }
      />
      {collectMessage && (
        <div className="text-[12px]" style={{ color: collect.isError ? "var(--danger)" : "var(--mute)" }}>
          {collectMessage}
        </div>
      )}

      <Card className="p-4">
        <div className="flex flex-col lg:flex-row lg:items-center gap-3 lg:justify-between">
          <div className="flex items-center gap-2 shrink-0">
            <Database size={16} style={{ color: "var(--accent)" }} />
            <div>
              <div className="text-[13px] font-medium" style={{ color: "var(--ink)" }}>当前分析范围</div>
              <div className="text-[12px]" style={{ color: "var(--mute)" }}>切换来源后，页面全部分析结果同步更新</div>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            {VOICE_SOURCES.map(({ key }) => (
              <button
                key={key}
                type="button"
                onClick={() => setSelectedSources((current) => toggleSourceSelection(current, key))}
                className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[12px]"
                title={`只查看${SOURCE_LABEL[key]}`}
                style={{ background: selectedSources.length === 1 && selectedSources.includes(key) ? "rgba(0,112,243,0.12)" : "var(--bg-soft-2)", color: selectedSources.includes(key) ? "var(--accent)" : "var(--mute)", cursor: "pointer", opacity: selectedSources.includes(key) ? 1 : 0.72 }}
              >
                {SOURCE_LABEL[key]}
                <span className="tabular-nums" style={{ color: "var(--mute)" }}>{fmtNum(Number(sourceCounts[key] || 0))}</span>
              </button>
            ))}
            {selectedSources.length === 1 && (
              <button type="button" onClick={() => setSelectedSources(ALL_SOURCE_KEYS)} className="text-[12px] px-2 py-1 cursor-pointer" style={{ color: "var(--mute)" }}>显示全部</button>
            )}
            <button onClick={openSourcePicker} className="text-[12px] px-2 py-1 cursor-pointer" style={{ color: "var(--accent)" }}>更改</button>
          </div>
        </div>
      </Card>

      <Card>
        <SectionTitle title="用户之声智能总结" subtitle="结合当前时间范围与数据来源，提炼主要话题、情绪和需要关注的舆情" />
        <SmartSummary brandId={brandId} dimension="voc" range={range} filters={{ sources: selectedSources, sentiment: sentimentFilter === "all" ? undefined : sentimentFilter }} />
      </Card>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label="反馈总量" value={fmtNum(totals.total)} hint={`${selectedSources.length} 个来源`} />
        <StatCard label="负向反馈" value={fmtNum(totals.negative)} tone="negative" hint={`负向率 ${(totals.negative_rate * 100).toFixed(0)}%`} />
        <StatCard label="正向反馈" value={fmtNum(totals.positive)} tone="accent" hint={`中性 ${fmtNum(totals.neutral)}`} />
        <StatCard label="P0 风险" value={fmtNum(p0.total)} tone={p0.total ? "negative" : undefined} hint={p0.total ? `${p0.issue_count} 类问题需立即确认` : "未发现高危信号"} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card className="lg:col-span-2">
          <SectionTitle title="反馈与负向趋势" subtitle="趋势随时间范围和数据来源实时重算" />
          <TrendChart
            data={summary.trend}
            keys={[
              { key: "total", name: "全部反馈", color: "var(--accent)" },
              { key: "positive", name: "正向反馈", color: "#16a34a" },
              { key: "neutral", name: "中性反馈", color: "#a3a3a3" },
              { key: "negative", name: "负向反馈", color: "var(--danger)" },
            ]}
            interactive
          />
        </Card>
        <Card>
          <SectionTitle title="情绪分析" subtitle="各渠道反馈的情绪倾向" />
          <SentimentDistribution totals={totals} />
        </Card>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 items-start">
        <Card className="lg:col-span-2">
          <SectionTitle
            title="突出问题与标签归类"
            subtitle="按具体问题归并反馈，优先显示高风险和高频问题"
            hint="P0 仅用于命中安全、隐私、数据丢失、异常扣款或大范围故障等高危信号；其余问题结合负向声量、负向率和帖子在当前范围内的相对传播排名划分 P1-P3。"
          />
          <IssueBoard topics={topics} summaries={feedbackSummaries.data} onOpenEvidence={setOpenEvidence} />
        </Card>
        <div className="space-y-4">
          <Card>
            <SectionTitle title="P0 风险扫描" subtitle="高优先级用户问题自动筛查" />
            <P0Panel p0={p0} />
          </Card>
        </div>
      </div>

      <Card>
        <SectionTitle title="原始反馈明细" subtitle="按来源和情绪筛选，查看具体问题、建议和原始反馈" />
        <SourceSummaryGrid
          sources={sourceCatalog}
          summaries={feedbackSummaries.data}
          selectedSources={selectedSources}
          onSelect={(key) => setSelectedSources((current) => toggleSourceSelection(current, key as SourceKey))}
        />
        <div className="flex flex-wrap items-center gap-2 mt-4 mb-3">
          <span className="text-[12px]" style={{ color: "var(--mute)" }}>情绪筛选</span>
          {([
            ["all", "全部"],
            ["positive", "正向"],
            ["neutral", "中性"],
            ["negative", "负向"],
          ] as const).map(([key, label]) => (
            <button key={key} type="button" onClick={() => setSentimentFilter((current) => current === key && key !== "all" ? "all" : key)} className="rounded-full px-2.5 py-1 text-[12px] cursor-pointer" style={{ background: sentimentFilter === key ? "var(--accent)" : "var(--bg-soft-2)", color: sentimentFilter === key ? "white" : "var(--body)" }}>{label}{key !== "all" && <span className="ml-1 opacity-70">{fmtNum(Number(totals[key] || 0))}</span>}</button>
          ))}
          {sentimentFilter !== "all" && <span className="text-[12px]" style={{ color: "var(--mute)" }}>当前显示 {filteredRecords.length} 条</span>}
        </div>
        {recordsLoading ? <Spinner /> : recordsError ? (
          <EmptyState title="反馈明细加载失败" hint="汇总数据已加载，但明细接口暂时不可用。" action={<Button size="sm" onClick={() => void refetchRecords()}>重试明细</Button>} />
        ) : <VoiceFeedbackList records={filteredRecords} summaries={feedbackSummaries.data} onOpenEvidence={setOpenEvidence} />}
      </Card>

      <Modal open={!!openEvidence} onClose={() => setOpenEvidence(null)} title="完整原文" width={720}>
        {openEvidence && (
          <div className="space-y-3">
            <EvidenceMetadata evidence={openEvidence} />
            {openEvidence.title && openEvidence.title !== openEvidence.text && (
              <h4 className="text-[15px] font-semibold break-words" style={{ color: "var(--ink)" }}>{openEvidence.title}</h4>
            )}
            <p className="whitespace-pre-wrap break-words text-[14px] leading-relaxed" style={{ color: "var(--body)" }}>
              <HighlightedQuote text={openEvidence.text} highlights={openEvidence.highlights} />
            </p>
            <EvidenceLink evidence={openEvidence} />
          </div>
        )}
      </Modal>

      <DataSourceModal
        open={sourceOpen}
        onClose={() => setSourceOpen(false)}
        draft={sourceDraft}
        counts={sourceCounts}
        onToggle={(key) => setSourceDraft((current) => current.includes(key) ? current.filter((item) => item !== key) : [...current, key])}
        onSelectAll={() => setSourceDraft(sourceDraft.length === VOICE_SOURCES.length ? [] : ALL_SOURCE_KEYS)}
        onApply={() => {
          if (!sourceDraft.length) return;
          setSelectedSources(sourceDraft);
          setSourceOpen(false);
        }}
        onImport={() => {
          setSourceOpen(false);
          setImportOpen(true);
        }}
        onManual={() => {
          setSourceOpen(false);
          setAddOpen(true);
        }}
      />
      <AddModal open={addOpen} onClose={() => setAddOpen(false)} brandId={brandId!} />
      <ImportModal open={importOpen} onClose={() => setImportOpen(false)} brandId={brandId!} />
    </div>
  );
}

function SentimentDistribution({ totals }: { totals: any }) {
  const total = Math.max(Number(totals.total || 0), 1);
  const rows = [
    { key: "positive", label: "正向", value: Number(totals.positive || 0), color: "var(--accent)" },
    { key: "neutral", label: "中性", value: Number(totals.neutral || 0), color: "var(--hairline-strong)" },
    { key: "negative", label: "负向", value: Number(totals.negative || 0), color: "var(--danger)" },
  ];
  return (
    <div className="space-y-4 pt-1">
      {rows.map((row) => {
        const rate = row.value / total;
        return (
          <div key={row.key}>
            <div className="flex items-center justify-between text-[13px] mb-1.5">
              <span style={{ color: "var(--body)" }}>{row.label}</span>
              <span className="tabular-nums font-medium" style={{ color: "var(--ink)" }}>{fmtNum(row.value)} · {(rate * 100).toFixed(0)}%</span>
            </div>
            <div className="h-2 rounded-full overflow-hidden" style={{ background: "var(--bg-soft-2)" }}>
              <div className="h-full rounded-full" style={{ width: `${rate * 100}%`, background: row.color }} />
            </div>
          </div>
        );
      })}
      <div className="rounded-md p-3 text-[12px] leading-relaxed" style={{ background: "var(--bg-soft)", color: "var(--mute)" }}>
        情绪结论来自每条反馈的文本分析；在下方明细中悬停情绪标签可查看判断依据。
      </div>
    </div>
  );
}

function SourceSummaryGrid({ sources, selectedSources, onSelect, summaries }: { sources: VoiceSourceSummary[]; selectedSources: SourceKey[]; onSelect: (key: string) => void; summaries?: FeedbackSummaries }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2.5">
      {sources.map((source) => {
        const selected = selectedSources.some((key) => key === source.key);
        return (
          <button key={source.key} type="button" onClick={() => onSelect(source.key)} className="text-left rounded-lg p-3 transition-colors cursor-pointer" style={{ border: selected ? "1px solid var(--accent)" : "1px solid var(--hairline)", background: selected ? "rgba(0,112,243,0.06)" : "var(--bg-soft)" }}>
            <div className="flex items-center justify-between gap-2">
              <span className="text-[13px] font-semibold" style={{ color: "var(--ink)" }}>{source.label}</span>
              <span className="text-[12px] tabular-nums" style={{ color: "var(--mute)" }}>{fmtNum(Number(source.count || 0))} 条</span>
            </div>
            {source.examples?.length > 0 && (
              <ul className="mt-2 space-y-1 text-[12px] leading-relaxed" style={{ color: "var(--ink)" }}>
                {source.examples.map((example) => (
                  <li key={example.key} className="flex gap-2">
                    <span className="min-w-0 break-words">{example.summary || summaries?.[example.evidence.record_id]?.summary || example.title}</span>
                    {example.count > 1 && <span className="shrink-0 tabular-nums" style={{ color: "var(--mute)" }}>{example.count} 条</span>}
                  </li>
                ))}
              </ul>
            )}
            <div className="flex items-center gap-2 mt-2 text-[11px]" style={{ color: "var(--mute)" }}>
              <span style={{ color: "var(--accent)" }}>正 {fmtNum(Number(source.positive || 0))}</span>
              <span>中 {fmtNum(Number(source.neutral || 0))}</span>
              <span style={{ color: source.negative ? "var(--danger)" : "var(--mute)" }}>负 {fmtNum(Number(source.negative || 0))}</span>
              {source.hot_risk_count > 0 && <Badge tone="negative">高热负向 {source.hot_risk_count}</Badge>}
            </div>
          </button>
        );
      })}
    </div>
  );
}

function IssueBoard({ topics, summaries, onOpenEvidence }: { topics: IssueCard[]; summaries?: FeedbackSummaries; onOpenEvidence: (evidence: VoiceEvidence) => void }) {
  if (!topics.length) return <EmptyState title="暂无突出问题" hint="当前筛选范围内暂无可归类的问题或功能建议。" />;
  return (
    <div className="space-y-2">
      {topics.map((topic) => {
        const priority = priorityView(topic.priority);
        const topicLabels = (topic.topics || []).map((item) => TOPIC_LABEL[item] || item);
        const suggestedTeams = topic.suggested_teams?.length ? topic.suggested_teams : [topic.suggested_team];
        const evidence = topic.evidence || [];
        const generated = summaries?.[evidence[0]?.record_id];
        const title = topic.id?.startsWith("quote_") ? generated?.headline || topic.title : topic.title;
        const description = topic.problem_summary || generated?.summary;
        return (
          <article key={topic.id} className="rounded-lg p-3.5" style={{ border: "1px solid var(--hairline)" }}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <Badge tone={priority.tone}>{priority.level}</Badge>
                <span className="text-[11px]" style={{ color: "var(--mute)" }}>{priority.label}</span>
              </div>
              <Badge tone="accent">建议关注：{suggestedTeams.map((team) => TEAM_LABEL[team] || team).join("、")}</Badge>
            </div>
            <h3 className="mt-2 text-[15px] font-semibold leading-snug break-words" style={{ color: "var(--ink)" }}>{title}</h3>
            <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[12px] tabular-nums" style={{ color: "var(--mute)" }}>
              <span>{topic.total} 条反馈 · 负向 {topic.negative} 条{topic.negative > 0 && ` · 负向率 ${(topic.negative_rate * 100).toFixed(0)}%`}</span>
              {topicLabels.map((label) => <Badge key={label} tone="neutral">#{label}</Badge>)}
            </div>
            {description && description.replace(/[。！？，.!?]+$/, "") !== title && (
              <p className="mt-2 text-[13px] leading-relaxed" style={{ color: "var(--ink)" }}>{description}</p>
            )}
            {topic.priority_reason && <div className="mt-1 text-[11px]" style={{ color: "var(--mute)" }}>{topic.priority_reason}</div>}
            {topic.impact && <ImpactMetrics impact={topic.impact} />}
            {evidence.length > 0 && (
              <div className="mt-3 space-y-1.5">
                <h4 className="text-[11px] font-medium" style={{ color: "var(--mute)" }}>代表原文</h4>
                {evidence.map((quote) => <EvidenceQuote key={quote.record_id} evidence={quote} onOpen={onOpenEvidence} />)}
              </div>
            )}
          </article>
        );
      })}
    </div>
  );
}

function HighlightedQuote({ text, highlights }: { text: string; highlights: string[] }) {
  const escaped = [...highlights].filter(Boolean).sort((a, b) => b.length - a.length)
    .map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!escaped.length) return <>{text}</>;
  return <>{text.split(new RegExp(`(${escaped.join("|")})`, "gi")).map((part, index) => index % 2 ? (
    <mark key={index} className="rounded px-0.5 font-medium" style={{ background: "var(--warning-soft)", color: "var(--ink)" }}>{part}</mark>
  ) : part)}</>;
}

function EvidenceMetadata({ evidence }: { evidence: VoiceEvidence }) {
  const platform = evidence.platform === "app_store" ? "App Store" : evidence.platform === "google_play" ? "Google Play" : evidence.platform;
  const source = evidence.source_key && SOURCE_LABEL[evidence.source_key];
  const rating = Number(evidence.rating);
  const details = [platform || source, evidence.author, evidence.occurred_at && fmtDateTime(evidence.occurred_at), rating >= 1 && rating <= 5 ? `${rating}/5 星` : ""].filter(Boolean);
  return <span className="text-[11px] break-words" style={{ color: "var(--mute)" }}>{details.join(" · ")}</span>;
}

function EvidenceLink({ evidence }: { evidence: VoiceEvidence }) {
  if (!evidence.url) return null;
  const labels = { source: "打开来源页面", store: "打开应用页面", review: "打开商店原评论", thread: "打开所在讨论" };
  const label = labels[evidence.url_kind || "source"];
  return <a href={evidence.url} target="_blank" rel="noreferrer" className="text-[12px] hover:underline" style={{ color: "var(--accent)" }}>{label} ↗</a>;
}

function EvidenceQuote({ evidence, onOpen, showLink = false }: { evidence: VoiceEvidence; onOpen: (evidence: VoiceEvidence) => void; showLink?: boolean }) {
  return (
    <blockquote className="rounded-md px-3 py-2" style={{ background: "var(--bg-soft)", borderLeft: "2px solid var(--hairline-strong)" }}>
      <p className="whitespace-pre-wrap break-words text-[13px] leading-relaxed" style={{ color: "var(--body)" }}>
        <HighlightedQuote text={evidence.excerpt} highlights={evidence.highlights} />
      </p>
      <footer className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
        <EvidenceMetadata evidence={evidence} />
        <button type="button" onClick={() => onOpen(evidence)} className="cursor-pointer text-[12px] hover:underline" style={{ color: "var(--accent)" }}>查看完整原文</button>
        {(showLink || evidence.url_kind === "review" || evidence.url_kind === "source") && <EvidenceLink evidence={evidence} />}
      </footer>
    </blockquote>
  );
}

function VoiceFeedbackList({ records, summaries, onOpenEvidence }: { records: RecordItem[]; summaries?: FeedbackSummaries; onOpenEvidence: (evidence: VoiceEvidence) => void }) {
  if (!records.length) return <EmptyState title="暂无反馈" hint="当前筛选范围内暂无反馈。" />;
  return (
    <div className="space-y-2">
      {records.map((record) => {
        const issue = record.voice_issue;
        if (!issue) return null;
        const generated = summaries?.[record.id];
        const summary = issue.summary || generated?.summary;
        const originalTitle = !summary && issue.evidence.title !== issue.evidence.text ? issue.evidence.title : "";
        const title = issue.key.startsWith("quote_") ? generated?.headline || originalTitle : issue.title;
        const source = record.voice_source && SOURCE_LABEL[record.voice_source];
        return (
          <article key={record.id} className="panel p-4 space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <SentimentBadge record={record} />
              {source && <Badge tone="neutral">{source}</Badge>}
              {record.hot_risk && <Badge tone="negative">高热度负向</Badge>}
            </div>
            {title && <h3 className="text-[14px] font-semibold break-words" style={{ color: "var(--ink)" }}>{title}</h3>}
            {summary && summary.replace(/[。！？，.!?]+$/, "") !== title && (
              <p className="text-[14px] leading-relaxed" style={{ color: "var(--ink)" }}>{summary}</p>
            )}
            <EvidenceQuote evidence={issue.evidence} onOpen={onOpenEvidence} showLink />
          </article>
        );
      })}
    </div>
  );
}

function ImpactMetrics({ impact }: { impact: any }) {
  const metrics = [
    ["浏览", "views"],
    ["评论/回复", "comments"],
    ["互动", "interactions"],
    ["触达", "reach"],
    ["互动速度", "velocity"],
  ] as const;
  const visible = metrics.filter(([, key]) => Number(impact[key] || 0) > 0);
  if (!visible.length) return null;
  return (
    <div className="mt-2 rounded-md px-2.5 py-2" style={{ background: "var(--bg-soft)", border: "1px solid var(--hairline)" }}>
      <div className="text-[11px] font-medium" style={{ color: "var(--mute)" }}>传播数据（相对当前范围 {fmtNum(Number(impact.comparison_count || 0))} 条帖子）</div>
      <div className="flex flex-wrap gap-x-3 gap-y-1 mt-1 text-[11px]" style={{ color: "var(--body)" }}>
        {visible.map(([label, key]) => {
          const relative = impact.relative?.[key];
          const percentile = relative?.percentile;
          const percentileLabel = percentile == null ? "暂无对比" : `前${Math.max(1, Math.ceil((relative.rank || 1) / Math.max(relative.sample_size || 1, 1) * 100))}%`;
          return <span key={key}><span style={{ color: "var(--mute)" }}>{label}</span> {fmtNum(Number(impact[key]))} · <span style={{ color: percentile != null && percentile >= 0.75 ? "var(--warning)" : "var(--body)" }}>{percentileLabel}</span></span>;
        })}
      </div>
    </div>
  );
}

function P0Panel({ p0 }: { p0: any }) {
  if (!p0.total) {
    return (
      <div className="rounded-lg p-4" style={{ background: "rgba(0,112,243,0.08)", border: "1px solid rgba(0,112,243,0.18)" }}>
        <div className="flex items-center gap-2 text-[14px] font-medium" style={{ color: "var(--accent)" }}>
          <ShieldCheck size={17} /> 未发现 P0 风险信号
        </div>
        <p className="text-[12px] mt-2 leading-relaxed" style={{ color: "var(--mute)" }}>
          已扫描设备安全、隐私与账号安全、数据丢失、异常扣款和大范围服务故障。
        </p>
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {p0.issues.map((issue: any) => (
        <div key={issue.key} className="rounded-lg p-3" style={{ background: "var(--danger-soft)", border: "1px solid rgba(238,0,0,0.18)" }}>
          <div className="flex items-start gap-2">
            <AlertTriangle size={16} className="shrink-0 mt-0.5" style={{ color: "var(--danger)" }} />
            <div className="min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[13px] font-semibold" style={{ color: "var(--danger)" }}>{issue.label}</span>
                <Badge tone="negative">{issue.count} 条</Badge>
              </div>
              <p className="text-[12px] mt-1 line-clamp-3" style={{ color: "var(--body)" }}>“{issue.representative}”</p>
              <div className="text-[12px] mt-2" style={{ color: "var(--mute)" }}>立即对接：{TEAM_LABEL[issue.owner_team] || issue.owner_team}</div>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function DataSourceModal({
  open,
  onClose,
  draft,
  counts,
  onToggle,
  onSelectAll,
  onApply,
  onImport,
  onManual,
}: {
  open: boolean;
  onClose: () => void;
  draft: SourceKey[];
  counts: Record<string, number>;
  onToggle: (key: SourceKey) => void;
  onSelectAll: () => void;
  onApply: () => void;
  onImport: () => void;
  onManual: () => void;
}) {
  return (
    <Modal open={open} onClose={onClose} title="数据来源" width={780}>
      <div className="flex items-start justify-between gap-3 mb-4">
        <div>
          <p className="text-[14px]" style={{ color: "var(--body)" }}>选择要纳入用户之声分析的渠道，可多选。</p>
          <p className="text-[12px] mt-0.5" style={{ color: "var(--mute)" }}>应用后，情绪、标签、趋势、突出问题和 P0 扫描会同步更新。</p>
        </div>
        <button onClick={onSelectAll} className="text-[12px] shrink-0 cursor-pointer" style={{ color: "var(--accent)" }}>
          {draft.length === VOICE_SOURCES.length ? "清空" : "全选"}
        </button>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
        {VOICE_SOURCES.map((source) => {
          const selected = draft.includes(source.key);
          const Icon = source.icon;
          return (
            <button
              key={source.key}
              type="button"
              aria-pressed={selected}
              onClick={() => onToggle(source.key)}
              className="flex items-start gap-3 rounded-lg p-3.5 text-left cursor-pointer transition-colors"
              style={{
                border: selected ? "1px solid var(--accent)" : "1px solid var(--hairline-strong)",
                background: selected ? "rgba(0,112,243,0.06)" : "var(--panel)",
              }}
            >
              <span
                className="h-8 w-8 rounded-md inline-flex items-center justify-center shrink-0"
                style={{ background: selected ? "var(--accent)" : "var(--bg-soft-2)", color: selected ? "white" : "var(--body)" }}
              >
                <Icon size={16} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex items-center justify-between gap-2">
                  <span className="text-[13px] font-medium" style={{ color: "var(--ink)" }}>{source.label}</span>
                  <span className="text-[12px] tabular-nums" style={{ color: "var(--mute)" }}>{fmtNum(Number(counts[source.key] || 0))} 条</span>
                </span>
                <span className="block text-[12px] mt-0.5 leading-relaxed" style={{ color: "var(--mute)" }}>{source.description}</span>
              </span>
              <span
                className="h-4 w-4 rounded inline-flex items-center justify-center shrink-0 mt-0.5"
                style={{ border: selected ? "1px solid var(--accent)" : "1px solid var(--hairline-strong)", background: selected ? "var(--accent)" : "transparent", color: "white" }}
              >
                {selected && <Check size={11} strokeWidth={3} />}
              </span>
            </button>
          );
        })}
      </div>
      {!draft.length && <p className="text-[12px] mt-3" style={{ color: "var(--danger)" }}>请至少选择一个数据来源。</p>}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pt-5 mt-5" style={{ borderTop: "1px solid var(--hairline)" }}>
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={onImport}><FileUp size={14} />导入 CSV</Button>
          <Button size="sm" onClick={onManual}><MessageSquareText size={14} />手动录入</Button>
        </div>
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>取消</Button>
          <Button variant="primary" disabled={!draft.length} onClick={onApply}>应用筛选 · {draft.length} 个来源</Button>
        </div>
      </div>
    </Modal>
  );
}

function AddModal({ open, onClose, brandId }: { open: boolean; onClose: () => void; brandId: string }) {
  const { addRecord } = useVocMutations();
  const [form, setForm] = useState<any>({ platform: "", body: "" });
  return (
    <Modal open={open} onClose={onClose} title="录入用户反馈">
      <div className="space-y-3">
        <Field label="来源平台"><Input value={form.platform} onChange={(event) => setForm({ ...form, platform: event.target.value })} placeholder="如客服工单 / 用户访谈 / 线下活动" /></Field>
        <Field label="标题（可选）"><Input value={form.title || ""} onChange={(event) => setForm({ ...form, title: event.target.value })} /></Field>
        <Field label="反馈内容"><Textarea rows={4} value={form.body} onChange={(event) => setForm({ ...form, body: event.target.value })} /></Field>
        <div className="flex justify-end gap-2 pt-1">
          <Button onClick={onClose}>取消</Button>
          <Button
            variant="primary"
            disabled={!form.body || addRecord.isPending}
            onClick={async () => {
              await addRecord.mutateAsync({ ...form, brand_id: brandId, dimension: "voc", data_type: "user_voice", source_id: "manual_csv" });
              setForm({ platform: "", body: "" });
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

function ImportModal({ open, onClose, brandId }: { open: boolean; onClose: () => void; brandId: string }) {
  const { importRows } = useVocMutations();
  const [text, setText] = useState("");
  const parse = (raw: string) => {
    const lines = raw.trim().split(/\r?\n/);
    if (!lines.length) return [];
    const headers = lines[0].split(",").map((header) => header.trim());
    return lines.slice(1).map((line) => {
      const cells = line.split(",");
      const row: Record<string, string> = {};
      headers.forEach((header, index) => (row[header] = (cells[index] || "").trim()));
      return row;
    });
  };
  return (
    <Modal open={open} onClose={onClose} title="导入 CSV 反馈" width={640}>
      <p className="text-[13px] mb-2" style={{ color: "var(--mute)" }}>粘贴 CSV，首行为表头（建议含 body/platform/title/author 列）。</p>
      <Textarea rows={8} value={text} onChange={(event) => setText(event.target.value)} placeholder={"platform,body\n客服工单,这个新版本一直闪退"} className="font-mono" />
      <div className="flex justify-end gap-2 pt-3">
        <Button onClick={onClose}>取消</Button>
        <Button
          variant="primary"
          disabled={!text.trim() || importRows.isPending}
          onClick={async () => {
            await importRows.mutateAsync({ rows: parse(text), brand_id: brandId, source_id: "manual_csv" });
            setText("");
            onClose();
          }}
        >
          导入
        </Button>
      </div>
    </Modal>
  );
}
