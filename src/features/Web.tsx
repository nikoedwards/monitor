import { useState } from "react";
import { useParams } from "react-router-dom";
import { Badge, Button, Card, EmptyState, Field, Input, Modal, SectionTitle, Select, Spinner, StatCard } from "../components/ui";
import { useWebMonitors, useWebMutations, useWebSnapshots, useWebSummary } from "../lib/hooks";
import { fmtDate, fmtDateTime } from "../lib/format";

export default function Web() {
  const { brandId } = useParams();
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<string | undefined>();
  const { data: monitors = [], isLoading } = useWebMonitors(brandId);
  const { data: summary } = useWebSummary(brandId);
  const { data: snapshots = [] } = useWebSnapshots(brandId, selected);
  const { capture, remove } = useWebMutations();

  if (isLoading) return <Spinner />;

  return (
    <div className="space-y-6">
      <SectionTitle
        title="网页快照监控"
        subtitle="每日截图 + 可见文本变更分析，支持子页面发现与历史回溯"
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
                      <Badge tone={m.scope === "domain" ? "accent" : "neutral"}>{m.scope === "domain" ? "整站" : "单页"}</Badge>
                    </div>
                    <div className="text-[12px] truncate mt-0.5" style={{ color: "var(--mute)" }}>{m.url}</div>
                    <div className="text-[12px] mt-1" style={{ color: "var(--mute)" }}>{m.snapshot_count} 张快照 · {fmtDate(m.latest_snapshot_date)}</div>
                  </button>
                  <div className="flex gap-2 mt-2">
                    <Button size="sm" disabled={capture.isPending} onClick={() => capture.mutate(m.id)}>立即截图</Button>
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

      <CreateModal open={open} onClose={() => setOpen(false)} brandId={brandId!} />
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

function CreateModal({ open, onClose, brandId }: { open: boolean; onClose: () => void; brandId: string }) {
  const { create } = useWebMutations();
  const [form, setForm] = useState<any>({ url: "", scope: "single_page", crawl_limit: 20 });
  return (
    <Modal open={open} onClose={onClose} title="新增网页监控">
      <div className="space-y-3">
        <Field label="页面 URL"><Input value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} placeholder="https://competitor.com/pricing" /></Field>
        <Field label="名称(可选)"><Input value={form.name || ""} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="范围">
            <Select value={form.scope} onChange={(e) => setForm({ ...form, scope: e.target.value })} className="w-full">
              <option value="single_page">单页</option>
              <option value="domain">整站(自动发现子页)</option>
            </Select>
          </Field>
          <Field label="子页上限"><Input type="number" value={form.crawl_limit} onChange={(e) => setForm({ ...form, crawl_limit: Number(e.target.value) })} /></Field>
        </div>
        <div className="flex justify-end gap-2 pt-1">
          <Button onClick={onClose}>取消</Button>
          <Button
            variant="primary"
            disabled={!form.url || create.isPending}
            onClick={async () => {
              await create.mutateAsync({ ...form, brand_id: brandId, capture_now: true });
              setForm({ url: "", scope: "single_page", crawl_limit: 20 });
              onClose();
            }}
          >
            {create.isPending ? "截图中…" : "创建并截图"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
