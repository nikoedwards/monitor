import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { AlertTriangle, Plus, RefreshCw, SlidersHorizontal, X } from "lucide-react";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { TimeRangePicker } from "../components/TimeRangePicker";
import { MultiLineChart } from "../components/charts";
import { Badge, Button, Card, EmptyState, InfoHint, Modal, SectionTitle, Select, Spinner, StatCard } from "../components/ui";
import { fmtNum } from "../lib/format";
import { useBrands, useMarketShare, useMarketShareTrend, useRefreshMarketShareData } from "../lib/hooks";
import { useTimeRange } from "../lib/timeRange";
import { MarketShareBreakdown } from "./MarketShareObservations";

const STORAGE_KEY = "monitor.marketShare.brandIds";
const COLORS = ["#0070f3", "#7928ca", "#29bc9b", "#f5a623", "#ff0080", "#6b7280", "#ef4444", "#14b8a6"];
const SIGNALS = [
  { key: "app", label: "真实下载量" },
  { key: "conversation", label: "应用商店评分数" },
] as const;

const COUNTRY_LABELS: Record<string, string> = {
  AU: "澳大利亚", BR: "巴西", CA: "加拿大", CN: "中国", DE: "德国", ES: "西班牙",
  FR: "法国", GB: "英国", HK: "中国香港", ID: "印度尼西亚", IN: "印度", IT: "意大利",
  JP: "日本", KR: "韩国", MX: "墨西哥", NL: "荷兰", SG: "新加坡", TH: "泰国",
  TW: "中国台湾", US: "美国", VN: "越南",
};

function countryLabel(code: string): string {
  return COUNTRY_LABELS[code] ? `${COUNTRY_LABELS[code]}（${code}）` : code;
}

function initialSavedBrands(): string[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string") : [];
  } catch {
    return [];
  }
}

function confidenceTone(label?: string): "positive" | "warning" | "negative" {
  if (label === "高") return "positive";
  if (label === "中") return "warning";
  return "negative";
}

export default function MarketShare() {
  const { brandId } = useParams();
  const navigate = useNavigate();
  const [range] = useTimeRange();
  const { data: brands = [], isLoading: brandsLoading } = useBrands();
  const [selected, setSelected] = useState<string[]>(initialSavedBrands);
  const [draft, setDraft] = useState<string[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [params, setParams] = useSearchParams();
  const country = params.get("country") || "all";
  const setCountry = (code: string) => {
    const next = new URLSearchParams(params);
    if (code === "all") next.delete("country");
    else next.set("country", code);
    setParams(next, { replace: true });
  };
  const [trendFocus, setTrendFocus] = useState("all");
  const autoRefreshKeys = useRef(new Set<string>());
  const refreshAppData = useRefreshMarketShareData();

  useEffect(() => {
    if (!brands.length) return;
    const validIds = new Set(brands.map((brand) => brand.id));
    setSelected((current) => {
      const valid = current.filter((id) => validIds.has(id));
      if (valid.length >= 2) return valid;
      const preferred = brands
        .filter((brand) => brand.id === brandId || brand.is_primary || brand.is_competitor)
        .map((brand) => brand.id);
      return Array.from(new Set([...preferred, ...brands.map((brand) => brand.id)])).slice(0, Math.min(6, brands.length));
    });
  }, [brands, brandId]);

  useEffect(() => {
    if (selected.length) localStorage.setItem(STORAGE_KEY, JSON.stringify(selected));
  }, [selected]);

  const selectedBrands = useMemo(
    () => selected.map((id) => brands.find((brand) => brand.id === id)).filter(Boolean),
    [brands, selected],
  );
  const query = useMarketShare(selected, "balanced", country, range);
  const trendQuery = useMarketShareTrend(selected, "balanced", country, range);
  const result = query.data;
  const selectedKey = useMemo(() => [...selected].sort().join(","), [selected]);
  const appDataMissing = Boolean(result?.brands.some(
    (row) => !row.signals.app && !row.signals.conversation,
  ));

  useEffect(() => {
    if (!appDataMissing || selected.length < 2 || refreshAppData.isPending || autoRefreshKeys.current.has(selectedKey)) return;
    autoRefreshKeys.current.add(selectedKey);
    refreshAppData.mutate(selected);
  }, [appDataMissing, refreshAppData, selected, selectedKey]);

  useEffect(() => {
    if (trendFocus !== "all" && !selected.includes(trendFocus)) setTrendFocus("all");
  }, [selected, trendFocus]);

  const countryOptions = useMemo(
    () => Array.from(new Set([...(result?.countries || []), ...(country === "all" ? [] : [country])])).sort(),
    [result?.countries, country],
  );
  const leader = result?.brands.find((row) => row.share > 0);
  const appRatingCount = result?.brands.reduce((sum, row) => sum + row.raw.app_reviews, 0) || 0;
  const activeSignals = result ? SIGNALS.filter((signal) => result.model.active_weights[signal.key] > 0) : [];
  const trendData = useMemo(
    () => (trendQuery.data?.points || []).map((point) => ({ date: point.date, ...point.shares })),
    [trendQuery.data?.points],
  );
  const trendKeys = useMemo(
    () => selectedBrands
      .map((brand, index) => ({ brand, color: COLORS[index % COLORS.length] }))
      .filter(({ brand }) => brand && (trendFocus === "all" || brand.id === trendFocus))
      .map(({ brand, color }) => ({ key: brand!.id, name: brand!.name, color })),
    [selectedBrands, trendFocus],
  );
  const trendSummary = useMemo(
    () => (trendQuery.data?.summary || []).filter((row) => trendFocus === "all" || row.brand_id === trendFocus),
    [trendFocus, trendQuery.data?.summary],
  );

  if (brandsLoading) return <Spinner />;
  if (brands.length < 2) {
    return (
      <EmptyState
        title="至少需要 2 个已配置品牌"
        hint="市占分析只从品牌管理中的品牌选择，请先补充自家品牌和竞品。"
        action={<Button variant="primary" onClick={() => navigate("/brands")}>去品牌管理</Button>}
      />
    );
  }

  return (
    <div className="space-y-6">
      <SectionTitle
        title="市占分析"
        subtitle="使用应用商店累计评分次数计算样本份额，有可比较的实采下载量时一并计入"
        hint="这是监测样本内的可解释估算，不是第三方机构发布的官方全行业市占。"
        action={
          <div className="flex items-center gap-2 flex-wrap justify-end">
            <Select value={country} onChange={(event) => setCountry(event.target.value)} aria-label="国家">
              <option value="all">全部国家</option>
              {countryOptions.map((code) => <option key={code} value={code}>{countryLabel(code)}</option>)}
            </Select>
            <TimeRangePicker />
          </div>
        }
      />

      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[12px] uppercase tracking-wide mr-1" style={{ color: "var(--mute)" }}>分析品牌</span>
        {selectedBrands.map((brand) => brand && (
          <span key={brand.id} className="inline-flex items-center gap-1.5 h-8 px-2.5 rounded-md text-[13px]" style={{ background: "var(--bg-soft-2)", color: "var(--ink)", border: "1px solid var(--hairline)" }}>
            {brand.name}
            {brand.is_primary ? <Badge tone="positive">自家</Badge> : brand.is_competitor ? <Badge tone="warning">竞品</Badge> : null}
            {selected.length > 2 && (
              <button onClick={() => setSelected((ids) => ids.filter((id) => id !== brand.id))} title="移除" className="cursor-pointer" style={{ color: "var(--mute)" }}>
                <X size={13} />
              </button>
            )}
          </span>
        ))}
        <Button
          size="sm"
          onClick={() => {
            setDraft(selected);
            setPickerOpen(true);
          }}
        >
          <Plus size={14} /> 添加分析品牌
        </Button>
        <Button
          size="sm"
          disabled={selected.length < 2 || refreshAppData.isPending}
          onClick={() => refreshAppData.mutate(selected)}
        >
          <RefreshCw size={14} className={refreshAppData.isPending ? "animate-spin" : ""} />
          {refreshAppData.isPending ? "正在获取 App 数据" : "刷新 App 数据"}
        </Button>
      </div>

      {query.isLoading && <Spinner />}
      {refreshAppData.isError && (
        <Card>
          <div className="text-[13px]" style={{ color: "var(--danger)" }}>
            App 数据获取失败：{refreshAppData.error?.message || "请稍后重试"}
          </div>
        </Card>
      )}
      {query.isError && (
        <Card>
          <div className="text-[14px]" style={{ color: "var(--danger)" }}>{query.error?.message || "市占估算失败"}</div>
        </Card>
      )}

      {result && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <StatCard label="领先品牌" value={leader?.name || "—"} hint={leader ? `${result.country === "all" ? "全部国家" : countryLabel(result.country)} · ${leader.share.toFixed(1)}%` : undefined} tone="accent" />
            <StatCard
              label="模型置信度"
              labelHint={<InfoHint align="right" symbol="!" label="查看模型置信度说明" text="系统根据评级与下载数据的覆盖情况、品牌间可比性和采集证据数量衡量证据完整度。" />}
              value={`${result.confidence.score}%`}
              hint={`${result.confidence.label} · ${result.confidence.evidence_total} 条证据`}
            />
            <StatCard
              label="可用信号"
              labelHint={<InfoHint align="right" symbol="!" label="查看可用信号说明" text="表示本次实际参与计算的基础信号数量。2 类信号是 App 下载信号和 App Store 累计评分数；缺失信号不会按 0 强行计入，而是重新分配剩余权重。" />}
              value={`${activeSignals.length}/2`}
              hint={activeSignals.map(({ label }) => label).join(" + ") || "缺少可比较的 App 数据"}
            />
            <StatCard label="应用商店评分数" value={appRatingCount ? fmtNum(appRatingCount) : "—"} hint={`${result.country === "all" ? "全部国家" : countryLabel(result.country)} · 商店累计值；非所选期间新增`} />
          </div>

          {result.warnings.length > 0 && (
            <div className="rounded-lg px-4 py-3 flex items-start gap-2.5" style={{ background: "var(--warning-soft)", color: "var(--body)", border: "1px solid var(--hairline)" }}>
              <AlertTriangle size={16} className="shrink-0 mt-0.5" style={{ color: "var(--warning)" }} />
              <div className="text-[13px] space-y-1">
                {result.warnings.map((warning) => <div key={warning}>{warning}</div>)}
              </div>
            </div>
          )}

          <Card>
            <SectionTitle
              title="每日样本份额趋势"
              subtitle={trendQuery.data?.latest_date
                ? `${result.country === "all" ? "全部国家" : countryLabel(result.country)} · 已记录至 ${trendQuery.data.latest_date}`
                : "每天采集 App 数据后生成国家 × 品牌快照"}
              action={
                <Select value={trendFocus} onChange={(event) => setTrendFocus(event.target.value)} aria-label="趋势品牌">
                  <option value="all">全部品牌趋势</option>
                  {selectedBrands.map((brand) => brand && <option key={brand.id} value={brand.id}>{brand.name}</option>)}
                </Select>
              }
            />
            {trendQuery.isLoading ? <Spinner /> : trendQuery.isError ? (
              <div className="text-[13px]" style={{ color: "var(--danger)" }}>{trendQuery.error?.message || "市占趋势加载失败"}</div>
            ) : trendData.length ? (
              <>
                <MultiLineChart data={trendData} keys={trendKeys} valueFormatter={(value) => `${Number(value).toFixed(1)}%`} />
                <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 mt-3">
                  {trendSummary.map((row) => {
                    const positive = row.change_pp > 0;
                    const negative = row.change_pp < 0;
                    return (
                      <div key={row.brand_id} className="rounded-md p-3" style={{ background: "var(--bg-soft)", border: "1px solid var(--hairline)" }}>
                        <div className="text-[12px] truncate" style={{ color: "var(--mute)" }}>{row.name}</div>
                        <div className="flex items-baseline justify-between gap-2 mt-1">
                          <span className="text-[20px] font-semibold tabular-nums" style={{ color: "var(--ink)" }}>{row.latest_share.toFixed(1)}%</span>
                          <span className="text-[12px] font-medium tabular-nums" style={{ color: positive ? "var(--accent)" : negative ? "var(--danger)" : "var(--mute)" }}>
                            {positive ? "+" : ""}{row.change_pp.toFixed(1)}pp
                          </span>
                        </div>
                        <div className="text-[11px] mt-1" style={{ color: "var(--mute)" }}>
                          期初 {row.start_share.toFixed(1)}% · 公开评分 {fmtNum(row.latest_rating_count)}
                          {row.rating_count_change > 0 ? `（+${fmtNum(row.rating_count_change)}）` : ""}
                        </div>
                      </div>
                    );
                  })}
                </div>
                <div className="text-[11px] mt-3" style={{ color: "var(--mute)" }}>
                  每日自动刷新；某天未获得新商店数据时沿用最近一次快照，并在下一次成功采集后更新当天值。
                </div>
              </>
            ) : (
              <div className="text-[13px] py-8 text-center" style={{ color: "var(--mute)" }}>尚无历史快照，完成首次每日采集或点击“刷新 App 数据”后开始记录趋势。</div>
            )}
          </Card>

        </>
      )}

      <div className={result ? "grid grid-cols-1 2xl:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] gap-4" : undefined}>
        {result && (
          <Card>
            <SectionTitle title="App 评分样本份额" subtitle={`${result.country === "all" ? "全部国家" : countryLabel(result.country)} · ${result.range.start} 至 ${result.range.end}`} />
            <div className="grid grid-cols-1 md:grid-cols-[260px_1fr] gap-5 items-center">
              <div className="h-[260px] relative">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie data={result.brands} dataKey="share" nameKey="name" innerRadius={66} outerRadius={100} paddingAngle={2} stroke="none">
                      {result.brands.map((row, index) => <Cell key={row.brand_id} fill={COLORS[index % COLORS.length]} />)}
                    </Pie>
                    <Tooltip formatter={(value) => [`${Number(value).toFixed(2)}%`, "估算份额"]} />
                  </PieChart>
                </ResponsiveContainer>
                <div className="absolute inset-0 pointer-events-none grid place-items-center text-center">
                  <div>
                    <div className="text-[12px]" style={{ color: "var(--mute)" }}>领先份额</div>
                    <div className="text-[30px] font-semibold tabular-nums" style={{ color: "var(--ink)" }}>{leader ? `${leader.share.toFixed(1)}%` : "—"}</div>
                  </div>
                </div>
              </div>
              <div className="space-y-3">
                {result.brands.map((row, index) => (
                  <div key={row.brand_id}>
                    <div className="flex items-center justify-between gap-3 text-[13px] mb-1.5">
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="h-2.5 w-2.5 rounded-full shrink-0" style={{ background: COLORS[index % COLORS.length] }} />
                        <span className="font-medium truncate" style={{ color: "var(--ink)" }}>{row.rank}. {row.name}</span>
                      </div>
                      <span className="font-semibold tabular-nums" style={{ color: "var(--ink)" }}>{activeSignals.some(({ key }) => row.signals[key] > 0) ? `${row.share.toFixed(2)}%` : "缺数据"}</span>
                    </div>
                    <div className="h-1.5 rounded-full overflow-hidden" style={{ background: "var(--bg-soft-2)" }}>
                      <div className="h-full rounded-full" style={{ width: `${Math.max(row.share, 0.5)}%`, background: COLORS[index % COLORS.length] }} />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </Card>
        )}
        {selected.length > 0 && <MarketShareBreakdown brandIds={selected} country={country} range={range} result={result} />}
      </div>

      {result && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Card>
            <SectionTitle title="估算模型" subtitle={result.model.description} />
            <div className="space-y-3">
              {activeSignals.map((signal) => {
                const active = result.model.active_weights[signal.key] * 100;
                return (
                  <div key={signal.key}>
                    <div className="flex justify-between text-[13px] mb-1">
                      <span style={{ color: "var(--body)" }}>{signal.label}</span>
                      <span className="tabular-nums" style={{ color: "var(--ink)" }}>{active.toFixed(0)}%</span>
                    </div>
                    <div className="h-2 rounded-full overflow-hidden" style={{ background: "var(--bg-soft-2)" }}>
                      <div className="h-full rounded-full" style={{ width: `${active}%`, background: "var(--accent)" }} />
                    </div>
                  </div>
                );
              })}
            </div>
            <div className="mt-4 p-3 rounded-md text-[12px] leading-relaxed" style={{ background: "var(--bg-soft)", color: "var(--mute)" }}>
              每项数据先计算所选品牌中的占比，再按本次生效的权重合成。Google Play 全球评分在“全部国家”中计入一次。
            </div>
          </Card>

          <Card>
            <SectionTitle title="数据完整性" subtitle="补齐这些数据可明显提升估算可信度" />
            <div className="space-y-4">
              {result.brands.map((row) => (
                <div key={row.brand_id} className="pb-4 last:pb-0" style={{ borderBottom: "1px solid var(--hairline)" }}>
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <span className="font-medium text-[13px]" style={{ color: "var(--ink)" }}>{row.name}</span>
                    <Badge tone={row.gaps.length <= 1 ? "positive" : row.gaps.length <= 2 ? "warning" : "negative"}>{row.coverage}% 覆盖</Badge>
                  </div>
                  {row.gaps.length ? (
                    <div className="flex flex-wrap gap-1.5">
                      {row.gaps.map((gap) => <span key={gap} className="text-[12px] px-2 py-1 rounded" style={{ background: "var(--bg-soft-2)", color: "var(--mute)" }}>{gap}</span>)}
                    </div>
                  ) : (
                    <div className="text-[12px]" style={{ color: "var(--accent)" }}>核心信号已覆盖</div>
                  )}
                  </div>
                ))}
              </div>
            </Card>
          </div>
      )}

      <Modal open={pickerOpen} onClose={() => setPickerOpen(false)} title="选择市占分析品牌" width={520}>
        <div className="space-y-2 max-h-[52vh] overflow-y-auto pr-1">
          {brands.map((brand) => {
            const checked = draft.includes(brand.id);
            return (
              <label key={brand.id} className="flex items-center gap-3 p-3 rounded-md cursor-pointer" style={{ background: checked ? "var(--bg-soft-2)" : "transparent", border: "1px solid var(--hairline)" }}>
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => setDraft((ids) => checked ? ids.filter((id) => id !== brand.id) : [...ids, brand.id])}
                />
                <span className="h-8 w-8 rounded-md grid place-items-center text-[13px] font-semibold" style={{ background: "var(--ink)", color: "var(--bg)" }}>{brand.name.slice(0, 1).toUpperCase()}</span>
                <span className="flex-1 min-w-0">
                  <span className="block font-medium truncate" style={{ color: "var(--ink)" }}>{brand.name}</span>
                  <span className="block text-[12px] truncate" style={{ color: "var(--mute)" }}>{brand.category || "未设置品类"}</span>
                </span>
                {brand.is_primary ? <Badge tone="positive">自家</Badge> : brand.is_competitor ? <Badge tone="warning">竞品</Badge> : null}
              </label>
            );
          })}
        </div>
        <div className="flex items-center justify-between gap-3 mt-4 pt-4" style={{ borderTop: "1px solid var(--hairline)" }}>
          <Button variant="ghost" onClick={() => navigate("/brands")}><SlidersHorizontal size={14} /> 管理品牌</Button>
          <div className="flex items-center gap-2">
            <span className="text-[12px]" style={{ color: draft.length < 2 ? "var(--danger)" : "var(--mute)" }}>已选 {draft.length} 个，至少 2 个</span>
            <Button onClick={() => setPickerOpen(false)}>取消</Button>
            <Button variant="primary" disabled={draft.length < 2} onClick={() => { setSelected(draft); setPickerOpen(false); }}>应用</Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
