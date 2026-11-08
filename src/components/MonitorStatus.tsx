import { useMonitoringRefresh, useMonitoringStatus } from "../lib/hooks";
import { fmtDateTime, fmtInterval } from "../lib/format";
import { Button } from "./ui";

export function MonitorStatus({ brandId, dimension }: { brandId?: string; dimension: "marketing" | "sales" }) {
  const { data } = useMonitoringStatus(brandId, dimension);
  const refresh = useMonitoringRefresh();
  const enabled = data?.scheduler?.enabled;
  const interval = data?.scheduler?.interval_seconds;
  const failures = (data?.sources || []).filter((source: any) => ["error", "partial", "blocked", "network", "needs_credential", "unsupported"].includes(source.last_status));

  return (
    <div
      className="flex items-center gap-3 px-3 h-9 rounded-md"
      style={{ background: "var(--bg-soft-2)", border: "1px solid var(--hairline)" }}
    >
      <span className="flex items-center gap-1.5 text-[13px] font-medium whitespace-nowrap" style={{ color: "var(--ink)" }}>
        <span className="relative flex h-2 w-2">
          {enabled && (
            <span className="absolute inline-flex h-full w-full rounded-full opacity-60 animate-ping" style={{ background: "var(--accent)" }} />
          )}
          <span className="relative inline-flex h-2 w-2 rounded-full" style={{ background: enabled ? "var(--accent)" : "var(--hairline-strong)" }} />
        </span>
        {enabled ? `定时采集 · 每${fmtInterval(interval)}` : "采集已暂停"}
      </span>
      <span className="text-[12px] whitespace-nowrap hidden sm:inline" style={{ color: "var(--mute)" }}>
        上次 {fmtDateTime(data?.last_run)} · 预计下次 {fmtDateTime(data?.next_run_estimate)}
      </span>
      {failures.length > 0 && <span className="text-[12px] whitespace-nowrap" style={{ color: "var(--warning, #b45309)" }} title={failures.map((source: any) => `${source.name}: ${source.last_error || source.last_status}`).join("\n")}>
        {failures.length} 个数据源异常或受限
      </span>}
      <Button
        size="sm"
        disabled={!brandId || refresh.isPending}
        onClick={() => brandId && refresh.mutate({ brandId, dimension })}
        className="whitespace-nowrap"
      >
        {refresh.isPending ? "刷新中…" : "手动刷新"}
      </Button>
    </div>
  );
}

export function SocialAccountStatus({ brandId }: { brandId?: string }) {
  const { data } = useMonitoringStatus(brandId, "marketing");
  const accounts = data?.sources?.find((source: any) => source.id === "social_accounts")?.accounts || [];
  const labels: Record<string, string> = { ok: "采集正常", empty: "未发现公开内容", unsupported: "尚未支持", error: "采集失败", blocked: "访问受限", network: "网络异常", needs_credential: "需要凭证" };
  if (!accounts.length) return null;
  return <div className="flex flex-wrap gap-3 text-[12px]" aria-label="社媒账号采集状态">
    {accounts.map((account: any) => <div key={account.id} className="rounded-md border px-3 py-2" style={{ borderColor: "var(--hairline)", color: ["ok", "empty"].includes(account.last_status) ? "var(--body)" : "var(--warning, #b45309)" }} title={account.last_error || ""}>
      <span className="font-medium">{account.platform === "instagram" ? "Instagram" : account.platform === "youtube" ? "YouTube" : account.platform === "linkedin" ? "LinkedIn" : account.platform === "x" ? "X" : account.platform}</span>
      {` · ${labels[account.last_status] || "等待采集"} · ${fmtDateTime(account.last_collect_at)}`}
      {account.last_error && <div className="mt-1 max-w-lg">{account.last_error}</div>}
    </div>)}
  </div>;
}
