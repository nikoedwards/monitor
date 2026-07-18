import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Badge, Button, Card, EmptyState, Field, Input, Modal, SectionTitle, Select, Spinner, StatCard } from "../components/ui";
import { useWebMonitors, useWebMutations, useWebSnapshots, useWebSummary } from "../lib/hooks";
import { fmtDate, fmtDateTime } from "../lib/format";
import type { WebMonitor } from "../lib/api";

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

export default function Web() {
  const { brandId } = useParams();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<WebMonitor | null>(null);
  const [selected, setSelected] = useState<string | undefined>();
  const [now, setNow] = useState(() => Date.now());
  const { data: monitors = [], isLoading } = useWebMonitors(brandId);
  const { data: summary } = useWebSummary(brandId);
  const { data: snapshots = [] } = useWebSnapshots(brandId, selected);
  const { capture, update, remove } = useWebMutations();

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  if (isLoading) return <Spinner />;

  return (
    <div className="space-y-6">
      <SectionTitle
        title="网页快照监控"
        subtitle="按任务频率检查页面并生成截图，支持子页面发现与历史回溯"
        action={<Button variant="primary" onClick={() => setOpen(true)}>+ 新增监控</Button>}
      />

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label="监控页面" value={monitors.length} />
        <StatCard label="快照总数" value={summary?.total_snapshots ?? 0} />
        <StatCard label="检测到变更" value={summary?.changed ?? 0} tone="negative" />
        <StatCard label="活跃监控" value={monitors.filter((m) => m.status === "active").length} tone="accent" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card>
          <SectionTitle title="监控列表" />
          {monitors.length ? (
            <div className="space-y-2">
              <button
                onClick={() => setSelected(undefined)}
                className="w-full text-left px-3 py-2 rounded-md text-[13px] cursor-pointer"
                style={{ background: !selected ? "var(--bg-soft-2)" : "transparent", color: "var(--ink)" }}
              >
                全部页面
              </button>
              {monitors.map((m) => (
                <div key={m.id} className="p-3 rounded-md" style={{ background: selected === m.id ? "var(--bg-soft-2)" : "var(--bg-soft)" }}>
                  <button onClick={() => setSelected(m.id)} className="w-full text-left cursor-pointer">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[13px] font-medium truncate" style={{ color: "var(--ink)" }}>{m.name}</span>
                      <div className="flex gap-1">
                        <Badge tone={m.status === "active" ? "positive" : "neutral"}>{m.status === "active" ? "运行中" : "已暂停"}</Badge>
                        <Badge tone={m.scope === "domain" ? "accent" : "neutral"}>{m.scope === "domain" ? "整站" : "单页"}</Badge>
                      </div>
                    </div>
                    <div className="text-[12px] truncate mt-0.5" style={{ color: "var(--mute)" }}>{m.url}</div>
                    <div className="text-[12px] mt-1" style={{ color: "var(--mute)" }}>{m.snapshot_count} 张快照 · {fmtDate(m.latest_snapshot_date)}</div>
                    <div className="text-[12px] mt-1" style={{ color: "var(--body)" }}>
                      {schedulesMatch(m)
                        ? `${intervalLabel(m.check_interval_minutes)}检查并截图`
                        : `${intervalLabel(m.check_interval_minutes)}检查 · ${intervalLabel(m.snapshot_interval_minutes)}截图`}
                    </div>
                    <div className="mt-2 space-y-0.5 text-[12px]" style={{ color: "var(--mute)" }}>
                      {schedulesMatch(m) ? (
                        <div>下次检查并截图：{m.next_snapshot_at ? fmtDateTime(m.next_snapshot_at) : "—"} · {countdownLabel(m.next_snapshot_at, m.status, now)}</div>
                      ) : (
                        <>
                          <div>下次检查：{m.next_check_at ? fmtDateTime(m.next_check_at) : "—"} · {countdownLabel(m.next_check_at, m.status, now)}</div>
                          <div>下次截图：{m.next_snapshot_at ? fmtDateTime(m.next_snapshot_at) : "—"} · {countdownLabel(m.next_snapshot_at, m.status, now)}</div>
                        </>
                      )}
                    </div>
                  </button>
                  <div className="flex flex-wrap gap-2 mt-3">
                    <Button size="sm" disabled={capture.isPending} onClick={() => capture.mutate(m.id)}>立即截图</Button>
                    <Button size="sm" onClick={() => setEditing(m)}>编辑</Button>
                    <Button
                      size="sm"
                      disabled={update.isPending}
                      onClick={() => update.mutate({ id: m.id, status: m.status === "active" ? "paused" : "active" })}
                    >
                      {m.status === "active" ? "暂停" : "启用"}
                    </Button>
                    <Button size="sm" variant="danger" onClick={() => remove.mutate(m.id)}>删除</Button>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState title="暂无监控" hint="添加竞品官网、落地页或定价页开始监控。" />
          )}
        </Card>

        <Card className="lg:col-span-2">
          <SectionTitle title="快照时间线" subtitle="按变更程度高亮，点击查看截图" />
          <SnapshotTimeline snapshots={snapshots} />
        </Card>
      </div>

      <MonitorModal open={open} onClose={() => setOpen(false)} brandId={brandId!} />
      <MonitorModal open={!!editing} onClose={() => setEditing(null)} brandId={brandId!} monitor={editing || undefined} />
    </div>
  );
}

function SnapshotTimeline({ snapshots }: { snapshots: any[] }) {
  const [active, setActive] = useState<any | null>(null);
  if (!snapshots.length) return <EmptyState title="暂无快照" hint="对监控页面点击「立即截图」生成首张基线快照。" />;
  return (
    <>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        {snapshots.map((s) => {
          const changed = (s.change_score || 0) >= 0.15;
          return (
            <button key={s.id} onClick={() => setActive(s)} className="text-left panel overflow-hidden cursor-pointer" style={{ padding: 0 }}>
              <div className="aspect-[4/3] overflow-hidden" style={{ background: "var(--bg-soft-2)" }}>
                {s.screenshot_url && <img src={s.screenshot_url} alt={s.title} className="w-full h-full object-cover object-top" />}
              </div>
              <div className="p-2.5">
                <div className="flex items-center justify-between">
                  <span className="text-[12px]" style={{ color: "var(--mute)" }}>{fmtDate(s.snapshot_date)}</span>
                  {changed ? <Badge tone="negative">变更 {(s.change_score * 100).toFixed(0)}%</Badge> : <Badge tone="neutral">无变化</Badge>}
                </div>
                <div className="text-[12px] truncate mt-1" style={{ color: "var(--body)" }}>{s.page_path}</div>
              </div>
            </button>
          );
        })}
      </div>
      <Modal open={!!active} onClose={() => setActive(null)} title={active?.title || "快照"} width={820}>
        {active && (
          <div className="space-y-3">
            <div className="text-[13px]" style={{ color: "var(--body)" }}>{active.summary}</div>
            {active.changes?.length > 0 && (
              <div className="space-y-1">
                {active.changes.map((c: any, i: number) => (
                  <div key={i} className="text-[12px] flex gap-2" style={{ color: c.type === "removed" ? "var(--danger)" : "var(--body)" }}>
                    <Badge tone={c.type === "added" ? "positive" : c.type === "removed" ? "negative" : "neutral"}>{c.type}</Badge>
                    <span className="truncate">{c.text || `${c.from} → ${c.to}`}</span>
                  </div>
                ))}
              </div>
            )}
            <img src={active.screenshot_url} alt={active.title} className="w-full rounded-md" style={{ border: "1px solid var(--hairline)" }} />
            <div className="text-[12px]" style={{ color: "var(--mute)" }}>{fmtDateTime(active.created_at)} · <a href={active.final_url || active.url} target="_blank" rel="noreferrer" className="hover:underline">{active.final_url || active.url}</a></div>
          </div>
        )}
      </Modal>
    </>
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
        <Field label="页面 URL"><Input value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} placeholder="https://competitor.com/pricing" /></Field>
        <Field label="名称(可选)"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="范围">
            <Select value={form.scope} onChange={(e) => setForm({ ...form, scope: e.target.value })} className="w-full">
              <option value="single_page">单页</option>
              <option value="domain">整站(自动发现子页)</option>
            </Select>
          </Field>
          <Field label="子页上限" hint={form.scope === "single_page" ? "单页模式不使用" : undefined}>
            <Input type="number" min={1} max={60} disabled={form.scope === "single_page"} value={form.crawl_limit} onChange={(e) => setForm({ ...form, crawl_limit: Number(e.target.value) })} />
          </Field>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="页面检查频率" hint="抓取可见文本并判断是否变化">
            <Select value={form.check_interval_minutes} onChange={(e) => setForm({ ...form, check_interval_minutes: Number(e.target.value) })} className="w-full">
              {INTERVAL_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </Select>
          </Field>
          <Field label="完整截图频率" hint="使用 Chromium 生成网页 PNG">
            <Select value={form.snapshot_interval_minutes} onChange={(e) => setForm({ ...form, snapshot_interval_minutes: Number(e.target.value) })} className="w-full">
              {INTERVAL_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </Select>
          </Field>
        </div>
        {isEditing ? (
          <Field label="运行状态">
            <Select value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })} className="w-full">
              <option value="active">运行中</option>
              <option value="paused">已暂停</option>
            </Select>
          </Field>
        ) : (
          <label className="flex items-center gap-2 text-[13px] cursor-pointer" style={{ color: "var(--body)" }}>
            <input type="checkbox" checked={form.capture_now} onChange={(e) => setForm({ ...form, capture_now: e.target.checked })} />
            创建后立即生成首张快照
          </label>
        )}
        <div className="text-[12px] rounded-md px-3 py-2" style={{ color: "var(--mute)", background: "var(--bg-soft)" }}>
          自动任务由后台调度器执行，页面会显示预计执行时间和实时倒计时；实际开始时间可能有少量调度延迟。
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
            {pending ? "保存中…" : isEditing ? "保存设置" : form.capture_now ? "创建并截图" : "创建监控"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
