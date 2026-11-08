import { AlertTriangle, ArrowRight, ArrowUpRight, Lightbulb } from "lucide-react";
import { Link } from "react-router-dom";
import { Badge } from "./ui";

const DOMAINS: Record<string, { label: string; path: string }> = {
  sales: { label: "销售", path: "sales" }, market: { label: "市场", path: "market-share" },
  hiring: { label: "招聘", path: "hiring" }, marketing: { label: "营销", path: "marketing" },
  voice: { label: "用户之声", path: "voice" }, web: { label: "网页动态", path: "web" },
};
const POLARITY: Record<string, { label: string; tone: "positive" | "negative" | "warning" | "neutral" }> = {
  positive: { label: "正向信号", tone: "positive" }, negative: { label: "负向信号", tone: "negative" },
  mixed: { label: "两极分化", tone: "warning" }, neutral: { label: "值得观察", tone: "neutral" },
};
const METRICS: Record<string, string> = {
  rating: "评分", rating_count: "累计评分数", review_count: "累计评论数", version: "版本", appVersion: "评论版本",
  current_version_release_date: "版本发布时间", price: "价格", currency: "币种", rank: "排名", bsr: "BSR",
  units_est: "销量估算", revenue_est: "销售额估算", estimate_method: "估算方法", estimate_confidence: "估算置信度",
  sentiment: "情绪", positive: "正向", negative: "负向", neutral: "中性", likes: "点赞", comments: "评论", views: "浏览", score: "得分",
  department: "部门", city: "城市", first_seen: "首次发现", posted_at: "发布于", closed_at: "下线于",
  started_at: "投放开始", first_seen_at: "首次发现", stopped_at: "停止于", active_status: "投放状态", status: "状态",
  change_score: "文本变化幅度", visual_change_score: "视觉变化幅度", release_notes: "更新说明", releaseNotes: "更新说明",
};

function SourceEvidence({ source, index, brandId }: { source: any; index: number; brandId?: string }) {
  const meta = DOMAINS[source.domain];
  const metrics = Object.entries(source.metrics || {}).filter(([key, value]) => METRICS[key] && value != null && value !== "" && typeof value !== "object");
  return <div className="py-3 first:pt-0 last:pb-0" style={{ borderBottom: "1px solid var(--hairline)" }}>
    <div className="flex flex-wrap items-center gap-2 text-[11px]" style={{ color: "var(--mute)" }}>
      <span>[{index + 1}] {meta?.label}</span><span>{String(source.at || "").slice(0, 10)}</span>
      {source.platform && <span>{source.platform}</span>}{source.region && <span>{source.region}</span>}{source.product && <span>{source.product}</span>}
    </div>
    <div className="mt-1 text-[13px] font-medium" style={{ color: "var(--ink)" }}>{source.title}</div>
    <p className="mt-1 text-[12px] whitespace-pre-wrap break-words leading-relaxed" style={{ color: "var(--body)" }}>{source.detail}</p>
    {metrics.length > 0 && <div className="flex flex-wrap gap-x-3 gap-y-1 mt-2 text-[11px]" style={{ color: "var(--body)" }}>{metrics.map(([key, value]) => <span key={key}>{METRICS[key]}：{String(value)}</span>)}</div>}
    {source.kind === "module" && <details className="text-[11px] mt-2" style={{ color: "var(--mute)" }}><summary className="cursor-pointer">查看统计口径与数值</summary><pre className="mt-2 whitespace-pre-wrap break-all">{JSON.stringify(source.metrics, null, 2)}</pre></details>}
    <div className="flex gap-4 mt-2 text-[12px]" style={{ color: "var(--accent)" }}>
      {source.url && <a href={source.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1">查看原文<ArrowUpRight size={12} /></a>}
      {brandId && meta && <Link to={`/brand/${brandId}/${meta.path}`} className="inline-flex items-center gap-1">{meta.label}明细<ArrowUpRight size={12} /></Link>}
    </div>
  </div>;
}

function ResearchCard({ item, index, hypothesis, brandId }: { item: any; index: number; hypothesis?: boolean; brandId?: string }) {
  const polarity = POLARITY[item.polarity] || POLARITY.neutral;
  const sources = Array.isArray(item.evidence) ? item.evidence : [];
  const domains = Array.isArray(item.domains) ? item.domains : [];
  const priority = item.priority || (item.polarity === "negative" ? "high" : "medium");
  const priorityLabel = priority === "high" ? "高优先" : priority === "medium" ? "中优先" : "观察";
  return <article className="min-w-0 rounded-lg p-4 space-y-3" style={{ border: hypothesis && index === 0 ? "1px solid color-mix(in srgb, var(--accent) 55%, var(--hairline-strong))" : "1px solid var(--hairline-strong)", background: hypothesis && index === 0 ? "var(--bg-soft-2)" : "var(--panel)" }}>
    <div className="flex flex-wrap items-center justify-between gap-2">
      <span className="text-[11px] font-mono" style={{ color: "var(--mute)" }}>{hypothesis ? "深度洞察" : "优先事项"} / {String(index + 1).padStart(2, "0")}</span>
      <div className="flex items-center gap-1.5">
        {!hypothesis && <Badge tone={priority === "high" ? "negative" : priority === "medium" ? "warning" : "neutral"}>{priorityLabel}</Badge>}
        <Badge tone={hypothesis ? "accent" : polarity.tone}>{hypothesis ? item.confidence === "medium" ? "交叉支持 · 待验证" : "线索 · 待验证" : polarity.label}</Badge>
      </div>
    </div>
    <h3 className="text-[16px] font-semibold leading-snug" style={{ color: "var(--ink)" }}>{item.title}</h3>
    <div className="flex flex-wrap items-center gap-1.5 text-[11px]" style={{ color: "var(--mute)" }}>{domains.map((domain: string, i: number) => <span className="inline-flex items-center gap-1.5" key={domain}>{i > 0 && <ArrowRight size={11} />}<span>{DOMAINS[domain]?.label || domain}</span></span>)}</div>
    <div className="text-[13px] leading-relaxed" style={{ color: "var(--body)" }}><span className="text-[11px] block mb-1" style={{ color: "var(--mute)" }}>观察到的事实</span>{item.observation}</div>
    {item.inference && <div className="text-[13px] leading-relaxed" style={{ color: "var(--ink)" }}><span className="text-[11px] block mb-1" style={{ color: "var(--accent)" }}>{hypothesis ? "连接起来看" : "可能意味着"}</span>{item.inference}</div>}
    {item.why_it_matters && <p className="text-[12px] leading-relaxed" style={{ color: "var(--body)" }}><b className="font-medium" style={{ color: "var(--ink)" }}>为何值得关注 · </b>{item.why_it_matters}</p>}
    {item.next_signal && <div className="p-3 rounded-md text-[12px] leading-relaxed" style={{ background: "var(--bg-soft)", color: "var(--body)" }}><div className="font-medium mb-1" style={{ color: "var(--ink)" }}>接下来验证{item.time_horizon ? ` · ${item.time_horizon}` : ""}</div>{item.next_signal}</div>}
    <details className="text-[12px]" style={{ color: "var(--body)" }}>
      <summary className="cursor-pointer select-none" style={{ color: "var(--accent)" }}>展开证据链（{sources.length}）{item.alternative ? "与其他解释" : ""}</summary>
      <div className="mt-3 space-y-3">
        {item.alternative && <p className="p-3 rounded-md leading-relaxed" style={{ background: "var(--bg-soft-2)" }}><b className="font-medium">也可能是 · </b>{item.alternative}</p>}
        <div>{sources.map((source: any, i: number) => <SourceEvidence key={source.id} source={source} index={i} brandId={brandId} />)}</div>
      </div>
    </details>
  </article>;
}

export function OverviewResearch({ data, brandId }: { data: any; brandId?: string }) {
  const insights = Array.isArray(data.deep_insights) ? data.deep_insights : (Array.isArray(data.insights) ? data.insights : []);
  const priority = Array.isArray(data.priority_alerts) ? data.priority_alerts : (Array.isArray(data.recent_highlights) ? data.recent_highlights : []);
  const coverage = data.coverage || {};
  return <div className="space-y-5">
    <div className="p-4 rounded-lg" style={{ background: "var(--bg-soft-2)" }}>
      <div className="text-[11px] mb-2" style={{ color: "var(--accent)" }}>{data.mode === "ai" ? "先给结论" : "已采集数据速览"}</div>
      <p className="text-[15px] font-medium leading-relaxed" style={{ color: "var(--ink)" }}>{data.summary}</p>
      {Object.keys(coverage).length > 0 && <div className="flex flex-wrap gap-x-4 gap-y-2 mt-3 text-[11px]" style={{ color: "var(--mute)" }}>{Object.entries(DOMAINS).map(([key, meta]) => <span key={key} style={{ color: coverage[key]?.status === "ready" ? "var(--body)" : "var(--mute)" }}>{meta.label} · {coverage[key]?.status === "ready" ? "有数据" : "待补充"}</span>)}</div>}
    </div>
    <section>
      <div className="flex items-center gap-2 mb-1"><AlertTriangle size={15} style={{ color: "var(--warning)" }} /><h3 className="text-[14px] font-medium" style={{ color: "var(--ink)" }}>优先关注</h3><span className="text-[11px]" style={{ color: "var(--mute)" }}>{priority.length} 条</span></div>
      <p className="text-[12px] mb-3" style={{ color: "var(--mute)" }}>先处理最可能影响经营判断的异常：客诉、评分、产品变化和关键动作信号。</p>
      {priority.length ? <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">{priority.map((item: any, i: number) => <ResearchCard key={i} item={item} index={i} brandId={brandId} />)}</div> : <p className="text-[13px] p-4 rounded-md" style={{ background: "var(--bg-soft)", color: "var(--mute)" }}>{data.mode === "ai" ? "当前没有证据充分的高优先事项。" : "近期优先事项暂未生成。"}</p>}
    </section>
    <section>
      <div className="flex items-center gap-2 mb-1"><Lightbulb size={15} style={{ color: "var(--accent)" }} /><h3 className="text-[14px] font-medium" style={{ color: "var(--ink)" }}>深度预测与洞察</h3><span className="text-[11px]" style={{ color: "var(--mute)" }}>{insights.length} 条</span></div>
      <p className="text-[12px] mb-3" style={{ color: "var(--mute)" }}>把招聘 JD、投放、用户反馈、销售、市场和官网变化串起来，寻找单看一个板块看不到的下一步。</p>
      {insights.length ? <div className={`grid grid-cols-1 ${insights.length > 1 ? "xl:grid-cols-2" : ""} gap-3`}>{insights.map((item: any, i: number) => <ResearchCard key={i} item={item} index={i} hypothesis brandId={brandId} />)}</div> : <p className="text-[13px] p-4 rounded-md" style={{ background: "var(--bg-soft)", color: "var(--mute)" }}>{data.mode === "ai" ? "当前证据尚不足以支持跨板块预测；继续积累不同来源的同一实体信号。" : "深度洞察暂未生成；当前仅展示已采集的经营统计。"}</p>}
    </section>
    {Array.isArray(data.caveats) && data.caveats.length > 0 && <details className="text-[12px]" style={{ color: "var(--mute)" }}><summary className="cursor-pointer">数据范围与判断边界</summary><ul className="list-disc pl-5 space-y-1 mt-2">{data.caveats.map((c: string, i: number) => <li key={i}>{c}</li>)}</ul></details>}
  </div>;
}
