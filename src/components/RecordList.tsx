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

function thumbnailSrc(url?: string): string | undefined {
  const normalized = textValue(url);
  if (!normalized) return undefined;
  if (normalized.startsWith("/") || normalized.startsWith("data:") || normalized.startsWith("blob:")) return normalized;
  return `/api/media/thumbnail?url=${encodeURIComponent(normalized)}`;
}

function recordThumbnail(record: RecordItem): string | undefined {
  if (record.channel !== "social" && record.channel !== "creators" && record.data_type !== "creator_post" && record.data_type !== "social_post") return undefined;
  const metrics = record.metrics || {};
  const raw = record.raw || {};
  const keys = ["thumbnail_url", "cover_url", "image_url", "media_url", "display_url", "display_uri", "thumbnail_src"];
  if (record.data_type === "social_post") keys.push("avatar_url");
  for (const key of keys) {
    const value = metrics[key] ?? raw[key];
    const normalized = textValue(value);
    if (normalized) return normalized;
  }
  if ((record.platform === "instagram" || record.platform === "tiktok" || record.platform === "x") && textValue(record.url)) {
    return textValue(record.url);
  }
  return undefined;
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

type CreatorEvidence = Record<string, unknown>;

function creatorEvidence(record: RecordItem): CreatorEvidence | undefined {
  if (record.channel !== "creators" && record.data_type !== "creator_post") return undefined;
  const raw = record.raw || {};
  if (raw.collection_evidence && typeof raw.collection_evidence === "object") {
    return raw.collection_evidence as CreatorEvidence;
  }
  if (raw.evidence_type || raw.matched_in || raw.transcript_status || raw.transcript_matches) return raw;
  return undefined;
}

function secondsLabel(value: unknown): string {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds < 0) return "";
  const whole = Math.floor(seconds);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const rest = whole % 60;
  if (hours) return `${hours}:${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

function timestampUrl(url: string | undefined, value: unknown): string | undefined {
  const seconds = Number(value);
  if (!url || !Number.isFinite(seconds) || seconds < 0) return undefined;
  try {
    const parsed = new URL(url);
    parsed.searchParams.set("t", String(Math.floor(seconds)));
    return parsed.toString();
  } catch {
    return undefined;
  }
}

function evidenceSummary(evidence: CreatorEvidence): string {
  const matches = Array.isArray(evidence.transcript_matches) ? evidence.transcript_matches : [];
  const scope = String(evidence.scope || "");
  if (matches.length) {
    const first = matches[0] as Record<string, unknown>;
    const start = secondsLabel(first.start);
    const end = secondsLabel(first.end);
    return start && end ? `字幕命中片段 · ${start}–${end}` : "字幕命中片段";
  }
  if (scope === "transcript_and_metadata") return "字幕 + 标题/简介命中";
  if (scope === "title_and_description") return "标题 + 简介命中";
  if (scope === "title") return "标题命中";
  if (scope === "description") return "简介命中";
  if (scope === "metadata") return "公开元数据命中";
  if (evidence.evidence_type === "product_keyword") return "产品名/别名命中";
  return "历史记录未保存字段级证据";
}

function CreatorEvidenceDetails({ record }: { record: RecordItem }) {
  const evidence = creatorEvidence(record);
  if (!evidence) return null;
  const matches = (Array.isArray(evidence.transcript_matches) ? evidence.transcript_matches : []) as Record<string, unknown>[];
  const terms = Array.from(new Set([
    ...(typeof evidence.matched_text === "string" ? evidence.matched_text.split(/[、,，]/) : []),
    ...(Array.isArray(evidence.matched_texts) ? evidence.matched_texts : []),
    ...(Array.isArray(evidence.matched_terms) ? evidence.matched_terms : []),
    ...matches.flatMap((item) => [item.matched_text, ...(Array.isArray(item.matched_terms) ? item.matched_terms : [])]),
  ].map((item) => String(item || "").trim()).filter(Boolean)));
  const locationLabels: Record<string, string> = {
    body: "简介",
    title_and_description: "标题 + 简介",
    transcript: "视频字幕",
    metadata: "公开元数据",
  };
  const locations = Array.from(new Set([
    ...(evidence.matched_in ? [String(evidence.matched_in)] : []),
    ...(matches.length ? ["transcript"] : []),
  ].map((value) => locationLabels[value] || value)));
  const status = String(record.raw?.transcript_status || evidence.transcript_status || "");
  const note = String(evidence.analysis_note || "");
  const variantTarget = matches.find((item) => item.match_rule === "automatic_caption_alias")?.matched_query;
  const scopeLabel = matches.length
    ? "局部字幕片段（可定位）"
    : String(evidence.scope || "") === "title_and_description"
      ? "标题 + 简介（未确认完整视频）"
      : String(evidence.scope || "") === "title"
        ? "标题（未确认完整视频）"
        : String(evidence.scope || "") === "description"
          ? "简介（未确认完整视频）"
          : "公开元数据（未确认完整视频）";
  return (
    <details className="mt-3 rounded-md" style={{ background: "var(--bg-soft-2)", border: "1px solid var(--hairline)" }}>
      <summary className="cursor-pointer select-none px-2.5 py-2 text-[12px]" style={{ color: "var(--body)" }}>
        <span className="font-medium" style={{ color: "var(--ink)" }}>为什么收录？</span>
        <span className="ml-2" style={{ color: "var(--accent)" }}>{evidenceSummary(evidence)}</span>
      </summary>
      <div className="space-y-2 px-2.5 pb-2.5 text-[12px] leading-relaxed" style={{ color: "var(--mute)" }}>
        {locations.length > 0 && <div><span style={{ color: "var(--body)" }}>命中位置：</span>{locations.join("、")}</div>}
        <div><span style={{ color: "var(--body)" }}>证据范围：</span>{scopeLabel}</div>
        {terms.length > 0 && <div><span style={{ color: "var(--body)" }}>命中词：</span>{terms.join("、")}</div>}
        {Array.isArray(evidence.matched_queries) && evidence.matched_queries.length > 0 && (
          <div><span style={{ color: "var(--body)" }}>搜索入口：</span>{evidence.matched_queries.map((item) => String(item)).join("、")}</div>
        )}
        {matches.length > 0 && (
          <div className="space-y-1.5">
            <div style={{ color: "var(--body)" }}>字幕证据（命中片段，不代表整段视频）：</div>
            {matches.slice(0, 4).map((item, index) => {
              const start = secondsLabel(item.start);
              const end = secondsLabel(item.end);
              const label = start && end ? `${start}–${end}` : start || "视频片段";
              const href = timestampUrl(record.url, item.start);
              const text = String(item.text || "").trim();
              return (
                <div key={`${label}-${index}`} className="rounded px-2 py-1.5" style={{ background: "var(--panel)" }}>
                  {href ? <a href={href} target="_blank" rel="noreferrer" className="font-medium hover:underline" style={{ color: "var(--accent)" }}>{label}</a> : <span className="font-medium" style={{ color: "var(--accent)" }}>{label}</span>}
                  {text && <span className="ml-2">“{text}”</span>}
                </div>
              );
            })}
          </div>
        )}
        {Array.isArray(evidence.transcript_variants) && evidence.transcript_variants.length > 0 && (
          <div><span style={{ color: "var(--body)" }}>自动字幕变体：</span>{evidence.transcript_variants.join("、")} → {String(variantTarget || "品牌词")}</div>
        )}
        {status === "unavailable" || status === "error" ? <div>没有可用的公开字幕，因此无法定位视频时间段。</div> : null}
        {status === "available_no_match" ? <div>已检查到公开字幕，但字幕中没有额外命中品牌词。</div> : null}
        {status === "not_checked_budget" ? <div>本次采集达到字幕检查上限，暂未检查公开字幕。</div> : null}
        {status === "not_checked" ? <div>本次采集没有取得可定位的公开字幕证据。</div> : null}
        {note && <div className="border-t pt-1" style={{ borderColor: "var(--hairline)" }}>{note}</div>}
      </div>
    </details>
  );
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
  const thumbnail = thumbnailSrc(recordThumbnail(record));
  const platform = record.platform || "social";
  const platformLabel = SOCIAL_PLATFORM_LABEL[platform] || platform;
  const instagramReel = platform === "instagram"
    && (record.raw?.is_video === true || record.raw?.post_type === "clips");
  const aspectClass = platform === "tiktok" || instagramReel
    ? "aspect-[9/16]"
    : platform === "instagram"
      ? "aspect-[4/5]"
      : "aspect-video";
  const media = (
    <div
      className={`relative overflow-hidden ${aspectClass}`}
      style={{ background: SOCIAL_PLATFORM_BACKGROUND[platform] || "linear-gradient(135deg, #475569, #0f172a)" }}
    >
      {thumbnail && !failed ? (
        <img
          src={thumbnail}
          alt={record.title ? `${record.title} 封面` : `${platformLabel} 内容封面`}
          loading="lazy"
          referrerPolicy="origin"
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
    <article className="panel mb-4 break-inside-avoid overflow-hidden flex flex-col">
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
        <CreatorEvidenceDetails record={record} />
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
      <div className="columns-1 gap-4 md:columns-2 xl:columns-3">
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
              <CreatorEvidenceDetails record={r} />
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
