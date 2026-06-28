import { useState } from "react";
import { useParams } from "react-router-dom";
import { Badge, Button, Card, SectionTitle, Spinner } from "../components/ui";
import { useBrands, useSourceMutations, useSources } from "../lib/hooks";
import { fmtDateTime, SOURCE_STATUS_LABEL, TIER_LABEL } from "../lib/format";

const STATUS_TONE: Record<string, "positive" | "warning" | "neutral"> = {
  ready: "positive",
  needs_credential: "warning",
  planned: "neutral",
};

export default function Sources() {
  const { brandId } = useParams();
  const { data, isLoading } = useSources();
  const { data: brands = [] } = useBrands();
  const { collect } = useSourceMutations();
  const [result, setResult] = useState<Record<string, string>>({});

  if (isLoading || !data) return <Spinner />;
  const brand = brands.find((b) => b.id === brandId);
  const tiers = [1, 2, 3];

  const run = async (sourceId: string) => {
    if (!brandId) return;
    setResult((r) => ({ ...r, [sourceId]: "采集中…" }));
    try {
      const res = await collect.mutateAsync({ sourceId, brandId });
      setResult((r) => ({ ...r, [sourceId]: res.status === "ok" ? `新增 ${res.created} 条` : res.error || res.status }));
    } catch (e: any) {
      setResult((r) => ({ ...r, [sourceId]: e.message }));
    }
  };

  return (
    <div className="space-y-6">
      <SectionTitle title="数据源采集控制台" subtitle={`针对品牌「${brand?.name || ""}」按连接器分档采集真实数据`} />

      <Card className="p-4">
        <div className="text-[13px]" style={{ color: "var(--body)" }}>
          采集前请确认品牌已配置「监控关键词」。第二档需在环境变量中配置凭证后才会启用，第三档为付费/接缝，建议以手动 / CSV 录入为主。
        </div>
        <div className="flex gap-2 flex-wrap mt-3">
          {Object.entries(data.credentials).map(([key, ok]) => (
            <Badge key={key} tone={ok ? "positive" : "neutral"}>{key}: {ok ? "已配置" : "未配置"}</Badge>
          ))}
        </div>
      </Card>

      {tiers.map((tier) => {
        const sources = data.sources.filter((s) => s.tier === tier);
        if (!sources.length) return null;
        return (
          <div key={tier}>
            <div className="flex items-center gap-2 mb-3">
              <h3 className="text-[15px] font-semibold" style={{ color: "var(--ink)" }}>第 {tier} 档 · {TIER_LABEL[tier]}</h3>
              <span className="text-[12px]" style={{ color: "var(--mute)" }}>{sources.length} 个连接器</span>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {sources.map((s) => (
                <Card key={s.id} className="p-4">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-[14px] font-medium" style={{ color: "var(--ink)" }}>{s.name}</span>
                        <Badge tone={STATUS_TONE[s.status]}>{SOURCE_STATUS_LABEL[s.status] || s.status}</Badge>
                      </div>
                      <div className="text-[12px] mt-1" style={{ color: "var(--mute)" }}>{s.vendor} · {s.category} · {s.sync_mode}</div>
                    </div>
                  </div>
                  <p className="text-[13px] mt-2" style={{ color: "var(--body)" }}>{s.notes}</p>
                  <div className="flex items-center justify-between mt-3">
                    <div className="text-[12px]" style={{ color: "var(--mute)" }}>
                      {s.last_collect_at ? `上次：${fmtDateTime(s.last_collect_at)} · 累计 ${s.item_count}` : "尚未采集"}
                      {result[s.id] && <span className="ml-2" style={{ color: "var(--accent)" }}>{result[s.id]}</span>}
                    </div>
                    <Button size="sm" variant={s.status === "ready" ? "primary" : "secondary"} disabled={s.status !== "ready" || collect.isPending} onClick={() => run(s.id)}>
                      采集
                    </Button>
                  </div>
                </Card>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
