import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { TimeRangePicker } from "../components/TimeRangePicker";
import { TrendChart } from "../components/charts";
import { Badge, Button, Card, EmptyState, Field, Input, Modal, SectionTitle, Select, Spinner, StatCard } from "../components/ui";
import { useDeleteWebSnapshot, useWebAnalysis, useWebMonitors, useWebMutations, useWebSnapshotHistory, useWebSnapshots, useWebSummary } from "../lib/hooks";
import { fmtDate, fmtDateTime } from "../lib/format";
import { rangeLabel, useTimeRange } from "../lib/timeRange";
import type { WebAiAnalysis, WebMonitor, WebSnapshot, WebSummary } from "../lib/api";

const INTERVAL_OPTIONS = [
  { value: 60, label: "每小时" },
  { value: 360, label: "每 6 小时" },
  { value: 720, label: "每 12 小时" },
  { value: 1440, label: "每天" },
  { value: 4320, label: "每 3 天" },
  { value: 10080, label: "每周" },
  { value: 43200, label: "每 30 天" },
];

type MonitorForm = {
  url: string;
  name: string;
  scope: string;
  crawl_limit: number;
  check_interval_minutes: number;
  snapshot_interval_minutes: number;
  status: string;
  capture_now: boolean;
};

const EMPTY_FORM: MonitorForm = {
  url: "",
  name: "",
  scope: "single_page",
  crawl_limit: 20,
  check_interval_minutes: 1440,
  snapshot_interval_minutes: 1440,
  status: "active",
  capture_now: true,
};

function intervalLabel(minutes?: number) {
  return INTERVAL_OPTIONS.find((option) => option.value === minutes)?.label || `每 ${minutes || 1440} 分钟`;
}

function countdownLabel(nextAt: string | undefined, status: string, now: number) {
  if (status !== "active") return "已暂停";
  if (!nextAt) return "等待调度";
  const seconds = Math.ceil((new Date(nextAt).getTime() - now) / 1000);
  if (!Number.isFinite(seconds) || seconds <= 0) return "等待调度";
  const totalMinutes = Math.ceil(seconds / 60);
  if (totalMinutes < 60) return `${totalMinutes} 分钟后`;
  if (totalMinutes < 1440) {
    const hours = Math.floor(totalMinutes / 60);
    const minutes = totalMinutes % 60;
    return minutes ? `${hours} 小时 ${minutes} 分钟后` : `${hours} 小时后`;
  }
  const days = Math.floor(totalMinutes / 1440);
  const hours = Math.floor((totalMinutes % 1440) / 60);
  return hours ? `${days} 天 ${hours} 小时后` : `${days} 天后`;
}

function schedulesMatch(monitor: WebMonitor) {
  if (monitor.check_interval_minutes !== monitor.snapshot_interval_minutes) return false;
  if (!monitor.next_check_at && !monitor.next_snapshot_at) return true;
  if (!monitor.next_check_at || !monitor.next_snapshot_at) return false;
  return Math.abs(new Date(monitor.next_check_at).getTime() - new Date(monitor.next_snapshot_at).getTime()) < 60_000;
}

function percent(value?: number | null) {
  return `${Math.round((value || 0) * 100)}%`;
}

function archiveSize(value?: number) {
  if (!value) return "—";
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function comparisonLabel(summary?: WebSummary) {
  if (!summary) return "—";
  const delta = summary.comparison.changed_days_delta;
  if (summary.comparison.trend === "more_active") return `更活跃 +${delta} 天`;
  if (summary.comparison.trend === "more_stable") return `更稳定 ${delta} 天`;
  return "与上期持平";
}

export default function Web() {
  const { brandId } = useParams();
  const [range] = useTimeRange();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<WebMonitor | null>(null);
  const [selected, setSelected] = useState<string | undefined>();
  const [now, setNow] = useState(() => Date.now());
  const [analysis, setAnalysis] = useState<WebAiAnalysis | null>(null);
  const { data: monitors = [], isLoading } = useWebMonitors(brandId);
  const { data: summary } = useWebSummary(brandId, selected, range);
  const { data: snapshots = [] } = useWebSnapshots(brandId, selected, range);
  const webAnalysis = useWebAnalysis();
  const { capture, update, remove } = useWebMutations();

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    setAnalysis(null);
    webAnalysis.reset();
  }, [selected, range.start_date, range.end_date]);

  if (isLoading) return <Spinner />;

  return (
    <div className="space-y-6">
      <SectionTitle
        title="网页快照监控"
        subtitle="保存截图与离线网页归档，并按日期范围分析视觉、内容和变化频率"
        action={(
          <div className="flex flex-wrap items-center justify-end gap-2">
            <TimeRangePicker />
            <Button variant="primary" onClick={() => setOpen(true)}>+ 新增监控</Button>
          </div>
        )}
      />

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatCard label="范围内快照" value={summary?.total_snapshots ?? 0} hint={rangeLabel(range)} />
        <StatCard label="发生变化的天数" value={summary?.changed_days ?? 0} hint={`覆盖率 ${percent(summary?.change_day_rate)}`} tone="negative" />
        <StatCard label="平均变化间隔" value={summary?.average_interval_days != null ? `${summary.average_interval_days} 天` : "—"} hint={`${summary?.changed ?? 0} 次有效变化`} />
        <StatCard label="相对上一周期" value={comparisonLabel(summary)} hint={`${summary?.previous_period?.changed_days ?? 0} 个变化日`} tone="accent" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card>
          <SectionTitle title="监控列表" subtitle={`${monitors.length} 个任务`} />
          {monitors.length ? (
            <div className="space-y-2">
              <button
                onClick={() => setSelected(undefined)}
                className="w-full text-left px-3 py-2 rounded-md text-[13px] cursor-pointer"
                style={{ background: !selected ? "var(--bg-soft-2)" : "transparent", color: "var(--ink)" }}
              >
                全部页面
              </button>
              {monitors.map((monitor) => (
                <div key={monitor.id} className="p-3 rounded-md" style={{ background: selected === monitor.id ? "var(--bg-soft-2)" : "var(--bg-soft)" }}>
                  <button onClick={() => setSelected(monitor.id)} className="w-full text-left cursor-pointer">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[13px] font-medium truncate" style={{ color: "var(--ink)" }}>{monitor.name}</span>
                      <div className="flex gap-1">
                        <Badge tone={monitor.status === "active" ? "positive" : "neutral"}>{monitor.status === "active" ? "运行中" : "已暂停"}</Badge>
                        <Badge tone={monitor.scope === "domain" ? "accent" : "neutral"}>{monitor.scope === "domain" ? "整站" : "单页"}</Badge>
                      </div>
                    </div>
                    <div className="text-[12px] truncate mt-0.5" style={{ color: "var(--mute)" }}>{monitor.url}</div>
                    <div className="text-[12px] mt-1" style={{ color: "var(--mute)" }}>{monitor.snapshot_count} 张快照 · {fmtDate(monitor.latest_snapshot_date)}</div>
                    <div className="text-[12px] mt-1" style={{ color: "var(--body)" }}>
                      {schedulesMatch(monitor)
                        ? `${intervalLabel(monitor.check_interval_minutes)}检查并截图归档`
                        : `${intervalLabel(monitor.check_interval_minutes)}检查 · ${intervalLabel(monitor.snapshot_interval_minutes)}截图归档`}
                    </div>
                    <div className="mt-2 space-y-0.5 text-[12px]" style={{ color: "var(--mute)" }}>
                      {schedulesMatch(monitor) ? (
                        <div>下次检查并截图：{monitor.next_snapshot_at ? fmtDateTime(monitor.next_snapshot_at) : "—"} · {countdownLabel(monitor.next_snapshot_at, monitor.status, now)}</div>
                      ) : (
                        <>
                          <div>下次检查：{monitor.next_check_at ? fmtDateTime(monitor.next_check_at) : "—"} · {countdownLabel(monitor.next_check_at, monitor.status, now)}</div>
                          <div>下次截图归档：{monitor.next_snapshot_at ? fmtDateTime(monitor.next_snapshot_at) : "—"} · {countdownLabel(monitor.next_snapshot_at, monitor.status, now)}</div>
                        </>
                      )}
                    </div>
                  </button>
                  <div className="flex flex-wrap gap-2 mt-3">
                    <Button size="sm" disabled={capture.isPending} onClick={() => capture.mutate(monitor.id)}>立即截图</Button>
                    <Button size="sm" onClick={() => setEditing(monitor)}>编辑</Button>
                    <Button size="sm" disabled={update.isPending} onClick={() => update.mutate({ id: monitor.id, status: monitor.status === "active" ? "paused" : "active" })}>
                      {monitor.status === "active" ? "暂停" : "启用"}
                    </Button>
                    <Button size="sm" variant="danger" onClick={() => remove.mutate(monitor.id)}>删除</Button>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState title="暂无监控" hint="添加竞品官网、落地页或定价页开始监控。" />
          )}
        </Card>

        <Card className="lg:col-span-2">
          <SnapshotTimeline snapshots={snapshots} brandId={brandId || ""} monitorId={selected} rangeText={rangeLabel(range)} />
        </Card>
      </div>

      <SnapshotAnalysisPanel
        summary={summary}
        analysis={analysis}
        pending={webAnalysis.isPending}
        error={webAnalysis.error instanceof Error ? webAnalysis.error.message : ""}
        onAnalyze={async (refresh) => {
          if (!brandId) return;
          const result = await webAnalysis.mutateAsync({ brandId, monitorId: selected, range, refresh });
          setAnalysis(result);
        }}
      />

      <MonitorModal open={open} onClose={() => setOpen(false)} brandId={brandId!} />
      <MonitorModal open={!!editing} onClose={() => setEditing(null)} brandId={brandId!} monitor={editing || undefined} />
    </div>
  );
}

type SnapshotMode = "screenshot" | "archive";

function snapshotMonth(snapshot: WebSnapshot) {
  return snapshot.snapshot_date.slice(0, 7);
}

function snapshotTime(snapshot: WebSnapshot) {
  const value = new Date(snapshot.created_at);
  if (Number.isNaN(value.getTime())) return "—";
  return value.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
}

function SnapshotViewer({
  snapshot,
  mode,
  onModeChange,
  onDelete,
}: {
  snapshot: WebSnapshot;
  mode: SnapshotMode;
  onModeChange: (mode: SnapshotMode) => void;
  onDelete?: () => void;
}) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex gap-2">
          <Button size="sm" variant={mode === "screenshot" ? "primary" : "secondary"} onClick={() => onModeChange("screenshot")}>截图</Button>
          <Button size="sm" variant={mode === "archive" ? "primary" : "secondary"} disabled={!snapshot.archive_url} onClick={() => onModeChange("archive")}>交互归档</Button>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone={snapshot.archive_self_contained ? "positive" : "warning"}>{snapshot.archive_self_contained ? "自包含归档" : "受限归档"}</Badge>
          <Badge tone="neutral">{archiveSize(snapshot.archive_size)}</Badge>
          {onDelete && <Button size="sm" variant="danger" onClick={onDelete}>删除此快照</Button>}
        </div>
      </div>
      <div className="text-[13px]" style={{ color: "var(--body)" }}>{snapshot.summary}</div>
      {snapshot.changes?.length > 0 && (
        <div className="space-y-1 max-h-32 overflow-y-auto">
          {snapshot.changes.map((change, index) => (
            <div key={index} className="text-[12px] flex gap-2" style={{ color: change.type === "removed" ? "var(--danger)" : "var(--body)" }}>
              <Badge tone={change.type === "added" ? "positive" : change.type === "removed" ? "negative" : "neutral"}>{change.type}</Badge>
              <span>{change.text || `${change.from} → ${change.to}`}</span>
            </div>
          ))}
        </div>
      )}
      {mode === "screenshot" ? (
        <img src={snapshot.screenshot_url} alt={snapshot.title} className="w-full rounded-md" style={{ border: "1px solid var(--hairline)" }} />
      ) : (
        <div>
          <div className="text-[12px] mb-2 rounded-md px-3 py-2" style={{ color: "var(--mute)", background: "var(--bg-soft)" }}>
            归档在独立沙箱中运行：允许离线脚本交互，但禁止联网、表单提交、下载和外部跳转。视频仅保存封面。
          </div>
          <iframe
            src={snapshot.archive_url}
            title={`${snapshot.title || "网页"}交互归档`}
            sandbox="allow-scripts"
            referrerPolicy="no-referrer"
            className="w-full rounded-md bg-white"
            style={{ height: "72vh", border: "1px solid var(--hairline)" }}
          />
        </div>
      )}
      <div className="text-[12px] flex flex-wrap gap-x-3 gap-y-1" style={{ color: "var(--mute)" }}>
        <span>{fmtDateTime(snapshot.created_at)}</span>
        <span>视觉变化 {percent(snapshot.visual_change_score)}</span>
        <span>文本变化 {percent(snapshot.change_score)}</span>
        <a href={snapshot.final_url || snapshot.url} target="_blank" rel="noreferrer" className="hover:underline">访问当前网页</a>
      </div>
    </div>
  );
}

function SnapshotHistoryModal({
  open,
  onClose,
  snapshots,
  loading,
  selectedId,
  onSelect,
  mode,
  onModeChange,
  onDelete,
}: {
  open: boolean;
  onClose: () => void;
  snapshots: WebSnapshot[];
  loading: boolean;
  selectedId?: string;
  onSelect: (id: string) => void;
  mode: SnapshotMode;
  onModeChange: (mode: SnapshotMode) => void;
  onDelete: (snapshot: WebSnapshot) => void;
}) {
  const selected = snapshots.find((snapshot) => snapshot.id === selectedId) || snapshots[0];
  const years = Array.from(new Set(snapshots.map((snapshot) => snapshot.snapshot_date.slice(0, 4)))).sort((a, b) => b.localeCompare(a));
  const selectedYear = selected?.snapshot_date.slice(0, 4) || years[0];
  const selectedMonth = selected ? snapshotMonth(selected) : "";
  const monthSnapshots = selectedMonth ? snapshots.filter((snapshot) => snapshotMonth(snapshot) === selectedMonth) : [];
  const selectedIndex = selected ? snapshots.findIndex((snapshot) => snapshot.id === selected.id) : -1;
  return (
    <Modal open={open} onClose={onClose} title="快照历史" width={1280}>
      {loading && !snapshots.length ? <Spinner /> : !selected ? (
        <EmptyState title="暂无历史快照" hint="创建快照后，可以在这里按年份、月份和抓取时间浏览。" />
      ) : (
        <div className="space-y-4">
          <div className="rounded-lg p-4 space-y-3" style={{ background: "var(--bg-soft)", border: "1px solid var(--hairline)" }}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap gap-2">
                {years.map((year) => (
                  <button
                    key={year}
                    onClick={() => {
                      const candidate = snapshots.find((snapshot) => snapshot.snapshot_date.startsWith(year));
                      if (candidate) onSelect(candidate.id);
                    }}
                    className="h-8 px-3 rounded-md text-[13px] font-medium cursor-pointer"
                    style={year === selectedYear
                      ? { background: "var(--ink)", color: "var(--bg)" }
                      : { background: "var(--panel)", color: "var(--body)", border: "1px solid var(--hairline-strong)" }}
                  >
                    {year}
                  </button>
                ))}
              </div>
              <div className="text-[12px]" style={{ color: "var(--mute)" }}>
                共 {snapshots.length} 张 · {snapshots[snapshots.length - 1]?.snapshot_date} 至 {snapshots[0]?.snapshot_date}
              </div>
            </div>

            <div className="grid grid-cols-4 sm:grid-cols-6 lg:grid-cols-12 gap-1.5">
              {Array.from({ length: 12 }, (_, index) => {
                const month = `${selectedYear}-${String(index + 1).padStart(2, "0")}`;
                const items = snapshots.filter((snapshot) => snapshotMonth(snapshot) === month);
                const isSelected = month === selectedMonth;
                return (
                  <button
                    key={month}
                    disabled={!items.length}
                    onClick={() => items[0] && onSelect(items[0].id)}
                    className="rounded-md py-2 text-center cursor-pointer disabled:cursor-default"
                    style={isSelected
                      ? { background: "rgba(0,112,243,0.12)", color: "var(--accent)", border: "1px solid var(--accent)" }
                      : { background: items.length ? "var(--panel)" : "transparent", color: items.length ? "var(--body)" : "var(--mute)", border: "1px solid var(--hairline)" }}
                  >
                    <div className="text-[12px] font-medium">{index + 1}月</div>
                    <div className="text-[10px] mt-0.5 tabular-nums">{items.length || "—"}</div>
                  </button>
                );
              })}
            </div>

            <div className="flex gap-2 overflow-x-auto pb-1">
              {monthSnapshots.map((snapshot) => (
                <button
                  key={snapshot.id}
                  onClick={() => onSelect(snapshot.id)}
                  className="shrink-0 min-w-[92px] rounded-md px-3 py-2 text-left cursor-pointer"
                  style={snapshot.id === selected.id
                    ? { background: "var(--ink)", color: "var(--bg)" }
                    : { background: "var(--panel)", color: "var(--body)", border: "1px solid var(--hairline-strong)" }}
                >
                  <div className="text-[12px] font-medium">{snapshot.snapshot_date.slice(5)}</div>
                  <div className="text-[10px] mt-0.5 opacity-70">{snapshotTime(snapshot)} · {snapshot.page_path}</div>
                  {snapshot.has_meaningful_change && <div className="text-[10px] mt-1" style={{ color: snapshot.id === selected.id ? "inherit" : "var(--danger)" }}>● 发生变化</div>}
                </button>
              ))}
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-[15px] font-medium" style={{ color: "var(--ink)" }}>{selected.title || selected.url}</div>
              <div className="text-[12px] mt-0.5" style={{ color: "var(--mute)" }}>{fmtDateTime(selected.created_at)} · {selected.page_path}</div>
            </div>
            <div className="flex gap-2">
              <Button size="sm" disabled={selectedIndex < 0 || selectedIndex >= snapshots.length - 1} onClick={() => onSelect(snapshots[selectedIndex + 1].id)}>← 较早</Button>
              <Button size="sm" disabled={selectedIndex <= 0} onClick={() => onSelect(snapshots[selectedIndex - 1].id)}>较新 →</Button>
            </div>
          </div>
          <SnapshotViewer snapshot={selected} mode={mode} onModeChange={onModeChange} onDelete={() => onDelete(selected)} />
        </div>
      )}
    </Modal>
  );
}

function SnapshotTimeline({ snapshots, brandId, monitorId, rangeText }: { snapshots: WebSnapshot[]; brandId: string; monitorId?: string; rangeText: string }) {
  const [active, setActive] = useState<WebSnapshot | null>(null);
  const [mode, setMode] = useState<SnapshotMode>("screenshot");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historySelectedId, setHistorySelectedId] = useState<string | undefined>();
  const [historyMode, setHistoryMode] = useState<SnapshotMode>("screenshot");
  const [deleting, setDeleting] = useState<WebSnapshot | null>(null);
  const history = useWebSnapshotHistory(brandId, monitorId, historyOpen);
  const deleteSnapshot = useDeleteWebSnapshot();
  const detailSnapshots = history.data?.length ? history.data : snapshots;

  useEffect(() => {
    if (!historyOpen) return;
    if (!detailSnapshots.length) {
      setHistorySelectedId(undefined);
      return;
    }
    if (!detailSnapshots.some((snapshot) => snapshot.id === historySelectedId)) setHistorySelectedId(detailSnapshots[0].id);
  }, [historyOpen, historySelectedId, detailSnapshots]);

  const selectHistory = (id: string) => {
    setHistorySelectedId(id);
    setHistoryMode("screenshot");
  };

  const requestDelete = (snapshot: WebSnapshot) => {
    deleteSnapshot.reset();
    setDeleting(snapshot);
  };

  const confirmDelete = async () => {
    if (!deleting) return;
    try {
      await deleteSnapshot.mutateAsync(deleting.id);
      const remaining = detailSnapshots.filter((snapshot) => snapshot.id !== deleting.id);
      if (active?.id === deleting.id) setActive(null);
      if (historySelectedId === deleting.id) setHistorySelectedId(remaining[0]?.id);
      if (!remaining.length) setHistoryOpen(false);
      setDeleting(null);
    } catch {
      /* error is rendered in the confirmation modal */
    }
  };

  return (
    <>
      <SectionTitle
        title="快照时间线"
        subtitle={`${rangeText} · 点击查看截图或交互归档`}
        action={<Button size="sm" onClick={() => { setHistoryOpen(true); setHistorySelectedId(snapshots[0]?.id); setHistoryMode("screenshot"); }}>查看更多</Button>}
      />
      {snapshots.length ? (
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3 max-h-[720px] overflow-y-auto pr-1">
          {snapshots.map((snapshot) => (
            <div key={snapshot.id} className="relative panel overflow-hidden" style={{ padding: 0 }}>
              <button
                onClick={() => { setActive(snapshot); setMode("screenshot"); }}
                className="w-full text-left cursor-pointer"
                aria-label={`查看 ${fmtDate(snapshot.snapshot_date)} 快照`}
              >
                <div className="aspect-[4/3] overflow-hidden" style={{ background: "var(--bg-soft-2)" }}>
                  {snapshot.screenshot_url && <img src={snapshot.screenshot_url} alt={snapshot.title} loading="lazy" className="w-full h-full object-cover object-top" />}
                </div>
                <div className="p-2.5">
                  <div className="flex items-center justify-between gap-1">
                    <span className="text-[12px]" style={{ color: "var(--mute)" }}>{fmtDate(snapshot.snapshot_date)}</span>
                    {snapshot.has_meaningful_change
                      ? <Badge tone="negative">变化 {percent(snapshot.effective_change_score)}</Badge>
                      : <Badge tone="neutral">无明显变化</Badge>}
                  </div>
                  <div className="text-[12px] truncate mt-1" style={{ color: "var(--body)" }}>{snapshot.page_path}</div>
                  <div className="text-[11px] mt-1" style={{ color: "var(--mute)" }}>视觉 {percent(snapshot.visual_change_score)} · 文本 {percent(snapshot.change_score)}</div>
                </div>
              </button>
              <button
                type="button"
                aria-label={`删除 ${fmtDate(snapshot.snapshot_date)} 快照`}
                title="删除快照"
                onClick={() => requestDelete(snapshot)}
                className="absolute top-2 right-2 z-10 inline-flex h-7 w-7 items-center justify-center rounded-full text-[17px] leading-none cursor-pointer"
                style={{ background: "rgba(255,255,255,0.92)", color: "#555", border: "1px solid rgba(0,0,0,0.14)", boxShadow: "0 1px 4px rgba(0,0,0,0.14)" }}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      ) : (
        <EmptyState title="所选范围内暂无快照" hint="调整日期范围，或对监控页面点击「立即截图」生成基线快照。" />
      )}

      <Modal open={!!active} onClose={() => setActive(null)} title={active?.title || "快照"} width={1120}>
        {active && <SnapshotViewer snapshot={active} mode={mode} onModeChange={setMode} onDelete={() => requestDelete(active)} />}
      </Modal>

      <SnapshotHistoryModal
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        snapshots={detailSnapshots}
        loading={history.isLoading}
        selectedId={historySelectedId}
        onSelect={selectHistory}
        mode={historyMode}
        onModeChange={setHistoryMode}
        onDelete={requestDelete}
      />

      <Modal open={!!deleting} onClose={() => { if (!deleteSnapshot.isPending) setDeleting(null); }} title="删除这张快照？" width={520}>
        {deleting && (
          <div className="space-y-4">
            <div>
              <div className="text-[14px] font-medium" style={{ color: "var(--ink)" }}>{deleting.title || deleting.url}</div>
              <div className="text-[12px] mt-1" style={{ color: "var(--mute)" }}>{fmtDateTime(deleting.created_at)} · {deleting.page_path}</div>
            </div>
            <div className="rounded-md px-3 py-2 text-[13px]" style={{ background: "var(--danger-soft)", color: "var(--danger)" }}>
              截图文件、HTML 交互归档和这条时间线记录都会永久删除，无法恢复。监控任务本身不会被删除。
            </div>
            {deleteSnapshot.error instanceof Error && <div className="text-[12px]" style={{ color: "var(--danger)" }}>{deleteSnapshot.error.message}</div>}
            <div className="flex justify-end gap-2">
              <Button disabled={deleteSnapshot.isPending} onClick={() => setDeleting(null)}>取消</Button>
              <Button variant="danger" disabled={deleteSnapshot.isPending} onClick={confirmDelete}>{deleteSnapshot.isPending ? "删除中…" : "确认删除"}</Button>
            </div>
          </div>
        )}
      </Modal>
    </>
  );
}

function SnapshotAnalysisPanel({
  summary,
  analysis,
  pending,
  error,
  onAnalyze,
}: {
  summary?: WebSummary;
  analysis: WebAiAnalysis | null;
  pending: boolean;
  error: string;
  onAnalyze: (refresh: boolean) => Promise<void>;
}) {
  const trendText = summary?.comparison.trend === "more_active"
    ? "所选周期比上一等长周期更活跃"
    : summary?.comparison.trend === "more_stable"
      ? "所选周期比上一等长周期更稳定"
      : "所选周期与上一等长周期基本持平";
  return (
    <Card>
      <SectionTitle
        title="快照智能分析"
        subtitle="视觉差异负责发现真实前端变化，HTML 与文本用于补充证据，大模型负责解释变化意义"
        action={(
          <Button
            variant="primary"
            disabled={pending || !summary?.total_snapshots || (!summary?.ai_configured && !!summary?.changed)}
            onClick={() => onAnalyze(!!analysis)}
          >
            {pending ? "分析中…" : analysis ? "重新分析" : "生成 AI 分析"}
          </Button>
        )}
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 rounded-lg p-3" style={{ background: "var(--bg-soft)" }}>
          <div className="text-[13px] font-medium mb-2" style={{ color: "var(--ink)" }}>变化频率趋势</div>
          <TrendChart
            data={summary?.daily || []}
            keys={[
              { key: "captures", name: "快照", color: "var(--accent)" },
              { key: "changed", name: "有效变化", color: "var(--danger)" },
            ]}
          />
          <div className="text-[12px] mt-1" style={{ color: "var(--mute)" }}>
            {trendText}。本期 {summary?.changed_days ?? 0} 个变化日，上期 {summary?.previous_period.changed_days ?? 0} 个；
            平均严重度 {percent(summary?.average_severity)}。
          </div>
        </div>
        <div className="rounded-lg p-3" style={{ background: "var(--bg-soft)" }}>
          <div className="text-[13px] font-medium mb-3" style={{ color: "var(--ink)" }}>变化最活跃页面</div>
          <div className="space-y-3">
            {(summary?.page_activity || []).slice(0, 6).map((page) => (
              <div key={page.page}>
                <div className="flex items-center justify-between gap-2 text-[12px]">
                  <span className="truncate" style={{ color: "var(--body)" }}>{page.page}</span>
                  <span className="tabular-nums" style={{ color: "var(--mute)" }}>{page.changed}/{page.captures}</span>
                </div>
                <div className="h-1.5 rounded-full mt-1 overflow-hidden" style={{ background: "var(--bg-soft-2)" }}>
                  <div className="h-full rounded-full" style={{ width: percent(page.captures ? page.changed / page.captures : 0), background: "var(--danger)" }} />
                </div>
              </div>
            ))}
            {!summary?.page_activity?.length && <div className="text-[12px]" style={{ color: "var(--mute)" }}>暂无页面变化数据</div>}
          </div>
        </div>
      </div>

      {!summary?.ai_configured && !!summary?.changed && (
        <div className="text-[12px] mt-4 rounded-md px-3 py-2" style={{ color: "var(--warning)", background: "var(--warning-soft)" }}>
          当前未配置支持图片输入的大模型 Token；基础视觉统计仍可使用，配置后可生成变化解释和商业信号。
        </div>
      )}
      {error && <div className="text-[12px] mt-4 rounded-md px-3 py-2" style={{ color: "var(--danger)", background: "var(--danger-soft)" }}>{error}</div>}

      {analysis && (
        <div className="mt-5 space-y-4">
          <div className="rounded-lg p-4" style={{ background: "var(--bg-soft-2)" }}>
            <div className="flex items-center justify-between gap-2 mb-2">
              <div className="text-[14px] font-semibold" style={{ color: "var(--ink)" }}>AI 总结</div>
              <Badge tone="neutral">{analysis.cached ? "缓存结果" : analysis.model || "AI"}</Badge>
            </div>
            <p className="text-[13px] leading-relaxed" style={{ color: "var(--body)" }}>{analysis.summary}</p>
            {analysis.frequency_assessment && <p className="text-[13px] leading-relaxed mt-2" style={{ color: "var(--body)" }}>{analysis.frequency_assessment}</p>}
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <AnalysisList title="关键发现" items={analysis.highlights} />
            <AnalysisList title="商业信号" items={analysis.business_signals} />
          </div>
          {!!analysis.major_events?.length && (
            <div>
              <div className="text-[14px] font-semibold mb-2" style={{ color: "var(--ink)" }}>重大变化事件</div>
              <div className="space-y-2">
                {analysis.major_events.map((event, index) => (
                  <div key={`${event.date}-${index}`} className="rounded-md p-3" style={{ border: "1px solid var(--hairline)" }}>
                    <div className="flex flex-wrap items-center gap-2 mb-1">
                      <Badge tone="accent">{event.date}</Badge>
                      <span className="text-[12px]" style={{ color: "var(--mute)" }}>{event.page}</span>
                      {event.persistence && <Badge tone="neutral">{event.persistence}</Badge>}
                    </div>
                    <div className="text-[13px]" style={{ color: "var(--body)" }}>{event.change}</div>
                    {event.impact && <div className="text-[12px] mt-1" style={{ color: "var(--mute)" }}>{event.impact}</div>}
                  </div>
                ))}
              </div>
            </div>
          )}
          {!!analysis.change_categories?.length && (
            <div className="flex flex-wrap gap-2">
              {analysis.change_categories.map((category) => <Badge key={category.category} tone="accent">{category.category} · {category.count}</Badge>)}
            </div>
          )}
          {!!analysis.caveats?.length && <AnalysisList title="分析限制" items={analysis.caveats} muted />}
        </div>
      )}
    </Card>
  );
}

function AnalysisList({ title, items, muted = false }: { title: string; items?: string[]; muted?: boolean }) {
  return (
    <div>
      <div className="text-[14px] font-semibold mb-2" style={{ color: "var(--ink)" }}>{title}</div>
      {items?.length ? (
        <ul className="space-y-1.5">
          {items.map((item, index) => <li key={index} className="text-[13px] leading-relaxed" style={{ color: muted ? "var(--mute)" : "var(--body)" }}>• {item}</li>)}
        </ul>
      ) : <div className="text-[12px]" style={{ color: "var(--mute)" }}>暂无</div>}
    </div>
  );
}

function MonitorModal({ open, onClose, brandId, monitor }: { open: boolean; onClose: () => void; brandId: string; monitor?: WebMonitor }) {
  const { create, update } = useWebMutations();
  const [form, setForm] = useState<MonitorForm>(EMPTY_FORM);
  const isEditing = !!monitor;

  useEffect(() => {
    if (!open) return;
    setForm(
      monitor
        ? {
            url: monitor.url,
            name: monitor.name,
            scope: monitor.scope,
            crawl_limit: monitor.crawl_limit,
            check_interval_minutes: monitor.check_interval_minutes || 1440,
            snapshot_interval_minutes: monitor.snapshot_interval_minutes || 1440,
            status: monitor.status,
            capture_now: false,
          }
        : EMPTY_FORM,
    );
  }, [open, monitor]);

  const pending = create.isPending || update.isPending;
  return (
    <Modal open={open} onClose={onClose} title={isEditing ? "编辑网页监控" : "新增网页监控"} width={680}>
      <div className="space-y-3">
        <Field label="页面 URL"><Input value={form.url} onChange={(event) => setForm({ ...form, url: event.target.value })} placeholder="https://competitor.com/pricing" /></Field>
        <Field label="名称（可选）"><Input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="范围">
            <Select value={form.scope} onChange={(event) => setForm({ ...form, scope: event.target.value })} className="w-full">
              <option value="single_page">单页</option>
              <option value="domain">整站（自动发现子页）</option>
            </Select>
          </Field>
          <Field label="子页上限" hint={form.scope === "single_page" ? "单页模式不使用" : undefined}>
            <Input type="number" min={1} max={60} disabled={form.scope === "single_page"} value={form.crawl_limit} onChange={(event) => setForm({ ...form, crawl_limit: Number(event.target.value) })} />
          </Field>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="页面检查频率" hint="抓取可见文本并判断是否变化">
            <Select value={form.check_interval_minutes} onChange={(event) => setForm({ ...form, check_interval_minutes: Number(event.target.value) })} className="w-full">
              {INTERVAL_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </Select>
          </Field>
          <Field label="截图与归档频率" hint="同一次 Chromium 渲染生成 PNG 和自包含 HTML">
            <Select value={form.snapshot_interval_minutes} onChange={(event) => setForm({ ...form, snapshot_interval_minutes: Number(event.target.value) })} className="w-full">
              {INTERVAL_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </Select>
          </Field>
        </div>
        {isEditing ? (
          <Field label="运行状态">
            <Select value={form.status} onChange={(event) => setForm({ ...form, status: event.target.value })} className="w-full">
              <option value="active">运行中</option>
              <option value="paused">已暂停</option>
            </Select>
          </Field>
        ) : (
          <label className="flex items-center gap-2 text-[13px] cursor-pointer" style={{ color: "var(--body)" }}>
            <input type="checkbox" checked={form.capture_now} onChange={(event) => setForm({ ...form, capture_now: event.target.checked })} />
            创建后立即生成首张截图与网页归档
          </label>
        )}
        <div className="text-[12px] rounded-md px-3 py-2" style={{ color: "var(--mute)", background: "var(--bg-soft)" }}>
          每次完整快照都会先滚动页面触发懒加载，再保存 PNG 和离线 HTML。视频只保留封面；单个归档默认最多内嵌约 30 MB 资源。
        </div>
        <div className="flex justify-end gap-2 pt-1">
          <Button onClick={onClose}>取消</Button>
          <Button
            variant="primary"
            disabled={!form.url || pending}
            onClick={async () => {
              if (monitor) {
                await update.mutateAsync({ ...form, id: monitor.id, capture_now: undefined });
              } else {
                await create.mutateAsync({ ...form, brand_id: brandId });
              }
              setForm(EMPTY_FORM);
              onClose();
            }}
          >
            {pending ? "保存中…" : isEditing ? "保存设置" : form.capture_now ? "创建并生成快照" : "创建监控"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
