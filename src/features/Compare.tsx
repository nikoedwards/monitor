import { useNavigate } from "react-router-dom";
import { Bars } from "../components/charts";
import { Badge, Card, EmptyState, SectionTitle, Spinner } from "../components/ui";
import { TimeRangePicker } from "../components/TimeRangePicker";
import { useCompare } from "../lib/hooks";
import { useTimeRange } from "../lib/timeRange";
import { fmtNum } from "../lib/format";

export default function Compare() {
  const [range] = useTimeRange();
  const { data: brands = [], isLoading } = useCompare(range);
  const navigate = useNavigate();
  if (isLoading) return <Spinner />;
  if (!brands.length) return <EmptyState title="暂无品牌" hint="先在品牌管理添加自家品牌与竞品。" />;

  const cols = [
    { key: "records_total", label: "总声量" },
    { key: "voc_negative", label: "负向之声" },
    { key: "marketing_total", label: "营销声量" },
    { key: "sales_revenue", label: "销售额(估)" },
    { key: "web_changes", label: "网页变更" },
  ];

  return (
    <div className="space-y-6">
      <SectionTitle title="竞品对比" subtitle="跨品牌的声量、口碑、营销、销售与网页动态横向对比" action={<TimeRangePicker />} />

      <Card>
        <div className="overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead>
              <tr style={{ color: "var(--mute)", borderBottom: "1px solid var(--hairline)" }}>
                <th className="text-left font-medium py-2.5 px-3">品牌</th>
                {cols.map((c) => <th key={c.key} className="text-right font-medium py-2.5 px-3">{c.label}</th>)}
              </tr>
            </thead>
            <tbody>
              {brands.map((b) => (
                <tr key={b.brand_id} className="cursor-pointer" onClick={() => navigate(`/brand/${b.brand_id}/overview`)} style={{ borderBottom: "1px solid var(--hairline)", color: "var(--body)" }}>
                  <td className="py-2.5 px-3">
                    <div className="flex items-center gap-2">
                      <span className="font-medium" style={{ color: "var(--ink)" }}>{b.name}</span>
                      {b.is_primary ? <Badge tone="positive">自家</Badge> : b.is_competitor ? <Badge tone="warning">竞品</Badge> : null}
                    </div>
                  </td>
                  <td className="py-2.5 px-3 text-right tabular-nums">{fmtNum(b.records_total)}</td>
                  <td className="py-2.5 px-3 text-right tabular-nums" style={{ color: b.voc_negative ? "var(--danger)" : undefined }}>{fmtNum(b.voc_negative)}</td>
                  <td className="py-2.5 px-3 text-right tabular-nums">{fmtNum(b.marketing_total)}</td>
                  <td className="py-2.5 px-3 text-right tabular-nums">{fmtNum(b.sales_revenue)}</td>
                  <td className="py-2.5 px-3 text-right tabular-nums">{fmtNum(b.web_changes)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card>
          <SectionTitle title="声量对比" />
          <Bars data={brands.map((b) => ({ name: b.name, total: b.records_total }))} dataKey="total" nameKey="name" name="总声量" color="var(--accent)" />
        </Card>
        <Card>
          <SectionTitle title="负向之声对比" />
          <Bars data={brands.map((b) => ({ name: b.name, total: b.voc_negative }))} dataKey="total" nameKey="name" name="负向" color="var(--danger)" />
        </Card>
      </div>
    </div>
  );
}
