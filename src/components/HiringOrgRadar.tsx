import { Bars, TrendChart } from "./charts";
import { Badge, Button, Card, EmptyState, SectionTitle, StatCard } from "./ui";
import type { HiringOrgEstimate, HiringOrgRadar as HiringOrgRadarData } from "../lib/api";
import { fmtNum } from "../lib/format";
import { useHiringOrgEstimate, useSettings } from "../lib/hooks";
import type { TimeRange } from "../lib/timeRange";
import { useState } from "react";

const CONFIDENCE_LABEL: Record<string, string> = { high: "较高", medium: "中等", low: "较低" };

function confidenceTone(value: string): "positive" | "warning" | "neutral" {
  if (value === "high") return "positive";
  if (value === "medium") return "warning";
  return "neutral";
}

export function HiringOrgRadar({ data, brandId, platform, range }: { data?: HiringOrgRadarData; brandId?: string; platform?: string; range?: TimeRange }) {
  const settings = useSettings();
  const estimateMutation = useHiringOrgEstimate();
  const [estimate, setEstimate] = useState<HiringOrgEstimate>();
  const [estimateError, setEstimateError] = useState("");
  if (!data) return <EmptyState title="暂无组织推断" hint="配置招聘采集源并完成至少一次同步后展示招聘组织信号。" />;
  const departments = data.departments.filter((item) => item.department !== "未分类").slice(0, 10);
  const locations = data.locations.filter((item) => item.city !== "未标注地区").slice(0, 8);
  const roles = data.role_families.slice(0, 12);
  const runEstimate = async () => {
    if (!brandId) return;
    setEstimateError("");
    try {
      setEstimate(await estimateMutation.mutateAsync({ brandId, platform, range, refresh: !!estimate }));
    } catch (error: any) {
      setEstimateError(error?.message || "人数估算失败");
    }
  };

  return (
    <Card>
      <SectionTitle
        title="招聘组织雷达"
        subtitle={`${data.window.start} 至 ${data.window.end} · 用岗位与可见员工样本推断组织结构`}
        action={<Badge tone={confidenceTone(data.overview.confidence)}>整体置信度：{CONFIDENCE_LABEL[data.overview.confidence] || data.overview.confidence}</Badge>}
      />
      <div className="text-[12px] mb-4" style={{ color: "var(--mute)" }}>
        招聘岗位代表招聘需求，不等于真实员工数；可见员工是 LinkedIn 企业员工样本下界。
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <StatCard label="当前在招需求" value={fmtNum(data.overview.open_roles)} tone="accent" />
        <StatCard label="可见员工样本" value={fmtNum(data.overview.known_people)} hint="不代表完整 headcount" />
        <StatCard label="招聘中心" value={fmtNum(data.overview.city_count)} />
        <StatCard label="部门信号" value={fmtNum(data.overview.department_count)} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="panel p-3">
          <div className="flex items-center justify-between mb-2">
            <div className="text-[13px] font-medium" style={{ color: "var(--ink)" }}>部门招聘规模</div>
            <span className="text-[11px]" style={{ color: "var(--mute)" }}>在招岗位</span>
          </div>
          {departments.length ? <Bars data={departments} dataKey="open_roles" nameKey="department" name="在招需求" color="var(--accent)" /> : <p className="text-[12px]" style={{ color: "var(--mute)" }}>暂无可归类部门信号</p>}
        </div>
        <div className="panel p-3">
          <div className="flex items-center justify-between mb-2">
            <div className="text-[13px] font-medium" style={{ color: "var(--ink)" }}>地区招聘重心</div>
            <span className="text-[11px]" style={{ color: "var(--mute)" }}>在招岗位</span>
          </div>
          {locations.length ? <Bars data={locations} dataKey="open_roles" nameKey="city" name="在招需求" color="var(--violet)" /> : <p className="text-[12px]" style={{ color: "var(--mute)" }}>暂无城市信号</p>}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4 mt-4">
        <div className="panel p-3 lg:col-span-3">
          <div className="text-[13px] font-medium mb-2" style={{ color: "var(--ink)" }}>组织扩张与收缩</div>
          <TrendChart data={data.trend} interactive keys={[
            { key: "released", name: "新增岗位", color: "var(--accent)" },
            { key: "active", name: "在招岗位", color: "var(--violet)" },
            { key: "closed", name: "下线岗位", color: "var(--warning)" },
          ]} />
        </div>
        <div className="panel p-3 lg:col-span-2">
          <div className="text-[13px] font-medium mb-2" style={{ color: "var(--ink)" }}>岗位分工线索</div>
          {roles.length ? (
            <div className="space-y-2">
              {roles.map((role) => (
                <div key={role.role_family} className="flex items-center justify-between gap-3 text-[12px]">
                  <span className="truncate" style={{ color: "var(--body)" }}>{role.role_family}</span>
                  <span className="shrink-0 tabular-nums" style={{ color: "var(--ink)" }}>{role.open_roles} 个在招</span>
                </div>
              ))}
            </div>
          ) : <p className="text-[12px]" style={{ color: "var(--mute)" }}>暂无岗位族信号</p>}
        </div>
      </div>

      <div className="mt-4 pt-3 text-[11px]" style={{ borderTop: "1px solid var(--hairline)", color: "var(--mute)" }}>
        数据质量：{data.data_quality.postings_total} 条岗位，{data.data_quality.with_city} 条带城市，{data.data_quality.people_total} 个可见员工样本。{data.data_quality.limitations.join(" ")}
      </div>

      <div className="mt-4 pt-4" style={{ borderTop: "1px solid var(--hairline)" }}>
        <SectionTitle
          title="模型辅助人数区间"
          subtitle="综合招聘节奏、可见员工样本与公司公开元数据，输出可解释区间"
          action={<Button size="sm" variant="primary" onClick={runEstimate} disabled={!settings.data?.configured || estimateMutation.isPending} title={!settings.data?.configured ? "请先配置大模型 Token" : ""}>{estimateMutation.isPending ? "估算中…" : estimate ? "重新估算" : "生成估算"}</Button>}
        />
        {!settings.data?.configured && <p className="text-[12px]" style={{ color: "var(--mute)" }}>未配置大模型 Token，配置后可生成区间估算。</p>}
        {estimateError && <p className="text-[12px]" style={{ color: "var(--danger)" }}>{estimateError}</p>}
        {estimate && <EstimateView estimate={estimate} />}
      </div>
    </Card>
  );
}

function EstimateView({ estimate }: { estimate: HiringOrgEstimate }) {
  const max = Math.max(1, ...estimate.departments.map((item) => item.high));
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <StatCard label="估算总人数区间" value={`${fmtNum(estimate.overall.low)}–${fmtNum(estimate.overall.high)}`} tone="accent" />
        <div className="panel p-3 md:col-span-2 text-[12px]" style={{ color: "var(--body)" }}>
          <div className="flex items-center gap-2 mb-1"><Badge tone={confidenceTone(estimate.overall.confidence)}>置信度：{CONFIDENCE_LABEL[estimate.overall.confidence] || estimate.overall.confidence}</Badge></div>
          <div>{estimate.summary || "模型未提供额外摘要。"}</div>
        </div>
      </div>
      {!!estimate.departments.length && <div className="panel p-3">
        <div className="text-[13px] font-medium mb-2" style={{ color: "var(--ink)" }}>部门人数区间</div>
        <div className="space-y-2">
          {estimate.departments.map((item) => (
            <div key={item.department} className="text-[12px]">
              <div className="flex items-center justify-between gap-2 mb-1"><span style={{ color: "var(--body)" }}>{item.department}</span><span className="tabular-nums" style={{ color: "var(--ink)" }}>{item.low}–{item.high} 人</span></div>
              <div className="h-2 rounded-full overflow-hidden" style={{ background: "var(--bg-soft-2)" }}><div className="h-full rounded-full" style={{ width: `${Math.max(4, (item.high / max) * 100)}%`, background: "linear-gradient(90deg, var(--accent), var(--violet))" }} /></div>
              <div className="mt-1" style={{ color: "var(--mute)" }}>{item.rationale} · {item.evidence_ids.length} 条证据</div>
            </div>
          ))}
        </div>
      </div>}
      {!!estimate.locations.length && <div className="panel p-3">
        <div className="text-[13px] font-medium mb-2" style={{ color: "var(--ink)" }}>地区人数区间</div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          {estimate.locations.map((item) => <div key={item.city} className="flex items-center justify-between p-2 rounded-md text-[12px]" style={{ background: "var(--bg-soft-2)" }}><span style={{ color: "var(--body)" }}>{item.city}</span><span className="tabular-nums" style={{ color: "var(--ink)" }}>{item.low}–{item.high} 人</span></div>)}
        </div>
      </div>}
      {!!estimate.assumptions.length && <div className="text-[11px]" style={{ color: "var(--mute)" }}>假设：{estimate.assumptions.join("；")}</div>}
      {!!estimate.limitations.length && <div className="text-[11px]" style={{ color: "var(--mute)" }}>限制：{estimate.limitations.join("；")}</div>}
    </div>
  );
}
