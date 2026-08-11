import { useEffect, useState } from "react";
import { Ban, Camera, Check, Eye, RefreshCw, Star, Upload } from "lucide-react";
import { CreatorQuadrantMap } from "../components/CreatorQuadrantMap";
import { Badge, Button, Card, EmptyState, Input, Modal, SectionTitle, Select, Spinner, Textarea } from "../components/ui";
import {
  useCreatorCandidateEvidence,
  useCreatorCandidates,
  useCreatorCurationMutations,
  useCreatorMapSnapshots,
} from "../lib/hooks";
import type { CreatorCandidate, CreatorReviewStatus } from "../lib/api";
import { fmtDateTime, fmtNum } from "../lib/format";

const STATUS_META: Record<CreatorReviewStatus, { label: string; tone: "neutral" | "positive" | "accent" | "negative" }> = {
  pending: { label: "待审核", tone: "neutral" },
  approved: { label: "已入库", tone: "positive" },
  priority: { label: "重点监控", tone: "accent" },
  rejected: { label: "已排除", tone: "negative" },
};

const RELATIONSHIP_LABEL: Record<string, string> = {
  potential: "潜在合作",
  contacted: "已接洽",
  collaborating: "合作中",
  past: "历史合作",
};

const SOURCE_LABEL: Record<string, string> = {
  collected: "自动发现",
  manual: "手动导入",
  "manual+collected": "导入 + 自动证据",
};

function parseCsvLine(line: string): string[] {
  const cells: string[] = [];
  let value = "";
  let quoted = false;
  for (let index = 0; index < line.length; index += 1) {
    const char = line[index];
    if (char === '"') {
      if (quoted && line[index + 1] === '"') {
        value += '"';
        index += 1;
      } else {
        quoted = !quoted;
      }
    } else if (char === "," && !quoted) {
      cells.push(value.trim());
      value = "";
    } else {
      value += char;
    }
  }
  cells.push(value.trim());
  return cells;
}

function parseCandidateRows(raw: string): Record<string, unknown>[] {
  const lines = raw.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (!lines.length) return [];
  const first = parseCsvLine(lines[0]).map((cell) => cell.toLowerCase());
  const knownHeaders = new Set(["platform", "url", "handle", "name", "notes", "follower_count"]);
  const hasHeader = first.some((cell) => knownHeaders.has(cell));
  if (hasHeader) {
    return lines.slice(1).map(parseCsvLine).map((cells) => {
      const row: Record<string, unknown> = {};
      first.forEach((header, index) => {
        if (header && knownHeaders.has(header) && cells[index]) row[header] = cells[index];
      });
      return row;
    }).filter((row) => row.url || row.handle);
  }
  return lines.map((line) => {
    const cells = parseCsvLine(line);
    if (cells.length === 1 && /^https?:\/\//i.test(cells[0])) return { url: cells[0] };
    if (cells.length >= 2 && /^https?:\/\//i.test(cells[1])) {
      return { platform: cells[0], url: cells[1], name: cells[2], handle: cells[3], notes: cells[4] };
    }
    return { platform: cells[0], handle: cells[1], name: cells[2], notes: cells[3] };
  }).filter((row) => row.url || row.handle);
}

function CandidateImportModal({
  open,
  onClose,
  brandId,
  onImport,
  pending,
}: {
  open: boolean;
  onClose: () => void;
  brandId: string;
  onImport: (payload: { brand_id: string; rows: Record<string, unknown>[] }) => Promise<unknown>;
  pending: boolean;
}) {
  const [text, setText] = useState("");
  const [message, setMessage] = useState("");
  useEffect(() => {
    if (!open) {
      setText("");
      setMessage("");
    }
  }, [open]);
  const submit = async () => {
    const rows = parseCandidateRows(text);
    if (!rows.length) {
      setMessage("没有识别到有效红人。可每行粘贴一个主页 URL，或使用 CSV 表头。");
      return;
    }
    try {
      const result: any = await onImport({ brand_id: brandId, rows });
      setMessage(`已新增 ${result.created || 0} 个，更新 ${result.updated || 0} 个候选红人。`);
      setText("");
    } catch (error: any) {
      setMessage(error?.message || "导入失败");
    }
  };
  return (
    <Modal open={open} onClose={onClose} title="导入 YT / IG / TikTok 红人" width={720}>
      <div className="space-y-3">
        <p className="text-[13px]" style={{ color: "var(--body)" }}>
          最简单的方式是每行粘贴一个主页 URL。也支持 CSV：<code>platform,url,name,handle,notes</code>。
        </p>
        <Textarea
          rows={10}
          value={text}
          onChange={(event) => { setText(event.target.value); setMessage(""); }}
          placeholder={"https://www.youtube.com/@creator\nhttps://www.instagram.com/creator/\nhttps://www.tiktok.com/@creator"}
          className="font-mono"
        />
        {message && <div className="text-[13px]" style={{ color: message.startsWith("已新增") ? "var(--accent)" : "var(--danger)" }}>{message}</div>}
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>关闭</Button>
          <Button variant="primary" disabled={pending || !text.trim()} onClick={submit}>
            {pending ? "导入中…" : "导入候选池"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function EvidenceModal({
  candidate,
  productId,
  onClose,
  onUpdate,
  pending,
}: {
  candidate?: CreatorCandidate;
  productId?: string;
  onClose: () => void;
  onUpdate: (payload: { id: string; review_status?: CreatorReviewStatus; relationship_status?: any; notes?: string }) => Promise<unknown>;
  pending: boolean;
}) {
  const { data: evidence = [], isLoading } = useCreatorCandidateEvidence(candidate?.id, productId);
  const [notes, setNotes] = useState("");
  useEffect(() => setNotes(candidate?.notes || ""), [candidate?.id, candidate?.notes]);
  if (!candidate) return null;
  const update = async (payload: Record<string, unknown>) => onUpdate({ id: candidate.id, ...payload });
  return (
    <Modal open={!!candidate} onClose={onClose} title={`${candidate.name || candidate.handle} · 入选证据`} width={820}>
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={STATUS_META[candidate.review_status].tone}>{STATUS_META[candidate.review_status].label}</Badge>
          <Badge>{candidate.platform}</Badge>
          <span className="text-[13px]" style={{ color: "var(--mute)" }}>相关性 {Math.round(candidate.relevance_score)} · 证据 {candidate.evidence_count}</span>
          {candidate.url && <a href={candidate.url} target="_blank" rel="noreferrer" className="text-[13px] hover:underline" style={{ color: "var(--accent)" }}>打开主页</a>}
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <label className="text-[13px]" style={{ color: "var(--body)" }}>
            合作阶段
            <Select className="w-full mt-1" value={candidate.relationship_status} disabled={pending} onChange={(event) => update({ relationship_status: event.target.value })}>
              {Object.entries(RELATIONSHIP_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </Select>
          </label>
          <label className="text-[13px]" style={{ color: "var(--body)" }}>
            策展备注
            <div className="flex gap-2 mt-1">
              <Input className="flex-1" value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="入选原因、联系人、合作判断…" />
              <Button disabled={pending || notes === (candidate.notes || "")} onClick={() => update({ notes })}>保存</Button>
            </div>
          </label>
        </div>

        <div className="flex flex-wrap gap-2">
          <Button size="sm" disabled={pending} onClick={() => update({ review_status: "approved" })}><Check size={14} /> 入库</Button>
          <Button size="sm" variant="primary" disabled={pending} onClick={() => update({ review_status: "priority" })}><Star size={14} /> 重点</Button>
          <Button size="sm" variant="danger" disabled={pending} onClick={() => update({ review_status: "rejected" })}><Ban size={14} /> 排除</Button>
        </div>

        <div>
          <div className="text-[13px] font-medium mb-2" style={{ color: "var(--ink)" }}>内容证据</div>
          {isLoading ? <Spinner /> : evidence.length ? (
            <div className="space-y-2 max-h-[430px] overflow-y-auto pr-1">
              {evidence.map((item) => (
                <div key={item.id} className="rounded-md p-3" style={{ background: "var(--bg-soft)", border: "1px solid var(--hairline)" }}>
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="text-[13px] font-medium truncate" style={{ color: "var(--ink)" }}>
                        {item.url ? <a href={item.url} target="_blank" rel="noreferrer" className="hover:underline">{item.title || "关联内容"}</a> : item.title || "关联内容"}
                      </div>
                      <div className="text-[11px] mt-0.5" style={{ color: "var(--mute)" }}>{item.query ? `发现词：${item.query}` : "手动/品牌证据"}</div>
                    </div>
                    <div className="flex gap-1 shrink-0">
                      {item.product_name && <Badge tone="accent">{item.product_name}</Badge>}
                      <Badge tone={item.confidence >= 0.9 ? "positive" : "neutral"}>{Math.round(item.confidence * 100)}%</Badge>
                    </div>
                  </div>
                  {item.excerpt && <p className="text-[12px] mt-2 line-clamp-3" style={{ color: "var(--body)" }}>{item.excerpt}</p>}
                  {!!item.evidence?.signals?.length && (
                    <div className="flex flex-wrap gap-1 mt-2">
                      {item.evidence.signals.slice(0, 6).map((signal, index) => <Badge key={`${signal.kind}-${index}`}>{signal.kind}: {signal.signal}</Badge>)}
                    </div>
                  )}
                  {item.occurred_at && <div className="text-[11px] mt-2" style={{ color: "var(--mute)" }}>{fmtDateTime(item.occurred_at)}</div>}
                </div>
              ))}
            </div>
          ) : <EmptyState title="暂无内容证据" hint="这是手动加入的候选红人；后续采集到相关帖子时会自动补充证据。" />}
        </div>
      </div>
    </Modal>
  );
}

function CandidateRow({
  candidate,
  onEvidence,
  onStatus,
  pending,
}: {
  candidate: CreatorCandidate;
  onEvidence: () => void;
  onStatus: (status: CreatorReviewStatus) => void;
  pending: boolean;
}) {
  const meta = STATUS_META[candidate.review_status];
  return (
    <div className="grid grid-cols-1 lg:grid-cols-[minmax(220px,1.3fr)_100px_140px_1fr_auto] gap-3 items-center px-3 py-3 rounded-md" style={{ border: "1px solid var(--hairline)", background: "var(--panel)" }}>
      <div className="flex items-center gap-2 min-w-0">
        <div className="h-9 w-9 rounded-full grid place-items-center text-[12px] font-bold shrink-0" style={{ background: "var(--ink)", color: "var(--bg)" }}>
          {(candidate.name || candidate.handle || "?").slice(0, 1).toUpperCase()}
        </div>
        <div className="min-w-0">
          <div className="text-[13px] font-medium truncate" style={{ color: "var(--ink)" }}>{candidate.name || candidate.handle}</div>
          <div className="text-[11px] truncate" style={{ color: "var(--mute)" }}>{candidate.platform} · {SOURCE_LABEL[candidate.discovery_source] || candidate.discovery_source}</div>
        </div>
      </div>
      <div>
        <Badge tone={meta.tone}>{meta.label}</Badge>
        <div className="text-[11px] mt-1" style={{ color: "var(--mute)" }}>{RELATIONSHIP_LABEL[candidate.relationship_status]}</div>
      </div>
      <div className="text-[12px] tabular-nums" style={{ color: "var(--body)" }}>
        <div>相关性 {Math.round(candidate.relevance_score)}</div>
        <div style={{ color: "var(--mute)" }}>证据 {candidate.evidence_count} · 内容 {candidate.post_count}</div>
      </div>
      <div className="min-w-0">
        <div className="flex flex-wrap gap-1">
          {candidate.products.length ? candidate.products.slice(0, 4).map((product) => <Badge key={product.id} tone="accent">{product.name}</Badge>) : <span className="text-[12px]" style={{ color: "var(--mute)" }}>未归因具体产品</span>}
        </div>
        <div className="text-[11px] mt-1 truncate" style={{ color: "var(--mute)" }}>粉丝 {fmtNum(candidate.follower_count)} · 触达 {fmtNum(candidate.total_views)} · 互动 {fmtNum(candidate.total_engagement)}</div>
      </div>
      <div className="flex flex-wrap gap-1 justify-start lg:justify-end">
        <Button size="sm" onClick={onEvidence}><Eye size={13} /> 证据</Button>
        <Button size="sm" disabled={pending} onClick={() => onStatus("approved")} title="加入策展红人库"><Check size={13} /></Button>
        <Button size="sm" variant="primary" disabled={pending} onClick={() => onStatus("priority")} title="重点监控"><Star size={13} /></Button>
        <Button size="sm" variant="danger" disabled={pending} onClick={() => onStatus("rejected")} title="排除"><Ban size={13} /></Button>
      </div>
    </div>
  );
}

export function CreatorCuration({
  brandId,
  productId,
  platform,
}: {
  brandId: string;
  productId?: string;
  platform?: string;
}) {
  const [status, setStatus] = useState("all");
  const [query, setQuery] = useState("");
  const [importOpen, setImportOpen] = useState(false);
  const [selected, setSelected] = useState<CreatorCandidate | undefined>();
  const { data, isLoading } = useCreatorCandidates(brandId, platform, productId, status === "all" ? undefined : status, query || undefined);
  const { data: snapshots = [] } = useCreatorMapSnapshots(brandId, platform, productId);
  const mutations = useCreatorCurationMutations();
  const candidates = data?.candidates || [];
  const totals = data?.totals || { all: 0, pending: 0, approved: 0, priority: 0, rejected: 0, curated: 0 };
  const map = data?.curated_map || { points: [], quadrants: [] };
  const update = async (payload: any) => {
    await mutations.updateCandidate.mutateAsync(payload);
    if (selected?.id === payload.id) setSelected((current) => current ? { ...current, ...payload } : current);
  };

  return (
    <div className="space-y-4">
      <Card>
        <SectionTitle
          title="候选发现与人工策展"
          subtitle="先收集候选和入选证据，再由人工决定谁进入品牌红人地图；这是 Gladia Market Map 的核心工作流。"
          action={(
            <div className="flex flex-wrap gap-2">
              <Button size="sm" onClick={() => mutations.rebuild.mutate(brandId)} disabled={mutations.rebuild.isPending}><RefreshCw size={14} className={mutations.rebuild.isPending ? "animate-spin" : ""} /> 从现有内容重建</Button>
              <Button size="sm" variant="primary" onClick={() => setImportOpen(true)}><Upload size={14} /> 导入红人</Button>
            </div>
          )}
        />
        <div className="grid grid-cols-2 md:grid-cols-5 gap-2 mb-4">
          {[
            ["候选总数", totals.all], ["待审核", totals.pending], ["已入库", totals.approved], ["重点监控", totals.priority], ["已排除", totals.rejected],
          ].map(([label, value]) => (
            <div key={String(label)} className="rounded-md px-3 py-2" style={{ background: "var(--bg-soft)", border: "1px solid var(--hairline)" }}>
              <div className="text-[11px]" style={{ color: "var(--mute)" }}>{label}</div>
              <div className="text-[20px] font-semibold tabular-nums" style={{ color: "var(--ink)" }}>{fmtNum(Number(value))}</div>
            </div>
          ))}
        </div>
        <div className="flex flex-wrap gap-2 mb-3">
          <Select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="all">全部审核状态</option>
            <option value="pending">待审核</option>
            <option value="approved">已入库</option>
            <option value="priority">重点监控</option>
            <option value="rejected">已排除</option>
          </Select>
          <Input className="w-60" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索红人 / 备注…" />
          <div className="text-[12px] self-center" style={{ color: "var(--mute)" }}>YT 可自动发现；IG / TikTok 可先导入主页，后续自动补证据。</div>
        </div>
        {isLoading ? <Spinner /> : candidates.length ? (
          <div className="space-y-2">
            {candidates.map((candidate) => (
              <CandidateRow
                key={candidate.id}
                candidate={candidate}
                pending={mutations.updateCandidate.isPending}
                onEvidence={() => setSelected(candidate)}
                onStatus={(review_status) => update({ id: candidate.id, review_status })}
              />
            ))}
          </div>
        ) : (
          <EmptyState
            title="候选池还是空的"
            hint="配置 YouTube API 后同步，或先导入 IG / TikTok / YouTube 红人主页。手动候选也可以先审核和进入观察池。"
            action={<Button variant="primary" onClick={() => setImportOpen(true)}><Upload size={14} /> 导入第一批红人</Button>}
          />
        )}
      </Card>

      <Card>
        <SectionTitle
          title="策展红人四象限"
          subtitle="仅展示标记为「已入库」或「重点监控」的红人；手动导入但暂无内容证据的红人进入观察池。"
          action={<Button size="sm" disabled={!map.points.length || mutations.saveSnapshot.isPending} onClick={() => mutations.saveSnapshot.mutate({ brand_id: brandId, product_id: productId, platform })}><Camera size={14} /> {mutations.saveSnapshot.isPending ? "保存中…" : "保存本期地图"}</Button>}
        />
        <CreatorQuadrantMap points={map.points} quadrants={map.quadrants as any} />
        {!!snapshots.length && (
          <div className="mt-4 pt-3 flex flex-wrap gap-2" style={{ borderTop: "1px solid var(--hairline)" }}>
            <span className="text-[12px] self-center" style={{ color: "var(--mute)" }}>地图快照：</span>
            {snapshots.slice(0, 8).map((snapshot) => <Badge key={snapshot.id}>{snapshot.snapshot_date} · {snapshot.map.points.length} 人</Badge>)}
          </div>
        )}
      </Card>

      <CandidateImportModal
        open={importOpen}
        onClose={() => setImportOpen(false)}
        brandId={brandId}
        pending={mutations.importCandidates.isPending}
        onImport={(payload) => mutations.importCandidates.mutateAsync(payload)}
      />
      <EvidenceModal
        candidate={selected}
        productId={productId}
        onClose={() => setSelected(undefined)}
        pending={mutations.updateCandidate.isPending}
        onUpdate={update}
      />
    </div>
  );
}
