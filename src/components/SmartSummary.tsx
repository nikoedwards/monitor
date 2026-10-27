import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, ChevronDown, RefreshCw, Sparkles, Target } from "lucide-react";
import { useMarketingOverviewSummary, useOverviewSummary, useInsightsSummary } from "../lib/hooks";
import { rangeLabel, type TimeRange } from "../lib/timeRange";
import { fmtNum } from "../lib/format";
import { dailyPersistentTaskKey, persistTaskValue, PERSISTENT_TASK_EVENT, readPersistentTask } from "../lib/persistentTask";
import { AsyncProgress, Badge, Button } from "./ui";

function dailySummaryKey(scope: string, parts: Array<string | undefined>): string {
  return dailyPersistentTaskKey(scope, parts);
}

function readDailySummary<T>(key: string): T | null {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { status?: string; data?: T } | T;
    return typeof parsed === "object" && parsed !== null && "status" in parsed
      ? parsed.status === "success" ? parsed.data || null : null
      : parsed as T;
  } catch {
    return null;
  }
}

function useDailySummaryCache<T>(key: string, fetched: T | undefined): T | null {
  const [cached, setCached] = useState<T | null>(() => readDailySummary<T>(key));

  useEffect(() => {
    setCached(readDailySummary<T>(key));
    const onChange = (event: Event) => {
      const detail = (event as CustomEvent<{ storageKey?: string }>).detail;
      if (detail?.storageKey === key) setCached(readDailySummary<T>(key));
    };
    window.addEventListener(PERSISTENT_TASK_EVENT, onChange);
    return () => window.removeEventListener(PERSISTENT_TASK_EVENT, onChange);
  }, [key]);

  useEffect(() => {
    if (fetched === undefined) return;
    persistTaskValue(key, fetched);
    setCached(fetched);
  }, [fetched, key]);

  return cached;
}

const DOMAIN_LABELS: Record<string, string> = {
  sales: "销售", market: "市场", hiring: "招聘", marketing: "营销", voice: "用户反馈", web: "网页",
  media: "媒体", ads: "广告", creators: "达人", social: "社交", community: "社群",
};

function domainLabel(domain?: string, fallback = "经营") {
  return DOMAIN_LABELS[domain || ""] || fallback;
}

function severityTone(severity?: string): "negative" | "warning" | "neutral" {
  return severity === "high" ? "negative" : severity === "medium" ? "warning" : "neutral";
}

function severityLabel(severity?: string) {
  return severity === "high" ? "优先处理" : severity === "medium" ? "值得跟进" : "继续观察";
}

function ExecutiveDecisionView({ data, pending, onRefresh, refreshLabel, progressLabel, fallbackDomain }: { data: any; pending: boolean; onRefresh: () => void; refreshLabel: string; progressLabel: string; fallbackDomain: string }) {
  const focus = Array.isArray(data?.focus) ? data.focus.slice(0, 3) : [];
  const highlights = Array.isArray(data?.highlights) ? data.highlights.slice(0, 4) : [];
  const hasFocus = focus.length > 0;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Badge tone={data?.mode === "ai" ? "accent" : "neutral"}>{data?.mode === "ai" ? "AI 经营判断" : "系统判断"}</Badge>
          <span className="text-[12px]" style={{ color: "var(--mute)" }}>只展示值得负责人决策的变化</span>
        </div>
        <div className="flex flex-col items-end">
          <Button size="sm" onClick={onRefresh} disabled={pending}>
            <RefreshCw size={13} className={pending ? "animate-spin" : ""} />
            {pending ? "更新中…" : refreshLabel}
          </Button>
          <AsyncProgress active={pending} label={progressLabel} />
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1.2fr_1fr] gap-3">
        <div className="rounded-lg p-4" style={{ background: "var(--bg-soft-2)", border: "1px solid var(--hairline)" }}>
          <div className="flex items-center gap-2 mb-2">
            <Target size={15} style={{ color: "var(--accent)" }} />
            <span className="text-[12px] font-medium uppercase tracking-wide" style={{ color: "var(--mute)" }}>本期判断</span>
          </div>
          <div className="text-[16px] leading-relaxed font-medium" style={{ color: "var(--ink)" }}>{data?.summary || "当前没有足够数据形成经营判断。"}</div>
          {data?.yesterday_summary && (
            <div className="mt-3 pt-3 text-[12px] leading-relaxed" style={{ borderTop: "1px solid var(--hairline)", color: "var(--body)" }}>
              <span className="font-medium" style={{ color: "var(--ink)" }}>最近一天：</span>{data.yesterday_summary}
            </div>
          )}
        </div>

        <div className="rounded-lg p-4" style={{ background: hasFocus ? "var(--warning-soft)" : "var(--bg-soft-2)", border: "1px solid var(--hairline)" }}>
          <div className="flex items-center gap-2 mb-2">
            {hasFocus ? <AlertTriangle size={15} style={{ color: "var(--warning)" }} /> : <CheckCircle2 size={15} style={{ color: "var(--accent)" }} />}
            <span className="text-[12px] font-medium uppercase tracking-wide" style={{ color: "var(--ink)" }}>负责人先看</span>
          </div>
          {hasFocus ? (
            <div className="space-y-2.5">
              {focus.map((item: any, index: number) => (
                <div key={index} className="flex gap-2 text-[13px] leading-relaxed" style={{ color: "var(--body)" }}>
                  <span className="w-5 h-5 shrink-0 rounded-full grid place-items-center text-[11px] font-semibold" style={{ background: "var(--panel)", color: "var(--warning)" }}>{index + 1}</span>
                  <span><b style={{ color: "var(--ink)" }}>{item.action}</b>{item.reason ? `：${item.reason}` : ""}</span>
                </div>
              ))}
            </div>
          ) : <div className="text-[13px] leading-relaxed" style={{ color: "var(--body)" }}>当前没有需要立即决策或跟进的事项。</div>}
        </div>
      </div>

      {highlights.length > 0 && (
        <div>
          <div className="flex items-center justify-between mb-2">
            <div className="text-[12px] font-medium uppercase tracking-wide" style={{ color: "var(--mute)" }}>变化证据</div>
            <span className="text-[11px]" style={{ color: "var(--mute)" }}>最多展示 4 项</span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {highlights.map((item: any, index: number) => (
              <div key={index} className="p-3 rounded-md" style={{ background: "var(--bg-soft-2)" }}>
                <div className="flex items-center gap-2">
                  <Badge tone={severityTone(item.severity)}>{domainLabel(item.domain, fallbackDomain)}</Badge>
                  <span className="text-[13px] font-medium truncate" style={{ color: "var(--ink)" }}>{item.title}</span>
                </div>
                <div className="text-[12px] mt-1.5 leading-relaxed" style={{ color: "var(--body)" }}>{item.detail}</div>
                <div className="text-[11px] mt-2" style={{ color: "var(--mute)" }}>{severityLabel(item.severity)}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {(Array.isArray(data?.caveats) && data.caveats.length > 0) && (
        <details className="text-[12px]" style={{ color: "var(--mute)" }}>
          <summary className="flex items-center gap-1 cursor-pointer select-none"><ChevronDown size={13} />数据口径与限制</summary>
          <div className="mt-1 pl-4">{data.caveats.join(" · ")}</div>
        </details>
      )}
    </div>
  );
}

export function ExecutiveSmartSummary({ brandId, range, marketBrandIds }: { brandId?: string; range: TimeRange; marketBrandIds?: string[] }) {
  const [refreshKey, setRefreshKey] = useState(0);
  const [forceRefresh, setForceRefresh] = useState(false);
  const cacheKey = dailySummaryKey("executive-summary", [brandId, range.start_date, range.end_date, marketBrandIds?.join(",")]);
  const summary = useOverviewSummary(brandId, range, marketBrandIds, refreshKey, !forceRefresh ? !readDailySummary(cacheKey) : true);
  const cachedData = useDailySummaryCache<any>(cacheKey, summary.data);
  const data = cachedData || summary.data;
  const pending = summary.isFetching || readPersistentTask<any>(cacheKey)?.status === "pending";

  useEffect(() => {
    if (summary.data && forceRefresh) setForceRefresh(false);
  }, [summary.data, forceRefresh]);

  const refresh = () => {
    setForceRefresh(true);
    setRefreshKey((value) => value + 1);
  };

  return (
    <>
      <div className="text-[12px] mb-3" style={{ color: "var(--mute)" }}>覆盖 {rangeLabel(range)} · 自动整理跨业务变化</div>
      {summary.isLoading && <p className="text-[13px]" style={{ color: "var(--mute)" }}>正在整理经营数据…</p>}
      {summary.isError && <p className="text-[13px]" style={{ color: "var(--danger)" }}>经营总结暂时不可用，下面的业务指标仍可正常查看。</p>}
      {data && <ExecutiveDecisionView data={data} pending={pending} onRefresh={refresh} refreshLabel="刷新判断" progressLabel="正在整理经营数据" fallbackDomain="经营" />}
    </>
  );
}

export function ExecutiveMarketingSummary({ brandId, range }: { brandId?: string; range: TimeRange }) {
  const [refreshKey, setRefreshKey] = useState(0);
  const summary = useMarketingOverviewSummary(brandId, range, refreshKey);
  const cacheKey = dailySummaryKey("marketing-summary", [brandId, range.start_date, range.end_date]);
  const cachedData = useDailySummaryCache<any>(cacheKey, summary.data);
  const data = cachedData || summary.data;
  const pending = summary.isFetching || readPersistentTask<any>(cacheKey)?.status === "pending";

  return (
    <>
      <div className="text-[12px] mb-3" style={{ color: "var(--mute)" }}>覆盖 {rangeLabel(range)} · 自动整理营销变化</div>
      {summary.isLoading && <p className="text-[13px]" style={{ color: "var(--mute)" }}>正在整理营销数据…</p>}
      {summary.isError && <p className="text-[13px]" style={{ color: "var(--danger)" }}>营销总结暂时不可用，下面的指标仍可正常查看。</p>}
      {data && <ExecutiveDecisionView data={data} pending={pending} onRefresh={() => setRefreshKey((value) => value + 1)} refreshLabel="刷新判断" progressLabel="正在整理营销数据" fallbackDomain="营销" />}
    </>
  );
}
export function SmartSummary({ brandId, dimension, channel, range, label = "智能总结" }: { brandId?: string; dimension?: string; channel?: string; range: TimeRange; label?: string }) {
  const summarize = useInsightsSummary(brandId, dimension, channel, range);
  const data = summarize.data;

  const run = () => {
    if (brandId) summarize.mutate({ brandId, dimension, channel, range });
  };

  return (
    <div>
      <div className="flex items-start gap-2">
        <div className="flex flex-col items-start">
          <Button size="sm" variant="primary" disabled={!brandId || summarize.isPending} onClick={run}>
            <Sparkles size={14} /> {summarize.isPending ? "生成中…" : label}
          </Button>
          <AsyncProgress active={summarize.isPending} label="正在生成智能总结" />
        </div>
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
