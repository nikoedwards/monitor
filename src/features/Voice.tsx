import { useState } from "react";
import {
  AlertTriangle,
  Check,
  Database,
  FileUp,
  MessageSquareText,
  MessagesSquare,
  ShoppingBag,
  ShieldCheck,
  Smartphone,
  UserRound,
  Video,
} from "lucide-react";
import { useParams } from "react-router-dom";
import { TrendChart } from "../components/charts";
import { RecordList } from "../components/RecordList";
import { Badge, Button, Card, EmptyState, Field, Input, Modal, SectionTitle, Spinner, StatCard, Textarea } from "../components/ui";
import { TimeRangePicker } from "../components/TimeRangePicker";
import { useVocMutations, useVocRecords, useVocSummary } from "../lib/hooks";
import { useTimeRange } from "../lib/timeRange";
import { fmtNum, TEAM_LABEL } from "../lib/format";
import { SmartSummary } from "../components/SmartSummary";

type SourceKey =
  | "sales_reviews"
  | "marketing_videos"
  | "social_posts_comments"
  | "creator_comments"
  | "app_reviews"
  | "manual_feedback";

type BadgeTone = "neutral" | "positive" | "negative" | "warning" | "accent";

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
  const { data: summary, isLoading } = useVocSummary(brandId, range, selectedSources);
  const { data: records = [], isLoading: recordsLoading } = useVocRecords(brandId, range, selectedSources, 40, sentimentFilter === "all" ? undefined : sentimentFilter);

  if (isLoading || !summary) return <Spinner />;
  const totals = summary.totals;
  const p0 = summary.p0 || { total: 0, issue_count: 0, issues: [] };
  const sourceCatalog = summary.source_catalog || [];
  const sourceCounts = Object.fromEntries(sourceCatalog.map((source: any) => [source.key, source.count]));
  const filteredRecords = records;

  const openSourcePicker = () => {
    setSourceDraft(selectedSources);
    setSourceOpen(true);
  };

  return (
    <div className="space-y-6">
      <SectionTitle
        title="用户之声"
        subtitle="聚合各业务板块的真实用户反馈，识别情绪、问题标签、责任团队与高优先级风险"
        action={
          <div className="flex flex-wrap items-center gap-2">
            <TimeRangePicker />
            <Button variant="primary" onClick={openSourcePicker}>
              <Database size={15} />
              数据来源
              <span className="text-[12px] opacity-70">{selectedSources.length}/{VOICE_SOURCES.length}</span>
            </Button>
          </div>
        }
      />

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
                onClick={() => setSelectedSources([key])}
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
        <SmartSummary brandId={brandId} dimension="voc" range={range} />
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
            subtitle="按负向声量与集中度排序，并给出建议关注部门"
            hint="P0 仅用于命中安全、隐私、数据丢失、异常扣款或大范围故障等高危信号的问题；其余问题按负向声量划分 P1-P3。"
          />
          <IssueBoard topics={summary.topics || []} />
        </Card>
        <div className="space-y-4">
          <Card>
            <SectionTitle title="P0 风险扫描" subtitle="高优先级用户问题自动筛查" />
            <P0Panel p0={p0} />
          </Card>
        </div>
      </div>

      <Card>
        <SectionTitle title="原始反馈明细" subtitle="每个数据源先给出摘要，再查看具体反馈、情绪判断、标签和采集依据" />
        <SourceSummaryGrid sources={sourceCatalog} selectedSources={selectedSources} onSelect={(key) => setSelectedSources([key as SourceKey])} />
        <div className="flex flex-wrap items-center gap-2 mt-4 mb-3">
          <span className="text-[12px]" style={{ color: "var(--mute)" }}>情绪筛选</span>
          {([
            ["all", "全部"],
            ["positive", "正向"],
            ["neutral", "中性"],
            ["negative", "负向"],
          ] as const).map(([key, label]) => (
            <button key={key} type="button" onClick={() => setSentimentFilter(key)} className="rounded-full px-2.5 py-1 text-[12px] cursor-pointer" style={{ background: sentimentFilter === key ? "var(--accent)" : "var(--bg-soft-2)", color: sentimentFilter === key ? "white" : "var(--body)" }}>{label}{key !== "all" && <span className="ml-1 opacity-70">{fmtNum(Number(totals[key] || 0))}</span>}</button>
          ))}
          {sentimentFilter !== "all" && <span className="text-[12px]" style={{ color: "var(--mute)" }}>当前显示 {filteredRecords.length} 条</span>}
        </div>
        {recordsLoading ? <Spinner /> : <RecordList records={filteredRecords} emptyHint="当前时间和来源范围内暂无反馈，请调整数据来源或补充导入。" />}
      </Card>

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

function SourceSummaryGrid({ sources, selectedSources, onSelect }: { sources: any[]; selectedSources: SourceKey[]; onSelect: (key: string) => void }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2.5">
      {sources.map((source) => {
        const selected = selectedSources.includes(source.key);
        return (
          <button key={source.key} type="button" onClick={() => onSelect(source.key)} className="text-left rounded-lg p-3 transition-colors cursor-pointer" style={{ border: selected ? "1px solid var(--accent)" : "1px solid var(--hairline)", background: selected ? "rgba(0,112,243,0.06)" : "var(--bg-soft)" }}>
            <div className="flex items-center justify-between gap-2">
              <span className="text-[13px] font-semibold" style={{ color: "var(--ink)" }}>{source.label}</span>
              <span className="text-[12px] tabular-nums" style={{ color: "var(--mute)" }}>{fmtNum(Number(source.count || 0))} 条</span>
            </div>
            <div className="mt-2">
              <div className="text-[11px] font-medium" style={{ color: "var(--mute)" }}>主要问题</div>
              <p className="text-[12px] mt-0.5 leading-relaxed" style={{ color: "var(--ink)" }}>{source.problem_summary || source.summary}</p>
            </div>
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

function IssueBoard({ topics }: { topics: any[] }) {
  if (!topics.length) return <EmptyState title="暂无突出问题" hint="当前筛选范围内没有负向问题标签。" />;
  return (
    <div className="space-y-2">
      {topics.map((topic) => {
        const priority = priorityView(topic.priority);
        const topicLabels = (topic.topics || [topic.topic]).map((item: string) => TOPIC_LABEL[item] || item);
        const suggestedTeams = topic.suggested_teams?.length ? topic.suggested_teams : [topic.suggested_team];
        return (
          <div key={topic.topic} className="rounded-lg p-3.5" style={{ border: "1px solid var(--hairline)" }}>
            <div className="flex flex-col md:flex-row md:items-start gap-3">
              <div className="flex items-center gap-2 md:w-[92px] shrink-0">
                <Badge tone={priority.tone}>{priority.level}</Badge>
                <span className="text-[11px]" style={{ color: "var(--mute)" }}>{priority.label}</span>
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-[14px] font-semibold" style={{ color: "var(--ink)" }}>{topicLabels.map((label: string) => `#${label}`).join("、")}</span>
                  <span className="text-[12px] tabular-nums" style={{ color: "var(--mute)" }}>
                    {topic.total} 条反馈 · 负向 {topic.negative} 条 · 负向率 {(topic.negative_rate * 100).toFixed(0)}%
                  </span>
                </div>
                <div className="mt-2">
                  <div className="text-[11px] font-medium" style={{ color: "var(--mute)" }}>问题概述</div>
                  <p className="text-[13px] mt-0.5 leading-relaxed" style={{ color: "var(--ink)" }}>{topic.problem_summary || "当前未生成明确的问题描述。"}</p>
                </div>
                {topic.representative && (
                  <div className="mt-2">
                    <div className="text-[11px] font-medium" style={{ color: "var(--mute)" }}>代表原文</div>
                    {topic.representative_url ? (
                      <a href={topic.representative_url} target="_blank" rel="noreferrer" className="block text-[12px] mt-0.5 line-clamp-2 leading-relaxed hover:underline" style={{ color: "var(--accent)" }} title="打开原文">
                        “{topic.representative}” ↗
                      </a>
                    ) : (
                      <p className="text-[12px] mt-0.5 line-clamp-2 leading-relaxed" style={{ color: "var(--body)" }}>“{topic.representative}”</p>
                    )}
                  </div>
                )}
                <div className="flex items-center gap-1.5 mt-2 flex-wrap">
                  {(topic.source_keys || []).map((key: string) => <Badge key={key} tone="neutral">{SOURCE_LABEL[key] || key}</Badge>)}
                </div>
              </div>
              <div className="flex items-center shrink-0">
                <Badge tone="accent">建议关注：{suggestedTeams.map((team: string) => TEAM_LABEL[team] || team).join("、")}</Badge>
              </div>
            </div>
          </div>
        );
      })}
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
