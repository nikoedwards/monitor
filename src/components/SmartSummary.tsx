import { Sparkles } from "lucide-react";
import { useInsightsSummary } from "../lib/hooks";
import { rangeLabel, type TimeRange } from "../lib/timeRange";
import { fmtNum } from "../lib/format";
import { Badge, Button } from "./ui";

export function SmartSummary({ brandId, dimension, channel, range, label = "智能总结" }: { brandId?: string; dimension?: string; channel?: string; range: TimeRange; label?: string }) {
  const summarize = useInsightsSummary();
  const data = summarize.data;

  const run = () => {
    if (brandId) summarize.mutate({ brandId, dimension, channel, range });
  };

  return (
    <div>
      <div className="flex items-center gap-2">
        <Button size="sm" variant="primary" disabled={!brandId || summarize.isPending} onClick={run}>
          <Sparkles size={14} /> {summarize.isPending ? "生成中…" : label}
        </Button>
        <span className="text-[12px]" style={{ color: "var(--mute)" }}>覆盖 {rangeLabel(range)}{channel ? ` · ${channel}` : ""}</span>
      </div>

      {summarize.isError && (
        <p className="text-[13px] mt-3" style={{ color: "var(--danger)" }}>
          {(summarize.error as any)?.message || "生成失败，请稍后重试。"}
        </p>
      )}

      {data && (
        <div className="mt-4 space-y-4">
          {data.summary && <p className="text-[14px] leading-relaxed" style={{ color: "var(--ink)" }}>{data.summary}</p>}

          {data.sentiment && (data.sentiment.overall || data.sentiment.positive != null) && (
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
                {data.highlights.map((h: string, i: number) => (
                  <li key={i} className="text-[13px] flex gap-2" style={{ color: "var(--body)" }}>
                    <span style={{ color: "var(--accent)" }}>•</span>
                    <span>{h}</span>
                  </li>
                ))}
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
