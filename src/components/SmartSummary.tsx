import { RefreshCw, Sparkles } from "lucide-react";
import { useMarketingOverviewSummary, useOverviewSummary, useInsightsSummary, type ExecutiveSummaryResult, type SummaryFilters } from "../lib/hooks";
import { rangeLabel, type TimeRange } from "../lib/timeRange";
import { fmtNum } from "../lib/format";
import { AsyncProgress, Badge, Button } from "./ui";
import { OverviewResearch } from "./OverviewResearch";
import { VoiceSummaryOpening } from "./VoiceSummaryOpening";

function highlightSource(highlight: string, representative: any[] = []) {
  const text = String(highlight || "").toLocaleLowerCase();
  const grams = new Set<string>();
  for (let i = 0; i < text.length - 1; i++) {
    const gram = text.slice(i, i + 2).trim();
    if (gram.length === 2 && !/[\s，。！？、,.!?]/.test(gram)) grams.add(gram);
  }
  let best: any = null;
  let bestScore = 0;
  for (const item of representative) {
    if (!item?.url) continue;
    const candidate = `${item.title || ""} ${item.why || ""}`.toLocaleLowerCase();
    const score = [...grams].filter((gram) => candidate.includes(gram)).length;
    if (score > bestScore) {
      best = item;
      bestScore = score;
    }
  }
  return bestScore >= 2 ? best : null;
}

export function ExecutiveSmartSummary({ brandId, range, marketBrandIds, preview }: { brandId?: string; range: TimeRange; marketBrandIds?: string[]; preview?: ExecutiveSummaryResult }) {
  const { data: analyzed, pending, takingLonger, error, refresh } = useOverviewSummary(brandId, range, marketBrandIds);
  const data = analyzed || preview;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Badge tone={data?.mode === "ai" ? "accent" : "neutral"}>{data?.mode === "ai" ? "AI 竞争情报" : "系统速览"}</Badge>
          <span className="text-[12px]" style={{ color: "var(--mute)" }}>覆盖 {rangeLabel(range)} · 跨板块证据研判</span>
        </div>
        <div className="flex flex-col items-end">
          <Button size="sm" onClick={refresh} disabled={pending}>
            <RefreshCw size={13} className={pending ? "animate-spin" : ""} />
            {pending ? "更新中…" : takingLonger ? "查看进度" : "重新研判"}
          </Button>
          <AsyncProgress active={pending} label="正在后台分析经营证据" />
        </div>
      </div>

      {error && <p role="alert" className="text-[13px]" style={{ color: "var(--danger)" }}>{error}</p>}
      {takingLonger && <p role="status" className="text-[13px]" style={{ color: "var(--mute)" }}>AI 研判耗时较长，仍在后台处理，完成后会自动更新。</p>}
      {!pending && !takingLonger && !error && analyzed?.summary_notice && <p role="status" className="text-[13px]" style={{ color: "var(--mute)" }}>{analyzed.summary_notice}</p>}
      {data && <OverviewResearch data={data} brandId={brandId} />}
    </div>
  );
}

export function ExecutiveMarketingSummary({ brandId, range, preview }: { brandId?: string; range: TimeRange; preview?: ExecutiveSummaryResult }) {
  const summary = useMarketingOverviewSummary(brandId, range);
  const data = summary.data || preview;
  const pending = summary.pending;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          {data && <Badge tone={data.mode === "ai" ? "accent" : "neutral"}>{data.mode === "ai" ? "AI 研判" : "数据观察"}</Badge>}
          <span className="text-[12px]" style={{ color: "var(--mute)" }}>{rangeLabel(range)}</span>
        </div>
        <div className="flex flex-col items-end">
          <Button size="sm" onClick={summary.refresh} disabled={pending}>
            <RefreshCw size={13} className={pending ? "animate-spin" : ""} />
            {pending ? "研判中…" : summary.takingLonger ? "查看进度" : "重新研判"}
          </Button>
          <AsyncProgress active={pending} label="正在分析营销证据" />
        </div>
      </div>

      {summary.error && <p role="alert" className="text-[13px]" style={{ color: "var(--danger)" }}>{summary.error}</p>}
      {summary.data?.summary_notice && <p role="status" className="text-[13px]" style={{ color: "var(--mute)" }}>{summary.data.summary_notice}</p>}
      {!!data?.findings?.length && (
        <ol>
          {data.findings.map((item) => (
            <li key={item.title} className="border-t first:border-t-0 py-3 first:pt-0 last:pb-0 space-y-1.5 break-words" style={{ borderColor: "var(--hairline)" }}>
              <h3 className="text-[15px] font-semibold" style={{ color: "var(--ink)" }}>{item.title}</h3>
              <ul className="space-y-1 text-[12px] leading-relaxed" style={{ color: "var(--body)" }}>
                {item.evidence.map((source, i) => (
                  <li key={i}>
                    {source.url
                      ? <a className="underline underline-offset-2" href={source.url} target="_blank" rel="noreferrer">{source.text}</a>
                      : source.text}
                    {source.excerpt && <details style={{ color: "var(--mute)" }}><summary className="cursor-pointer">查看摘录</summary>{source.excerpt}</details>}
                  </li>
                ))}
              </ul>
              {item.hypothesis && <p className="text-[13px] leading-relaxed"><b className="mr-2">战略推测</b>{item.hypothesis}</p>}
              {item.check && <p className="text-[13px] leading-relaxed" style={{ color: "var(--body)" }}><b className="mr-2">验证线索</b>{item.check}</p>}
            </li>
          ))}
        </ol>
      )}
      {data && !data.findings?.length && <p className="text-[13px]" style={{ color: "var(--mute)" }}>{data.summary}</p>}
    </div>
  );
}

export function SmartSummary({ brandId, dimension, channel, range, filters, label = "智能总结", sourceLabels }: { brandId?: string; dimension?: string; channel?: string; range: TimeRange; filters?: SummaryFilters; label?: string; sourceLabels?: Record<string, string> }) {
  const summarize = useInsightsSummary(brandId, dimension, channel, range, filters);
  const data = summarize.data;
  const voiceOpening = dimension === "voc" ? data?.voice_opening : undefined;
  const error = summarize.generate.error?.message || summarize.error?.message || summarize.generationError;

  const run = () => {
    if (brandId) summarize.generate.mutate({ refresh: !!summarize.data });
  };

  return (
    <div>
      <div className="flex items-start gap-2">
        <div className="flex flex-col items-start">
          <Button size="sm" variant="primary" disabled={!brandId || summarize.isGenerating || summarize.isLoading} onClick={run}>
            <Sparkles size={14} /> {summarize.isGenerating ? "生成中…" : summarize.isLoading ? "读取总结中…" : label}
          </Button>
          <AsyncProgress active={summarize.isGenerating} label="正在生成智能总结" />
        </div>
        <span className="text-[12px]" style={{ color: "var(--mute)" }}>覆盖 {rangeLabel(range)}{channel ? ` · ${channel}` : ""}{filters?.sentiment ? ` · ${filters.sentiment}` : ""}</span>
      </div>

      {error && (
        <p className="text-[13px] mt-3" role="alert" style={{ color: "var(--danger)" }}>
          {error}
        </p>
      )}

      {data && (
        <div className="mt-4 space-y-4">
          {voiceOpening ? (
            <VoiceSummaryOpening summary={data.summary} sentiment={data.sentiment} opening={voiceOpening} recordCount={data.record_count} sourceLabels={sourceLabels} />
          ) : data.summary && <p className="text-[14px] leading-relaxed" style={{ color: "var(--ink)" }}>{data.summary}</p>}

          {!voiceOpening && data.sentiment && (data.sentiment.overall || data.sentiment.positive != null) && (
            <div className="flex items-center gap-2 flex-wrap">
              {data.sentiment.overall && <Badge tone={data.sentiment.overall === "negative" ? "negative" : data.sentiment.overall === "positive" ? "positive" : "neutral"}>整体 {data.sentiment.overall}</Badge>}
              {data.sentiment.positive != null && <span className="text-[12px]" style={{ color: "var(--mute)" }}>正 {fmtNum(data.sentiment.positive)} · 中 {fmtNum(data.sentiment.neutral || 0)} · 负 {fmtNum(data.sentiment.negative || 0)}</span>}
              <span className="text-[12px]" style={{ color: "var(--mute)" }}>· 基于 {fmtNum(data.record_count || 0)} 条记录</span>
            </div>
          )}

          {Array.isArray(data.highlights) && data.highlights.length > 0 && (
            <div>
              <div className="text-[13px] font-medium mb-1.5" style={{ color: "var(--ink)" }}>关键发现</div>
              <ul className="space-y-1.5">
                {data.highlights.map((h: string, i: number) => {
                  const source = highlightSource(h, data.representative);
                  return (
                  <li key={i} className="text-[13px] flex gap-2" style={{ color: "var(--body)" }}>
                    <span style={{ color: "var(--accent)" }}>•</span>
                    <span>
                      {source && (
                        <a
                          href={source.url}
                          target="_blank"
                          rel="noreferrer"
                          className="ml-2 hover:text-[var(--accent)]"
                          style={{ color: "var(--accent)", borderBottom: "1px dotted currentColor" }}
                          title="打开信息源"
                        >
                          {h}
                        </a>
                      )}
                      {!source && h}
                    </span>
                  </li>
                  );
                })}
              </ul>
            </div>
          )}

          {Array.isArray(data.themes) && data.themes.length > 0 && (
            <div className="flex items-center gap-2 flex-wrap">
              {data.themes.map((t: any, i: number) => (
                <span key={i} className="text-[12px] px-2 py-0.5 rounded" style={{ background: "var(--bg-soft-2)", color: "var(--mute)" }}>
                  #{t.theme}{t.mentions ? ` · ${fmtNum(t.mentions)}` : ""}
                </span>
              ))}
            </div>
          )}

          {Array.isArray(data.representative) && data.representative.length > 0 && (
            <div>
              <div className="text-[13px] font-medium mb-1.5" style={{ color: "var(--ink)" }}>代表性内容</div>
              <div className="space-y-2">
                {data.representative.map((r: any, i: number) => (
                  <div key={i} className="text-[13px]">
                    {r.url ? (
                      <a href={r.url} target="_blank" rel="noreferrer" className="hover:underline" style={{ color: "var(--accent)" }}>{r.title || r.url}</a>
                    ) : (
                      <span style={{ color: "var(--ink)" }}>{r.title}</span>
                    )}
                    {r.why && <span className="text-[12px] ml-2" style={{ color: "var(--mute)" }}>{r.why}</span>}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
