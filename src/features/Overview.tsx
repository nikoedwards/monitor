import { useMemo } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowUpRight, BriefcaseBusiness, ChartNoAxesCombined, Megaphone, MessageSquareWarning, RefreshCw, ShoppingCart, Telescope } from "lucide-react";
import { SimpleLine } from "../components/charts";
import { Badge, Button, Card, EmptyState, SectionTitle, Spinner, StatCard } from "../components/ui";
import { ExecutiveSmartSummary } from "../components/SmartSummary";
import { TimeRangePicker } from "../components/TimeRangePicker";
import { useBrands, useOverview } from "../lib/hooks";
import { useTimeRange, rangeLabel } from "../lib/timeRange";
import { fmtNum } from "../lib/format";

const MARKET_STORAGE_KEY = "monitor.marketShare.brandIds";

const MODULE_META: Record<string, { title: string; href: string; icon: typeof ShoppingCart; accent: string }> = {
  sales: { title: "销售监控", href: "sales", icon: ShoppingCart, accent: "var(--accent)" },
  market: { title: "市场分析", href: "market-share", icon: ChartNoAxesCombined, accent: "var(--violet)" },
  hiring: { title: "招聘监控", href: "hiring", icon: BriefcaseBusiness, accent: "var(--warning)" },
  marketing: { title: "营销监控", href: "marketing", icon: Megaphone, accent: "var(--accent)" },
  voice: { title: "用户之声", href: "voice", icon: MessageSquareWarning, accent: "var(--danger)" },
  web: { title: "网页动态", href: "web", icon: Telescope, accent: "var(--body)" },
};

function savedMarketIds(): string[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(MARKET_STORAGE_KEY) || "[]");
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string") : [];
  } catch {
    return [];
  }
}

function statusLabel(status?: string): string {
  if (status === "ready") return "数据正常";
  if (status === "configured_no_data") return "已配置，等待数据";
  if (status === "needs_competitor") return "需要竞品";
  return "暂无数据";
}

function changeText(item?: any, suffix = "") {
  if (!item || item.delta == null) return "暂无环比";
  const value = Number(item.delta);
  return `${value > 0 ? "+" : ""}${fmtNum(value)}${suffix} · 环比${item.delta_pct == null ? "—" : `${value > 0 ? "+" : ""}${fmtNum(item.delta_pct)}%`}`;
}

function moduleValue(moduleKey: string, module: any): { label: string; value: string; hint?: string }[] {
  if (moduleKey === "sales") return [
    { label: "销售额(估)", value: fmtNum(module.total_revenue), hint: `${fmtNum(module.total_units)} 件` },
    { label: "变更 Listing", value: fmtNum(module.global_metrics?.changed_listings), hint: `数据点 ${fmtNum(module.data_points)}` },
  ];
  if (moduleKey === "market") return [
    { label: "当前份额", value: module.status !== "ready" || module.current?.share == null ? "—" : `${Number(module.current.share).toFixed(1)}%`, hint: module.status === "ready" && module.current?.rank ? `当前排名 #${module.current.rank}` : undefined },
    { label: "份额变化", value: module.status !== "ready" || module.current?.change_pp == null ? "—" : `${module.current.change_pp > 0 ? "+" : ""}${Number(module.current.change_pp).toFixed(1)}pp`, hint: module.status === "ready" && module.confidence ? `置信度 ${module.confidence.label || "—"}` : undefined },
  ];
  if (moduleKey === "hiring") return [
    { label: "在招职位", value: fmtNum(module.total_open) },
    { label: "新增 JD", value: fmtNum(module.released_in_window), hint: `下线 ${fmtNum(module.closed_in_window)}` },
  ];
  if (moduleKey === "marketing") return [
    { label: "营销记录", value: fmtNum(module.total) },
    { label: "广告样本", value: fmtNum(module.ads?.total), hint: `新增 ${fmtNum(module.ads?.new)} · 变化 ${fmtNum(module.ads?.changed)}` },
  ];
  if (moduleKey === "voice") return [
    { label: "反馈总量", value: fmtNum(module.totals?.total) },
    { label: "负向率", value: module.totals?.negative_rate == null ? "—" : `${(module.totals.negative_rate * 100).toFixed(1)}%`, hint: `P0 ${fmtNum(module.p0?.total)}` },
  ];
  return [
    { label: "变化次数", value: fmtNum(module.changed) },
    { label: "变化日", value: fmtNum(module.changed_days), hint: module.average_severity == null ? undefined : `平均严重度 ${(module.average_severity * 100).toFixed(0)}%` },
  ];
}

function ModuleTrend({ moduleKey, module, marketBrandIds }: { moduleKey: string; module: any; marketBrandIds: string[] }) {
  if (moduleKey === "sales" && module.trend?.length) return <SimpleLine data={module.trend} dataKey="revenue" name="销售额" color="var(--accent)" />;
  if (moduleKey === "hiring" && module.trend?.length) return <SimpleLine data={module.trend} dataKey="active" name="在招职位" color="var(--warning)" />;
  if (moduleKey === "marketing" && module.trend?.length) return <SimpleLine data={module.trend} dataKey="total" name="营销声量" color="var(--accent)" />;
  if (moduleKey === "voice" && module.trend?.length) return <SimpleLine data={module.trend} dataKey="negative" name="负向反馈" color="var(--danger)" />;
  if (moduleKey === "market" && module.trend?.length && marketBrandIds[0]) return <SimpleLine data={module.trend.map((point: any) => ({ date: point.date, share: point.shares?.[marketBrandIds[0]] }))} dataKey="share" name="份额" color="var(--violet)" />;
  if (moduleKey === "web" && module.trend?.length) return <SimpleLine data={module.trend} dataKey="changed" name="网页变化" color="var(--body)" />;
  return <div className="h-[200px] grid place-items-center text-[12px]" style={{ color: "var(--mute)" }}>暂无趋势数据</div>;
}

export default function Overview() {
  const { brandId } = useParams();
  const navigate = useNavigate();
  const [range] = useTimeRange();
  const { data: brands = [] } = useBrands();
  const marketBrandIds = useMemo(() => {
    const stored = savedMarketIds();
    if (brandId && stored.length >= 2 && stored.includes(brandId)) return stored;
    return Array.from(new Set([brandId, ...brands.filter((brand) => brand.is_competitor).map((brand) => brand.id)].filter(Boolean) as string[])).slice(0, 12);
  }, [brandId, brands]);
  const { data, isLoading, isError, refetch, isFetching } = useOverview(brandId, range, marketBrandIds);

  if (isLoading || !data) return <Spinner />;
  if (isError) return <Card><EmptyState title="经营总览加载失败" hint="请检查数据服务后重试。" action={<Button onClick={() => refetch()}>重新加载</Button>} /></Card>;

  const k = data.kpis || {};
  const modules = data.modules || {};
  const updatedValues = Object.values<any>(data.coverage || {}).map((item) => item?.last_data_at).filter(Boolean).sort();
  const lastUpdated = updatedValues[updatedValues.length - 1];

  return (
    <div className="space-y-6">
      <SectionTitle
        title="经营总览"
        subtitle={`经营驾驶舱 · ${rangeLabel(range)} · 销售、市场、招聘、营销与用户反馈`}
        action={<div className="flex items-center gap-2"><TimeRangePicker /><Button size="sm" onClick={() => refetch()} disabled={isFetching}><RefreshCw size={13} className={isFetching ? "animate-spin" : ""} />刷新数据</Button></div>}
      />

      <Card className="p-5">
        <SectionTitle title="智能总结" subtitle={lastUpdated ? `最近数据 ${String(lastUpdated).slice(0, 16).replace("T", " ")}` : "按当前时间范围自动整理经营变化"} />
        <ExecutiveSmartSummary brandId={brandId} range={range} marketBrandIds={marketBrandIds} />
      </Card>

      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        <StatCard label="销售额(估)" value={fmtNum(k.sales_revenue)} hint={changeText(k.sales)} tone="accent" />
        <StatCard label="销量(估)" value={fmtNum(k.sales_units)} hint={changeText(k.sales_units_comparison, " 件")} />
        <StatCard label="市场份额" value={k.market_share?.value == null ? "—" : `${Number(k.market_share.value).toFixed(1)}%`} hint={changeText(k.market_share, "pp")} tone="accent" />
        <StatCard label="在招职位" value={fmtNum(modules.hiring?.total_open)} hint={changeText(k.hiring_open)} />
        <StatCard label="营销声量" value={fmtNum(k.marketing_total)} hint={changeText(k.marketing)} />
        <StatCard label="负向反馈" value={fmtNum(k.voc_negative)} tone="negative" hint={changeText(k.negative_rate, " 个百分点")} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1.15fr_1fr] gap-4">
        <Card>
          <SectionTitle title="重点信号" subtitle="按优先级汇总各业务板块需要关注的变化" />
          {data.signals?.length ? <div className="space-y-2">
            {data.signals.map((signal: any, index: number) => (
              <button key={`${signal.domain}-${index}`} onClick={() => signal.href && navigate(signal.href)} className="w-full text-left flex items-start gap-3 p-3 rounded-md cursor-pointer" style={{ background: "var(--bg-soft-2)" }}>
                <Badge tone={signal.severity === "high" ? "negative" : signal.severity === "medium" ? "warning" : "neutral"}>{signal.severity === "high" ? "高" : signal.severity === "medium" ? "中" : "观察"}</Badge>
                <span className="min-w-0 flex-1"><span className="text-[13px] font-medium" style={{ color: "var(--ink)" }}>{signal.title}</span><span className="block text-[12px] mt-0.5" style={{ color: "var(--body)" }}>{signal.detail}</span></span>
                <ArrowUpRight size={14} className="shrink-0" style={{ color: "var(--mute)" }} />
              </button>
            ))}
          </div> : <EmptyState title="当前没有高优先级信号" hint="继续保持数据采集，后续变化会自动出现在这里。" />}
        </Card>
        <Card>
          <SectionTitle title="昨日速览" subtitle={`周期末日前一天 · ${data.yesterday?.date || "—"}`} />
          <div className="grid grid-cols-2 gap-3">
            <StatCard label="销售额(估)" value={fmtNum(data.yesterday?.sales_revenue)} />
            <StatCard label="销量(估)" value={fmtNum(data.yesterday?.sales_units)} />
            <StatCard label="营销记录" value={fmtNum(data.yesterday?.marketing_total)} />
            <StatCard label="负向反馈" value={fmtNum(data.yesterday?.voc_negative)} tone="negative" />
          </div>
          {!data.yesterday?.has_data && <p className="text-[12px] mt-3" style={{ color: "var(--mute)" }}>该日期暂无已采集数据。</p>}
        </Card>
      </div>

      <div>
        <SectionTitle title="业务板块概览" subtitle="点击卡片进入对应业务明细；各趋势图使用本板块自己的指标口径" />
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {Object.entries(MODULE_META).map(([moduleKey, meta]) => {
            const module = modules[moduleKey] || {};
            const Icon = meta.icon;
            return (
              <Card key={moduleKey} className="cursor-pointer hover:opacity-95" onClick={() => navigate(`/brand/${brandId}/${meta.href}`)}>
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-2"><span className="w-8 h-8 rounded-md grid place-items-center" style={{ background: "var(--bg-soft-2)", color: meta.accent }}><Icon size={16} /></span><div><div className="text-[15px] font-medium" style={{ color: "var(--ink)" }}>{meta.title}</div><div className="text-[11px] mt-0.5" style={{ color: "var(--mute)" }}>{statusLabel(module.status)}</div></div></div>
                  <ArrowUpRight size={15} style={{ color: "var(--mute)" }} />
                </div>
                <div className="grid grid-cols-2 gap-3 mt-4">
                  {moduleValue(moduleKey, module).map((item) => <div key={item.label}><div className="text-[11px]" style={{ color: "var(--mute)" }}>{item.label}</div><div className="text-[22px] font-semibold tabular-nums mt-0.5" style={{ color: "var(--ink)" }}>{item.value}</div>{item.hint && <div className="text-[11px] mt-0.5" style={{ color: "var(--mute)" }}>{item.hint}</div>}</div>)}
                </div>
                <div className="mt-3 -mx-1"><ModuleTrend moduleKey={moduleKey} module={module} marketBrandIds={marketBrandIds} /></div>
              </Card>
            );
          })}
        </div>
      </div>

      <Card>
        <SectionTitle title="数据覆盖" subtitle="总览只读取已采集数据，不会因为打开页面而触发采集" />
        <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-2">
          {Object.entries(data.coverage || {}).map(([key, item]: [string, any]) => <div key={key} className="p-2.5 rounded-md" style={{ background: "var(--bg-soft-2)" }}><div className="text-[12px] font-medium" style={{ color: "var(--ink)" }}>{MODULE_META[key]?.title || key}</div><div className="text-[11px] mt-1" style={{ color: item.status === "ready" ? "var(--accent)" : "var(--mute)" }}>{statusLabel(item.status)}</div></div>)}
        </div>
      </Card>
    </div>
  );
}
