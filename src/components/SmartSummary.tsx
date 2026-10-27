import { useEffect, useState } from "react";
import { AlertTriangle, RefreshCw, Sparkles } from "lucide-react";
import { useMarketingOverviewSummary, useOverviewSummary, useInsightsSummary } from "../lib/hooks";
import { rangeLabel, type TimeRange } from "../lib/timeRange";
import { fmtNum } from "../lib/format";
import { dailyPersistentTaskKey, persistTaskValue, PERSISTENT_TASK_EVENT, readPersistentTask } from "../lib/persistentTask";
import { AsyncProgress, Badge, Button } from "./ui";

function usePersistentSummary<T>(key: string, fetched: T | undefined): T | null {
  const [cached, setCached] = useState<T | null>(() => {
    const task = readPersistentTask<T>(key);
    return task?.status === "success" ? task.data || null : null;
  });

  useEffect(() => {
    const sync = () => {
      const task = readPersistentTask<T>(key);
      setCached(task?.status === "success" ? task.data || null : null);
    };
    sync();
    window.addEventListener(PERSISTENT_TASK_EVENT, sync);
    return () => window.removeEventListener(PERSISTENT_TASK_EVENT, sync);
  }, [key]);

  useEffect(() => {
    if (fetched !== undefined) {
      persistTaskValue(key, fetched);
      setCached(fetched);
    }
  }, [fetched, key]);

  return cached;
}

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

export function ExecutiveSmartSummary({ brandId, range, marketBrandIds }: { brandId?: string; range: TimeRange; marketBrandIds?: string[] }) {
  const [refreshKey, setRefreshKey] = useState(0);
  const summary = useOverviewSummary(brandId, range, marketBrandIds, refreshKey);
  const cacheKey = dailyPersistentTaskKey("executive-summary", [brandId, range.start_date, range.end_date, marketBrandIds?.join(",")]);
  const cached = usePersistentSummary<any>(cacheKey, summary.data);
  const data = cached || summary.data;
  const pending = summary.isFetching || readPersistentTask<any>(cacheKey)?.status === "pending";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Badge tone={data?.mode === "ai" ? "accent" : "neutral"}>{data?.mode === "ai" ? "AI 经营判断" : "系统速览"}</Badge>
          <span className="text-[12px]" style={{ color: "var(--mute)" }}>覆盖 {rangeLabel(range)} · 自动更新</span>
        </div>
        <div className="flex flex-col items-end">
          <Button size="sm" onClick={() => setRefreshKey((value) => value + 1)} disabled={pending}>
            <RefreshCw size={13} className={pending ? "animate-spin" : ""} />
            {pending ? "更新中…" : "刷新总结"}
          </Button>
          <AsyncProgress active={pending} label="正在整理经营数据" />
        </div>
      </div>

      {summary.isLoading && <p className="text-[13px]" style={{ color: "var(--mute)" }}>正在整理经营数据…</p>}
      {summary.isError && <p className="text-[13px]" style={{ color: "var(--danger)" }}>经营总结暂时不可用，下面的业务指标仍可正常查看。</p>}
      {data && (
        <>
          <div className="grid grid-cols-1 lg:grid-cols-[1.35fr_1fr] gap-4">
            <div>
              <div className="text-[14px] leading-relaxed" style={{ color: "var(--ink)" }}>{data.summary}</div>
              <div className="mt-3 p-3 rounded-md" style={{ background: "var(--bg-soft-2)" }}>
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-[12px] font-medium" style={{ color: "var(--ink)" }}>昨日速览</span>
                  <span className="text-[11px]" style={{ color: "var(--mute)" }}>{data.yesterday?.date || data.range?.end}</span>
                </div>
                <div className="text-[13px] leading-relaxed" style={{ color: "var(--body)" }}>{data.yesterday_summary || "暂无昨日数据。"}</div>
              </div>
            </div>
            <div>
              <div className="text-[13px] font-medium mb-2" style={{ color: "var(--ink)" }}>需要关注</div>
              {Array.isArray(data.focus) && data.focus.length ? (
                <div className="space-y-2">
                  {data.focus.map((item: any, index: number) => (
                    <div key={index} className="flex gap-2 text-[13px]" style={{ color: "var(--body)" }}>
                      <AlertTriangle size={14} className="shrink-0 mt-0.5" style={{ color: "var(--warning)" }} />
                      <span><b style={{ color: "var(--ink)" }}>{item.action}</b>{item.reason ? `：${item.reason}` : ""}</span>
                    </div>
                  ))}
                </div>
              ) : <span className="text-[13px]" style={{ color: "var(--mute)" }}>当前没有需要立即跟进的事项。</span>}
            </div>
          </div>

          {Array.isArray(data.highlights) && data.highlights.length > 0 && (
            <div>
              <div className="text-[13px] font-medium mb-2" style={{ color: "var(--ink)" }}>关键发现</div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                {data.highlights.map((item: any, index: number) => (
                  <div key={index} className="p-2.5 rounded-md" style={{ background: "var(--bg-soft-2)" }}>
                    <div className="flex items-center gap-2">
                      <Badge tone={item.severity === "high" ? "negative" : item.severity === "medium" ? "warning" : "neutral"}>{item.domain || "经营"}</Badge>
                      <span className="text-[13px] font-medium" style={{ color: "var(--ink)" }}>{item.title}</span>
                    </div>
                    <div className="text-[12px] mt-1 leading-relaxed" style={{ color: "var(--body)" }}>{item.detail}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {Array.isArray(data.caveats) && data.caveats.length > 0 && (
            <div className="text-[12px]" style={{ color: "var(--mute)" }}>{data.caveats.join(" · ")}</div>
          )}
        </>
      )}
    </div>
  );
}

export function ExecutiveMarketingSummary({ brandId, range }: { brandId?: string; range: TimeRange }) {
  const [refreshKey, setRefreshKey] = useState(0);
  const summary = useMarketingOverviewSummary(brandId, range, refreshKey);
  const cacheKey = dailyPersistentTaskKey("marketing-summary", [brandId, range.start_date, range.end_date]);
  const cached = usePersistentSummary<any>(cacheKey, summary.data);
  const data = cached || summary.data;
  const pending = summary.isFetching || readPersistentTask<any>(cacheKey)?.status === "pending";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Badge tone={data?.mode === "ai" ? "accent" : "neutral"}>{data?.mode === "ai" ? "AI 营销判断" : "系统速览"}</Badge>
          <span className="text-[12px]" style={{ color: "var(--mute)" }}>覆盖 {rangeLabel(range)} · 自动更新</span>
        </div>
        <div className="flex flex-col items-end">
          <Button size="sm" onClick={() => setRefreshKey((value) => value + 1)} disabled={pending}>
            <RefreshCw size={13} className={pending ? "animate-spin" : ""} />
            {pending ? "更新中…" : "刷新总结"}
          </Button>
          <AsyncProgress active={pending} label="正在整理营销数据" />
        </div>
      </div>

      {summary.isLoading && <p className="text-[13px]" style={{ color: "var(--mute)" }}>正在整理营销数据…</p>}
      {summary.isError && <p className="text-[13px]" style={{ color: "var(--danger)" }}>营销总结暂时不可用，下面的指标仍可正常查看。</p>}
      {data && (
        <>
          <div className="grid grid-cols-1 lg:grid-cols-[1.35fr_1fr] gap-4">
            <div>
              <div className="text-[14px] leading-relaxed" style={{ color: "var(--ink)" }}>{data.summary}</div>
              <div className="mt-3 p-3 rounded-md" style={{ background: "var(--bg-soft-2)" }}>
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-[12px] font-medium" style={{ color: "var(--ink)" }}>昨日速览</span>
                  <span className="text-[11px]" style={{ color: "var(--mute)" }}>{data.yesterday?.date || data.range?.end}</span>
                </div>
                <div className="text-[13px] leading-relaxed" style={{ color: "var(--body)" }}>{data.yesterday_summary || "暂无昨日数据。"}</div>
              </div>
            </div>
            <div>
              <div className="text-[13px] font-medium mb-2" style={{ color: "var(--ink)" }}>需要关注</div>
              {Array.isArray(data.focus) && data.focus.length ? (
                <div className="space-y-2">
                  {data.focus.map((item: any, index: number) => (
                    <div key={index} className="flex gap-2 text-[13px]" style={{ color: "var(--body)" }}>
                      <AlertTriangle size={14} className="shrink-0 mt-0.5" style={{ color: "var(--warning)" }} />
                      <span><b style={{ color: "var(--ink)" }}>{item.action}</b>{item.reason ? `：${item.reason}` : ""}</span>
                    </div>
                  ))}
                </div>
              ) : <span className="text-[13px]" style={{ color: "var(--mute)" }}>当前没有需要立即跟进的事项。</span>}
            </div>
          </div>
          {Array.isArray(data.highlights) && data.highlights.length > 0 && (
            <div>
              <div className="text-[13px] font-medium mb-2" style={{ color: "var(--ink)" }}>关键发现</div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                {data.highlights.map((item: any, index: number) => (
                  <div key={index} className="p-2.5 rounded-md" style={{ background: "var(--bg-soft-2)" }}>
                    <div className="flex items-center gap-2">
                      <Badge tone={item.severity === "high" ? "negative" : item.severity === "medium" ? "warning" : "neutral"}>{item.domain || "营销"}</Badge>
                      <span className="text-[13px] font-medium" style={{ color: "var(--ink)" }}>{item.title}</span>
                    </div>
                    <div className="text-[12px] mt-1 leading-relaxed" style={{ color: "var(--body)" }}>{item.detail}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
          {Array.isArray(data.caveats) && data.caveats.length > 0 && <div className="text-[12px]" style={{ color: "var(--mute)" }}>{data.caveats.join(" · ")}</div>}
        </>
      )}
    </div>
  );
}

export function SmartSummary({ brandId, dimension, channel, range, label = "智能总结" }: { brandId?: string; dimension?: string; channel?: string; range: TimeRange; label?: string }) {
  const summarize = useInsightsSummary(brandId, dimension, channel, range);
  const data = summarize.data;

  const run = () => {
    if (brandId) summarize.generate.mutate({ refresh: !!summarize.data });
  };

  return (
    <div>
      <div className="flex items-start gap-2">
        <div className="flex flex-col items-start">
          <Button size="sm" variant="primary" disabled={!brandId || summarize.generate.isPending || summarize.isLoading} onClick={run}>
            <Sparkles size={14} /> {summarize.generate.isPending ? "生成中…" : label}
          </Button>
          <AsyncProgress active={summarize.generate.isPending} label="正在生成智能总结" />
        </div>
        <span className="text-[12px]" style={{ color: "var(--mute)" }}>覆盖 {rangeLabel(range)}{channel ? ` · ${channel}` : ""}</span>
      </div>

      {(summarize.isError || summarize.generate.isError) && (
        <p className="text-[13px] mt-3" style={{ color: "var(--danger)" }}>
          {((summarize.generate.error || summarize.error) as any)?.message || "生成失败，请稍后重试。"}
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
