import { useMemo, useState } from "react";
import { Badge, Card, EmptyState, SectionTitle, Spinner } from "./ui";
import { useHiringOrgRadar } from "../lib/hooks";
import type { HiringOrgRadar, HiringOrgRadarGroup } from "../lib/api";

const OFFICE_POSITIONS = [
  { left: "16%", top: "27%" },
  { left: "41%", top: "17%" },
  { left: "72%", top: "27%" },
  { left: "23%", top: "69%" },
  { left: "52%", top: "78%" },
  { left: "82%", top: "63%" },
  { left: "66%", top: "51%" },
  { left: "34%", top: "48%" },
];

const CONFIDENCE_LABEL: Record<string, string> = { high: "较高", medium: "中等", low: "较低" };

function confidenceTone(value?: string): "positive" | "warning" | "neutral" {
  if (value === "high") return "positive";
  if (value === "medium") return "warning";
  return "neutral";
}

function number(value?: number | null) {
  return value == null ? "—" : new Intl.NumberFormat("zh-CN").format(value);
}

function direction(item: HiringOrgRadarGroup) {
  if (item.net_change > 0) return { label: "扩张", tone: "positive" as const, color: "var(--accent)" };
  if (item.net_change < 0) return { label: "回落", tone: "warning" as const, color: "var(--warning)" };
  return { label: "观察", tone: "neutral" as const, color: "var(--mute)" };
}

export function HiringOrganizationAtlas({ brandId, platform }: { brandId?: string; platform?: string }) {
  const { data, isLoading, isError } = useHiringOrgRadar(brandId, platform, undefined, { lifetime: true });
  const [selectedCity, setSelectedCity] = useState<string>();

  if (isLoading) return <Card><Spinner /></Card>;
  if (isError) return <Card><EmptyState title="组织地图暂时不可用" hint="招聘数据仍可查看，组织画像稍后可以重试。" /></Card>;
  if (!data) return <Card><EmptyState title="还没有组织画像" hint="采集职位或公开人员后，这里会从全量历史重建公司的办公和职能分布。" /></Card>;

  return <AtlasContent data={data} selectedCity={selectedCity} onSelectCity={setSelectedCity} />;
}

function AtlasContent({ data, selectedCity, onSelectCity }: { data: HiringOrgRadar; selectedCity?: string; onSelectCity: (city: string) => void }) {
  const departments = data.departments.filter((item) => item.department && item.department !== "未分类").slice(0, 7);
  const locations = data.locations.filter((item) => item.city && item.city !== "未标注地区").slice(0, 8);
  const selected = locations.find((item) => item.city === selectedCity) || locations[0];
  const totalRoles = Math.max(1, departments.reduce((sum, item) => sum + item.open_roles, 0));
  const topDepartment = departments[0];
  const topOffice = locations[0];
  const lifetimeLabel = `${data.window.start} 至 ${data.window.end}`;
  const roleFamilies = useMemo(
    () => data.role_families.filter((item) => item.role_family && item.role_family !== "未分类岗位").slice(0, 4),
    [data.role_families],
  );

  return <Card className="overflow-hidden p-0">
    <div className="px-5 pt-4 pb-3" style={{ background: "linear-gradient(120deg, var(--panel), var(--bg-soft-2))" }}>
      <SectionTitle
        title="组织地图"
        subtitle="一屏看懂 lifetime 招聘轨迹：公司在哪里扩张、各地在招什么"
        action={<div className="flex items-center gap-2"><Badge tone="neutral">全量历史</Badge><Badge tone={confidenceTone(data.overview.confidence)}>置信度：{CONFIDENCE_LABEL[data.overview.confidence] || "较低"}</Badge></div>}
      />
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px]" style={{ color: "var(--mute)" }}>
        <span>观察期 <strong style={{ color: "var(--ink)" }}>{lifetimeLabel}</strong></span>
        <span>招聘需求是组织变化信号，不等于完整 headcount</span>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-3">
        <AtlasMetric label="在招岗位" value={number(data.overview.open_roles)} note="当前需求" />
        <AtlasMetric label="办公地区" value={number(data.overview.city_count)} note="已识别城市" />
        <AtlasMetric label="职能簇" value={number(data.overview.department_count)} note="岗位归类" />
        <AtlasMetric label="可见人员" value={number(data.overview.known_people)} note="公开样本下界" />
      </div>
    </div>

    <div className="grid grid-cols-1 lg:grid-cols-[1.34fr_.86fr_.86fr] gap-3 p-3">
      <section className="rounded-xl p-3" style={{ background: "var(--bg-soft-2)", border: "1px solid var(--hairline)" }}>
        <PanelHeading title="办公室图谱" hint="节点大小 = 在招岗位；颜色 = lifetime 净变化" />
        {locations.length ? <OfficeConstellation locations={locations} selectedCity={selected?.city} onSelectCity={onSelectCity} /> : <div className="h-[330px] flex items-center justify-center"><EmptyState title="还没有可定位的办公室" hint="职位城市字段补齐后，这里会自动形成办公室画像。" /></div>}
      </section>

      <section className="rounded-xl p-3" style={{ background: "var(--bg-soft-2)", border: "1px solid var(--hairline)" }}>
        <PanelHeading title="组织构成" hint="按当前在招需求归类" />
        <div className="space-y-2.5">{departments.length ? departments.map((item) => <DepartmentRow key={item.department} item={item} totalRoles={totalRoles} />) : <EmptyState title="暂无职能数据" />}</div>
        {roleFamilies.length ? <div className="mt-3 pt-3" style={{ borderTop: "1px solid var(--hairline)" }}><div className="text-[10px] mb-1.5" style={{ color: "var(--mute)" }}>角色族</div><div className="flex flex-wrap gap-1.5">{roleFamilies.map((item) => <span key={item.role_family} className="px-2 py-1 rounded-md text-[10px] truncate max-w-full" style={{ color: "var(--body)", background: "var(--panel)" }}>{item.role_family} · {item.open_roles}</span>)}</div></div> : null}
      </section>

      <OfficeDetail location={selected} topDepartment={topDepartment} topOffice={topOffice} locationCount={data.overview.city_count} />
    </div>

    <div className="flex flex-wrap items-center justify-between gap-2 px-4 pb-3 text-[10px]" style={{ color: "var(--mute)" }}>
      <span>点击办公室节点查看地区构成 · 数据覆盖 {number(data.data_quality.postings_total)} 条岗位记录</span>
      <span>可见人员：{number(data.data_quality.people_total)} · 地点识别率：{data.data_quality.postings_total ? Math.round((data.data_quality.with_city / data.data_quality.postings_total) * 100) : 0}%</span>
    </div>
  </Card>;
}

function PanelHeading({ title, hint }: { title: string; hint: string }) {
  return <div className="flex items-baseline justify-between gap-2 px-1 mb-2"><div className="text-[13px] font-medium" style={{ color: "var(--ink)" }}>{title}</div><div className="text-[10px] truncate" style={{ color: "var(--mute)" }}>{hint}</div></div>;
}

function AtlasMetric({ label, value, note }: { label: string; value: string; note: string }) {
  return <div className="rounded-lg px-3 py-2" style={{ background: "rgba(255,255,255,.52)", border: "1px solid var(--hairline)" }}><div className="text-[10px]" style={{ color: "var(--mute)" }}>{label}</div><div className="text-[19px] leading-6 font-semibold mt-0.5 tabular-nums" style={{ color: "var(--ink)" }}>{value}</div><div className="text-[10px] mt-0.5" style={{ color: "var(--mute)" }}>{note}</div></div>;
}

function OfficeConstellation({ locations, selectedCity, onSelectCity }: { locations: (HiringOrgRadarGroup & { city: string; departments: { department: string; count: number }[] })[]; selectedCity?: string; onSelectCity: (city: string) => void }) {
  const maxRoles = Math.max(1, ...locations.map((item) => item.open_roles));
  return <div className="relative h-[330px] overflow-hidden rounded-lg" style={{ background: "radial-gradient(circle at 50% 45%, rgba(126,92,255,.12), transparent 42%), linear-gradient(135deg, var(--panel), var(--bg-soft-2))" }}>
    <div className="absolute inset-0 opacity-40" style={{ backgroundImage: "linear-gradient(var(--hairline) 1px, transparent 1px), linear-gradient(90deg, var(--hairline) 1px, transparent 1px)", backgroundSize: "36px 36px" }} />
    <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-24 h-24 rounded-full flex flex-col items-center justify-center" style={{ background: "linear-gradient(145deg, var(--accent), var(--violet))", boxShadow: "0 14px 40px rgba(86,64,180,.28)" }}><div className="text-white text-[16px] font-semibold">组织</div><div className="text-white/75 text-[9px] mt-1">招聘轨迹</div></div>
    {locations.map((item, index) => { const pos = OFFICE_POSITIONS[index % OFFICE_POSITIONS.length]; const active = item.city === selectedCity; const radius = 20 + Math.round((item.open_roles / maxRoles) * 13); const signal = direction(item); return <button key={item.city} onClick={() => onSelectCity(item.city)} aria-label={`查看${item.city}办公室`} className="absolute -translate-x-1/2 -translate-y-1/2 flex flex-col items-center group" style={{ left: pos.left, top: pos.top }}><span className="absolute w-20 h-px origin-left" style={{ background: signal.color, opacity: .28, transform: `rotate(${index % 2 ? 28 : -22}deg)` }} /><span className="relative rounded-full flex items-center justify-center transition-transform group-hover:scale-110" style={{ width: radius * 2, height: radius * 2, background: active ? "var(--ink)" : "var(--panel)", border: `2px solid ${active ? "var(--accent)" : signal.color}`, boxShadow: active ? "0 0 0 5px rgba(105,82,230,.14)" : "none" }}><span className="text-[10px] font-medium" style={{ color: active ? "white" : "var(--ink)" }}>{item.city.length > 8 ? `${item.city.slice(0, 8)}…` : item.city}</span></span><span className="mt-1 whitespace-nowrap text-[9px]" style={{ color: "var(--body)" }}>{item.open_roles} 在招 · {signal.label}</span></button>; })}
  </div>;
}

function OfficeDetail({ location, topDepartment, topOffice, locationCount }: { location?: HiringOrgRadarGroup & { city: string; departments: { department: string; count: number }[] }; topDepartment?: HiringOrgRadarGroup; topOffice?: HiringOrgRadarGroup & { city: string }; locationCount: number }) {
  if (!location) return <section className="rounded-xl p-3 flex items-center justify-center" style={{ background: "var(--bg-soft-2)", border: "1px solid var(--hairline)" }}><EmptyState title="选择一个办公室" hint="点击左侧节点查看地区职能构成。" /></section>;
  const signal = direction(location);
  const departments = location.departments || [];
  const max = Math.max(1, ...departments.map((item) => item.count));
  return <section className="rounded-xl p-3" style={{ background: "var(--bg-soft-2)", border: "1px solid var(--hairline)" }}>
    <div className="flex items-start justify-between gap-2"><div><div className="text-[18px] font-semibold" style={{ color: "var(--ink)" }}>{location.city}</div><div className="text-[10px] mt-0.5" style={{ color: "var(--mute)" }}>地区画像 · 岗位城市信号</div></div><Badge tone={signal.tone}>{signal.label}</Badge></div>
    <div className="grid grid-cols-3 gap-1.5 mt-3"><MiniStat label="在招" value={number(location.open_roles)} /><MiniStat label="新增" value={number(location.released)} /><MiniStat label="下线" value={number(location.closed)} /></div>
    <div className="mt-3"><div className="text-[10px]" style={{ color: "var(--mute)" }}>地区职能</div><div className="space-y-2 mt-2">{departments.slice(0, 5).map((item) => <div key={item.department}><div className="flex justify-between text-[10px]" style={{ color: "var(--body)" }}><span>{item.department}</span><span>{item.count}</span></div><div className="h-1.5 rounded-full mt-1" style={{ background: "var(--hairline)" }}><div className="h-full rounded-full" style={{ width: `${Math.max(8, (item.count / max) * 100)}%`, background: "linear-gradient(90deg, var(--accent), var(--violet))" }} /></div></div>)}</div></div>
    <div className="mt-3 pt-3 space-y-2 text-[10px] leading-4" style={{ color: "var(--body)", borderTop: "1px solid var(--hairline)" }}>
      <div><span style={{ color: "var(--mute)" }}>当前重心：</span>{topDepartment ? `${topDepartment.department} · ${topDepartment.open_roles} 个在招` : "暂无"}</div>
      <div><span style={{ color: "var(--mute)" }}>空间重心：</span>{topOffice ? `${topOffice.city} · ${topOffice.open_roles} 个在招` : "暂无"}</div>
      <div><span style={{ color: "var(--mute)" }}>覆盖：</span>{locationCount ? `已识别 ${locationCount} 个地区` : "等待城市数据"}</div>
      <div style={{ color: "var(--mute)" }}>{location.net_change > 0 ? "历史新增多于下线，招聘信号偏向扩张。" : location.net_change < 0 ? "历史下线多于新增，招聘信号正在回落。" : "暂时看不出明显的净变化。"}</div>
    </div>
  </section>;
}

function MiniStat({ label, value }: { label: string; value: string }) { return <div className="rounded-md px-2 py-1.5" style={{ background: "var(--panel)" }}><div className="text-[9px]" style={{ color: "var(--mute)" }}>{label}</div><div className="text-[15px] font-semibold mt-0.5" style={{ color: "var(--ink)" }}>{value}</div></div>; }

function DepartmentRow({ item, totalRoles }: { item: HiringOrgRadarGroup; totalRoles: number }) {
  const signal = direction(item);
  return <div><div className="flex items-center justify-between gap-2 text-[11px]"><div className="flex items-center gap-1.5 min-w-0"><span className="truncate font-medium" style={{ color: "var(--ink)" }}>{item.department}</span><span className="text-[9px]" style={{ color: signal.color }}>{signal.label}</span></div><span className="shrink-0 tabular-nums" style={{ color: "var(--body)" }}>{item.open_roles}</span></div><div className="h-1.5 rounded-full mt-1" style={{ background: "var(--hairline)" }}><div className="h-full rounded-full" style={{ width: `${Math.max(5, (item.open_roles / totalRoles) * 100)}%`, background: signal.color }} /></div><div className="flex justify-between mt-0.5 text-[9px]" style={{ color: "var(--mute)" }}><span>新增 {item.released} · 下线 {item.closed}</span><span>{item.known_people ? `${item.known_people} 人` : "无人员样本"}</span></div></div>;
}
