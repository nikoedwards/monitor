import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, Compass, RefreshCw, Sparkles } from "lucide-react";
import { api, qs, type CompareFinding as Finding, type StrategySummary } from "../lib/api";
import { type TimeRange } from "../lib/timeRange";
import { AsyncProgress, Badge, Button, Card } from "./ui";

type StrategyReply = (StrategySummary | { mode?: undefined; cache_miss?: true }) & {
  generation?: { status: "running" | "completed" | "failed"; error?: string };
};

function keepPreviousSummary(previous: StrategyReply | undefined, next: StrategyReply): StrategyReply {
  if (previous?.mode && !next.mode && next.generation) {
    return { ...previous, generation: next.generation };
  }
  return next;
}

function FindingCard({ item }: { item: Finding }) {
  const confidence = { low: "初步判断", medium: "中等置信", high: "较高置信" };
  return (
    <article className="rounded-lg p-4 space-y-2 min-w-0" style={{ background: "var(--bg-soft)", border: "1px solid var(--hairline)" }}>
      <div className="flex items-start justify-between gap-2">
        <h4 className="text-[14px] font-medium" style={{ color: "var(--ink)" }}>{item.title}</h4>
        <span className="shrink-0 text-[11px]" style={{ color: "var(--mute)" }}>{confidence[item.confidence]}</span>
      </div>
      <p className="text-[13px] leading-relaxed" style={{ color: "var(--body)" }}>{item.observation}</p>
      {item.implication && <p className="text-[13px] leading-relaxed" style={{ color: "var(--ink)" }}><span className="font-medium">战略含义：</span>{item.implication}</p>}
      {item.next_signal && <p className="text-[12px] leading-relaxed" style={{ color: "var(--mute)" }}>下一步验证：{item.next_signal}</p>}
      <details className="text-[12px] pt-1">
        <summary className="cursor-pointer" style={{ color: "var(--accent)" }}>查看依据 · {item.evidence.length} 条</summary>
        <ul className="mt-2 space-y-3">
          {item.evidence.map((ref) => (
            <li key={ref.id} className="min-w-0" style={{ color: "var(--body)" }}>
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px]" style={{ color: "var(--mute)" }}>
                <b>{ref.brand}</b><span>{ref.at?.slice(0, 10)}</span><span>{ref.platform}</span>
                <span>{ref.period === "previous" ? "前期对照" : ref.period === "active_in_window" ? "本期持续可见" : "本期"}</span>
              </div>
              {ref.url ? <a href={ref.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 hover:underline break-words" style={{ color: "var(--accent)" }}>{ref.title}<ArrowUpRight size={12} className="shrink-0" /></a> : <span>{ref.title}</span>}
              {ref.detail && <p className="mt-0.5 line-clamp-3 break-words">{ref.detail}</p>}
            </li>
          ))}
        </ul>
      </details>
    </article>
  );
}

function FindingSection({ title, items, empty, stacked = false }: { title: string; items: Finding[]; empty: string; stacked?: boolean }) {
  return (
    <section className="space-y-2">
      <h3 className="text-[14px] font-semibold" style={{ color: "var(--ink)" }}>{title}</h3>
      {items.length ? <div className={`grid grid-cols-1 ${stacked ? "" : "xl:grid-cols-2"} gap-3`}>{items.map((item, index) => <FindingCard key={index} item={item} />)}</div> : <p className="text-[13px]" style={{ color: "var(--mute)" }}>{empty}</p>}
    </section>
  );
}

export function CompareSummary({ range, brandIds, preview }: { range: TimeRange; brandIds: string[]; preview?: StrategySummary }) {
  const qc = useQueryClient();
  const queryKey = ["compare-strategy-v1", range.start_date, range.end_date, [...brandIds].sort().join(",")];
  const params = { start_date: range.start_date, end_date: range.end_date };
  const endpoint = "/api/compare/summary";
  const cached = useQuery({
    queryKey,
    queryFn: async () => {
      const previous = qc.getQueryData<StrategyReply>(queryKey);
      const next = await api.get<StrategyReply>(`${endpoint}${qs({ ...params, background: true })}`);
      if (previous?.generation?.status === "running" && !next.generation) {
        return keepPreviousSummary(previous, {
          ...next, generation: { status: "failed", error: "生成任务已中断，请重试。" },
        });
      }
      return keepPreviousSummary(previous, next);
    },
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    refetchInterval: (query) => query.state.data?.generation?.status === "running" ? 3000 : false,
    retry: 2,
  });
  const generate = useMutation({
    mutationKey: queryKey,
    onMutate: () => qc.cancelQueries({ queryKey, exact: true }),
    mutationFn: () => api.get<StrategyReply>(`${endpoint}${qs({ ...params, refresh: true, background: true })}`),
    onSuccess: (result) => qc.setQueryData<StrategyReply>(queryKey, (previous) => keepPreviousSummary(previous, result)),
  });
  const saved = cached.data?.mode ? cached.data : preview;
  const matchesBrands = saved?.brands?.map(brand => brand.brand_id).sort().join(",") === [...brandIds].sort().join(",");
  const data = matchesBrands ? saved : undefined;
  const error = generate.error?.message || cached.error?.message || cached.data?.generation?.error;
  const pending = generate.isPending || cached.data?.generation?.status === "running";

  return (
    <Card className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Compass size={19} style={{ color: "var(--violet)" }} />
            <h2 className="text-[18px] font-semibold tracking-tight" style={{ color: "var(--ink)" }}>AI 行业战略总结</h2>
            {data?.mode === "ai" && <Badge tone="accent">跨品牌研判</Badge>}
          </div>
          <p className="text-[12px] mt-1.5" style={{ color: "var(--mute)" }}>{range.start_date} 至 {range.end_date} · 覆盖全部 {brandIds.length} 个品牌 · 营销 / 用户反馈 / 销售 / 招聘 / 官网</p>
        </div>
        <div className="flex flex-col items-end">
          <Button size="sm" variant="primary" disabled={pending || cached.isFetching} onClick={() => generate.mutate()}>
            {pending ? <RefreshCw size={14} className="animate-spin" /> : <Sparkles size={14} />}
            {pending ? "正在分析行业…" : data ? "重新生成" : "AI 生成总结"}
          </Button>
          <AsyncProgress active={pending} label="正在关联各品牌与各板块证据" />
        </div>
      </div>

      {error && <p role="alert" className="text-[13px]" style={{ color: "var(--danger)" }}>{error}</p>}
      {!data && !pending && <div className="rounded-lg p-4" style={{ background: "var(--bg-soft)" }}>
        <p className="text-[14px] font-medium" style={{ color: "var(--ink)" }}>这段时间，行业的竞争重心发生了什么变化？</p>
        <p className="text-[13px] leading-relaxed mt-1" style={{ color: "var(--mute)" }}>{cached.isFetching ? "正在读取此时间范围的总结…" : "结合各品牌的真实动作和前期对照，梳理行业共性、品牌策略分化、机会与风险。切换时间后，分析范围同步更新。"}</p>
      </div>}
      {pending && <p role="status" className="text-[13px]" style={{ color: "var(--mute)" }}>正在对照所选周期与前期数据，连接品牌动作与行业信号…{data ? "下方保留本周期上次生成的结果。" : ""}</p>}

      {data && <div className="space-y-5" aria-live="polite">
        <div className="rounded-lg p-4" style={{ background: "var(--bg-soft-2)", borderLeft: "3px solid var(--violet)" }}>
          <div className="text-[12px] font-medium mb-1.5" style={{ color: "var(--violet)" }}>行业主线</div>
          <p className="text-[14px] leading-relaxed whitespace-pre-line" style={{ color: "var(--ink)" }}>{data.summary}</p>
          <p className="text-[11px] mt-2" style={{ color: "var(--mute)" }}>前期对照：{data.range.previous_start} 至 {data.range.previous_end} · 抽样分析 {data.evidence_count} 条证据{data.generated_at ? ` · 生成于 ${new Date(data.generated_at).toLocaleString("zh-CN")}` : ""}</p>
        </div>
        <FindingSection title="行业共性变化" items={data.industry_changes} empty="本期尚无足够跨品牌证据支持共同趋势。" />
        <section className="space-y-2">
          <h3 className="text-[14px] font-semibold" style={{ color: "var(--ink)" }}>品牌策略分化</h3>
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
            {data.brands.map((brand) => {
              const move = data.brand_moves.find((item) => item.brand_id === brand.brand_id);
              const covered = brand.coverage.filter((item) => item.evidence_count > 0);
              return <div key={brand.brand_id} className="space-y-2 min-w-0">
                <div className="flex flex-wrap items-center gap-2 text-[13px]">
                  <span className="font-medium" style={{ color: "var(--ink)" }}>{brand.name}</span>
                  {brand.is_primary && <Badge tone="positive">自家</Badge>}
                  <span className="text-[11px]" style={{ color: "var(--mute)" }}>{covered.length ? covered.map((item) => item.label).join(" · ") : "本期缺少证据"}</span>
                </div>
                {move ? <FindingCard item={move} /> : <p className="text-[13px] p-4 rounded-lg" style={{ color: "var(--mute)", background: "var(--bg-soft)" }}>{covered.length ? "已纳入本期分析，尚不足以形成独立的品牌策略判断。" : "所选周期暂无足够证据，无法判断品牌策略变化。"}</p>}
              </div>;
            })}
          </div>
        </section>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          <FindingSection title="战略机会" items={data.opportunities} empty="暂无足够证据支持明确的战略机会。" stacked />
          <FindingSection title="竞争风险" items={data.risks} empty="暂无足够证据支持明确的竞争风险。" stacked />
        </div>
        <details className="text-[12px]" style={{ color: "var(--mute)" }}>
          <summary className="cursor-pointer">数据范围与判断边界</summary>
          <ul className="mt-2 space-y-1 list-disc pl-4">{data.caveats.map((item, index) => <li key={index}>{item}</li>)}</ul>
        </details>
      </div>}
    </Card>
  );
}
