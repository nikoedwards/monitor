import { useState } from "react";
import type { RecordItem } from "../lib/api";
import { CHANNEL_LABEL, fmtDateTime, fmtNum, SENTIMENT_LABEL, sentimentTone } from "../lib/format";
import { Badge, EmptyState } from "./ui";

const TIER_LABEL: Record<string, string> = {
  tier_1: "一线媒体",
  tier_2: "二线媒体",
  tier_3: "三线媒体",
  tier_4: "长尾媒体",
  wire: "通讯社/发稿",
  unknown: "未知",
};

function hostOf(url?: string): string {
  if (!url) return "";
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

function SourceIcon({ url, platform, domain }: { url?: string; platform?: string; domain?: string }) {
  const host = domain || hostOf(url);
  const [failed, setFailed] = useState(false);
  const letter = (platform || host || "?").slice(0, 1).toUpperCase();
  if (host && !failed) {
    return (
      <img
        src={`https://www.google.com/s2/favicons?domain=${host}&sz=64`}
        alt=""
        loading="lazy"
        onError={() => setFailed(true)}
        className="h-5 w-5 rounded shrink-0 mt-0.5 object-contain"
        style={{ border: "1px solid var(--hairline)", background: "var(--bg-soft)" }}
      />
    );
  }
  return (
    <span
      className="h-5 w-5 rounded shrink-0 mt-0.5 inline-flex items-center justify-center text-[11px] font-semibold"
      style={{ background: "var(--bg-soft-2)", color: "var(--mute)" }}
    >
      {letter}
    </span>
  );
}

function MediaMeta({ metrics }: { metrics: Record<string, unknown> }) {
  const reach = Number(metrics.monthly_traffic ?? metrics.estimated_reach ?? 0);
  const ave = Number(metrics.ave ?? 0);
  const tier = metrics.media_tier as string | undefined;
  const coverage = metrics.coverage_type as string | undefined;
  const parts: string[] = [];
  if (reach > 0) parts.push(`预估触达 ${fmtNum(reach)}`);
  if (tier) parts.push(TIER_LABEL[tier] || tier);
  if (ave > 0) parts.push(`AVE $${fmtNum(ave)}`);
  if (coverage) parts.push(coverage === "paid_pr" ? "付费PR" : "自然报道");
  if (!parts.length) return null;
  return (
    <div className="flex items-center gap-2 mt-1.5 text-[12px] tabular-nums" style={{ color: "var(--mute)" }}>
      {parts.join("  ·  ")}
    </div>
  );
}

function SentimentBadge({ record }: { record: RecordItem }) {
  const sentiment = record.sentiment;
  if (!sentiment) return null;
  const explanation = record.sentiment_explanation;
  return (
    <span className="relative inline-flex group">
      <span tabIndex={0} className="inline-flex cursor-help outline-none">
        <Badge tone={sentimentTone(sentiment)}>{SENTIMENT_LABEL[sentiment] || sentiment}</Badge>
      </span>
      {explanation && (
        <span
          role="tooltip"
          className="pointer-events-none absolute left-0 top-[calc(100%+8px)] z-50 w-[360px] max-w-[80vw] p-3 rounded-md text-[12px] font-normal leading-relaxed opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity"
          style={{ background: "var(--panel)", color: "var(--body)", border: "1px solid var(--hairline-strong)", boxShadow: "var(--shadow)" }}
        >
          <span className="block font-medium mb-1" style={{ color: "var(--ink)" }}>判定依据 · {explanation.method}</span>
          <span className="block">{explanation.reason}</span>
          {explanation.positive_terms.length > 0 && <span className="block mt-1.5">正向词：{explanation.positive_terms.join("、")}</span>}
          {explanation.negative_terms.length > 0 && <span className="block mt-1.5">负向词：{explanation.negative_terms.join("、")}</span>}
          {explanation.negation_terms.length > 0 && <span className="block mt-1.5">否定词：{explanation.negation_terms.join("、")}</span>}
          {explanation.evidence.length > 0 && (
            <span className="block mt-2 pt-2" style={{ borderTop: "1px solid var(--hairline)" }}>
              <span className="block mb-1" style={{ color: "var(--mute)" }}>相关原文</span>
              {explanation.evidence.map((snippet, index) => <span key={index} className="block">“{snippet}”</span>)}
            </span>
          )}
        </span>
      )}
    </span>
  );
}

export function RecordList({ records, emptyHint }: { records: RecordItem[]; emptyHint?: string }) {
  if (!records.length) {
    return <EmptyState title="暂无数据" hint={emptyHint || "在数据源页发起一次采集，或手动录入后再查看。"} />;
  }
  return (
    <div className="space-y-2">
      {records.map((r) => {
        const isReply = r.data_type === "community_reply";
        return (
        <div key={r.id} className="panel p-4" style={isReply ? { borderLeft: "2px solid var(--accent)" } : undefined}>
          <div className="flex items-start gap-3">
            <SourceIcon url={r.url} platform={r.platform} domain={(r.metrics?.publication_domain as string) || undefined} />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 flex-wrap mb-1">
                {isReply && <Badge tone="accent">回复</Badge>}
                <SentimentBadge record={r} />
                {r.platform && <Badge tone="neutral">{r.platform}</Badge>}
                {r.channel && <Badge tone="neutral">{CHANNEL_LABEL[r.channel] || r.channel}</Badge>}
                {r.intent && <span className="text-[12px]" style={{ color: "var(--mute)" }}>{r.intent}</span>}
              </div>
              {r.title && (
                <div className="text-[14px] font-medium truncate" style={{ color: "var(--ink)" }}>
                  {r.url ? (
                    <a href={r.url} target="_blank" rel="noreferrer" className="hover:underline">{r.title}</a>
                  ) : (
                    r.title
                  )}
                </div>
              )}
              <p className="text-[13px] mt-1 line-clamp-2" style={{ color: "var(--body)" }}>{r.body}</p>
              {r.metrics && <MediaMeta metrics={r.metrics} />}
              <div className="flex items-center gap-2 mt-2 flex-wrap">
                {r.topics.map((t) => (
                  <span key={t} className="text-[11px] px-1.5 py-0.5 rounded" style={{ background: "var(--bg-soft-2)", color: "var(--mute)" }}>#{t}</span>
                ))}
              </div>
            </div>
            <div className="text-[12px] whitespace-nowrap" style={{ color: "var(--mute)" }}>{fmtDateTime(r.occurred_at)}</div>
          </div>
        </div>
        );
      })}
    </div>
  );
}
