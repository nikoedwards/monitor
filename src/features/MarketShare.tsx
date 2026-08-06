import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { AlertTriangle, Plus, RefreshCw, SlidersHorizontal, X } from "lucide-react";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { TimeRangePicker } from "../components/TimeRangePicker";
import { Badge, Button, Card, EmptyState, Modal, SectionTitle, Select, Spinner, StatCard } from "../components/ui";
import { fmtNum } from "../lib/format";
import { useBrands, useMarketShare, useRefreshMarketShareData } from "../lib/hooks";
import { useTimeRange } from "../lib/timeRange";
import type { MarketShareBrand } from "../lib/api";

const STORAGE_KEY = "monitor.marketShare.brandIds";
const COLORS = ["#0070f3", "#7928ca", "#29bc9b", "#f5a623", "#ff0080", "#6b7280", "#ef4444", "#14b8a6"];
const SIGNALS = [
  { key: "app", label: "App 下载" },
  { key: "conversation", label: "App 评论" },
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

function downloadText(row: MarketShareBrand): string {
  const raw = row.raw;
  if (!raw.app_downloads_est) return "—";
  if (raw.app_download_basis === "observed") return `${fmtNum(raw.app_downloads_est)}（采集值）`;
  return `${fmtNum(raw.app_downloads_est)}（${fmtNum(raw.app_downloads_low)}–${fmtNum(raw.app_downloads_high)}）`;
}

function salesText(row: MarketShareBrand): string {
  if (row.raw.sales_revenue) return `¥${fmtNum(row.raw.sales_revenue)}`;
  if (row.raw.sales_units) return `${fmtNum(row.raw.sales_units)} 件`;
  return "—";
}

export default function MarketShare() {
  const { brandId } = useParams();
  const navigate = useNavigate();
  const [range] = useTimeRange();
  const { data: brands = [], isLoading: brandsLoading } = useBrands();
  const [selected, setSelected] = useState<string[]>(initialSavedBrands);
  const [draft, setDraft] = useState<string[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [country, setCountry] = useState("all");
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
  const result = query.data;
  const selectedKey = useMemo(() => [...selected].sort().join(","), [selected]);
  const appDataMissing = Boolean(result?.brands.some(
    (row) => row.raw.app_download_basis === "unavailable",
  ));

  useEffect(() => {
    if (!appDataMissing || selected.length < 2 || refreshAppData.isPending || autoRefreshKeys.current.has(selectedKey)) return;
    autoRefreshKeys.current.add(selectedKey);
    refreshAppData.mutate(selected);
  }, [appDataMissing, refreshAppData, selected, selectedKey]);

  const countryOptions = useMemo(
    () => Array.from(new Set([...(result?.countries || []), ...(country === "all" ? [] : [country])])).sort(),
    [result?.countries, country],
  );
  const leader = result?.brands[0];
  const appDownloads = result?.brands.reduce((sum, row) => sum + row.raw.app_downloads_est, 0) || 0;
  const activeSignals = result ? SIGNALS.filter((signal) => result.model.active_weights[signal.key] > 0).length : 0;

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
        subtitle="按国家，仅根据 App 下载估算和 App Store 评论数计算相对份额"
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
            <StatCard label="模型置信度" value={`${result.confidence.score}%`} hint={`${result.confidence.label} · ${result.confidence.evidence_total} 条证据`} />
            <StatCard label="可用信号" value={`${activeSignals}/2`} hint="App 下载估算 + App Store 评论" />
            <StatCard label="App 下载估算" value={appDownloads ? fmtNum(appDownloads) : "—"} hint={`${result.country === "all" ? "全部国家" : countryLabel(result.country)} · 所选品牌合计`} />
          </div>

          {result.warnings.length > 0 && (
            <div className="rounded-lg px-4 py-3 flex items-start gap-2.5" style={{ background: "var(--warning-soft)", color: "var(--body)", border: "1px solid var(--hairline)" }}>
              <AlertTriangle size={16} className="shrink-0 mt-0.5" style={{ color: "var(--warning)" }} />
              <div className="text-[13px] space-y-1">
                {result.warnings.map((warning) => <div key={warning}>{warning}</div>)}
              </div>
            </div>
          )}

          <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)] gap-4">
            <Card>
              <SectionTitle title="综合估算份额" subtitle={`${result.country === "all" ? "全部国家" : countryLabel(result.country)} · ${result.range.start} 至 ${result.range.end}`} />
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
                      <div className="text-[30px] font-semibold tabular-nums" style={{ color: "var(--ink)" }}>{leader?.share.toFixed(1)}%</div>
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
                        <span className="font-semibold tabular-nums" style={{ color: "var(--ink)" }}>{row.share.toFixed(2)}%</span>
                      </div>
                      <div className="h-1.5 rounded-full overflow-hidden" style={{ background: "var(--bg-soft-2)" }}>
                        <div className="h-full rounded-full" style={{ width: `${Math.max(row.share, 0.5)}%`, background: COLORS[index % COLORS.length] }} />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </Card>

            <Card>
              <SectionTitle title="份额拆解" subtitle="每列先在所选品牌内归一化，再按模型权重合成" />
              <div className="overflow-x-auto">
                <table className="w-full text-[13px] min-w-[620px]">
                  <thead>
                    <tr style={{ color: "var(--mute)", borderBottom: "1px solid var(--hairline)" }}>
                      <th className="text-left font-medium py-2.5 px-2">品牌</th>
                      <th className="text-right font-medium py-2.5 px-2">综合份额</th>
                      {SIGNALS.map((signal) => (
                        <th key={signal.key} className="text-right font-medium py-2.5 px-2">
                          {signal.label}
                          <div className="text-[10px] font-normal">权重 {(result.model.active_weights[signal.key] * 100).toFixed(0)}%</div>
                        </th>
                      ))}
                      <th className="text-right font-medium py-2.5 px-2">覆盖度</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.brands.map((row) => (
                      <tr key={row.brand_id} style={{ borderBottom: "1px solid var(--hairline)", color: "var(--body)" }}>
                        <td className="py-3 px-2 font-medium" style={{ color: "var(--ink)" }}>{row.name}</td>
                        <td className="py-3 px-2 text-right font-semibold tabular-nums" style={{ color: "var(--accent)" }}>{row.share.toFixed(2)}%</td>
                        {SIGNALS.map((signal) => (
                          <td key={signal.key} className="py-3 px-2 text-right tabular-nums">
                            {result.model.active_weights[signal.key] ? `${row.signal_shares[signal.key].toFixed(1)}%` : "—"}
                          </td>
                        ))}
                        <td className="py-3 px-2 text-right"><Badge tone={row.coverage >= 75 ? "positive" : row.coverage >= 50 ? "warning" : "negative"}>{row.coverage}%</Badge></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>

          <Card>
            <SectionTitle title="原始依据" subtitle="查看份额背后的直接指标与推算值，避免只看一个百分比" />
            <div className="overflow-x-auto">
              <table className="w-full text-[13px] min-w-[960px]">
                <thead>
                  <tr style={{ color: "var(--mute)", borderBottom: "1px solid var(--hairline)" }}>
                    <th className="text-left font-medium py-2.5 px-3">品牌</th>
                    <th className="text-right font-medium py-2.5 px-3">销售结果（不计权）</th>
                    <th className="text-right font-medium py-2.5 px-3">App 下载估算</th>
                    <th className="text-right font-medium py-2.5 px-3">App 评论</th>
                    <th className="text-right font-medium py-2.5 px-3">商品评论量（不计权）</th>
                    <th className="text-right font-medium py-2.5 px-3">公开提及（不计权）</th>
                    <th className="text-right font-medium py-2.5 px-3">评论 / 回复（不计权）</th>
                    <th className="text-right font-medium py-2.5 px-3">互动（不计权）</th>
                    <th className="text-right font-medium py-2.5 px-3">播放 / 浏览</th>
                  </tr>
                </thead>
                <tbody>
                  {result.brands.map((row) => (
                    <tr key={row.brand_id} style={{ borderBottom: "1px solid var(--hairline)", color: "var(--body)" }}>
                      <td className="py-3 px-3 font-medium" style={{ color: "var(--ink)" }}>{row.name}</td>
                      <td className="py-3 px-3 text-right tabular-nums">{salesText(row)}</td>
                      <td className="py-3 px-3 text-right tabular-nums">{downloadText(row)}</td>
                      <td className="py-3 px-3 text-right tabular-nums">{fmtNum(row.raw.app_reviews)}</td>
                      <td className="py-3 px-3 text-right tabular-nums">{fmtNum(row.raw.product_reviews)}</td>
                      <td className="py-3 px-3 text-right tabular-nums">{fmtNum(row.raw.mentions)}</td>
                      <td className="py-3 px-3 text-right tabular-nums">{fmtNum(row.raw.comments)}</td>
                      <td className="py-3 px-3 text-right tabular-nums">{fmtNum(row.raw.engagement)}</td>
                      <td className="py-3 px-3 text-right tabular-nums">{fmtNum(row.raw.views)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card>
              <SectionTitle title="估算模型" subtitle={result.model.description} />
              <div className="space-y-3">
                {SIGNALS.map((signal) => {
                  const base = result.model.base_weights[signal.key] * 100;
                  const active = result.model.active_weights[signal.key] * 100;
                  return (
                    <div key={signal.key}>
                      <div className="flex justify-between text-[13px] mb-1">
                        <span style={{ color: "var(--body)" }}>{signal.label}</span>
                        <span className="tabular-nums" style={{ color: "var(--ink)" }}>{active.toFixed(0)}%{Math.abs(active - base) > 0.1 ? `（原 ${base.toFixed(0)}%）` : ""}</span>
                      </div>
                      <div className="h-2 rounded-full overflow-hidden" style={{ background: "var(--bg-soft-2)" }}>
                        <div className="h-full rounded-full" style={{ width: `${active}%`, background: "var(--accent)" }} />
                      </div>
                    </div>
                  );
                })}
              </div>
              <div className="mt-4 p-3 rounded-md text-[12px] leading-relaxed" style={{ background: "var(--bg-soft)", color: "var(--mute)" }}>
                算法：App 下载估算权重 {(result.model.base_weights.app * 100).toFixed(0)}%，App Store 评论数权重 {(result.model.base_weights.conversation * 100).toFixed(0)}%。两项先在所选品牌和国家内归一化；销售、商品评论、媒体与互动不参与计算，大模型也不参与数值计算。
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
        </>
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
