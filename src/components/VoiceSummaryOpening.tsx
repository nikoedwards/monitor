import { fmtNum } from "../lib/format";
import { Badge } from "./ui";

type VoiceOpening = {
  positive: string;
  negative: string;
  sample_count: number;
  frequent_issues: { title: string; detail: string; count: number; sources: string[] }[];
  strategic_insights: {
    title: string;
    observation: string;
    implication: string;
    cross_domain: boolean;
    evidence: { id: string; title: string; url: string; domain: string }[];
  }[];
};

const SENTIMENT_LABELS: Record<string, string> = {
  positive: "整体偏正向",
  negative: "整体偏负向",
  neutral: "整体偏中性",
  mixed: "情绪分化",
};

const DOMAIN_LABELS: Record<string, string> = {
  voice: "用户之声", sales: "销售", market: "市场", hiring: "招聘", marketing: "营销", web: "网页动态",
};

export function VoiceSummaryOpening({
  summary, sentiment, opening, recordCount, sourceLabels,
}: {
  summary: string;
  sentiment: { overall?: string; positive?: number; neutral?: number; negative?: number };
  opening: VoiceOpening;
  recordCount: number;
  sourceLabels?: Record<string, string>;
}) {
  const overall = sentiment.overall || "";
  const hasFeedback = recordCount > 0;
  return (
    <div className="divide-y divide-[var(--hairline)] rounded-lg border border-[var(--hairline)]">
      <section className="p-4 sm:p-5">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-[15px] font-semibold" style={{ color: "var(--ink)" }}>整体情绪</h3>
          {SENTIMENT_LABELS[overall] && (
            <Badge tone={overall === "positive" ? "positive" : overall === "negative" ? "negative" : "neutral"}>
              {SENTIMENT_LABELS[overall]}
            </Badge>
          )}
          {hasFeedback && (
            <span className="text-[12px] tabular-nums" style={{ color: "var(--mute)" }}>
              正向 {fmtNum(sentiment.positive || 0)} · 中性 {fmtNum(sentiment.neutral || 0)} · 负向 {fmtNum(sentiment.negative || 0)} · 共 {fmtNum(recordCount)} 条
            </span>
          )}
        </div>
        <p className="mt-2 text-[14px] leading-relaxed" style={{ color: "var(--ink)" }}>{summary}</p>
        {hasFeedback && (
          <dl className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
            {([
              ["用户肯定", opening.positive, sentiment.positive ? "正向反馈较分散，暂无明确的共同主题。" : "样本中暂无明确的正向反馈。"],
              ["用户不满", opening.negative, sentiment.negative ? "负向反馈较分散，暂无明确的共同主题。" : "样本中暂无明确的负向反馈。"],
            ] as const).map(([label, detail, empty]) => (
              <div key={label} className="rounded-md px-3 py-2" style={{ background: "var(--bg-soft)" }}>
                <dt className="text-[12px] font-medium" style={{ color: "var(--ink)" }}>{label}</dt>
                <dd className="mt-0.5 text-[13px] leading-relaxed" style={{ color: "var(--body)" }}>{detail || empty}</dd>
              </div>
            ))}
          </dl>
        )}
      </section>

      <section className="p-4 sm:p-5">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <h3 className="text-[15px] font-semibold" style={{ color: "var(--ink)" }}>高频反馈问题</h3>
          {opening.sample_count > 0 && <span className="text-[12px]" style={{ color: "var(--mute)" }}>基于 {fmtNum(opening.sample_count)} 条反馈样本</span>}
        </div>
        {opening.frequent_issues.length ? (
          <ol className="mt-2 space-y-3">
            {opening.frequent_issues.map((issue) => (
              <li key={issue.title} className="space-y-1">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="text-[13px] font-semibold" style={{ color: "var(--ink)" }}>{issue.title}</span>
                  <Badge tone="warning">{fmtNum(issue.count)} 条提及</Badge>
                  {!!issue.sources.length && <span className="text-[12px]" style={{ color: "var(--mute)" }}>{issue.sources.map((source) => sourceLabels?.[source] || source).join("、")}</span>}
                </div>
                {issue.detail && <p className="text-[13px] leading-relaxed" style={{ color: "var(--body)" }}>{issue.detail}</p>}
              </li>
            ))}
          </ol>
        ) : <p className="mt-2 text-[13px]" style={{ color: "var(--mute)" }}>当前样本中暂无可确认的重复问题。</p>}
      </section>

      <section className="p-4 sm:p-5">
        <h3 className="text-[15px] font-semibold" style={{ color: "var(--ink)" }}>战略洞察</h3>
        {opening.strategic_insights.length ? (
          <ul className="mt-2 space-y-3">
            {opening.strategic_insights.map((insight, index) => (
              <li key={index} className="rounded-md px-3 py-2.5" style={{ background: "var(--bg-soft)" }}>
                <div className="flex flex-wrap items-center gap-2">
                  <h4 className="text-[13px] font-semibold" style={{ color: "var(--ink)" }}>{insight.title}</h4>
                  <Badge tone={insight.cross_domain ? "accent" : "neutral"}>{insight.cross_domain ? "跨板块证据" : "待验证判断"}</Badge>
                </div>
                <p className="mt-1 text-[13px] leading-relaxed" style={{ color: "var(--body)" }}>{insight.observation}</p>
                <p className="mt-1 text-[13px] leading-relaxed" style={{ color: "var(--ink)" }}><strong className="font-medium">业务含义</strong> · {insight.implication}</p>
                <ul className="mt-2 flex flex-wrap gap-1.5">
                  {insight.evidence.map((item) => {
                    const domain = DOMAIN_LABELS[item.domain] || item.domain;
                    const label = item.title ? `${domain} · ${item.title}` : domain;
                    return (
                      <li key={item.id} className="min-w-0 max-w-full text-[12px]">
                        {item.url ? (
                          <a href={item.url} target="_blank" rel="noreferrer" title={label} className="block max-w-[min(100%,28rem)] truncate hover:underline" style={{ color: "var(--accent)" }}>{label}</a>
                        ) : (
                          <span title={label} className="block max-w-[min(100%,28rem)] truncate" style={{ color: "var(--mute)" }}>{label}</span>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </li>
            ))}
          </ul>
        ) : <p className="mt-2 text-[13px]" style={{ color: "var(--mute)" }}>当前证据不足以形成战略判断。</p>}
      </section>
    </div>
  );
}
