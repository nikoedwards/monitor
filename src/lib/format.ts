export function fmtDate(value?: string): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value.slice(0, 10);
  return d.toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
}

export function fmtDateTime(value?: string): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function fmtInterval(seconds?: number): string {
  if (!seconds || seconds <= 0) return "—";
  if (seconds % 86400 === 0) return `${seconds / 86400} 天`;
  if (seconds % 3600 === 0) return `${seconds / 3600} 小时`;
  if (seconds % 60 === 0) return `${seconds / 60} 分钟`;
  return `${seconds} 秒`;
}

export function fmtNum(value?: number | null): string {
  if (value === undefined || value === null) return "—";
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (Math.abs(value) >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(Math.round(value * 100) / 100);
}

export function sentimentTone(s?: string): "positive" | "negative" | "neutral" | "warning" {
  if (s === "positive") return "positive";
  if (s === "negative") return "negative";
  return "neutral";
}

export const SENTIMENT_LABEL: Record<string, string> = {
  positive: "正向",
  negative: "负向",
  neutral: "中性",
};

export const TEAM_LABEL: Record<string, string> = {
  experience_team: "体验团队",
  support_team: "客服团队",
  product_team: "产品团队",
  marketing_team: "市场团队",
};

export const CHANNEL_LABEL: Record<string, string> = {
  amazon: "Amazon",
  dtc: "独立站 DTC",
  other_ecom: "其他电商",
  offline: "线下渠道",
  media: "媒体公关",
  social: "社交媒体",
  ads: "广告投放",
  creators: "红人达人",
  community: "社群",
  app: "应用商店",
};

export const PRIORITY_LABEL: Record<string, string> = {
  urgent: "紧急",
  high: "高",
  medium: "中",
  low: "低",
};

export const STATUS_LABEL: Record<string, string> = {
  open: "待处理",
  assigned: "已分派",
  in_progress: "处理中",
  resolved: "已解决",
  closed: "已关闭",
};

export const TIER_LABEL: Record<number, string> = {
  1: "免费实时",
  2: "需凭证",
  3: "付费/接缝",
};

export const SOURCE_STATUS_LABEL: Record<string, string> = {
  ready: "可采集",
  needs_credential: "需凭证",
  planned: "规划中",
};
