import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Badge, Button, Card, EmptyState, Field, InfoHint, Input, Modal, SectionTitle, SegmentGroup, Spinner } from "../components/ui";
import { CollectionStatusChip } from "../components/CollectionStatusChip";
import {
  useAiDraft,
  useBrandMutations,
  useBrands,
  useCatalogMutations,
  useCreateFromDraft,
  useLinks,
  useProducts,
  useSalesSync,
  useSettings,
} from "../lib/hooks";
import { AUTOMATED_SALES_CHANNELS, SECTION_BY_KEY, TOUCHPOINTS, type TouchpointPlatform, type TouchpointSection } from "../lib/touchpoints";
import type { Brand, BrandDraft, Link } from "../lib/api";

const CUSTOM_CHANNEL: Record<string, string> = { sales: "other_ecom", social: "social", community: "community" };

const KEYWORD_HINT = (
  <span>
    监控关键词决定<b>媒体公关(PR)等渠道的抓取范围</b>:系统会用每个关键词<b>分别</b>去 Google News、Reddit、YouTube 等源搜索品牌内容。
    <br />
    建议穷举品牌的<b>所有写法</b>——中文名、英文名、缩写、别称、旧名,以及核心子品牌 / 产品线名。漏写某种写法,只用该写法报道的内容会<b>整批抓不到</b>。
    <br />
    留空则<b>仅用品牌名</b>搜索;不要填品类等宽泛词(会引入大量无关报道)。
  </span>
);

function platformChannel(sectionKey: string, platform: string): string {
  const preset = SECTION_BY_KEY[sectionKey]?.platforms.find((p) => p.platform === platform);
  return preset?.channel || CUSTOM_CHANNEL[sectionKey] || "other_ecom";
}

// Tag the configured sales link with its role so the runner / UI know whether it
// is a multi-listing storefront or a single product listing.
function salesConfig(channel: string, url: string): Record<string, string> {
  const lower = url.toLowerCase();
  if (channel === "amazon") {
    const isStorefront = lower.includes("me=") || lower.includes("/s?") || lower.includes("/stores/");
    return { role: isStorefront ? "storefront" : "listing" };
  }
  const single = /\/(products?|product|item|dp|p)\//.test(lower);
  return { role: single ? "listing" : "storefront" };
}

function sectionLinks(section: TouchpointSection, links: Link[]): Link[] {
  if (section.key === "sales") return links.filter((l) => l.dimension === "sales");
  return links.filter((l) => l.dimension === "marketing" && l.channel === section.key);
}

export default function Brands() {
  const { data: brands = [], isLoading } = useBrands();
  const [createOpen, setCreateOpen] = useState(false);
  const [selected, setSelected] = useState<string | undefined>();
  if (isLoading) return <Spinner />;
  const brand = brands.find((b) => b.id === selected) || brands[0];

  return (
    <div className="space-y-6">
      <SectionTitle title="品牌管理" subtitle="维护自家品牌与竞品、产品层级与全触点（销售 / 社媒 / 社群）配置" action={<Button variant="primary" onClick={() => setCreateOpen(true)}>+ 新增品牌</Button>} />

      {!brands.length ? (
        <EmptyState title="还没有品牌" hint="添加你的第一个品牌，可输入关键词让 AI 自动生成全触点草稿。" action={<Button variant="primary" onClick={() => setCreateOpen(true)}>+ 新增品牌</Button>} />
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <Card>
            <SectionTitle title="品牌列表" />
            <div className="space-y-1.5">
              {brands.map((b) => (
                <button key={b.id} onClick={() => setSelected(b.id)} className="w-full text-left px-3 py-2.5 rounded-md cursor-pointer" style={{ background: brand?.id === b.id ? "var(--bg-soft-2)" : "transparent" }}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[14px] font-medium" style={{ color: "var(--ink)" }}>{b.name}</span>
                    {b.is_primary ? <Badge tone="positive">自家</Badge> : b.is_competitor ? <Badge tone="warning">竞品</Badge> : null}
                  </div>
                  {b.category && <div className="text-[12px] mt-0.5" style={{ color: "var(--mute)" }}>{b.category}</div>}
                </button>
              ))}
            </div>
          </Card>
          <div className="lg:col-span-2">{brand && <BrandDetail key={brand.id} brand={brand} />}</div>
        </div>
      )}

      <CreateModal open={createOpen} onClose={() => setCreateOpen(false)} />
    </div>
  );
}

function BrandDetail({ brand }: { brand: Brand }) {
  const navigate = useNavigate();
  const { update, remove } = useBrandMutations();
  const { data: products = [] } = useProducts(brand.id);
  const { data: links = [] } = useLinks(brand.id);
  const { addProduct, delProduct } = useCatalogMutations();
  const [keywords, setKeywords] = useState(brand.monitoring_keywords.join(", "));
  const [productName, setProductName] = useState("");

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-[18px] font-semibold tracking-tight" style={{ color: "var(--ink)" }}>{brand.name}</h3>
              {brand.is_primary ? <Badge tone="positive">自家</Badge> : brand.is_competitor ? <Badge tone="warning">竞品</Badge> : null}
            </div>
            {brand.official_website && <a href={brand.official_website} target="_blank" rel="noreferrer" className="text-[13px] hover:underline" style={{ color: "var(--accent)" }}>{brand.official_website}</a>}
          </div>
          <div className="flex gap-2">
            <Button size="sm" onClick={() => navigate(`/brand/${brand.id}/overview`)}>进入工作区</Button>
            <Button size="sm" variant="danger" onClick={() => remove.mutate(brand.id)}>删除</Button>
          </div>
        </div>
        {brand.description && <p className="text-[13px] mt-2" style={{ color: "var(--body)" }}>{brand.description}</p>}

        <div className="mt-3 grid grid-cols-1 md:grid-cols-[1fr_auto] gap-2 items-end">
          <Field label="监控关键词(逗号分隔)" hint={KEYWORD_HINT}><Input value={keywords} onChange={(e) => setKeywords(e.target.value)} /></Field>
          <Button onClick={() => update.mutate({ ...brand, monitoring_keywords: keywords.split(",").map((k) => k.trim()).filter(Boolean) })}>保存关键词</Button>
        </div>
        <div className="flex gap-2 mt-3">
          <Button size="sm" variant={brand.is_competitor ? "primary" : "secondary"} onClick={() => update.mutate({ ...brand, is_competitor: !brand.is_competitor })}>{brand.is_competitor ? "✓ 标记为竞品" : "标记为竞品"}</Button>
          <Button size="sm" variant={brand.is_primary ? "primary" : "secondary"} onClick={() => update.mutate({ ...brand, is_primary: !brand.is_primary })}>{brand.is_primary ? "✓ 自家品牌" : "设为自家"}</Button>
        </div>
      </Card>

      <Card>
        <SectionTitle title="产品" />
        <div className="flex gap-2 mb-3">
          <Input value={productName} onChange={(e) => setProductName(e.target.value)} placeholder="产品名称" />
          <Button onClick={() => { if (productName) { addProduct.mutate({ brand_id: brand.id, name: productName }); setProductName(""); } }}>添加</Button>
        </div>
        {products.length ? (
          <div className="flex flex-wrap gap-2">
            {products.map((p) => (
              <span key={p.id} className="inline-flex items-center gap-2 px-2.5 py-1 rounded-md text-[13px]" style={{ background: "var(--bg-soft-2)", color: "var(--body)" }}>
                {p.name}
                <button onClick={() => delProduct.mutate(p.id)} className="cursor-pointer" style={{ color: "var(--mute)" }}>×</button>
              </span>
            ))}
          </div>
        ) : (
          <p className="text-[13px]" style={{ color: "var(--mute)" }}>暂无产品</p>
        )}
      </Card>

      {TOUCHPOINTS.map((section) => (
        <TouchpointCard key={section.key} brandId={brand.id} section={section} links={sectionLinks(section, links)} />
      ))}
    </div>
  );
}

function CrawlHint({ tp }: { tp: TouchpointPlatform }) {
  if (!tp.dataSource && !tp.method && !tp.requires) return null;
  return (
    <InfoHint
      text={
        <div className="space-y-1.5">
          {tp.dataSource && <div><span style={{ color: "var(--mute)" }}>数据来源：</span>{tp.dataSource}</div>}
          {tp.method && <div><span style={{ color: "var(--mute)" }}>采集方式：</span>{tp.method}</div>}
          {tp.requires && <div><span style={{ color: "var(--mute)" }}>前置条件：</span>{tp.requires}</div>}
        </div>
      }
    />
  );
}

function TouchpointCard({ brandId, section, links }: { brandId: string; section: TouchpointSection; links: Link[] }) {
  const presetKeys = new Set(section.platforms.map((p) => p.platform));
  const customLinks = links.filter((l) => !l.platform || !presetKeys.has(l.platform));
  const [customName, setCustomName] = useState("");
  const [customUrl, setCustomUrl] = useState("");
  const { addLink } = useCatalogMutations();

  return (
    <Card>
      <SectionTitle
        title={section.title}
        subtitle={section.key === "sales" ? "填入店铺/品牌页或单品链接，保存后自动展开 Listing 并开启监控" : "填入官方账号 / 店铺链接，留空表示未配置"}
      />
      <div className="space-y-2">
        {section.platforms.map((tp) => {
          if (tp.multi) {
            const matching = links.filter((l) => l.platform === tp.platform);
            return (
              <div key={tp.platform} className="rounded-md p-2.5" style={{ background: "var(--bg-soft)", border: "1px solid var(--hairline)" }}>
                <div className="flex items-center gap-1.5 mb-2">
                  <span className="text-[13px] font-medium" style={{ color: "var(--ink)" }}>{tp.label}</span>
                  <CrawlHint tp={tp} />
                  {matching.length > 0 && <span className="text-[12px]" style={{ color: "var(--mute)" }}>· {matching.length} 个来源</span>}
                </div>
                <div className="space-y-1.5">
                  {matching.map((l) => (
                    <TouchpointSlot key={l.id} brandId={brandId} section={section} platform={tp.platform} label="" channel={tp.channel} placeholder={tp.placeholder} link={l} grouped />
                  ))}
                  <TouchpointSlot
                    key={`${tp.platform}-new`}
                    brandId={brandId}
                    section={section}
                    platform={tp.platform}
                    label=""
                    channel={tp.channel}
                    placeholder={matching.length ? `+ 再添加一个 ${tp.label}` : tp.placeholder || `+ 添加 ${tp.label}`}
                    grouped
                  />
                </div>
              </div>
            );
          }
          return (
            <TouchpointSlot key={tp.platform} brandId={brandId} section={section} platform={tp.platform} label={tp.label} channel={tp.channel} placeholder={tp.placeholder} link={links.find((l) => l.platform === tp.platform)} crawlInfo={tp} />
          );
        })}
        {customLinks.map((l) => (
          <TouchpointSlot key={l.id} brandId={brandId} section={section} platform={l.platform || "custom"} label={l.platform || "自定义"} channel={l.channel} link={l} />
        ))}
      </div>

      <div className="flex gap-2 mt-3 pt-3" style={{ borderTop: "1px solid var(--hairline)" }}>
        <Input value={customName} onChange={(e) => setCustomName(e.target.value)} placeholder="自定义平台名" className="max-w-[160px]" />
        <Input value={customUrl} onChange={(e) => setCustomUrl(e.target.value)} placeholder="URL" />
        <Button onClick={() => { if (customName && customUrl) { addLink.mutate({ brand_id: brandId, dimension: section.dimension, channel: CUSTOM_CHANNEL[section.key], platform: customName.trim(), url: customUrl.trim(), label: customName.trim() }); setCustomName(""); setCustomUrl(""); } }}>添加</Button>
      </div>
    </Card>
  );
}

type SlotFeedback = { kind: "pending" | "ok" | "saved" | "err"; text?: string };

function TouchpointSlot({ brandId, section, platform, label, channel, placeholder, link, grouped, crawlInfo }: { brandId: string; section: TouchpointSection; platform: string; label: string; channel: string; placeholder?: string; link?: Link; grouped?: boolean; crawlInfo?: TouchpointPlatform }) {
  const { addLink, updateLink, delLink } = useCatalogMutations();
  const sync = useSalesSync();
  const [val, setVal] = useState(link?.url || "");
  const [feedback, setFeedback] = useState<SlotFeedback | null>(null);
  useEffect(() => { setVal(link?.url || ""); }, [link?.id, link?.url]);

  const isAutoSales = section.dimension === "sales" && AUTOMATED_SALES_CHANNELS.has(channel);
  const paused = link?.status === "paused";
  const url = val.trim();
  const dirty = url !== (link?.url || "");
  const busy = addLink.isPending || updateLink.isPending || sync.isPending;

  const runSync = async (linkId: string) => {
    setFeedback({ kind: "pending" });
    try {
      const res: any = await sync.mutateAsync({ brandId, linkId });
      const n = res?.listings ?? 0;
      setFeedback(n > 0 ? { kind: "ok", text: `✓ 展开 ${n} 个 Listing` } : { kind: "ok", text: "✓ 已保存，未发现 Listing（可改用店铺/商品页）" });
    } catch (e: any) {
      setFeedback({ kind: "err", text: e?.message || "同步失败" });
    }
  };

  const save = async () => {
    if (!url) return;
    let linkId = link?.id;
    if (link) {
      if (dirty) await updateLink.mutateAsync({ id: link.id, url });
    } else {
      const created: any = await addLink.mutateAsync({ brand_id: brandId, dimension: section.dimension, channel, platform, url, label, config: isAutoSales ? salesConfig(channel, url) : undefined });
      linkId = created?.id;
    }
    if (isAutoSales && linkId) await runSync(linkId);
    else setFeedback({ kind: "saved", text: "✓ 已保存" });
  };

  const clear = async () => {
    setVal("");
    setFeedback(null);
    if (link) await delLink.mutateAsync(link.id);
  };

  return (
    <div className="flex items-center gap-2" style={paused ? { opacity: 0.6 } : undefined}>
      {!grouped && (
        <span className="w-28 shrink-0 text-[13px] flex items-center gap-1.5" style={{ color: link ? "var(--ink)" : "var(--mute)" }}>
          <span className="h-1.5 w-1.5 rounded-full shrink-0" style={{ background: link && !dirty ? "var(--accent)" : "var(--hairline-strong)" }} />
          {label}
          {crawlInfo && <CrawlHint tp={crawlInfo} />}
        </span>
      )}
      {grouped && (
        <span className="h-1.5 w-1.5 rounded-full shrink-0" style={{ background: link && !dirty ? "var(--accent)" : "var(--hairline-strong)" }} />
      )}
      <Input
        value={val}
        onChange={(e) => { setVal(e.target.value); setFeedback(null); }}
        onKeyDown={(e) => { if (e.key === "Enter" && dirty && url) save(); }}
        placeholder={placeholder || `${label} 链接`}
      />

      {/* explicit save when there are unsaved edits */}
      {dirty && url && (
        <Button size="sm" variant="primary" disabled={busy} onClick={save} className="shrink-0 whitespace-nowrap">
          {busy ? "处理中…" : isAutoSales ? "保存并同步" : "保存"}
        </Button>
      )}

      {/* re-sync an already-saved sales link */}
      {!dirty && link && isAutoSales && (
        <Button size="sm" disabled={busy} onClick={() => runSync(link.id)} className="shrink-0 whitespace-nowrap">
          {sync.isPending ? "同步中…" : "同步"}
        </Button>
      )}

      {/* status feedback */}
      {feedback && (
        <span
          className="text-[12px] shrink-0 max-w-[220px] truncate"
          title={feedback.text}
          style={{ color: feedback.kind === "err" ? "var(--danger)" : feedback.kind === "pending" ? "var(--mute)" : "var(--accent)" }}
        >
          {feedback.kind === "pending" ? "同步中…" : feedback.text}
        </span>
      )}

      {/* persisted crawl status for already-saved links (click chip for reason + fix) */}
      {!feedback && !dirty && link && (
        paused
          ? <span className="text-[12px] shrink-0 px-2 h-[22px] inline-flex items-center rounded-full" style={{ background: "var(--bg-soft-2)", color: "var(--mute)", border: "1px solid var(--hairline)" }}>已暂停采集</span>
          : <CollectionStatusChip lastStatus={link.last_status} lastError={link.last_error} lastCollectAt={link.last_collect_at} />
      )}

      {link && !dirty && (
        <button
          onClick={() => updateLink.mutate({ id: link.id, status: paused ? "active" : "paused" })}
          className="text-[12px] cursor-pointer shrink-0"
          style={{ color: paused ? "var(--accent)" : "var(--mute)" }}
        >
          {paused ? "恢复采集" : "暂停采集"}
        </button>
      )}
      {link && <button onClick={clear} className="text-[12px] cursor-pointer shrink-0" style={{ color: "var(--mute)" }}>清除</button>}
    </div>
  );
}

// ------------------------------------------------------------------- create modal
function CreateModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [mode, setMode] = useState<"ai" | "manual">("ai");
  return (
    <Modal open={open} onClose={onClose} title="新增品牌" width={680}>
      <div className="mb-4">
        <SegmentGroup value={mode} options={[{ value: "ai", label: "AI 智能生成" }, { value: "manual", label: "手动" }]} onChange={setMode} />
      </div>
      {mode === "ai" ? <AiCreate onClose={onClose} /> : <ManualCreate onClose={onClose} />}
    </Modal>
  );
}

function AiCreate({ onClose }: { onClose: () => void }) {
  const { data: settings } = useSettings();
  const aiDraft = useAiDraft();
  const createFromDraft = useCreateFromDraft();
  const sync = useSalesSync();
  const [keyword, setKeyword] = useState("");
  const [draft, setDraft] = useState<BrandDraft | null>(null);
  const [error, setError] = useState("");

  const generate = async () => {
    setError("");
    try {
      setDraft(await aiDraft.mutateAsync(keyword));
    } catch (e: any) {
      setError(e.message);
    }
  };

  const confirm = async () => {
    if (!draft) return;
    const links = [
      ...draft.sales.map((s) => ({ dimension: "sales", channel: platformChannel("sales", s.platform), platform: s.platform, url: s.url })),
      ...draft.social.map((s) => ({ dimension: "marketing", channel: "social", platform: s.platform, url: s.url })),
      ...draft.community.map((s) => ({ dimension: "marketing", channel: "community", platform: s.platform, url: s.url })),
    ].filter((l) => l.url && l.url.trim());
    const created = await createFromDraft.mutateAsync({
      name: draft.name,
      category: draft.category,
      description: draft.description,
      official_website: draft.official_website,
      is_competitor: !!draft.is_competitor,
      monitoring_keywords: draft.monitoring_keywords,
      products: draft.products.filter((p) => p.name?.trim()),
      links,
    });
    const brandId = (created as { id?: string })?.id;
    if (brandId && links.some((l) => l.dimension === "sales")) {
      sync.mutate({ brandId });
    }
    onClose();
  };

  if (settings && !settings.configured) {
    return (
      <div className="text-[13px] space-y-2" style={{ color: "var(--body)" }}>
        <p>尚未配置大模型 Token。请点击右上角的设置图标填写 API Key 后再使用 AI 生成。</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <Field label="关键词（品牌名）">
        <div className="flex gap-2">
          <Input value={keyword} onChange={(e) => setKeyword(e.target.value)} placeholder="如 Anker" onKeyDown={(e) => e.key === "Enter" && generate()} />
          <Button variant="primary" onClick={generate} disabled={!keyword || aiDraft.isPending}>{aiDraft.isPending ? "生成中…" : "生成"}</Button>
        </div>
      </Field>
      {error && <div className="text-[13px] p-2 rounded-md" style={{ background: "var(--danger-soft)", color: "var(--danger)" }}>{error}</div>}

      {draft && (
        <div className="space-y-4 max-h-[55vh] overflow-y-auto pr-1">
          <div className="grid grid-cols-2 gap-3">
            <Field label="品牌名"><Input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></Field>
            <Field label="品类"><Input value={draft.category || ""} onChange={(e) => setDraft({ ...draft, category: e.target.value })} /></Field>
          </div>
          <Field label="简介"><Input value={draft.description || ""} onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></Field>
          <Field label="官网"><Input value={draft.official_website || ""} onChange={(e) => setDraft({ ...draft, official_website: e.target.value })} /></Field>
          <Field label="监控关键词(逗号分隔)" hint={KEYWORD_HINT}><Input value={draft.monitoring_keywords.join(", ")} onChange={(e) => setDraft({ ...draft, monitoring_keywords: e.target.value.split(",").map((k) => k.trim()).filter(Boolean) })} /></Field>
          <label className="flex items-center gap-2 text-[14px]" style={{ color: "var(--body)" }}>
            <input type="checkbox" checked={!!draft.is_competitor} onChange={(e) => setDraft({ ...draft, is_competitor: e.target.checked })} /> 标记为竞品
          </label>

          <DraftListEditor title="产品" items={draft.products.map((p) => ({ a: p.name, b: p.category || "" }))} aPlaceholder="产品名" bPlaceholder="品类" onChange={(rows) => setDraft({ ...draft, products: rows.map((r) => ({ name: r.a, category: r.b })) })} />
          <DraftLinkEditor title="销售渠道" items={draft.sales} onChange={(items) => setDraft({ ...draft, sales: items })} />
          <DraftLinkEditor title="社媒" items={draft.social} onChange={(items) => setDraft({ ...draft, social: items })} />
          <DraftLinkEditor title="社群" items={draft.community} onChange={(items) => setDraft({ ...draft, community: items })} />
        </div>
      )}

      <div className="flex justify-end gap-2 pt-2">
        <Button onClick={onClose}>取消</Button>
        <Button variant="primary" disabled={!draft || createFromDraft.isPending} onClick={confirm}>{createFromDraft.isPending ? "创建中…" : "确认创建"}</Button>
      </div>
    </div>
  );
}

function DraftLinkEditor({ title, items, onChange }: { title: string; items: { platform: string; url: string }[]; onChange: (items: { platform: string; url: string }[]) => void }) {
  const update = (i: number, k: "platform" | "url", v: string) => onChange(items.map((it, idx) => (idx === i ? { ...it, [k]: v } : it)));
  return (
    <div>
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-[13px] font-medium" style={{ color: "var(--ink)" }}>{title}</span>
        <button onClick={() => onChange([...items, { platform: "", url: "" }])} className="text-[12px] cursor-pointer" style={{ color: "var(--accent)" }}>+ 添加</button>
      </div>
      {items.length === 0 && <p className="text-[12px]" style={{ color: "var(--mute)" }}>无</p>}
      <div className="space-y-1.5">
        {items.map((it, i) => (
          <div key={i} className="flex items-center gap-2">
            <Input value={it.platform} onChange={(e) => update(i, "platform", e.target.value)} placeholder="平台" className="max-w-[140px]" />
            <Input value={it.url} onChange={(e) => update(i, "url", e.target.value)} placeholder="URL" />
            <button onClick={() => onChange(items.filter((_, idx) => idx !== i))} className="text-[12px] cursor-pointer shrink-0" style={{ color: "var(--mute)" }}>删除</button>
          </div>
        ))}
      </div>
    </div>
  );
}

function DraftListEditor({ title, items, aPlaceholder, bPlaceholder, onChange }: { title: string; items: { a: string; b: string }[]; aPlaceholder: string; bPlaceholder: string; onChange: (rows: { a: string; b: string }[]) => void }) {
  const update = (i: number, k: "a" | "b", v: string) => onChange(items.map((it, idx) => (idx === i ? { ...it, [k]: v } : it)));
  return (
    <div>
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-[13px] font-medium" style={{ color: "var(--ink)" }}>{title}</span>
        <button onClick={() => onChange([...items, { a: "", b: "" }])} className="text-[12px] cursor-pointer" style={{ color: "var(--accent)" }}>+ 添加</button>
      </div>
      {items.length === 0 && <p className="text-[12px]" style={{ color: "var(--mute)" }}>无</p>}
      <div className="space-y-1.5">
        {items.map((it, i) => (
          <div key={i} className="flex items-center gap-2">
            <Input value={it.a} onChange={(e) => update(i, "a", e.target.value)} placeholder={aPlaceholder} />
            <Input value={it.b} onChange={(e) => update(i, "b", e.target.value)} placeholder={bPlaceholder} className="max-w-[160px]" />
            <button onClick={() => onChange(items.filter((_, idx) => idx !== i))} className="text-[12px] cursor-pointer shrink-0" style={{ color: "var(--mute)" }}>删除</button>
          </div>
        ))}
      </div>
    </div>
  );
}

function ManualCreate({ onClose }: { onClose: () => void }) {
  const { create, analyze } = useBrandMutations();
  const [form, setForm] = useState<any>({ name: "", official_website: "", is_competitor: false, monitoring_keywords: [] });
  const [url, setUrl] = useState("");

  const runAnalyze = async () => {
    if (!url) return;
    const data = await analyze.mutateAsync(url);
    setForm((f: any) => ({ ...f, name: data.name || f.name, official_website: data.official_website || url, description: data.description, logo_url: data.logo_url, social_links: data.social_links || {}, ecommerce_links: data.ecommerce_links || {} }));
  };

  return (
    <div className="space-y-3">
      <Field label="官网 URL（可自动抓取信息）">
        <div className="flex gap-2">
          <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://brand.com" />
          <Button onClick={runAnalyze} disabled={analyze.isPending}>{analyze.isPending ? "抓取中…" : "抓取"}</Button>
        </div>
      </Field>
      <Field label="品牌名称"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
      <Field label="监控关键词(逗号分隔)" hint={KEYWORD_HINT}>
        <Input defaultValue="" onChange={(e) => setForm({ ...form, monitoring_keywords: e.target.value.split(",").map((k) => k.trim()).filter(Boolean) })} placeholder="中文名, 英文名, 缩写/别称, 子品牌" />
      </Field>
      <label className="flex items-center gap-2 text-[14px]" style={{ color: "var(--body)" }}>
        <input type="checkbox" checked={form.is_competitor} onChange={(e) => setForm({ ...form, is_competitor: e.target.checked })} /> 标记为竞品
      </label>
      <div className="flex justify-end gap-2 pt-1">
        <Button onClick={onClose}>取消</Button>
        <Button variant="primary" disabled={!form.name || create.isPending} onClick={async () => { await create.mutateAsync(form); onClose(); }}>创建</Button>
      </div>
    </div>
  );
}
