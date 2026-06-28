import { useParams } from "react-router-dom";
import { TrendChart, Bars } from "../components/charts";
import { RecordList } from "../components/RecordList";
import { Card, SectionTitle, Spinner, StatCard } from "../components/ui";
import { TimeRangePicker } from "../components/TimeRangePicker";
import { SmartSummary } from "../components/SmartSummary";
import { useOverview } from "../lib/hooks";
import { useTimeRange } from "../lib/timeRange";
import { fmtNum } from "../lib/format";

export default function Overview() {
  const { brandId } = useParams();
  const [range] = useTimeRange();
  const { data, isLoading } = useOverview(brandId, range);
  if (isLoading || !data) return <Spinner />;
  const k = data.kpis;
  return (
    <div className="space-y-6">
      <SectionTitle title="经营总览" subtitle="跨销售、营销、用户之声、网页监控的品牌全景" action={<TimeRangePicker />} />
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <StatCard label="总声量" value={fmtNum(k.records_total)} />
        <StatCard label="负向之声" value={fmtNum(k.voc_negative)} tone="negative" hint={`共 ${k.voc_total} 条用户之声`} />
        <StatCard label="营销声量" value={fmtNum(k.marketing_total)} tone="accent" />
        <StatCard label="销售额(估)" value={fmtNum(k.sales_revenue)} hint={`${fmtNum(k.sales_units)} 件`} />
        <StatCard label="网页变更" value={fmtNum(k.web_changes)} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card className="lg:col-span-2">
          <SectionTitle title="声量趋势" />
          <TrendChart
            data={data.trend}
            keys={[
              { key: "total", name: "总声量", color: "var(--accent)" },
              { key: "negative", name: "负向", color: "var(--danger)" },
            ]}
          />
        </Card>
        <Card>
          <SectionTitle title="热点主题" />
          {data.top_topics?.length ? (
            <Bars data={data.top_topics} dataKey="total" nameKey="topic" name="声量" color="var(--violet)" />
          ) : (
            <p className="text-[13px]" style={{ color: "var(--mute)" }}>暂无主题数据</p>
          )}
        </Card>
      </div>

      <Card>
        <SectionTitle title="智能总结" subtitle="基于所选时间范围内的全维度记录，由大模型生成要点 / 情绪 / 代表内容" />
        <SmartSummary brandId={brandId} range={range} />
      </Card>

      <Card>
        <SectionTitle title="高优先级信号" subtitle="负向之声与头部媒体提及" />
        <RecordList records={(data.high_signal || []).slice(0, 12)} />
      </Card>
    </div>
  );
}
