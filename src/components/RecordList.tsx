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

const COLLECTION_SOURCE_LABEL: Record<string, string> = {
  google_news: "Google News（RSS 定时采集）",
  google_web_search: "Google 普通网页搜索补漏（定时任务）",
  reddit_search: "Reddit 采集",
  community_site: "社区站点采集",
  meta_ads: "Meta 广告采集",
  youtube_search: "YouTube 搜索采集",
  social_accounts: "官方社媒账号采集",
  app_store_reviews: "App Store 评论采集",
  instagram_listening: "Instagram 红人采集",
  tiktok_listening: "TikTok 红人采集",
  x_search: "X 红人采集",
  manual_csv: "手动导入",
};

const VOICE_SOURCE_LABEL: Record<string, string> = {
  sales_reviews: "销售渠道评论",
  marketing_videos: "营销视频",
  social_posts_comments: "社交帖子与评论",
  creator_comments: "红人视频与评论",
  app_reviews: "应用商店评论",
  manual_feedback: "手动反馈",
};

const SOCIAL_PLATFORM_LABEL: Record<string, string> = {
  youtube: "YouTube",
  instagram: "Instagram",
  tiktok: "TikTok",
  x: "X",
  facebook: "Facebook",
  linkedin: "LinkedIn",
};

const SOCIAL_PLATFORM_BACKGROUND: Record<string, string> = {
  youtube: "linear-gradient(135deg, #ff0033, #9d001f)",
  instagram: "linear-gradient(135deg, #833ab4, #fd1d1d 55%, #fcb045)",
  tiktok: "linear-gradient(135deg, #161823, #25f4ee)",
  x: "linear-gradient(135deg, #111827, #4b5563)",
  facebook: "linear-gradient(135deg, #1877f2, #0b4aa2)",
  linkedin: "linear-gradient(135deg, #0a66c2, #06427d)",
};

function collectionSourceLabel(sourceId?: string): string | undefined {
  if (!sourceId) return undefined;
  return COLLECTION_SOURCE_LABEL[sourceId] || sourceId;
}

function textValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function collectionReason(record: RecordItem): string | undefined {
  const raw = record.raw || {};
  const query = textValue(raw.query);
  if (record.source_id === "google_news") {
    const matchedIn = textValue(raw.matched_in);
    const matchedText = textValue(raw.matched_text);
    const matchedQuery = textValue(raw.matched_query) || query;
    if (matchedText) {
      return `收录原因：Google News RSS 使用关键词「${query || matchedQuery}」发现，并在${matchedIn === "title" ? "标题" : "摘要"}命中「${matchedText}」后通过品牌相关性校验。`;
    }
    return matchedQuery
      ? `收录原因：Google News RSS 使用关键词「${matchedQuery}」发现；该历史记录已按当前标题/摘要品牌关键词规则复核通过。`
      : "收录原因：Google News RSS 历史记录；已按当前标题/摘要品牌关键词规则复核通过。";
  }
  if (record.source_id === "google_web_search") {
    return query
      ? `收录原因：定时普通网页搜索补漏以关键词「${query}」发现，并按任务规则核验后收录。`
      : "收录原因：由定时普通网页搜索补漏任务核验后收录；该历史记录未保存触发关键词。";
  }
  if (record.source_id === "reddit_search") {
    const matchedIn = textValue(raw.matched_in);
    const matchedText = textValue(raw.matched_text);
    const subreddit = textValue(raw.subreddit);
    const scope = textValue(raw.scope);
    if (query) {
      const evidence = matchedText
        ? `，并在${matchedIn === "title" ? "标题" : "正文"}命中「${matchedText}」`
        : "；历史记录已按当前标题/正文关键词规则复核";
      return `收录原因：Reddit 使用品牌主名称「${query}」进行全站搜索${evidence}后收录。`;
    }
    if (subreddit || scope?.startsWith("subreddit:")) {
      const name = subreddit || scope?.slice("subreddit:".length);
      return `收录原因：来自已配置的 Reddit 官方大本营 r/${name}；按发布时间顺序直接收录，不要求帖子命中关键词。`;
    }
    return "收录原因：Reddit 社群采集；该历史记录未保存触发关键词。";
  }
  return record.source_id ? `收录原因：${collectionSourceLabel(record.source_id) || record.source_id}` : undefined;
}

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

function metricNumber(value: unknown): number | undefined {
  if (value === null || value === undefined || value === "") return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function SocialMeta({ metrics }: { metrics: Record<string, unknown> }) {
  const views = metricNumber(metrics.views);
  const likes = metricNumber(metrics.likes);
  const comments = metricNumber(metrics.comments);
  const engagement = metricNumber(metrics.engagement);
  const engagementRate = metricNumber(metrics.engagement_rate);
  const followers = metricNumber(metrics.follower_count);
  const parts: string[] = [];
  if (views !== undefined) parts.push(`播放 ${fmtNum(views)}`);
  if (likes !== undefined) parts.push(`点赞 ${fmtNum(likes)}`);
  if (comments !== undefined) parts.push(`评论 ${fmtNum(comments)}`);
  if (engagement !== undefined) parts.push(`互动 ${fmtNum(engagement)}`);
  if (engagementRate !== undefined) parts.push(`互动率 ${(engagementRate * 100).toFixed(2)}%`);
  if (followers !== undefined) parts.push(`粉丝 ${fmtNum(followers)}`);
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

function SocialThumbnail({ record }: { record: RecordItem }) {
  const [failed, setFailed] = useState(false);
  const thumbnail = textValue(record.metrics?.thumbnail_url);
  const platform = record.platform || "social";
  const platformLabel = SOCIAL_PLATFORM_LABEL[platform] || platform;
  const media = (
    <div
      className="relative aspect-video overflow-hidden"
      style={{ background: SOCIAL_PLATFORM_BACKGROUND[platform] || "linear-gradient(135deg, #475569, #0f172a)" }}
    >
      {thumbnail && !failed ? (
        <img
          src={thumbnail}
          alt={record.title ? `${record.title} 封面` : `${platformLabel} 内容封面`}
          loading="lazy"
          referrerPolicy="no-referrer"
          onError={() => setFailed(true)}
          className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.02]"
        />
      ) : (
        <div className="absolute inset-0 flex flex-col items-center justify-center text-white">
          <span className="text-[30px] font-semibold tracking-tight">{platformLabel}</span>
          <span className="mt-1 text-[12px] opacity-75">暂无可用封面</span>
        </div>
      )}
      <span
        className="absolute left-3 top-3 rounded-md px-2 py-1 text-[11px] font-semibold"
        style={{ background: "rgba(0, 0, 0, 0.68)", color: "white", backdropFilter: "blur(6px)" }}
      >
        {platformLabel}
      </span>
    </div>
  );
  if (!record.url) return media;
  return (
    <a href={record.url} target="_blank" rel="noreferrer" className="group block" aria-label={`打开 ${platformLabel} 原内容`}>
      {media}
    </a>
  );
}

function SocialRecordCard({ record }: { record: RecordItem }) {
  const title = (record.title || record.body || "社交媒体内容").trim();
  const body = (record.body || "").trim();
  const showBody = body && body !== title;
  return (
    <article className="panel overflow-hidden flex min-h-full flex-col">
      <SocialThumbnail record={record} />
      <div className="flex flex-1 flex-col p-4">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <SentimentBadge record={record} />
          {record.intent && <span className="text-[12px]" style={{ color: "var(--mute)" }}>{record.intent}</span>}
        </div>
        <h3 className="line-clamp-2 text-[15px] font-semibold leading-snug" style={{ color: "var(--ink)" }}>
          {record.url ? (
            <a href={record.url} target="_blank" rel="noreferrer" className="hover:underline">{title}</a>
          ) : title}
        </h3>
        {showBody && <p className="mt-2 line-clamp-3 text-[13px] leading-relaxed" style={{ color: "var(--body)" }}>{body}</p>}
        {record.metrics && <SocialMeta metrics={record.metrics} />}
        {record.topics.length > 0 && (
          <div className="mt-3 flex flex-wrap items-center gap-1.5">
            {record.topics.slice(0, 4).map((topic) => (
              <span key={topic} className="rounded px-1.5 py-0.5 text-[11px]" style={{ background: "var(--bg-soft-2)", color: "var(--mute)" }}>#{topic}</span>
            ))}
          </div>
        )}
        <div className="mt-auto flex items-center justify-between gap-3 pt-4 text-[12px]" style={{ color: "var(--mute)" }}>
          <span className="truncate">{record.author || (record.metrics?.author_handle as string) || "官方账号"}</span>
          <span className="shrink-0 whitespace-nowrap">{fmtDateTime(record.occurred_at)}</span>
        </div>
      </div>
    </article>
  );
}

export function RecordList({
  records,
  emptyHint,
  variant = "list",
}: {
  records: RecordItem[];
  emptyHint?: string;
  variant?: "list" | "social-cards";
}) {
  if (!records.length) {
    return <EmptyState title="暂无数据" hint={emptyHint || "在数据源页发起一次采集，或手动录入后再查看。"} />;
  }
  if (variant === "social-cards") {
    return (
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {records.map((record) => <SocialRecordCard key={record.id} record={record} />)}
      </div>
    );
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
                {r.voice_source && <Badge tone="accent">来源：{VOICE_SOURCE_LABEL[r.voice_source] || r.voice_source}</Badge>}
                {r.platform && <Badge tone="neutral">{r.platform}</Badge>}
                {r.channel && <Badge tone="neutral">{CHANNEL_LABEL[r.channel] || r.channel}</Badge>}
                {collectionSourceLabel(r.source_id) && (
                  <Badge tone="neutral">采集：{collectionSourceLabel(r.source_id)}</Badge>
                )}
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
              {r.metrics && (r.channel === "social" ? <SocialMeta metrics={r.metrics} /> : <MediaMeta metrics={r.metrics} />)}
              {collectionReason(r) && (
                <div
                  className="mt-2 rounded px-2 py-1.5 text-[12px] leading-relaxed"
                  style={{ background: "var(--bg-soft-2)", color: "var(--mute)" }}
                >
                  {collectionReason(r)}
                </div>
              )}
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
