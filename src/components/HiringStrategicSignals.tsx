import { useState } from "react";
import { ChevronRight } from "lucide-react";
import { Badge, Button, EmptyState, Modal, Spinner } from "./ui";
import { useHiringStrategicSignals } from "../lib/hooks";
import { fmtDateTime } from "../lib/format";
import type { HiringStrategicSignal } from "../lib/api";

const DOMAIN_LABEL: Record<string, string> = {
  hiring: "招聘",
  voice: "用户之声",
  sales: "销售",
  marketing: "营销",
  web: "网页动态",
  market: "市场",
};

function domainLabel(value: string) {
  return DOMAIN_LABEL[value] || value;
}

export function HiringStrategicSignals({ brandId }: { brandId: string }) {
  const { data, isLoading, isError, error, refetch, refresh } = useHiringStrategicSignals(brandId);
  const [selectedId, setSelectedId] = useState<string>();

  if (isLoading) return <Spinner />;
  if (isError && !data) return <div className="px-4 sm:px-6 pb-5"><EmptyState title="战略推测暂时不可用" hint={error?.message} action={<Button onClick={() => refetch()}>重试</Button>} /></div>;
  if (!data) return null;

  const generating = refresh.isPending || (!isError && data.generation?.status === "running");
  const selected = data.signals.find((signal) => signal.id === selectedId);
  const issue = refresh.error?.message || error?.message || data.generation?.error;
  const covered = Object.keys(data.coverage).filter((key) => data.coverage[key]?.status === "ready");

  return (
    <div className="px-4 sm:px-6 pb-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5 text-[12px]" style={{ color: "var(--mute)" }}>
          <span>{data.window.start} 至 {data.window.end}</span>
          {data.previous_result && <span>上次研判{data.generated_at && " · " + fmtDateTime(data.generated_at)}</span>}
          {covered.map((key) => <Badge key={key}>{domainLabel(key)}</Badge>)}
        </div>
        <Button size="sm" onClick={() => refresh.mutate()} disabled={generating}>{generating ? "推测中…" : "刷新战略推测"}</Button>
      </div>
      {generating && <p role="status" className="mt-2 text-[12px]" style={{ color: "var(--mute)" }}>正在关联各板块的证据，完成后自动更新。</p>}
      {issue && <p role="alert" className="mt-2 text-[12px]" style={{ color: "var(--danger)" }}>{issue}</p>}
      {data.analysis_status === "fallback" && <p className="mt-2 text-[12px]" style={{ color: "var(--warning)" }}>{data.caveats.join(" · ")}</p>}
      {(data.analysis_status || data.cached) && <p className="my-3 text-[13px] leading-6" style={{ color: "var(--body)" }}>{data.headline.summary}</p>}
      <div className="grid gap-2 mt-3">
        {data.signals.map((signal) => (
          <button key={signal.id} type="button" onClick={() => setSelectedId(signal.id)}
            className="w-full rounded-xl border p-3 text-left cursor-pointer hover:bg-[var(--bg-soft-2)]"
            style={{ borderColor: "var(--hairline)" }}>
            <div className="flex items-start justify-between gap-2">
              <span className="text-[14px] font-medium" style={{ color: "var(--ink)" }}>{signal.title}</span>
              <ChevronRight size={16} className="shrink-0" style={{ color: "var(--mute)" }} />
            </div>
            <p className="mt-1 text-[13px] leading-5 line-clamp-2" style={{ color: "var(--body)" }}>{signal.inference || signal.observation}</p>
            <div className="flex flex-wrap items-center gap-1.5 mt-2 text-[12px]" style={{ color: "var(--mute)" }}>
              {signal.domains.map((domain) => <Badge key={domain}>{domainLabel(domain)}</Badge>)}
              <span className="ml-auto">{signal.evidence.length} 条证据</span>
            </div>
          </button>
        ))}
        {!data.signals.length && !generating && !issue && <p className="text-[13px]" style={{ color: "var(--mute)" }}>当前证据尚不足以推测战略动作。</p>}
      </div>
      {selected && <SignalDetail signal={selected} onClose={() => setSelectedId(undefined)} />}
    </div>
  );
}

function SignalDetail({ signal, onClose }: { signal: HiringStrategicSignal; onClose: () => void }) {
  const confidence = signal.confidence === "high" ? "较高" : signal.confidence === "medium" ? "中等" : "较低";
  return (
    <Modal title={signal.title} open onClose={onClose} width={680}>
      <div className="space-y-3 text-[13px] leading-5" style={{ color: "var(--body)" }}>
        <Badge>{confidence}置信度</Badge>
        <DetailRow label="观察到的事实" value={signal.observation} />
        <DetailRow label="可能的业务动作" value={signal.inference} />
        <DetailRow label="潜在影响" value={signal.business_impact} />
        <DetailRow label="其他解释" value={signal.alternative} />
        <DetailRow label="观察周期" value={signal.time_horizon} />
        <DetailRow label="后续验证线索" value={signal.next_signal} />
        {signal.evidence.length > 0 && (
          <div>
            <h4 className="font-medium" style={{ color: "var(--ink)" }}>原始证据</h4>
            <ul className="mt-1 space-y-1">
              {signal.evidence.map((item) => (
                <li key={item.id} className="wrap-anywhere">
                  <span className="mr-2 text-[12px]" style={{ color: "var(--mute)" }}>{[domainLabel(item.domain), item.at?.slice(0, 10)].filter(Boolean).join(" · ")}</span>
                  {item.url && /^https?:\/\//i.test(item.url)
                    ? <a href={item.url} target="_blank" rel="noreferrer" className="hover:underline" style={{ color: "var(--accent)" }}>{item.title || item.id}</a>
                    : item.title || item.id}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Modal>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  if (!value) return null;
  return <div><h4 className="font-medium" style={{ color: "var(--ink)" }}>{label}</h4><p className="mt-0.5">{value}</p></div>;
}
