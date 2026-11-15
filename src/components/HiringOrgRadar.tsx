import { useState } from "react";
import { Badge, Button, Card, EmptyState, SectionTitle } from "./ui";
import type { HiringOrgEstimate, HiringOrgRadar as HiringOrgRadarData } from "../lib/api";
import { useHiringOrgEstimate, useSettings } from "../lib/hooks";
import type { TimeRange } from "../lib/timeRange";

const CONFIDENCE_LABEL: Record<string, string> = { high: "较高", medium: "中等", low: "较低" };
const NODE_POSITIONS = [
  { x: 82, y: 58 }, { x: 270, y: 38 }, { x: 458, y: 58 },
  { x: 82, y: 274 }, { x: 270, y: 300 }, { x: 458, y: 274 },
];

function confidenceTone(value: string): "positive" | "warning" | "neutral" {
  if (value === "high") return "positive";
  if (value === "medium") return "warning";
  return "neutral";
}

function shortLabel(value: string, length = 8) {
  return value.length > length ? `${value.slice(0, length)}…` : value;
}

function directionLabel(netChange: number) {
  if (netChange > 0) return { label: "扩张", color: "var(--accent)" };
  if (netChange < 0) return { label: "收缩", color: "var(--warning)" };
  return { label: "观察", color: "var(--mute)" };
}

export function HiringOrgRadar({ data, brandId, platform, range }: { data?: HiringOrgRadarData; brandId?: string; platform?: string; range?: TimeRange }) {
  const settings = useSettings();
  const estimateMutation = useHiringOrgEstimate();
  const [estimate, setEstimate] = useState<HiringOrgEstimate>();
  const [estimateError, setEstimateError] = useState("");

  if (!data) return <EmptyState title="暂无组织推演" hint="完成一次招聘采集后，这里会把岗位蛛丝马迹整理成组织结构推断。" />;

  const departments = data.departments.filter((item) => item.department && item.department !== "未分类").slice(0, 6);
  const locations = estimate?.locations?.length ? estimate.locations : data.locations.filter((item) => item.city !== "未标注地区").slice(0, 3);
  const primaryDepartment = departments[0];
  const primaryLocation = data.locations.find((item) => item.city !== "未标注地区");
  const visibleEvidence = (estimate?.departments?.flatMap((item) => item.evidence_ids) || primaryDepartment?.evidence.map((item) => item.id) || []).slice(0, 4);

  const runEstimate = async () => {
    if (!brandId) return;
    setEstimateError("");
    try {
      setEstimate(await estimateMutation.mutateAsync({ brandId, platform, range, refresh: !!estimate }));
    } catch (error: any) {
      setEstimateError(error?.message || "人数推演失败");
    }
  };

  return (
    <Card className="overflow-hidden p-0">
      <div className="px-5 pt-5 pb-3" style={{ background: "linear-gradient(120deg, var(--panel), var(--bg-soft-2))" }}>
        <SectionTitle
          title="组织推演台"
          subtitle={`${data.window.start} 至 ${data.window.end} · 从公开招聘痕迹推断团队结构`}
          action={<Badge tone={confidenceTone(estimate?.overall.confidence || data.overview.confidence)}>置信度：{CONFIDENCE_LABEL[estimate?.overall.confidence || data.overview.confidence] || "待推演"}</Badge>}
        />
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-3 text-[12px]" style={{ color: "var(--body)" }}>
          <span><strong style={{ color: "var(--ink)" }}>{primaryDepartment?.department || "—"}</strong> 是当前最明显的组织信号</span>
          <span>招聘落点：<strong style={{ color: "var(--ink)" }}>{primaryLocation?.city || "待发现"}</strong></span>
          <span className="inline-flex items-center gap-1" style={{ color: "var(--mute)" }}><i className="w-1.5 h-1.5 rounded-full" style={{ background: "var(--accent)" }} />节点大小代表组织信号强度</span>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1.25fr_.75fr] gap-4 p-4">
        <div className="rounded-xl p-3" style={{ background: "var(--bg-soft-2)", border: "1px solid var(--hairline)" }}>
          <div className="flex items-center justify-between mb-1 px-1"><div className="text-[13px] font-medium" style={{ color: "var(--ink)" }}>组织结构地图</div><div className="text-[11px]" style={{ color: "var(--mute)" }}>部门 · 岗位族 · 方向</div></div>
          <OrganizationMap departments={departments} estimate={estimate} />
        </div>

        <div className="rounded-xl p-4 flex flex-col" style={{ background: "var(--bg-soft-2)", border: "1px solid var(--hairline)" }}>
          <div className="flex items-start justify-between gap-3"><div><div className="text-[13px] font-medium" style={{ color: "var(--ink)" }}>人数推演</div><div className="text-[11px] mt-1" style={{ color: "var(--mute)" }}>区间比单点更诚实，证据可以回溯</div></div><Button size="sm" variant="primary" onClick={runEstimate} disabled={!settings.data?.configured || estimateMutation.isPending} title={!settings.data?.configured ? "请先配置大模型 Token" : ""}>{estimateMutation.isPending ? "推演中…" : estimate ? "重新推演" : "开始推演"}</Button></div>
          {estimate ? <EstimateSummary estimate={estimate} locations={locations} /> : <div className="flex-1 flex flex-col justify-center py-8"><div className="text-[42px] leading-none font-semibold tracking-tight" style={{ color: "var(--ink)" }}>—</div><div className="mt-2 text-[13px]" style={{ color: "var(--body)" }}>还没有把招聘信号换算成人数区间</div><div className="mt-2 text-[11px] leading-5" style={{ color: "var(--mute)" }}>模型会结合岗位节奏、可见员工样本、城市分布和品牌公开资料，输出部门与地区的范围估计。</div>{!settings.data?.configured && <div className="mt-3 text-[11px]" style={{ color: "var(--warning)" }}>请先配置大模型 Token</div>}</div>}
          {estimateError && <div className="mt-2 text-[11px]" style={{ color: "var(--danger)" }}>{estimateError}</div>}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-[1fr_auto] gap-3 px-5 pb-5"><div className="rounded-lg px-3 py-2.5" style={{ background: "var(--bg-soft-2)", border: "1px solid var(--hairline)" }}><div className="text-[11px] uppercase tracking-wide" style={{ color: "var(--mute)" }}>证据链</div><div className="flex flex-wrap gap-1.5 mt-1.5">{visibleEvidence.length ? visibleEvidence.map((id) => <span key={id} className="px-2 py-0.5 rounded-full text-[11px]" style={{ background: "var(--panel)", color: "var(--body)" }}>职位 {id.slice(0, 8)}</span>) : <span className="text-[11px]" style={{ color: "var(--mute)" }}>等待可回溯职位证据</span>}</div></div><div className="rounded-lg px-3 py-2.5 md:min-w-[270px]" style={{ background: "var(--bg-soft-2)", border: "1px solid var(--hairline)" }}><div className="text-[11px] uppercase tracking-wide" style={{ color: "var(--mute)" }}>解读边界</div><div className="text-[11px] mt-1.5 leading-4" style={{ color: "var(--body)" }}>招聘需求不是实际 headcount；员工数据是公开可见样本。</div></div></div>
    </Card>
  );
}

function OrganizationMap({ departments, estimate }: { departments: HiringOrgRadarData["departments"]; estimate?: HiringOrgEstimate }) {
  return <svg viewBox="0 0 540 350" className="w-full h-[300px] sm:h-[330px]" role="img" aria-label="组织结构地图"><defs><radialGradient id="org-core" cx="35%" cy="25%"><stop offset="0%" stopColor="var(--accent)" stopOpacity="0.95" /><stop offset="100%" stopColor="var(--violet)" stopOpacity="0.88" /></radialGradient><filter id="org-glow"><feGaussianBlur stdDeviation="5" result="blur" /><feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge></filter></defs>{departments.map((department, index) => { const position = NODE_POSITIONS[index]; const estimateItem = estimate?.departments.find((item) => item.department === department.department); const signal = directionLabel(department.net_change); const radius = estimateItem ? Math.max(18, Math.min(32, 15 + estimateItem.high / 3)) : Math.max(18, Math.min(30, 17 + department.open_roles / 2)); return <g key={department.department}><line x1="270" y1="174" x2={position.x} y2={position.y} stroke="var(--hairline-strong)" strokeWidth="1.5" strokeDasharray="4 5" /><circle cx={position.x} cy={position.y} r={radius + 7} fill={signal.color} opacity="0.08" /><circle cx={position.x} cy={position.y} r={radius} fill="var(--panel)" stroke={signal.color} strokeWidth="2" /><text x={position.x} y={position.y - 3} textAnchor="middle" fontSize="12" fontWeight="600" fill="var(--ink)">{shortLabel(department.department || "未分类")}</text><text x={position.x} y={position.y + 14} textAnchor="middle" fontSize="10" fill={signal.color}>{estimateItem ? `${estimateItem.low}–${estimateItem.high} 人` : signal.label}</text></g>; })}<circle cx="270" cy="174" r="54" fill="url(#org-core)" filter="url(#org-glow)" /><text x="270" y="169" textAnchor="middle" fontSize="16" fontWeight="700" fill="white">组织</text><text x="270" y="188" textAnchor="middle" fontSize="11" fill="rgba(255,255,255,.82)">{estimate ? "模型推演中" : "招聘信号"}</text></svg>;
}

function EstimateSummary({ estimate, locations }: { estimate: HiringOrgEstimate; locations: HiringOrgEstimate["locations"] | HiringOrgRadarData["locations"] }) {
  return <div className="flex-1 flex flex-col pt-5"><div className="text-[40px] leading-none font-semibold tracking-tight" style={{ color: "var(--ink)" }}>{estimate.overall.low}–{estimate.overall.high}<span className="text-[14px] ml-1 font-normal" style={{ color: "var(--mute)" }}>人</span></div><div className="flex items-center gap-2 mt-2"><Badge tone={confidenceTone(estimate.overall.confidence)}>置信度：{CONFIDENCE_LABEL[estimate.overall.confidence] || estimate.overall.confidence}</Badge><span className="text-[11px]" style={{ color: "var(--mute)" }}>{estimate.cached ? "已使用缓存" : "刚刚生成"}</span></div><p className="text-[12px] leading-5 mt-4" style={{ color: "var(--body)" }}>{estimate.summary || "模型未提供额外摘要。"}</p><div className="mt-auto pt-4"><div className="text-[11px]" style={{ color: "var(--mute)" }}>地区落点</div><div className="flex flex-wrap gap-1.5 mt-1.5">{locations.slice(0, 3).map((item: any) => <span key={item.city} className="px-2 py-1 rounded-md text-[11px]" style={{ background: "var(--panel)", color: "var(--body)" }}>{item.city}{item.low != null ? ` · ${item.low}–${item.high}人` : ""}</span>)}</div></div></div>;
}
