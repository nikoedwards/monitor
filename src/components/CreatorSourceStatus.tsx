import { useMonitoringStatus } from "../lib/hooks";
import { fmtDateTime } from "../lib/format";

const LABELS: Record<string, string> = {
  youtube_search: "YouTube",
  tiktok_listening: "TikTok",
  instagram_listening: "Instagram",
};

const STATUS_LABELS: Record<string, string> = {
  ok: "正常",
  error: "失败",
  needs_credential: "需配置",
  skipped: "跳过",
};

const PLATFORM_LABELS: Record<string, string> = {
  youtube_search: "YouTube",
  tiktok_listening: "TikTok",
  instagram_listening: "Instagram",
};

export function CreatorSourceStatus({ brandId }: { brandId?: string }) {
  const { data } = useMonitoringStatus(brandId, "marketing");
  const sources = (data?.sources || []).filter((source: any) => source.category === "creators");
  if (!sources.length) return null;

  return (
    <div className="flex flex-wrap items-center gap-2 text-[11px]" style={{ color: "var(--mute)" }}>
      {sources.filter((source: any) => LABELS[source.id]).map((source: any) => {
        const status = source.last_status || source.status || "pending";
        const tone = status === "ok" ? "var(--accent)" : status === "error" ? "var(--danger)" : "var(--warning)";
        const lastRun = source.last_collect_at ? fmtDateTime(source.last_collect_at) : "尚未采集";
        const itemCount = Number(source.item_count || 0);
        const resultLabel = status === "ok" && itemCount === 0 ? "0 条" : `${itemCount} 条`;
        return (
          <span key={source.id} title={source.last_error || `${LABELS[source.id]}：${lastRun}，${resultLabel}`}>
            <span className="font-medium" style={{ color: "var(--body)" }}>{PLATFORM_LABELS[source.id] || LABELS[source.id]}</span>{" "}
            <span style={{ color: tone }}>{STATUS_LABELS[status] || "待采集"}</span>
            {` · ${resultLabel} · ${lastRun}`}
          </span>
        );
      })}
      <span>每日采集 · 仅保留最近 24 小时内容</span>
    </div>
  );
}
