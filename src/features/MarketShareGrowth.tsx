import { useState } from "react";
import { MultiLineChart } from "../components/charts";
import { Card, EmptyState, Modal, SectionTitle, StatCard } from "../components/ui";
import type { MarketShareGrowthResponse } from "../lib/api";
import { presetRange } from "../lib/timeRange";
import { ObservationTable } from "./MarketShareObservations";

const counts = new Intl.NumberFormat("zh-CN");
const changes = new Intl.NumberFormat("zh-CN", { signDisplay: "exceptZero", maximumFractionDigits: 1 });
const percent = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 });

function ShareBar({ share, scale }: { share: number; scale: number }) {
  return (
    <div className="ml-auto w-28">
      <span>{percent.format(share)}%</span>
      <div className="relative h-1.5 mt-1 rounded-full" style={{ background: "var(--bg-soft-2)" }}>
        <span className="absolute left-1/2 h-full border-l" style={{ borderColor: "var(--mute)" }} />
        <span className="absolute h-full rounded-full" style={{
          [share < 0 ? "right" : "left"]: "50%", width: `${50 * Math.abs(share) / scale}%`,
          background: share < 0 ? "var(--danger)" : "var(--accent)",
        }} />
      </div>
    </div>
  );
}

export function MarketShareGrowth({ data, colors }: { data: MarketShareGrowthResponse; colors: string[] }) {
  const [openBrand, setOpenBrand] = useState<string | null>(null);
  const { brands, range, comparable_total_net: total } = data;
  const comparable = brands.filter((brand) => brand.net !== null);
  const ranked = comparable.filter((brand) => brand.comparable_share != null).sort((a, b) => b.net! - a.net!);
  const ordered = ranked.length ? [...ranked, ...brands.filter((brand) => brand.net == null)] : brands;
  const leaders = ranked.filter((brand) => brand.net === ranked[0].net);
  const scale = Math.max(1, ...ranked.map((brand) => Math.abs(brand.comparable_share!)));
  const selected = brands.find((brand) => brand.brand_id === openBrand);
  const localDay = presetRange("24h").end_date;
  const period = `${range.start} 至 ${range.end} · ${range.days} 天${range.end === localDay ? " · 截至今日最近采集" : ""}`;
  const coverage = `${brands.length} 个所选品牌中，${comparable.length} 个有完整区间数据。`;
  const status = comparable.length < 2 ? "至少需要两个有完整区间数据的品牌才能比较份额。"
    : total != null && total <= 0 ? "可比品牌合计净增为零或负数，仅展示净变化和日均值。"
    : "份额和排名按可比品牌的区间净增计算。";
  const keys = brands.map((brand, index) => ({ key: brand.brand_id, name: brand.name, color: colors[index % colors.length] }));
  const trendKeys = keys.filter(({ key }) => data.points.slice(1).some((point) => point.net[key] != null));
  const points = data.points.map((point) => ({ date: point.date, ...point.net }));

  return (
    <>
      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard label="可比品牌净增评分" value={total == null ? "—" : changes.format(total)} hint={period} />
        <StatCard label="可比品牌增量领先" value={leaders.map((brand) => brand.name).join("、") || "—"}
          hint={leaders.length ? `${leaders.length > 1 ? "并列 · " : ""}可比品牌内份额 ${percent.format(leaders[0].comparable_share!)}%` : undefined} tone="accent" />
        <StatCard label="可比品牌" value={`${comparable.length} / ${brands.length}`} hint={`基线 ${range.baseline} · 期末 ${range.end}`} />
      </div>
      <Card>
        <SectionTitle title="评分增量份额" subtitle={coverage + status}
          hint="分母是可比品牌的净增评分之和。个别品牌评分减少时，其贡献为负，其他品牌的贡献可能超过 100%。点击品牌可查看明细。" />
        <ObservationTable headers={["品牌", "区间净增评分", "可比品牌内份额", "日均净增"]} rows={ordered.map((brand) => {
          const rank = brand.comparable_share != null ? ranked.findIndex((other) => other.net === brand.net) + 1 : null;
          const observed = brand.observed_window;
          return { key: brand.brand_id, values: [
            <div>
              <button type="button" className="font-medium cursor-pointer hover:underline" style={{ color: "var(--accent)" }}
                onClick={() => setOpenBrand(brand.brand_id)}>{rank && `${rank}. `}{brand.name}</button>
              {brand.missing_dates.map((day) => <div key={day} className="text-xs mt-1" style={{ color: "var(--mute)" }}>
                缺 {day} {day === range.baseline ? "期初基线" : "期末评分"}
              </div>)}
              {observed && <div className="text-xs mt-1">
                {observed.end} 实采累计 {counts.format(observed.end_count)} 个评分（{observed.app_count} 项）
              </div>}
              {observed?.pair && <div className="text-xs mt-1">
                {observed.pair.start} 至 {observed.end}，{observed.pair.app_count} 项可配对样本净增 {changes.format(observed.pair.net)}
              </div>}
            </div>,
            brand.net == null ? "—" : changes.format(brand.net),
            brand.comparable_share == null ? "—" : <ShareBar share={brand.comparable_share} scale={scale} />,
            brand.daily_average == null ? "—" : changes.format(brand.daily_average),
          ] };
        })} />
      </Card>
      <Card>
        <SectionTitle title="区间累计净增趋势" subtitle={`以 ${range.baseline} 为零点，单位为评分个数。缺少实采数据的日期留空。`} />
        {trendKeys.length ? <>
          <MultiLineChart data={points} keys={trendKeys} valueFormatter={(value) => counts.format(value)} observedOnly />
          <div className="mt-3 flex flex-wrap gap-4 text-xs">
            {trendKeys.map((key) => <span key={key.key} className="inline-flex items-center gap-2">
              <span className="size-2 rounded-full" style={{ background: key.color }} />{key.name}
            </span>)}
          </div>
        </> : <EmptyState title="暂无可绘制的净增趋势" hint="品牌需要同时具备期初基线和至少一天可比评分。" />}
      </Card>
      <Modal open={Boolean(selected)} onClose={() => setOpenBrand(null)} title={`${selected?.name ?? ""} · 评分明细`} width={760}>
        <p className="mb-3 text-sm" style={{ color: "var(--mute)" }}>使用同一批 App、商店和地区比较两端评分。期中新纳入监测的项目单独标记。</p>
        {selected?.apps.length ? <ObservationTable headers={["App / 商店", `基线 · ${range.baseline}`, `期末 · ${range.end}`, "计入净增"]} rows={selected.apps.map((app) => ({
          key: app.key, values: [
            <div>{app.name}<div className="text-xs" style={{ color: "var(--mute)" }}>{app.platform === "google_play" ? "Google Play · 全球" : `App Store · ${app.scope || "地区未记录"}`}</div></div>,
            app.baseline_count == null ? "—" : counts.format(app.baseline_count),
            app.end_count == null ? "—" : counts.format(app.end_count),
            !app.in_baseline_sample ? "期中新纳入" : app.baseline_count == null || app.end_count == null ? "数据不足" : changes.format(app.end_count - app.baseline_count),
          ],
        }))} /> : <EmptyState title="所选期间没有应用商店评分观测" />}
      </Modal>
    </>
  );
}
