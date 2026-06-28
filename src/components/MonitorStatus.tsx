import { useMonitoringRefresh, useMonitoringStatus } from "../lib/hooks";
import { fmtDateTime, fmtInterval } from "../lib/format";
import { Button } from "./ui";

export function MonitorStatus({ brandId, dimension }: { brandId?: string; dimension: "marketing" | "sales" }) {
  const { data } = useMonitoringStatus(brandId, dimension);
  const refresh = useMonitoringRefresh();
  const enabled = data?.scheduler?.enabled;
  const interval = data?.scheduler?.interval_seconds;

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
