import { useState } from "react";
import { BriefcaseBusiness, Building2, ChevronRight, Layers3, MapPin, Users } from "lucide-react";
import { Badge, Button, Card, EmptyState, Modal, Spinner } from "./ui";
import { HiringStrategicSignals } from "./HiringStrategicSignals";
import { useHiringOrgEstimate, useHiringOrgRadar, useSettings } from "../lib/hooks";
import { fmtDateTime, fmtNum } from "../lib/format";
import type { HiringOrgEstimateGroup, HiringOrgRadarGroup } from "../lib/api";

const VIEWS = [
  { key: "locations", label: "地区 / 办公点", icon: MapPin },
  { key: "departments", label: "部门", icon: Layers3 },
  { key: "role_families", label: "岗位族", icon: BriefcaseBusiness },
] as const;

type View = typeof VIEWS[number]["key"];
type DistributionRow = {
  name: string;
  observed: HiringOrgRadarGroup;
  estimate?: HiringOrgEstimateGroup;
};

const CONFIDENCE: Record<string, string> = { high: "较高", medium: "中等", low: "较低" };
const TRACKED_GROUPS = 6;

function headcount(range?: { low: number; high: number } | null) {
  if (!range) return "证据不足";
  return (range.low === range.high ? fmtNum(range.low) : fmtNum(range.low) + "–" + fmtNum(range.high)) + " 人";
}

function confidenceBadge(value: string) {
  return <Badge tone={value === "high" ? "positive" : value === "medium" ? "warning" : "neutral"}>{CONFIDENCE[value] || "较低"}置信度</Badge>;
}

export function HiringOrgRadar({ brandId }: { brandId: string }) {
  const radarQuery = useHiringOrgRadar(brandId);
  const estimation = useHiringOrgEstimate(brandId);
  const settings = useSettings();
  const [view, setView] = useState<View>("locations");
  const [selection, setSelection] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [methodOpen, setMethodOpen] = useState(false);
  const [strategyOpen, setStrategyOpen] = useState(false);
  const data = radarQuery.data;
  const estimate = estimation.data?.estimate;
  const reference = data?.headcount_reference;
  const referenceLabel = reference && "约 " + reference.count.toLocaleString("zh-CN") + " 人";
  const status = estimation.data?.generation;
  const generating = estimation.generate.isPending || status?.status === "running";
  const hasEvidence = !!data && (data.data_quality.postings_total > 0 || data.overview.known_people > 0);
  const estimateError = estimation.generate.error?.message || estimation.error?.message || status?.error;
  const groups: DistributionRow[] = data ? {
    locations: data.locations.map((row) => ({ name: row.city, observed: row, estimate: estimate?.locations.find((item) => item.city === row.city) })),
    departments: data.departments.map((row) => ({ name: row.department || "未分类", observed: row, estimate: estimate?.departments.find((item) => item.department === row.department) })),
    role_families: data.role_families.map((row) => ({ name: row.role_family, observed: row, estimate: estimate?.role_families.find((item) => item.role_family === row.role_family) })),
  }[view] : [];
  const visibleGroups = showAll ? groups : groups.slice(0, TRACKED_GROUPS);
  const selected = visibleGroups.find((row) => row.name === selection) ?? visibleGroups[0];
  const city = view === "locations" ? data?.locations.find((item) => item.city === selected?.name) : undefined;
  const cityEstimate = city && estimate?.locations.find((item) => item.city === city.city);

  return (
    <Card className="p-0! overflow-hidden">
      <div className="px-4 sm:px-6 py-5 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-[20px] font-semibold tracking-tight" style={{ color: "var(--ink)" }}>组织推演台</h2>
          <p className="mt-1 text-[13px]" style={{ color: "var(--mute)" }}>从全量招聘记录与公开员工样本推测组织规模和分布</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={() => setMethodOpen(true)}>估算依据</Button>
          <Button size="sm" variant="primary" disabled={!hasEvidence || !settings.data?.configured || generating || estimation.isLoading}
            onClick={() => estimation.generate.mutate(!!estimate || status?.status === "failed")}>
            {generating ? "推演中…" : estimate ? "重新推演" : "推演人数"}
          </Button>
        </div>
      </div>
      {radarQuery.isLoading && <Spinner />}
      {radarQuery.isError && !data && (
        <div className="px-4 sm:px-6 pb-5">
          <EmptyState title="组织数据加载失败" hint={radarQuery.error.message} action={<Button onClick={() => radarQuery.refetch()}>重试</Button>} />
        </div>
      )}
      {data && (
        <div className="px-4 sm:px-6 pb-5">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-[1.5fr_1fr_1fr_1fr]">
            <div className="rounded-xl border p-4" style={{ background: "color-mix(in srgb, var(--accent) 6%, var(--panel))", borderColor: "color-mix(in srgb, var(--accent) 20%, var(--hairline))" }}>
              <p className="flex items-center gap-2 text-[13px] font-medium" style={{ color: "var(--accent)" }}><Building2 size={16} /> {reference ? "全球员工数参考" : "估计总人数"}</p>
              <p className="mt-2 text-[26px] font-semibold tracking-tight tabular-nums" style={{ color: "var(--ink)" }}>
                {referenceLabel || (estimate ? headcount(estimate.overall) : hasEvidence ? "待推演" : "证据不足")}
              </p>
              <div className="mt-1 flex flex-wrap items-center gap-2 text-[12px]" style={{ color: "var(--mute)" }}>
                {reference ? (
                  <span>{reference.source} · {reference.as_of}</span>
                ) : (
                  <>
                    {estimate?.overall && confidenceBadge(estimate.overall.confidence)}
                    {estimate?.generated_at ? fmtDateTime(estimate.generated_at) + " 更新" : "模型区间估算"}
                  </>
                )}
              </div>
              {reference && estimate?.overall && (
                <p className="mt-1.5 flex flex-wrap items-center gap-2 text-[12px]" style={{ color: "var(--body)" }}>
                  模型估算 {headcount(estimate.overall)} {confidenceBadge(estimate.overall.confidence)}
                </p>
              )}
            </div>
            <ObservedStat icon={MapPin} label="有记录的招聘地区" value={data.overview.city_count} />
            <ObservedStat icon={BriefcaseBusiness} label="当前在招职位记录" value={data.overview.open_roles} />
            <ObservedStat icon={Users} label="公开员工样本" value={data.overview.known_people} unit="人" />
          </div>

          <div className="mt-3 space-y-1 text-[12px]" style={{ color: "var(--mute)" }}>
            <p>招聘记录覆盖 {data.window.start} 至 {data.window.end}。地点来自职位地点字段或平台标题，实体办公室仍待核实。</p>
            {hasEvidence && settings.data && !settings.data.configured && <p>配置大模型后可生成人数区间。</p>}
            {generating && <p role="status">正在根据已采集的证据推演，完成后自动更新。</p>}
            {estimateError && !generating && <p role="alert" style={{ color: "var(--danger)" }}>{estimateError}</p>}
          </div>

          {hasEvidence ? (
            <div className="mt-5">
              <div className="flex flex-wrap gap-2 mb-3" aria-label="组织分布维度" role="group">
                {VIEWS.map(({ key, label, icon: Icon }) => (
                  <Button key={key} size="sm" aria-pressed={view === key} variant={view === key ? "primary" : "secondary"}
                    onClick={() => { setView(key); setSelection(""); setShowAll(false); }}>
                    <Icon size={14} />{label}
                  </Button>
                ))}
              </div>
              <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
                <div className="rounded-xl border self-start overflow-hidden" style={{ borderColor: "var(--hairline)" }}>
                  <div className="grid grid-cols-[minmax(0,1fr)_5.5rem_2rem] sm:grid-cols-[minmax(0,1fr)_7rem_3rem] gap-2 px-3 py-2.5 text-[12px]" style={{ color: "var(--mute)", background: "var(--bg-soft-2)" }}>
                    <span>{VIEWS.find((item) => item.key === view)?.label}</span><span>估计人数</span><span className="text-right">在招</span>
                  </div>
                  {visibleGroups.map((row) => (
                    <button key={row.name} type="button" aria-pressed={selected === row} onClick={() => setSelection(row.name)}
                      className="grid w-full items-center grid-cols-[minmax(0,1fr)_5.5rem_2rem] sm:grid-cols-[minmax(0,1fr)_7rem_3rem] gap-2 px-3 py-3 border-t text-left text-[13px] cursor-pointer hover:bg-[var(--bg-soft-2)]"
                      style={{ borderColor: "var(--hairline)", background: selected === row ? "color-mix(in srgb, var(--accent) 7%, var(--panel))" : undefined }}>
                      <span className="flex items-center gap-1.5 min-w-0 font-medium" style={{ color: selected === row ? "var(--accent)" : "var(--ink)" }}>
                        <span className="wrap-anywhere">{row.name}</span><ChevronRight size={13} className="shrink-0" />
                      </span>
                      <span className="tabular-nums" style={{ color: row.estimate ? "var(--ink)" : "var(--mute)" }}>{estimate ? headcount(row.estimate) : "—"}</span>
                      <span className="text-right tabular-nums" style={{ color: "var(--body)" }}>{fmtNum(row.observed.open_roles)}</span>
                    </button>
                  ))}
                  {!visibleGroups.length && <p className="p-3 text-[13px]" style={{ color: "var(--mute)" }}>当前数据未覆盖这个维度。</p>}
                  {groups.length > TRACKED_GROUPS && (
                    <div className="px-2 py-1 border-t" style={{ borderColor: "var(--hairline)" }}>
                      <Button size="sm" variant="ghost" aria-expanded={showAll} onClick={() => setShowAll(!showAll)}>{showAll ? "收起列表" : "展开 " + groups.length + " 项"}</Button>
                    </div>
                  )}
                </div>
                {selected && (
                  <div className="min-w-0 rounded-xl border p-4" style={{ borderColor: "var(--hairline)", background: "var(--bg-soft)" }}>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <h3 className="text-[16px] font-semibold wrap-anywhere" style={{ color: "var(--ink)" }}>{selected.name}</h3>
                      {selected.estimate && confidenceBadge(selected.estimate.confidence)}
                    </div>
                    <p className="mt-1 text-[12px]" style={{ color: "var(--mute)" }}>
                      累计 {fmtNum(selected.observed.postings_total)} 条职位记录
                      {view !== "locations" && " · " + fmtNum(selected.observed.known_people) + " 位公开员工样本"}
                    </p>
                    {selected.estimate?.rationale && <p className="mt-2 text-[13px] leading-5" style={{ color: "var(--body)" }}>{selected.estimate.rationale}</p>}
                    {city?.department_details.length ? (
                      <div key={city.city} className="mt-3">
                        <h4 className="text-[12px] font-medium" style={{ color: "var(--mute)" }}>地区内的部门与岗位</h4>
                        {city.department_details.map((department) => {
                          const localEstimate = cityEstimate?.departments.find((item) => item.department === department.department);
                          return (
                            <details key={department.department} className="group border-b last:border-b-0 py-2" style={{ borderColor: "var(--hairline)" }}>
                              <summary className="cursor-pointer list-none text-[13px]" style={{ color: "var(--ink)" }}>
                                <span className="flex flex-wrap items-center justify-between gap-x-2">
                                  <span className="flex items-center gap-1 font-medium"><ChevronRight size={13} className="group-open:rotate-90" />{department.department}</span>
                                  <span className="text-[12px]" style={{ color: "var(--mute)" }}>{department.open_roles} 条在招{localEstimate && " · 估计 " + headcount(localEstimate)}</span>
                                </span>
                                <span className="block mt-0.5 ml-[17px] truncate text-[12px]" style={{ color: "var(--body)" }}>
                                  {department.top_roles.slice(0, 2).map((item) => item.title).join(" · ")}
                                </span>
                              </summary>
                              <RoleTitles group={department} />
                              {localEstimate && <div className="mt-2 flex flex-wrap items-center gap-2 text-[12px]" style={{ color: "var(--body)" }}>
                                {confidenceBadge(localEstimate.confidence)}{localEstimate.rationale}
                              </div>}
                              <PostingEvidence group={department} estimate={localEstimate} />
                            </details>
                          );
                        })}
                      </div>
                    ) : <RoleTitles group={selected.observed} />}
                    <PostingEvidence key={view + selected.name} group={selected.observed} estimate={selected.estimate} />
                  </div>
                )}
              </div>
            </div>
          ) : <div className="mt-4"><EmptyState title="暂无组织线索" hint="采集招聘职位或公开员工资料后即可查看分布。" /></div>}
        </div>
      )}
      <details className="group border-t" style={{ borderColor: "var(--hairline)" }} open={strategyOpen}
        onToggle={(event) => setStrategyOpen(event.currentTarget.open)}>
        <summary className="list-none cursor-pointer px-4 sm:px-6 py-4 flex items-center justify-between gap-3">
          <div>
            <h3 className="text-[14px] font-semibold" style={{ color: "var(--ink)" }}>跨板块战略推测</h3>
            <p className="mt-0.5 text-[12px]" style={{ color: "var(--mute)" }}>结合招聘、销售、营销、用户之声与官网动态，推测可能的业务动作。</p>
          </div>
          <ChevronRight size={16} className="shrink-0 group-open:rotate-90" style={{ color: "var(--mute)" }} />
        </summary>
        {strategyOpen && <HiringStrategicSignals brandId={brandId} />}
      </details>
      <Modal title="人数估算依据" open={methodOpen} onClose={() => setMethodOpen(false)}>
        <div className="space-y-3 text-[13px] leading-5" style={{ color: "var(--body)" }}>
          {reference && <p>全球员工数参考为{referenceLabel}（{reference.source}，{reference.as_of}）。各地区和部门的人数需结合各自的规模证据估算。</p>}
          {estimate?.summary && <p>{estimate.summary}</p>}
          <p>部门沿用来源标签，缺失时根据职位名称或 JD 归类。岗位族归并职级不同的相似职位。公开员工样本没有可靠地区字段，目前用于公司、部门与岗位族的参考下界。</p>
          <p>在招职位反映当前招聘需求。人数区间按各维度独立估算，证据不足的分组留空，局部区间不可直接相加。</p>
          {!!estimate?.overall?.basis.length && <MethodList label="整体估算依据" items={estimate.overall.basis} />}
          {!!estimate?.assumptions.length && <MethodList label="估算假设" items={estimate.assumptions} />}
          {!!estimate?.limitations.length && <MethodList label="数据限制" items={estimate.limitations} />}
        </div>
      </Modal>
    </Card>
  );
}

function ObservedStat({ icon: Icon, label, value, unit }: { icon: typeof MapPin; label: string; value: number; unit?: string }) {
  return (
    <div className="rounded-xl border p-4" style={{ background: "var(--bg-soft)", borderColor: "var(--hairline)" }}>
      <p className="flex items-center gap-2 text-[13px]" style={{ color: "var(--body)" }}><Icon size={16} style={{ color: "var(--mute)" }} />{label}</p>
      <p className="mt-2 text-[26px] font-semibold tracking-tight tabular-nums" style={{ color: "var(--ink)" }}>
        {fmtNum(value)}{unit && <span className="ml-1 text-[13px] font-normal" style={{ color: "var(--mute)" }}>{unit}</span>}
      </p>
    </div>
  );
}

function RoleTitles({ group }: { group: HiringOrgRadarGroup }) {
  if (!group.top_roles.length) return null;
  return (
    <div className="mt-2 text-[12px]" style={{ color: "var(--body)" }}>
      <p style={{ color: "var(--mute)" }}>常见职位名称及记录数</p>
      <ul className="mt-1 flex flex-wrap gap-1.5">
        {group.top_roles.map((item) => (
          <li key={item.title} className="px-2 py-0.5 rounded-md wrap-anywhere" style={{ background: "var(--bg-soft-2)" }}>
            {item.title} <span className="tabular-nums" style={{ color: "var(--mute)" }}>{item.count}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function PostingEvidence({ group, estimate }: { group: HiringOrgRadarGroup; estimate?: HiringOrgEstimateGroup }) {
  if (!group.evidence.length) return null;
  const used = new Set(estimate?.evidence_ids);
  const evidence = [...group.evidence].sort((left, right) => Number(used.has(right.id)) - Number(used.has(left.id)));
  return (
    <details className="mt-2 text-[12px]" style={{ color: "var(--body)" }}>
      <summary className="cursor-pointer" style={{ color: "var(--accent)" }}>查看 {evidence.length} 条职位证据</summary>
      <ul className="mt-1 space-y-1">
        {evidence.map((item) => (
          <li key={item.id} className="flex flex-wrap gap-x-2 wrap-anywhere">
            {item.url && /^https?:\/\//i.test(item.url)
              ? <a href={item.url} target="_blank" rel="noreferrer" className="hover:underline" style={{ color: "var(--accent)" }}>{item.title}</a>
              : <span>{item.title}</span>}
            <span style={{ color: "var(--mute)" }}>
              {[item.city, item.city_source === "linkedin_title" && "标题地点", item.status === "closed" ? "已下线" : "在招", item.first_seen?.slice(0, 10), used.has(item.id) && "估算引用"].filter(Boolean).join(" · ")}
            </span>
          </li>
        ))}
      </ul>
    </details>
  );
}

function MethodList({ label, items }: { label: string; items: string[] }) {
  return <div><h3 className="font-medium" style={{ color: "var(--ink)" }}>{label}</h3><ul className="list-disc pl-5">{items.map((item, index) => <li key={index}>{item}</li>)}</ul></div>;
}
