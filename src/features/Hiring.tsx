import { useState } from "react";
import { useParams } from "react-router-dom";
import { TrendChart, Bars, SimpleLine } from "../components/charts";
import { Badge, Button, Card, EmptyState, Field, Input, Modal, SectionTitle, SegmentGroup, Select, Spinner, StatCard, Textarea } from "../components/ui";
import { TimeRangePicker } from "../components/TimeRangePicker";
import {
  useActivities,
  useAnalyzeJd,
  useCatalogMutations,
  useEmployeeHistory,
  useEmployeeMutations,
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
import type { JobPosting, Link, LinkedInEmployee } from "../lib/api";
import { fmtDate, fmtDateTime, fmtNum } from "../lib/format";

const PLATFORM_LABEL: Record<string, string> = {
  boss: "Boss 直聘",
  linkedin: "LinkedIn 职位",
  linkedin_people: "LinkedIn 人员",
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
        subtitle="监控 Boss 直聘 / LinkedIn 职位与 JD，并以重点人员变化补充判断企业投入方向"
        hint="Boss 直聘 / LinkedIn 反爬严格，需在设置中配置登录 Cookie；抓取 LinkedIn 个人公开页面与动态可能违反其服务条款，请自行评估合规与账号风险。"
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
        options={[{ value: "jobs", label: "职位与 JD" }, { value: "people", label: "重点人员" }]}
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
  const mutations = useEmployeeMutations();
  const [scope, setScope] = useState<"focus" | "all">("focus");
  const [addOpen, setAddOpen] = useState(false);
  const [detailId, setDetailId] = useState<string | undefined>();

  const focusPeople = employees.filter((employee) => employee.monitor);
  const visiblePeople = scope === "focus" ? focusPeople : employees;
  const focusIds = new Set(focusPeople.map((employee) => employee.id));
  const visibleActivities = scope === "focus"
    ? activities.filter((activity) => focusIds.has(activity.profile_id))
    : activities;
  const profileChanges = focusPeople.reduce((sum, employee) => sum + (employee.change_count || 0), 0);
  const seenDates = focusPeople.map((employee) => employee.last_seen).filter((value): value is string => !!value).sort();
  const latestSeen = seenDates.length ? seenDates[seenDates.length - 1] : undefined;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label="重点人员" value={fmtNum(focusPeople.length)} tone="accent" />
        <StatCard label="员工候选池" value={fmtNum(employees.length)} />
        <StatCard label="个人页变更" value={fmtNum(profileChanges)} />
        <StatCard label="最近采集" value={latestSeen ? fmtDate(latestSeen) : "—"} />
      </div>

      <Card>
        <SectionTitle
          title="重点人员监控"
          subtitle="公司 People 页用于发现候选人；只有标记为重点的人员才会持续采集个人页、头衔变化和公开动态"
          action={
            <div className="flex flex-wrap items-center gap-2">
              <SegmentGroup
                value={scope}
                options={[{ value: "focus", label: "仅重点" }, { value: "all", label: "全部候选" }]}
                onChange={setScope}
              />
              <Button onClick={() => sync.mutate({ brandId })} disabled={sync.isPending}>{sync.isPending ? "采集中…" : "立即采集"}</Button>
              <Button variant="primary" onClick={() => setAddOpen(true)}>+ 添加重点人员</Button>
            </div>
          }
        />
        {isLoading ? (
          <Spinner />
        ) : visiblePeople.length ? (
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead>
                <tr style={{ color: "var(--mute)", borderBottom: "1px solid var(--hairline)" }}>
                  {["人员", "当前身份", "个人页变化", "公开动态", "最近采集", "采集状态", ""].map((h) => (
                    <th key={h} className="text-left font-medium py-2 px-2 whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visiblePeople.map((e) => (
                  <tr key={e.id} style={{ borderBottom: "1px solid var(--hairline)", color: "var(--body)" }}>
                    <td className="py-2 px-2 min-w-[190px]">
                      <button onClick={() => setDetailId(e.id)} className="text-left font-medium cursor-pointer hover:underline" style={{ color: "var(--ink)" }}>
                        {e.name || "未识别姓名"}
                      </button>
                      <div className="text-[11px] mt-0.5" style={{ color: "var(--mute)" }}>
                        {e.source_type === "manual" ? "手动添加" : "公司 People 页发现"}
                      </div>
                    </td>
                    <td className="py-2 px-2 max-w-[300px]">
                      <div className="truncate">{e.title || e.headline || "—"}</div>
                      {e.notes && <div className="text-[11px] truncate mt-0.5" style={{ color: "var(--mute)" }}>{e.notes}</div>}
                    </td>
                    <td className="py-2 px-2">{e.change_count ? <Badge tone="warning">{e.change_count} 次</Badge> : "—"}</td>
                    <td className="py-2 px-2 tabular-nums">{fmtNum(e.activity_count)}</td>
                    <td className="py-2 px-2 whitespace-nowrap">{e.last_seen ? fmtDateTime(e.last_seen) : "待首次采集"}</td>
                    <td className="py-2 px-2">
                      {e.last_status === "ok" || e.last_status === "partial"
                        ? <Badge tone={e.last_status === "ok" ? "positive" : "warning"}>{e.last_status === "ok" ? "正常" : "部分数据"}</Badge>
                        : e.last_status
                          ? <Badge tone="warning">{e.last_status}</Badge>
                          : <span style={{ color: "var(--mute)" }}>未采集</span>}
                    </td>
                    <td className="py-2 px-2 whitespace-nowrap">
                      <button
                        onClick={() => mutations.update.mutate({ id: e.id, monitor: !e.monitor, status: "active" })}
                        className="text-[12px] px-2 py-1 rounded-md cursor-pointer mr-2"
                        style={e.monitor ? { background: "rgba(0,112,243,0.12)", color: "var(--accent)" } : { color: "var(--mute)", border: "1px solid var(--hairline-strong)" }}
                      >
                        {e.monitor ? "重点监控中" : "设为重点"}
                      </button>
                      <button onClick={() => setDetailId(e.id)} className="text-[12px] cursor-pointer mr-2" style={{ color: "var(--accent)" }}>详情</button>
                      {e.profile_url && <a href={e.profile_url} target="_blank" rel="noreferrer" className="text-[12px] cursor-pointer mr-2" style={{ color: "var(--accent)" }}>主页</a>}
                      <button onClick={() => mutations.remove.mutate(e.id)} className="text-[12px] cursor-pointer" style={{ color: "var(--mute)" }}>删除</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            title={scope === "focus" ? "暂无重点人员" : "暂无人员数据"}
            hint="可直接添加 LinkedIn 个人主页；也可配置公司 People 页批量发现候选人，再从名单中标记重点。"
            action={<Button variant="primary" onClick={() => setAddOpen(true)}>添加重点人员</Button>}
          />
        )}
      </Card>

      <Card>
        <SectionTitle title="重点人员信号 Feed" subtitle="整合个人页职位/头衔变化与 LinkedIn 公开动态，作为招聘趋势的补充证据" />
        {visibleActivities.length ? (
          <div className="space-y-2 max-h-[480px] overflow-y-auto">
            {visibleActivities.map((a) => (
              <div key={a.id} className="p-3 rounded-md" style={{ background: "var(--bg-soft-2)" }}>
                <div className="flex items-center justify-between gap-2 mb-1">
                  <div className="flex items-center gap-2">
                    <span className="text-[13px] font-medium" style={{ color: "var(--ink)" }}>{a.profile_name || "人员"}</span>
                    <Badge tone={a.activity_type === "profile_change" ? "warning" : "neutral"}>{a.activity_type === "profile_change" ? "个人页变更" : "公开动态"}</Badge>
                  </div>
                  <span className="text-[11px]" style={{ color: "var(--mute)" }}>{a.posted_at ? fmtDateTime(a.posted_at) : fmtDateTime(a.created_at)}</span>
                </div>
                <p className="text-[13px]" style={{ color: "var(--body)" }}>{a.text}</p>
                {a.url && <a href={a.url} target="_blank" rel="noreferrer" className="text-[11px] cursor-pointer" style={{ color: "var(--accent)" }}>查看</a>}
              </div>
            ))}
          </div>
        ) : (
          <EmptyState title="暂无人员信号" hint="首次采集后会在这里展示个人页变化和公开动态；LinkedIn 抓取为最佳努力，可能受 Cookie 或风控影响。" />
        )}
      </Card>

      <AddFocusPersonModal open={addOpen} onClose={() => setAddOpen(false)} brandId={brandId} />
      <PersonDetailModal profileId={detailId} onClose={() => setDetailId(undefined)} />
    </div>
  );
}

function AddFocusPersonModal({ open, onClose, brandId }: { open: boolean; onClose: () => void; brandId: string }) {
  const { add } = useEmployeeMutations();
  const [form, setForm] = useState({ profile_url: "", name: "", title: "", notes: "" });
  const [error, setError] = useState("");

  const save = async () => {
    setError("");
    try {
      await add.mutateAsync({ brand_id: brandId, ...form, monitor: true });
      setForm({ profile_url: "", name: "", title: "", notes: "" });
      onClose();
    } catch (err: any) {
      setError(err?.message || "添加失败");
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="添加重点人员" width={600}>
      <div className="space-y-3">
        <Field label="LinkedIn 个人主页" hint="目前只支持 linkedin.com/in/... 形式的公开个人页。">
          <Input value={form.profile_url} onChange={(e) => setForm({ ...form, profile_url: e.target.value })} placeholder="https://www.linkedin.com/in/..." />
        </Field>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <Field label="姓名（可选）"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
          <Field label="当前职位（可选）"><Input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field>
        </div>
        <Field label="关注原因 / 备注（可选）"><Textarea rows={3} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} placeholder="例如：AI 团队负责人、近期加入竞品、核心销售负责人" /></Field>
        {error && <p className="text-[12px]" style={{ color: "var(--danger)" }}>{error}</p>}
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>取消</Button>
          <Button variant="primary" onClick={save} disabled={!form.profile_url || add.isPending}>{add.isPending ? "添加中…" : "添加并监控"}</Button>
        </div>
      </div>
    </Modal>
  );
}

function PersonDetailModal({ profileId, onClose }: { profileId?: string; onClose: () => void }) {
  const { data, isLoading } = useEmployeeHistory(profileId);
  if (!profileId) return null;
  const profile: LinkedInEmployee | undefined = data?.profile;
  const snapshots: any[] = data?.snapshots || [];
  const activities: any[] = data?.activities || [];

  return (
    <Modal open={!!profileId} onClose={onClose} title={profile?.name || "重点人员详情"} width={720}>
      {isLoading || !data ? <Spinner /> : (
        <div className="space-y-4">
          <div>
            <div className="text-[15px] font-medium" style={{ color: "var(--ink)" }}>{profile?.title || profile?.headline || "未识别当前职位"}</div>
            {profile?.notes && <div className="text-[13px] mt-1" style={{ color: "var(--body)" }}>{profile.notes}</div>}
            <div className="text-[12px] mt-2" style={{ color: "var(--mute)" }}>
              {profile?.snapshot_count || 0} 个个人页快照 · {profile?.change_count || 0} 次变化 · {profile?.activity_count || 0} 条信号
            </div>
            {profile?.profile_url && <a href={profile.profile_url} target="_blank" rel="noreferrer" className="text-[12px] mt-1 inline-block" style={{ color: "var(--accent)" }}>打开 LinkedIn 主页</a>}
          </div>

          <div>
            <div className="text-[13px] font-medium mb-2" style={{ color: "var(--ink)" }}>个人页变化</div>
            {snapshots.some((snapshot) => snapshot.changes?.length) ? (
              <div className="space-y-2 max-h-56 overflow-y-auto">
                {snapshots.filter((snapshot) => snapshot.changes?.length).map((snapshot) => (
                  <div key={snapshot.id} className="p-2 rounded-md text-[12px]" style={{ background: "var(--bg-soft-2)" }}>
                    <div style={{ color: "var(--mute)" }}>{fmtDate(snapshot.snapshot_date)}</div>
                    {snapshot.changes.map((change: any, index: number) => (
                      <div key={index} style={{ color: "var(--body)" }}>
                        <span className="font-medium">{change.field === "name" ? "姓名" : change.field === "headline" ? "头衔" : "职位"}</span>：{String(change.from || "—")} → {String(change.to || "—")}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            ) : <p className="text-[12px]" style={{ color: "var(--mute)" }}>暂无个人页变化</p>}
          </div>

          <div>
            <div className="text-[13px] font-medium mb-2" style={{ color: "var(--ink)" }}>最近信号</div>
            {activities.length ? (
              <div className="space-y-2 max-h-64 overflow-y-auto">
                {activities.map((activity) => (
                  <div key={activity.id} className="p-2 rounded-md text-[12px]" style={{ background: "var(--bg-soft-2)" }}>
                    <div className="flex justify-between gap-2 mb-1">
                      <Badge tone={activity.activity_type === "profile_change" ? "warning" : "neutral"}>{activity.activity_type === "profile_change" ? "个人页变更" : "公开动态"}</Badge>
                      <span style={{ color: "var(--mute)" }}>{fmtDateTime(activity.posted_at || activity.created_at)}</span>
                    </div>
                    <div style={{ color: "var(--body)" }}>{activity.text}</div>
                  </div>
                ))}
              </div>
            ) : <p className="text-[12px]" style={{ color: "var(--mute)" }}>暂无公开动态</p>}
          </div>
        </div>
      )}
    </Modal>
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
