import { useState } from "react";
import { useParams } from "react-router-dom";
import { TrendChart } from "../components/charts";
import { RecordList } from "../components/RecordList";
import { Badge, Button, Card, EmptyState, Field, Input, Modal, SectionTitle, Select, Spinner, StatCard, Textarea } from "../components/ui";
import { TimeRangePicker } from "../components/TimeRangePicker";
import { useRecords, useVocActions, useVocMutations, useVocSummary } from "../lib/hooks";
import { useTimeRange, rangeParams } from "../lib/timeRange";
import { fmtNum, PRIORITY_LABEL, STATUS_LABEL, TEAM_LABEL } from "../lib/format";

export default function Voice() {
  const { brandId } = useParams();
  const [range] = useTimeRange();
  const [addOpen, setAddOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const { data: summary, isLoading } = useVocSummary(brandId, range);
  const { data: actions = [] } = useVocActions(brandId);
  const { data: records = [] } = useRecords({ brand_id: brandId, dimension: "voc", ...rangeParams(range), limit: 40 });

  if (isLoading || !summary) return <Spinner />;
  const t = summary.totals;

  return (
    <div className="space-y-6">
      <SectionTitle
        title="用户之声"
        subtitle="多渠道反馈聚合、情感与主题分析、预警与闭环责任分派"
        action={
          <div className="flex flex-wrap items-center gap-2">
            <TimeRangePicker />
            <Button onClick={() => setImportOpen(true)}>导入 CSV</Button>
            <Button variant="primary" onClick={() => setAddOpen(true)}>+ 录入反馈</Button>
          </div>
        }
      />

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <StatCard label="反馈总量" value={fmtNum(t.total)} />
        <StatCard label="负向" value={fmtNum(t.negative)} tone="negative" hint={`负向率 ${(t.negative_rate * 100).toFixed(0)}%`} />
        <StatCard label="正向" value={fmtNum(t.positive)} tone="accent" />
        <StatCard label="待办任务" value={summary.actions.open} />
        <StatCard label="闭环率" value={`${(summary.actions.closure_rate * 100).toFixed(0)}%`} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card className="lg:col-span-2">
          <SectionTitle title="反馈趋势" />
          <TrendChart data={summary.trend} keys={[{ key: "total", name: "反馈", color: "var(--accent)" }, { key: "negative", name: "负向", color: "var(--danger)" }]} />
        </Card>
        <Card>
          <SectionTitle title="实时预警" />
          {summary.alerts?.length ? (
            <div className="space-y-2">
              {summary.alerts.map((a: any) => (
                <div key={a.id} className="flex items-start gap-2 p-2.5 rounded-md" style={{ background: a.severity === "high" ? "var(--danger-soft)" : "var(--warning-soft)" }}>
                  <Badge tone={a.severity === "high" ? "negative" : "warning"}>{a.severity === "high" ? "高" : "中"}</Badge>
                  <div className="text-[13px]" style={{ color: "var(--body)" }}>
                    {a.label}
                    {a.suggested_team && <span className="block text-[12px] mt-0.5" style={{ color: "var(--mute)" }}>建议：{TEAM_LABEL[a.suggested_team] || a.suggested_team}</span>}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-[13px]" style={{ color: "var(--mute)" }}>暂无预警</p>
          )}
        </Card>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card>
          <SectionTitle title="主题分析" subtitle="负向主题与建议责任团队" />
          {summary.topics?.length ? (
            <div className="space-y-1.5">
              {summary.topics.map((tp: any) => (
                <div key={tp.topic} className="flex items-center justify-between py-1.5" style={{ borderBottom: "1px solid var(--hairline)" }}>
                  <div className="flex items-center gap-2">
                    <span className="text-[13px] font-medium" style={{ color: "var(--ink)" }}>#{tp.topic}</span>
                    <span className="text-[12px]" style={{ color: "var(--mute)" }}>{tp.total} 条 · 负向 {tp.negative}</span>
                  </div>
                  <Badge tone="neutral">{TEAM_LABEL[tp.suggested_team] || tp.suggested_team}</Badge>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-[13px]" style={{ color: "var(--mute)" }}>暂无主题</p>
          )}
        </Card>
        <Card>
          <SectionTitle title="闭环任务" />
          <ActionsBoard actions={actions} />
        </Card>
      </div>

      <Card>
        <SectionTitle title="反馈流" />
        <RecordList records={records} emptyHint="录入反馈 / 导入 CSV，或在数据源页采集 App Store 评论。" />
      </Card>

      <AddModal open={addOpen} onClose={() => setAddOpen(false)} brandId={brandId!} />
      <ImportModal open={importOpen} onClose={() => setImportOpen(false)} brandId={brandId!} />
    </div>
  );
}

function ActionsBoard({ actions }: { actions: any[] }) {
  const { updateAction, delAction } = useVocMutations();
  if (!actions.length) return <EmptyState title="暂无任务" hint="从反馈或预警创建闭环任务。" />;
  return (
    <div className="space-y-2">
      {actions.map((a) => (
        <div key={a.id} className="p-3 rounded-md" style={{ background: "var(--bg-soft)" }}>
          <div className="flex items-center justify-between gap-2">
            <span className="text-[13px] font-medium" style={{ color: "var(--ink)" }}>{a.title}</span>
            <Badge tone={a.priority === "urgent" || a.priority === "high" ? "negative" : "neutral"}>{PRIORITY_LABEL[a.priority] || a.priority}</Badge>
          </div>
          <div className="flex items-center gap-2 mt-2">
            <Badge tone="neutral">{TEAM_LABEL[a.owner_team] || a.owner_team}</Badge>
            <Select
              value={a.status}
              onChange={(e) => updateAction.mutate({ id: a.id, status: e.target.value })}
              className="h-7 text-[12px]"
            >
              {Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </Select>
            <button onClick={() => delAction.mutate(a.id)} className="text-[12px] ml-auto cursor-pointer" style={{ color: "var(--mute)" }}>删除</button>
          </div>
        </div>
      ))}
    </div>
  );
}

function AddModal({ open, onClose, brandId }: { open: boolean; onClose: () => void; brandId: string }) {
  const { addRecord } = useVocMutations();
  const [form, setForm] = useState<any>({ platform: "", body: "" });
  return (
    <Modal open={open} onClose={onClose} title="录入用户反馈">
      <div className="space-y-3">
        <Field label="来源平台"><Input value={form.platform} onChange={(e) => setForm({ ...form, platform: e.target.value })} placeholder="如 App Store / 微博 / 工单" /></Field>
        <Field label="标题(可选)"><Input value={form.title || ""} onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field>
        <Field label="反馈内容"><Textarea rows={4} value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} /></Field>
        <div className="flex justify-end gap-2 pt-1">
          <Button onClick={onClose}>取消</Button>
          <Button
            variant="primary"
            disabled={!form.body || addRecord.isPending}
            onClick={async () => {
              await addRecord.mutateAsync({ ...form, brand_id: brandId, dimension: "voc", data_type: "user_voice", source_id: "manual_csv" });
              setForm({ platform: "", body: "" });
              onClose();
            }}
          >
            保存
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function ImportModal({ open, onClose, brandId }: { open: boolean; onClose: () => void; brandId: string }) {
  const { importRows } = useVocMutations();
  const [text, setText] = useState("");
  const parse = (raw: string) => {
    const lines = raw.trim().split(/\r?\n/);
    if (!lines.length) return [];
    const headers = lines[0].split(",").map((h) => h.trim());
    return lines.slice(1).map((line) => {
      const cells = line.split(",");
      const row: Record<string, string> = {};
      headers.forEach((h, i) => (row[h] = (cells[i] || "").trim()));
      return row;
    });
  };
  return (
    <Modal open={open} onClose={onClose} title="导入 CSV 反馈" width={640}>
      <p className="text-[13px] mb-2" style={{ color: "var(--mute)" }}>粘贴 CSV，首行为表头（建议含 body/platform/title/author 列）。</p>
      <Textarea rows={8} value={text} onChange={(e) => setText(e.target.value)} placeholder={"platform,body\nApp Store,这个新版本一直闪退"} className="font-mono" />
      <div className="flex justify-end gap-2 pt-3">
        <Button onClick={onClose}>取消</Button>
        <Button
          variant="primary"
          disabled={!text.trim() || importRows.isPending}
          onClick={async () => {
            await importRows.mutateAsync({ rows: parse(text), brand_id: brandId, source_id: "manual_csv" });
            setText("");
            onClose();
          }}
        >
          导入
        </Button>
      </div>
    </Modal>
  );
}
