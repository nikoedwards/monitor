import { useState } from "react";
import { useParams } from "react-router-dom";
import { TrendChart, Bars, SimpleLine } from "../components/charts";
import { Badge, Button, Card, EmptyState, Field, Input, Modal, SectionTitle, SegmentGroup, Select, Spinner, StatCard } from "../components/ui";
import { TimeRangePicker } from "../components/TimeRangePicker";
import {
  useActivities,
  useAnalyzeJd,
  useCatalogMutations,
  useEmployees,
  useEmployeesSync,
  useHiringSummary,
  useHiringSync,
  useJobPostings,
  useLinks,
  usePostingHistory,
  usePostingMutations,
  useSettings,
} from "../lib/hooks";
import { useTimeRange } from "../lib/timeRange";
import type { JobPosting, Link } from "../lib/api";
import { fmtDate, fmtDateTime, fmtNum } from "../lib/format";

const PLATFORM_LABEL: Record<string, string> = {
  boss: "Boss 直聘",
  linkedin: "LinkedIn 职位",
  linkedin_people: "LinkedIn 员工",
};

const PLATFORM_FILTERS = [
  { value: "all", label: "全部平台" },
  { value: "boss", label: "Boss 直聘" },
  { value: "linkedin", label: "LinkedIn" },
];

const STATUS_FILTERS = [
  { value: "all", label: "全部" },
  { value: "open", label: "在招" },
  { value: "closed", label: "已下线" },
];

export default function Hiring() {
  const { brandId } = useParams();
  const [tab, setTab] = useState<"jobs" | "people">("jobs");
  const [platform, setPlatform] = useState("all");
  const [sourceOpen, setSourceOpen] = useState(false);

  const [range] = useTimeRange();
  const { data: summary, isLoading } = useHiringSummary(brandId, platform, range);
  const sync = useHiringSync();

  return (
    <div className="space-y-6">
      <SectionTitle
        title="招聘监控"
        subtitle="监控 Boss 直聘 / LinkedIn 职位与 JD，分析 JD 释放频率反推对方业务动向"
        hint="Boss 直聘 / LinkedIn 反爬严格，需在设置中配置登录 Cookie；抓取 LinkedIn 员工动态可能违反其服务条款，请自行评估合规与账号风险。"
        action={
          <div className="flex flex-wrap items-center gap-2">
            <TimeRangePicker />
            <Button onClick={() => brandId && sync.mutate({ brandId })} disabled={sync.isPending}>{sync.isPending ? "采集中…" : "立即采集职位"}</Button>
            <Button variant="primary" onClick={() => setSourceOpen(true)}>+ 配置采集源</Button>
          </div>
        }
      />

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label="在招职位" value={fmtNum(summary?.total_open)} tone="accent" />
        <StatCard label="窗口内新增 JD" value={fmtNum(summary?.released_in_window)} />
        <StatCard label="窗口内下线" value={fmtNum(summary?.closed_in_window)} />
        <StatCard label="平均在招天数" value={summary?.avg_open_days != null ? `${summary.avg_open_days}天` : "—"} />
      </div>

      <SegmentGroup
        value={tab}
        options={[{ value: "jobs", label: "职位与 JD" }, { value: "people", label: "员工动态" }]}
        onChange={setTab}
      />

      {tab === "jobs" ? (
        <JobsTab brandId={brandId!} platform={platform} setPlatform={setPlatform} summary={summary} loading={isLoading} range={range} />
      ) : (
        <PeopleTab brandId={brandId!} />
      )}

      <SourceModal open={sourceOpen} onClose={() => setSourceOpen(false)} brandId={brandId!} />
    </div>
  );
}

// --------------------------------------------------------------------- jobs tab
function JobsTab({ brandId, platform, setPlatform, summary, loading, range }: { brandId: string; platform: string; setPlatform: (v: string) => void; summary: any; loading: boolean; range: any }) {
  const [status, setStatus] = useState("all");
  const [detailId, setDetailId] = useState<string | undefined>();
  const { data: postings = [], isLoading: postingsLoading } = useJobPostings(brandId, platform, status);

  if (loading) return <Spinner />;

  const trend = summary?.trend || [];
  const departments = (summary?.by_department || []).map((d: any) => ({ ...d, label: d.department }));
  const hasTrend = trend.some((t: any) => t.released || t.closed || t.active);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card className="lg:col-span-2">
          <SectionTitle title="JD 释放与在招走势" subtitle="新增/下线 JD 频率与在招岗位数——招到人后岗位下线，指标回落" />
          {hasTrend ? (
            <TrendChart
              data={trend}
              keys={[
                { key: "released", name: "新增 JD", color: "var(--accent)" },
                { key: "active", name: "在招岗位", color: "var(--violet)" },
                { key: "closed", name: "下线", color: "var(--warning)" },
              ]}
            />
          ) : (
            <EmptyState title="暂无招聘时序" hint="配置采集源并采集后展示。Boss/LinkedIn 需在设置中填写 Cookie。" />
          )}
        </Card>
        <Card>
          <SectionTitle title="在招部门分布" />
          {departments.length ? (
            <Bars data={departments} dataKey="count" nameKey="label" name="在招职位" color="var(--accent)" />
          ) : (
            <p className="text-[13px]" style={{ color: "var(--mute)" }}>暂无部门数据</p>
          )}
        </Card>
      </div>

      <BusinessAnalysisCard brandId={brandId} range={range} />

      <Card>
        <SectionTitle
          title="职位列表"
          subtitle="每条职位的 JD、状态与变化；点击查看历史与 JD 全文"
          action={
            <div className="flex flex-wrap items-center gap-2">
              <SegmentGroup value={status} options={STATUS_FILTERS} onChange={setStatus} />
              <SegmentGroup value={platform} options={PLATFORM_FILTERS} onChange={setPlatform} />
            </div>
          }
        />
        {postingsLoading ? <Spinner /> : <PostingTable postings={postings} onDetail={setDetailId} />}
      </Card>

      <PostingDetailModal postingId={detailId} onClose={() => setDetailId(undefined)} />
    </div>
  );
}

function BusinessAnalysisCard({ brandId, range }: { brandId: string; range: any }) {
  const { data: settings } = useSettings();
  const analyze = useAnalyzeJd();
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState("");

  const run = async () => {
    setError("");
    setResult(null);
    try {
      const res = await analyze.mutateAsync({ brandId, range });
      setResult(res);
    } catch (e: any) {
      setError(e?.message || "分析失败");
    }
  };

  return (
    <Card>
      <SectionTitle
        title="业务动向分析"
        subtitle="用大模型从近期 JD 反推对方内部在推进的业务与团队重点"
        action={
          <Button onClick={run} disabled={analyze.isPending || !settings?.configured} title={!settings?.configured ? "请先在设置中配置大模型 Token" : ""}>
            {analyze.isPending ? "分析中…" : "生成分析"}
          </Button>
        }
      />
      {!settings?.configured && <p className="text-[13px]" style={{ color: "var(--mute)" }}>未配置大模型 Token，请在右上角设置中填写后再生成。</p>}
      {error && <p className="text-[13px]" style={{ color: "var(--danger)" }}>{error}</p>}
      {result && (
        <div className="space-y-3 text-[13px]" style={{ color: "var(--body)" }}>
          <p style={{ color: "var(--ink)" }}>{result.summary}</p>
          {!!result.business_directions?.length && (
            <div>
              <div className="font-medium mb-1" style={{ color: "var(--ink)" }}>推导业务方向</div>
              <div className="space-y-1.5">
                {result.business_directions.map((d: any, i: number) => (
                  <div key={i} className="p-2 rounded-md" style={{ background: "var(--bg-soft-2)" }}>
                    <div className="flex items-center gap-2">
                      <Badge tone="accent">{d.direction}</Badge>
                      {d.job_count ? <span style={{ color: "var(--mute)" }}>{d.job_count} 个岗位</span> : null}
                    </div>
                    {d.evidence && <div className="mt-1" style={{ color: "var(--mute)" }}>{d.evidence}</div>}
                  </div>
                ))}
              </div>
            </div>
          )}
          {!!result.strategic_signals?.length && (
            <div>
              <div className="font-medium mb-1" style={{ color: "var(--ink)" }}>战略信号</div>
              <ul className="list-disc pl-5 space-y-0.5">
                {result.strategic_signals.map((s: string, i: number) => <li key={i}>{s}</li>)}
              </ul>
            </div>
          )}
          {!!result.risks?.length && (
            <div className="text-[12px]" style={{ color: "var(--mute)" }}>
              解读说明：{result.risks.join("；")}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

function PostingTable({ postings, onDetail }: { postings: JobPosting[]; onDetail: (id: string) => void }) {
  const { remove } = usePostingMutations();
  if (!postings.length) {
    return <EmptyState title="暂无职位" hint="配置 Boss/LinkedIn 采集源并采集后在此展示。" />;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[13px]">
        <thead>
          <tr style={{ color: "var(--mute)", borderBottom: "1px solid var(--hairline)" }}>
            {["职位", "平台", "部门", "城市", "状态", "首次发现", "变更", ""].map((h) => (
              <th key={h} className="text-left font-medium py-2 px-2 whitespace-nowrap">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {postings.map((p) => {
            const changedToday = (p.latest?.changes?.length || 0) > 0;
            return (
              <tr key={p.id} style={{ borderBottom: "1px solid var(--hairline)", color: "var(--body)" }}>
                <td className="py-2 px-2">
                  <button onClick={() => onDetail(p.id)} className="text-left text-[13px] font-medium truncate block max-w-[280px] cursor-pointer hover:underline" style={{ color: "var(--ink)" }}>
                    {p.title || p.url || "(未抓取标题)"}
                  </button>
                  {p.last_status && p.last_status !== "ok" && <div className="text-[11px]" style={{ color: "var(--mute)" }}>{p.last_status}</div>}
                </td>
                <td className="py-2 px-2 whitespace-nowrap">{PLATFORM_LABEL[p.platform] || p.platform}</td>
                <td className="py-2 px-2 whitespace-nowrap">{p.department || "—"}</td>
                <td className="py-2 px-2 whitespace-nowrap">{p.city || "—"}</td>
                <td className="py-2 px-2">{p.status === "closed" ? <Badge tone="warning">已下线</Badge> : <Badge tone="positive">在招</Badge>}</td>
                <td className="py-2 px-2 whitespace-nowrap">{fmtDate(p.first_seen)}</td>
                <td className="py-2 px-2">{changedToday ? <Badge tone="warning">变更 {p.latest!.changes!.length}</Badge> : p.has_change ? <span className="text-[11px]" style={{ color: "var(--mute)" }}>曾变更</span> : "—"}</td>
                <td className="py-2 px-2 whitespace-nowrap">
                  <button onClick={() => onDetail(p.id)} className="text-[12px] cursor-pointer mr-2" style={{ color: "var(--accent)" }}>详情</button>
                  <button onClick={() => remove.mutate(p.id)} className="text-[12px] cursor-pointer" style={{ color: "var(--mute)" }}>删除</button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function PostingDetailModal({ postingId, onClose }: { postingId?: string; onClose: () => void }) {
  const { data, isLoading } = usePostingHistory(postingId);
  if (!postingId) return null;
  const posting: JobPosting | undefined = data?.posting;
  const snapshots: any[] = data?.snapshots || [];
  const changes: any[] = data?.changes || [];
  const series = snapshots.map((s) => ({ date: s.snapshot_date, open: s.is_open ? 1 : 0 }));

  return (
    <Modal open={!!postingId} onClose={onClose} title={posting?.title || "职位详情"} width={720}>
      {isLoading || !data ? (
        <Spinner />
      ) : (
        <div className="space-y-4">
          <div className="text-[12px]" style={{ color: "var(--mute)" }}>
            {PLATFORM_LABEL[posting?.platform || ""] || posting?.platform}
            {posting?.department ? ` · ${posting.department}` : ""}{posting?.city ? ` · ${posting.city}` : ""} · 数据点 {posting?.data_points}
            {posting?.status === "closed" ? " · 已下线" : " · 在招"}
          </div>
          {posting?.url && <a href={posting.url} target="_blank" rel="noreferrer" className="text-[13px] hover:underline break-all block" style={{ color: "var(--accent)" }}>{posting.url}</a>}

          {series.length > 1 && (
            <div className="rounded-md p-2" style={{ border: "1px solid var(--hairline)" }}>
              <div className="text-[12px] mb-1" style={{ color: "var(--mute)" }}>在招状态时序 (1=在招)</div>
              <SimpleLine data={series} dataKey="open" name="在招" color="var(--violet)" />
            </div>
          )}

          <div>
            <div className="text-[13px] font-medium mb-1" style={{ color: "var(--ink)" }}>JD 全文</div>
            <div className="text-[12px] whitespace-pre-wrap max-h-64 overflow-y-auto p-2 rounded-md" style={{ background: "var(--bg-soft-2)", color: "var(--body)" }}>
              {data.jd_text || "（未抓取到 JD 正文）"}
            </div>
          </div>

          <div>
            <div className="text-[13px] font-medium mb-2" style={{ color: "var(--ink)" }}>变更记录</div>
            {changes.length ? (
              <div className="space-y-2 max-h-48 overflow-y-auto">
                {changes.map((c, i) => (
                  <div key={i} className="text-[12px] p-2 rounded-md" style={{ background: "var(--bg-soft-2)" }}>
                    <span style={{ color: "var(--mute)" }}>{fmtDate(c.date)}</span>
                    {(c.changes || []).map((ch: any, j: number) => (
                      <div key={j} style={{ color: "var(--body)" }}>
                        <span className="font-medium">{ch.field}</span>：<span style={{ color: "var(--mute)" }}>{String(ch.from ?? "—")}</span> → {String(ch.to ?? "—")}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-[12px]" style={{ color: "var(--mute)" }}>暂无变更记录</p>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}

// ------------------------------------------------------------------- people tab
function PeopleTab({ brandId }: { brandId: string }) {
  const { data: employees = [], isLoading } = useEmployees(brandId);
  const { data: activities = [] } = useActivities(brandId);
  const sync = useEmployeesSync();

  return (
    <div className="space-y-4">
      <Card>
        <SectionTitle
          title="LinkedIn 员工名册"
          subtitle="从目标公司的 LinkedIn People 页采集员工账号（需配置 Cookie，best-effort）"
          action={<Button onClick={() => sync.mutate({ brandId })} disabled={sync.isPending}>{sync.isPending ? "采集中…" : "采集员工动态"}</Button>}
        />
        {isLoading ? (
          <Spinner />
        ) : employees.length ? (
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead>
                <tr style={{ color: "var(--mute)", borderBottom: "1px solid var(--hairline)" }}>
                  {["姓名", "头衔", "动态数", "最近动态", ""].map((h) => (
                    <th key={h} className="text-left font-medium py-2 px-2 whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {employees.map((e) => (
                  <tr key={e.id} style={{ borderBottom: "1px solid var(--hairline)", color: "var(--body)" }}>
                    <td className="py-2 px-2" style={{ color: "var(--ink)" }}>{e.name || e.profile_url || "(未知)"}</td>
                    <td className="py-2 px-2 max-w-[280px] truncate">{e.title || e.headline || "—"}</td>
                    <td className="py-2 px-2 tabular-nums">{fmtNum(e.activity_count)}</td>
                    <td className="py-2 px-2 whitespace-nowrap">{e.last_activity_at ? fmtDateTime(e.last_activity_at) : "—"}</td>
                    <td className="py-2 px-2">{e.profile_url && <a href={e.profile_url} target="_blank" rel="noreferrer" className="text-[12px] cursor-pointer" style={{ color: "var(--accent)" }}>主页</a>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="暂无员工数据" hint="配置一个 platform 为「LinkedIn 员工」的采集源（公司 People 页 URL），采集后展示。" />
        )}
      </Card>

      <Card>
        <SectionTitle title="员工动态 Feed" subtitle="一线员工的公开动态，用于侧面反馈其工作状态与团队节奏" />
        {activities.length ? (
          <div className="space-y-2 max-h-[480px] overflow-y-auto">
            {activities.map((a) => (
              <div key={a.id} className="p-3 rounded-md" style={{ background: "var(--bg-soft-2)" }}>
                <div className="flex items-center justify-between gap-2 mb-1">
                  <span className="text-[13px] font-medium" style={{ color: "var(--ink)" }}>{a.profile_name || "员工"}</span>
                  <span className="text-[11px]" style={{ color: "var(--mute)" }}>{a.posted_at ? fmtDateTime(a.posted_at) : fmtDateTime(a.created_at)}</span>
                </div>
                <p className="text-[13px]" style={{ color: "var(--body)" }}>{a.text}</p>
                {a.url && <a href={a.url} target="_blank" rel="noreferrer" className="text-[11px] cursor-pointer" style={{ color: "var(--accent)" }}>查看</a>}
              </div>
            ))}
          </div>
        ) : (
          <EmptyState title="暂无动态" hint="LinkedIn 员工动态抓取为最佳努力，可能因反爬或未配置 Cookie 而为空。" />
        )}
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------- source modal
function SourceModal({ open, onClose, brandId }: { open: boolean; onClose: () => void; brandId: string }) {
  const { data: links = [] } = useLinks(brandId, "hiring");
  const { addLink, delLink } = useCatalogMutations();
  const [form, setForm] = useState<any>({ platform: "boss" });
  const set = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }));

  const save = async () => {
    if (!form.url) return;
    await addLink.mutateAsync({ brand_id: brandId, dimension: "hiring", channel: form.platform, platform: form.platform, url: form.url, label: form.label });
    setForm({ platform: "boss" });
  };

  return (
    <Modal open={open} onClose={onClose} title="配置招聘采集源" width={640}>
      <div className="space-y-4">
        <div className="grid grid-cols-1 gap-3">
          <Field label="平台">
            <Select value={form.platform} onChange={(e) => set("platform", e.target.value)} className="w-full">
              <option value="boss">Boss 直聘（公司页 / 搜索结果 URL）</option>
              <option value="linkedin">LinkedIn 职位（公司 Jobs 页 URL）</option>
              <option value="linkedin_people">LinkedIn 员工（公司 People 页 URL）</option>
            </Select>
          </Field>
          <Field label="URL" hint="Boss 公司主页/搜索结果页；LinkedIn 公司 /jobs/ 或 /people/ 页。">
            <Input value={form.url || ""} onChange={(e) => set("url", e.target.value)} placeholder="https://www.zhipin.com/gongsi/... 或 https://www.linkedin.com/company/.../jobs/" />
          </Field>
          <Field label="备注（可选）"><Input value={form.label || ""} onChange={(e) => set("label", e.target.value)} /></Field>
        </div>
        <div className="flex justify-end">
          <Button variant="primary" onClick={save} disabled={addLink.isPending || !form.url}>{addLink.isPending ? "保存中…" : "添加采集源"}</Button>
        </div>

        <div style={{ borderTop: "1px solid var(--hairline)" }} className="pt-3">
          <div className="text-[13px] font-medium mb-2" style={{ color: "var(--ink)" }}>已配置采集源</div>
          {links.length ? (
            <div className="space-y-1.5">
              {(links as Link[]).map((l) => (
                <div key={l.id} className="flex items-center justify-between gap-2 p-2 rounded-md text-[13px]" style={{ background: "var(--bg-soft-2)" }}>
                  <div className="min-w-0">
                    <Badge tone="accent">{PLATFORM_LABEL[l.platform || l.channel] || l.platform || l.channel}</Badge>
                    <span className="ml-2 break-all" style={{ color: "var(--body)" }}>{l.label || l.url}</span>
                    {l.last_status && l.last_status !== "ok" && <span className="ml-2 text-[11px]" style={{ color: "var(--warning)" }}>{l.last_status}</span>}
                  </div>
                  <button onClick={() => delLink.mutate(l.id)} className="text-[12px] cursor-pointer shrink-0" style={{ color: "var(--mute)" }}>删除</button>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-[13px]" style={{ color: "var(--mute)" }}>暂无采集源</p>
          )}
        </div>
      </div>
    </Modal>
  );
}
