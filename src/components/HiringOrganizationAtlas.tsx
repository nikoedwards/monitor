import { useMemo, useState } from "react";
import { Badge, Card, EmptyState, SectionTitle, Spinner } from "./ui";
import { useHiringOrgRadar } from "../lib/hooks";
import type { HiringOrgRadar, HiringOrgRadarGroup } from "../lib/api";

const OFFICE_POSITIONS = [
  { left: "17%", top: "30%" },
  { left: "42%", top: "20%" },
  { left: "70%", top: "34%" },
  { left: "27%", top: "68%" },
  { left: "61%", top: "72%" },
  { left: "83%", top: "61%" },
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
  const departments = data.departments.filter((item) => item.department && item.department !== "未分类").slice(0, 8);
  const locations = data.locations.filter((item) => item.city && item.city !== "未标注地区").slice(0, 6);
  const selected = locations.find((item) => item.city === selectedCity) || locations[0];
  const totalRoles = Math.max(1, departments.reduce((sum, item) => sum + item.open_roles, 0));
  const topDepartment = departments[0];
  const topOffice = locations[0];
  const lifetimeLabel = `${data.window.start} 至 ${data.window.end}`;
  const roleFamilies = useMemo(() => data.role_families.filter((item) => item.role_family && item.role_family !== "未分类岗位").slice(0, 5), [data.role_families]);

  return <Card className="overflow-hidden p-0">
    <div className="px-5 pt-5 pb-4" style={{ background: "linear-gradient(120deg, var(--panel), var(--bg-soft-2))" }}>
      <SectionTitle
        title="组织地图"
        subtitle="从公开人员样本与招聘轨迹，重建公司的办公地理和职能构成"
        action={<div className="flex items-center gap-2"><Badge tone="neutral">全量历史</Badge><Badge tone={confidenceTone(data.overview.confidence)}>样本置信度：{CONFIDENCE_LABEL[data.overview.confidence] || "较低"}</Badge></div>}
      />
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px]" style={{ color: "var(--body)" }}>
        <span>观察期：<strong style={{ color: "var(--ink)" }}>{lifetimeLabel}</strong></span>
        <span>这是一张组织样本地图，不是完整 HR 花名册</span>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-4">
        <AtlasMetric label="可见人员样本下界" value={number(data.overview.known_people)} note="公开页面可见" />
        <AtlasMetric label="观察到的办公室" value={number(data.overview.city_count)} note="按职位城市归并" />
        <AtlasMetric label="在招岗位" value={number(data.overview.open_roles)} note="招聘需求信号" />
        <AtlasMetric label="职能簇" value={number(data.overview.department_count)} note="岗位与人员推断" />
      </div>
    </div>

    <div className="grid grid-cols-1 xl:grid-cols-[1.12fr_.88fr] gap-4 p-4">
      <div className="rounded-xl p-3" style={{ background: "var(--bg-soft-2)", border: "1px solid var(--hairline)" }}>
        <div className="flex items-center justify-between px-1 mb-2">
          <div><div className="text-[13px] font-medium" style={{ color: "var(--ink)" }}>办公室图谱</div><div className="text-[11px] mt-0.5" style={{ color: "var(--mute)" }}>点开一个地区，看它承担的职能</div></div>
          <span className="text-[11px]" style={{ color: "var(--mute)" }}>岗位落点 · lifetime</span>
        </div>
        {locations.length ? <OfficeConstellation locations={locations} selectedCity={selected?.city} onSelectCity={onSelectCity} /> : <div className="h-[300px] flex items-center justify-center"><EmptyState title="还没有可定位的办公室" hint="职位城市字段补齐后，这里会自动形成办公室画像。" /></div>}
      </div>
      <OfficeDetail location={selected} />
    </div>

    <div className="grid grid-cols-1 lg:grid-cols-[1.12fr_.88fr] gap-4 px-4 pb-5">
      <div className="rounded-xl p-4" style={{ background: "var(--bg-soft-2)", border: "1px solid var(--hairline)" }}>
        <div className="flex items-center justify-between mb-3"><div><div className="text-[13px] font-medium" style={{ color: "var(--ink)" }}>组织构成</div><div className="text-[11px] mt-0.5" style={{ color: "var(--mute)" }}>当前可见招聘需求的职能分布</div></div><span className="text-[11px]" style={{ color: "var(--mute)" }}>{departments.length} 个主要职能</span></div>
        <div className="space-y-2.5">{departments.map((item) => <DepartmentRow key={item.department} item={item} totalRoles={totalRoles} />)}</div>
        {roleFamilies.length ? <div className="flex flex-wrap gap-1.5 mt-4 pt-3" style={{ borderTop: "1px solid var(--hairline)" }}>{roleFamilies.map((item) => <span key={item.role_family} className="px-2 py-1 rounded-md text-[11px]" style={{ color: "var(--body)", background: "var(--panel)" }}>{item.role_family} · {item.open_roles}</span>)}</div> : null}
      </div>
      <SignalSummary topDepartment={topDepartment} topOffice={topOffice} locationCount={locations.length} />
    </div>
  </Card>;
}

function AtlasMetric({ label, value, note }: { label: string; value: string; note: string }) {
  return <div className="rounded-lg px-3 py-2.5" style={{ background: "rgba(255,255,255,.52)", border: "1px solid var(--hairline)" }}><div className="text-[10px]" style={{ color: "var(--mute)" }}>{label}</div><div className="text-[22px] leading-7 font-semibold mt-0.5" style={{ color: "var(--ink)" }}>{value}</div><div className="text-[10px] mt-0.5" style={{ color: "var(--mute)" }}>{note}</div></div>;
}

function OfficeConstellation({ locations, selectedCity, onSelectCity }: { locations: (HiringOrgRadarGroup & { city: string; departments: { department: string; count: number }[] })[]; selectedCity?: string; onSelectCity: (city: string) => void }) {
  const maxRoles = Math.max(1, ...locations.map((item) => item.open_roles));
  return <div className="relative h-[300px] overflow-hidden rounded-lg" style={{ background: "radial-gradient(circle at 50% 45%, rgba(126,92,255,.12), transparent 42%), linear-gradient(135deg, var(--panel), var(--bg-soft-2))" }}>
    <div className="absolute inset-0 opacity-40" style={{ backgroundImage: "linear-gradient(var(--hairline) 1px, transparent 1px), linear-gradient(90deg, var(--hairline) 1px, transparent 1px)", backgroundSize: "36px 36px" }} />
    <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-28 h-28 rounded-full flex flex-col items-center justify-center" style={{ background: "linear-gradient(145deg, var(--accent), var(--violet))", boxShadow: "0 14px 40px rgba(86,64,180,.28)" }}><div className="text-white text-[18px] font-semibold">组织</div><div className="text-white/75 text-[10px] mt-1">招聘轨迹</div></div>
    {locations.map((item, index) => { const pos = OFFICE_POSITIONS[index % OFFICE_POSITIONS.length]; const active = item.city === selectedCity; const radius = 25 + Math.round((item.open_roles / maxRoles) * 14); const signal = direction(item); return <button key={item.city} onClick={() => onSelectCity(item.city)} className="absolute -translate-x-1/2 -translate-y-1/2 flex flex-col items-center group" style={{ left: pos.left, top: pos.top }}><span className="absolute w-24 h-px origin-left" style={{ background: signal.color, opacity: .28, transform: `rotate(${index % 2 ? 28 : -22}deg)` }} /><span className="relative rounded-full flex items-center justify-center transition-transform group-hover:scale-110" style={{ width: radius * 2, height: radius * 2, background: active ? "var(--ink)" : "var(--panel)", border: `2px solid ${active ? "var(--accent)" : signal.color}`, boxShadow: active ? "0 0 0 6px rgba(105,82,230,.14)" : "none" }}><span className="text-[11px] font-medium" style={{ color: active ? "white" : "var(--ink)" }}>{item.city.length > 7 ? `${item.city.slice(0, 7)}…` : item.city}</span></span><span className="mt-1 whitespace-nowrap text-[10px]" style={{ color: "var(--body)" }}>{item.open_roles} 在招 · {signal.label}</span></button>; })}
  </div>;
}

function OfficeDetail({ location }: { location?: HiringOrgRadarGroup & { city: string; departments: { department: string; count: number }[] } }) {
  if (!location) return <div className="rounded-xl p-4 flex items-center justify-center" style={{ background: "var(--bg-soft-2)", border: "1px solid var(--hairline)" }}><EmptyState title="选择一个办公室" hint="点击左侧节点查看地区职能构成。" /></div>;
  const signal = direction(location);
  const departments = location.departments || [];
  const max = Math.max(1, ...departments.map((item) => item.count));
  return <div className="rounded-xl p-4" style={{ background: "var(--bg-soft-2)", border: "1px solid var(--hairline)" }}>
    <div className="flex items-start justify-between gap-3"><div><div className="text-[20px] font-semibold" style={{ color: "var(--ink)" }}>{location.city}</div><div className="text-[11px] mt-1" style={{ color: "var(--mute)" }}>办公室画像 · 基于岗位城市和招聘轨迹</div></div><Badge tone={signal.tone}>{signal.label}</Badge></div>
    <div className="grid grid-cols-3 gap-2 mt-5"><MiniStat label="在招" value={number(location.open_roles)} /><MiniStat label="历史新增" value={number(location.released)} /><MiniStat label="历史下线" value={number(location.closed)} /></div>
    <div className="mt-5"><div className="text-[11px]" style={{ color: "var(--mute)" }}>招聘职能构成</div><div className="space-y-2 mt-2">{departments.slice(0, 5).map((item) => <div key={item.department}><div className="flex justify-between text-[11px]" style={{ color: "var(--body)" }}><span>{item.department}</span><span>{item.count}</span></div><div className="h-1.5 rounded-full mt-1" style={{ background: "var(--hairline)" }}><div className="h-full rounded-full" style={{ width: `${Math.max(8, (item.count / max) * 100)}%`, background: "linear-gradient(90deg, var(--accent), var(--violet))" }} /></div></div>)}</div></div>
    <div className="mt-5 pt-3 text-[11px] leading-5" style={{ color: "var(--body)", borderTop: "1px solid var(--hairline)" }}>{location.net_change > 0 ? `这个地区历史上新增岗位多于下线岗位，招聘信号偏向扩张。` : location.net_change < 0 ? `这个地区的岗位下线多于新增，当前招聘信号正在回落。` : `这个地区已有招聘记录，但暂时看不出明显的净扩张方向。`} <span style={{ color: "var(--mute)" }}>这代表招聘需求变化，不等同于真实员工数变化。</span></div>
  </div>;
}

function MiniStat({ label, value }: { label: string; value: string }) { return <div className="rounded-md px-2.5 py-2" style={{ background: "var(--panel)" }}><div className="text-[10px]" style={{ color: "var(--mute)" }}>{label}</div><div className="text-[17px] font-semibold mt-0.5" style={{ color: "var(--ink)" }}>{value}</div></div>; }

function DepartmentRow({ item, totalRoles }: { item: HiringOrgRadarGroup; totalRoles: number }) {
  const signal = direction(item);
  return <div><div className="flex items-center justify-between gap-3 text-[12px]"><div className="flex items-center gap-2 min-w-0"><span className="truncate font-medium" style={{ color: "var(--ink)" }}>{item.department}</span><Badge tone={signal.tone}>{signal.label}</Badge></div><span className="shrink-0" style={{ color: "var(--body)" }}>{item.open_roles} 在招</span></div><div className="h-2 rounded-full mt-1.5" style={{ background: "var(--hairline)" }}><div className="h-full rounded-full" style={{ width: `${Math.max(5, (item.open_roles / totalRoles) * 100)}%`, background: signal.color }} /></div><div className="flex justify-between mt-1 text-[10px]" style={{ color: "var(--mute)" }}><span>历史新增 {item.released} · 下线 {item.closed}</span><span>{item.known_people ? `${item.known_people} 名可见人员` : "人员未按职能归属"}</span></div></div>;
}

function SignalSummary({ topDepartment, topOffice, locationCount }: { topDepartment?: HiringOrgRadarGroup; topOffice?: HiringOrgRadarGroup & { city: string }; locationCount: number }) {
  return <div className="rounded-xl p-4" style={{ background: "linear-gradient(145deg, rgba(126,92,255,.08), var(--bg-soft-2))", border: "1px solid var(--hairline)" }}><div className="text-[13px] font-medium" style={{ color: "var(--ink)" }}>组织线索</div><div className="text-[11px] mt-1" style={{ color: "var(--mute)" }}>先看确定性变化，再看推断</div><div className="space-y-3 mt-5"><SignalLine label="职能重心" value={topDepartment ? `${topDepartment.department} · ${topDepartment.open_roles} 个在招岗位` : "暂无"} tone="accent" /><SignalLine label="空间重心" value={topOffice ? `${topOffice.city} · ${topOffice.open_roles} 个在招岗位` : "暂无"} tone="violet" /><SignalLine label="组织覆盖" value={locationCount ? `已识别 ${locationCount} 个有招聘记录的地区` : "等待城市数据"} tone="neutral" /></div><div className="mt-5 pt-3 text-[11px] leading-5" style={{ color: "var(--body)", borderTop: "1px solid var(--hairline)" }}>下一步可以把人员地点、职级和历史快照补齐，进一步推导办公室构成、组织层级和人员流动。</div></div>;
}

function SignalLine({ label, value, tone }: { label: string; value: string; tone: "accent" | "violet" | "neutral" }) { const color = tone === "accent" ? "var(--accent)" : tone === "violet" ? "var(--violet)" : "var(--mute)"; return <div className="flex gap-3"><span className="mt-1.5 w-2 h-2 rounded-full shrink-0" style={{ background: color }} /><div><div className="text-[10px]" style={{ color: "var(--mute)" }}>{label}</div><div className="text-[12px] mt-0.5" style={{ color: "var(--ink)" }}>{value}</div></div></div>; }
