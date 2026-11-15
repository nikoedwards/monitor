import { useState } from "react";
import { Badge, Button, Card, EmptyState, SectionTitle, Spinner } from "./ui";
import { useHiringStrategicSignals } from "../lib/hooks";
import type { HiringStrategicSignal, HiringStrategicSignals as HiringStrategicSignalsData } from "../lib/api";

const DOMAIN_LABEL: Record<string, string> = {
  hiring: "招聘",
  voice: "用户之声",
  sales: "销售",
  marketing: "营销",
  web: "网页动态",
  market: "市场",
};

const POSTURE_LABEL: Record<string, string> = {
  expansion: "扩张",
  pressure: "承压",
  pivot: "转向",
  watch: "观察",
};

function tone(value?: string): "positive" | "warning" | "neutral" | "accent" {
  if (value === "expansion" || value === "positive") return "positive";
  if (value === "pressure" || value === "negative") return "warning";
  if (value === "pivot" || value === "mixed") return "accent";
  return "neutral";
}

function domainLabel(value: string) {
  return DOMAIN_LABEL[value] || value;
}

export function HiringStrategicSignals({ brandId }: { brandId?: string }) {
  const { data, isLoading, isError, refresh } = useHiringStrategicSignals(brandId);
  const [selected, setSelected] = useState<HiringStrategicSignal>();

  if (isLoading) return <Card><Spinner /></Card>;
  if (isError) return <EmptyState title="组织推演台暂时不可用" hint="跨板块证据正在准备中，可稍后重试。" />;
  if (!data) return <EmptyState title="暂无组织推演" hint="完成招聘采集后，系统会结合全量历史和其他业务信号形成研判。" />;

  return (
    <Card className="overflow-hidden p-0">
      <div className="px-5 pt-5 pb-4" style={{ background: "linear-gradient(120deg, var(--panel), var(--bg-soft-2))" }}>
        <SectionTitle
          title="组织推演台"
          subtitle="基于全量历史的跨板块组织与战略研判"
          action={<div className="flex items-center gap-2"><Badge tone={tone(data.headline.posture)}>{POSTURE_LABEL[data.headline.posture] || "观察"}</Badge><Badge tone="neutral">全量历史</Badge><Button size="sm" onClick={() => refresh.mutate()} disabled={refresh.isPending}>{refresh.isPending ? "研判中…" : "刷新"}</Button></div>}
        />
        <div className="mt-4 flex flex-wrap items-end justify-between gap-4">
          <div className="max-w-3xl">
            <div className="text-[20px] leading-7 font-semibold" style={{ color: "var(--ink)" }}>{data.headline.summary}</div>
            <div className="mt-2 text-[12px]" style={{ color: "var(--mute)" }}>{data.window.start} 至 {data.window.end} · 置信度 {data.headline.confidence === "high" ? "较高" : data.headline.confidence === "medium" ? "中等" : "较低"}</div>
          </div>
          <RadarOverview data={data} />
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1.05fr_.95fr] gap-4 p-4">
        <div className="rounded-xl p-3" style={{ background: "var(--bg-soft-2)", border: "1px solid var(--hairline)" }}>
          <div className="flex items-center justify-between px-1 mb-2"><div className="text-[13px] font-medium" style={{ color: "var(--ink)" }}>战略雷达</div><div className="text-[11px]" style={{ color: "var(--mute)" }}>证据连接才显示关联</div></div>
          <SignalRadar data={data} />
        </div>
        <div className="space-y-2">
          <div className="text-[13px] font-medium px-1" style={{ color: "var(--ink)" }}>最值得关注的信号</div>
          {data.signals.length ? data.signals.map((signal) => (
            <button key={signal.id} className="w-full text-left rounded-xl p-3 transition-colors" style={{ background: "var(--bg-soft-2)", border: "1px solid var(--hairline)" }} onClick={() => setSelected(signal)}>
              <div className="flex items-start justify-between gap-2"><div className="text-[13px] font-medium" style={{ color: "var(--ink)" }}>{signal.title}</div><Badge tone={tone(signal.direction)}>{signal.priority === "high" ? "高优先级" : signal.priority === "medium" ? "中优先级" : "观察"}</Badge></div>
              <div className="mt-1 text-[12px] leading-5 line-clamp-2" style={{ color: "var(--body)" }}>{signal.observation || signal.inference}</div>
              <div className="flex flex-wrap gap-1 mt-2">{signal.domains.map((domain) => <span key={domain} className="px-1.5 py-0.5 rounded text-[10px]" style={{ background: "var(--panel)", color: "var(--mute)" }}>{domainLabel(domain)}</span>)}<span className="text-[10px] ml-auto" style={{ color: "var(--mute)" }}>{signal.evidence_ids.length} 条证据 · 详情</span></div>
            </button>
          )) : <div className="rounded-xl p-4 text-[12px]" style={{ background: "var(--bg-soft-2)", color: "var(--mute)" }}>当前只能形成单板块观察，尚不足以形成战略研判。</div>}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-[1fr_auto] gap-3 px-5 pb-5">
        <div className="rounded-lg px-3 py-2.5" style={{ background: "var(--bg-soft-2)", border: "1px solid var(--hairline)" }}>
          <div className="text-[11px] uppercase tracking-wide" style={{ color: "var(--mute)" }}>关键时间线</div>
          <div className="flex flex-wrap gap-2 mt-2">{data.timeline.slice(0, 4).map((item) => <span key={item.date} className="px-2 py-1 rounded-md text-[11px]" style={{ background: "var(--panel)", color: "var(--body)" }}>{item.date} · {item.events.length} 条变化</span>)}{!data.timeline.length && <span className="text-[11px]" style={{ color: "var(--mute)" }}>暂无足够时间线数据</span>}</div>
        </div>
        <div className="rounded-lg px-3 py-2.5 md:min-w-[250px]" style={{ background: "var(--bg-soft-2)", border: "1px solid var(--hairline)" }}>
          <div className="text-[11px] uppercase tracking-wide" style={{ color: "var(--mute)" }}>二级详情</div>
          <div className="text-[11px] mt-1.5 leading-4" style={{ color: "var(--body)" }}>人数区间、部门/地区推断、证据链和模型方法在信号详情中展开。</div>
        </div>
      </div>
      {selected && <SignalDetail signal={selected} onClose={() => setSelected(undefined)} />}
    </Card>
  );
}

function RadarOverview({ data }: { data: HiringStrategicSignalsData }) {
  const domains = new Set(data.signals.flatMap((signal) => signal.domains));
  const coverage = ["hiring", "voice", "sales", "marketing", "web"].filter((key) => data.coverage[key]?.status === "ready" || domains.has(key));
  return <div className="flex flex-wrap gap-1.5 max-w-[250px] justify-end">{coverage.map((key) => <span key={key} className="px-2 py-1 rounded-md text-[11px]" style={{ background: "var(--panel)", color: "var(--body)" }}>{domainLabel(key)}</span>)}</div>;
}

function SignalRadar({ data }: { data: HiringStrategicSignalsData }) {
  const points = [
    { key: "hiring", x: 270, y: 32 }, { key: "voice", x: 450, y: 120 }, { key: "sales", x: 400, y: 275 },
    { key: "marketing", x: 140, y: 275 }, { key: "web", x: 90, y: 120 },
  ];
  const active = new Set(data.signals.flatMap((signal) => signal.domains));
  return <svg viewBox="0 0 540 330" className="w-full h-[255px] sm:h-[285px]" role="img" aria-label="跨板块战略雷达"><defs><radialGradient id="strategic-core" cx="35%" cy="25%"><stop offset="0%" stopColor="var(--accent)" stopOpacity="0.95" /><stop offset="100%" stopColor="var(--violet)" stopOpacity="0.88" /></radialGradient></defs><circle cx="270" cy="165" r="120" fill="none" stroke="var(--hairline-strong)" strokeDasharray="4 6" /><circle cx="270" cy="165" r="72" fill="none" stroke="var(--hairline)" /><line x1="270" y1="45" x2="270" y2="285" stroke="var(--hairline)" /><line x1="95" y1="105" x2="445" y2="225" stroke="var(--hairline)" /><line x1="445" y1="105" x2="95" y2="225" stroke="var(--hairline)" />{points.map((point) => { const isActive = active.has(point.key); return <g key={point.key}><line x1="270" y1="165" x2={point.x} y2={point.y} stroke={isActive ? "var(--accent)" : "var(--hairline)"} strokeWidth={isActive ? 2 : 1} strokeDasharray={isActive ? undefined : "3 5"} opacity={isActive ? 0.75 : 0.8} /><circle cx={point.x} cy={point.y} r={isActive ? 18 : 13} fill="var(--panel)" stroke={isActive ? "var(--accent)" : "var(--hairline-strong)"} strokeWidth="2" /><text x={point.x} y={point.y + 4} textAnchor="middle" fontSize="11" fill={isActive ? "var(--ink)" : "var(--mute)"}>{domainLabel(point.key)}</text></g>; })}<circle cx="270" cy="165" r="48" fill="url(#strategic-core)" /><text x="270" y="160" textAnchor="middle" fontSize="15" fontWeight="700" fill="white">公司</text><text x="270" y="180" textAnchor="middle" fontSize="11" fill="rgba(255,255,255,.82)">{POSTURE_LABEL[data.headline.posture] || "观察"}</text></svg>;
}

function SignalDetail({ signal, onClose }: { signal: HiringStrategicSignal; onClose: () => void }) {
  return <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-4" style={{ background: "rgba(0,0,0,.42)" }} onClick={onClose}><div className="w-full max-w-2xl max-h-[85vh] overflow-y-auto rounded-2xl p-5" style={{ background: "var(--panel)", border: "1px solid var(--hairline-strong)" }} onClick={(event) => event.stopPropagation()}><div className="flex items-start justify-between gap-3"><div><div className="text-[18px] font-semibold" style={{ color: "var(--ink)" }}>{signal.title}</div><div className="mt-1 text-[12px]" style={{ color: "var(--mute)" }}>{signal.domains.map(domainLabel).join(" · ")} · {signal.confidence === "medium" ? "中等置信度" : "较低置信度"}</div></div><Button size="sm" onClick={onClose}>关闭</Button></div><div className="grid gap-3 mt-5 text-[13px] leading-5" style={{ color: "var(--body)" }}><DetailRow label="观察到的事实" value={signal.observation} /><DetailRow label="可能意味着" value={signal.inference} /><DetailRow label="业务影响" value={signal.business_impact} /><DetailRow label="其它解释" value={signal.alternative} /><DetailRow label="下一步核查" value={signal.next_signal} /><div><div className="text-[11px]" style={{ color: "var(--mute)" }}>证据链</div><div className="flex flex-wrap gap-1.5 mt-1">{signal.evidence.map((evidence) => <span key={evidence.id} className="px-2 py-1 rounded-md text-[11px]" style={{ background: "var(--bg-soft-2)", color: "var(--body)" }}>{evidence.id} · {evidence.title}</span>)}</div></div></div></div></div>;
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return <div><div className="text-[11px]" style={{ color: "var(--mute)" }}>{label}</div><div className="mt-0.5">{value || "暂无"}</div></div>;
}
