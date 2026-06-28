import { NavLink, useNavigate, useParams } from "react-router-dom";
import {
  ChevronDown,
  Globe,
  LayoutDashboard,
  Megaphone,
  MessageSquare,
  Moon,
  Plug,
  Settings,
  ShoppingCart,
  Sparkles,
  Sun,
  Users,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useBrands, useSaveSettings, useSettings, useTheme } from "../lib/hooks";
import { Badge, Button, Field, Input, Modal } from "./ui";

const NAV = [
  { to: "overview", label: "经营总览", icon: LayoutDashboard },
  { to: "sales", label: "销售监控", icon: ShoppingCart },
  { to: "marketing", label: "营销监控", icon: Megaphone },
  { to: "creators", label: "红人达人", icon: Sparkles },
  { to: "voice", label: "用户之声", icon: MessageSquare },
  { to: "web", label: "网页快照", icon: Globe },
  { to: "sources", label: "数据源采集", icon: Plug },
];

function BrandSwitcher({ brandId }: { brandId?: string }) {
  const { data: brands = [] } = useBrands();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const current = brands.find((b) => b.id === brandId);
  return (
    <div className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-2 h-9 px-3 rounded-md cursor-pointer"
        style={{ background: "var(--bg-soft)", border: "1px solid var(--hairline-strong)" }}
      >
        <span className="h-5 w-5 rounded grid place-items-center text-[11px] font-bold" style={{ background: "var(--ink)", color: "var(--bg)" }}>
          {(current?.name || "?").slice(0, 1).toUpperCase()}
        </span>
        <span className="text-[14px] font-medium" style={{ color: "var(--ink)" }}>{current?.name || "选择品牌"}</span>
        {current?.is_competitor && <Badge tone="warning">竞品</Badge>}
        <ChevronDown size={14} style={{ color: "var(--mute)" }} />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute z-50 mt-1 w-64 panel py-1 max-h-80 overflow-auto" style={{ boxShadow: "var(--shadow)" }}>
            {brands.map((b) => (
              <button
                key={b.id}
                onClick={() => {
                  setOpen(false);
                  navigate(`/brand/${b.id}/overview`);
                }}
                className="w-full flex items-center justify-between px-3 py-2 text-[14px] cursor-pointer hover:opacity-80"
                style={{ color: b.id === brandId ? "var(--ink)" : "var(--body)", background: b.id === brandId ? "var(--bg-soft-2)" : "transparent" }}
              >
                <span className="truncate">{b.name}</span>
                {b.is_competitor ? <Badge tone="warning">竞品</Badge> : b.is_primary ? <Badge tone="positive">自家</Badge> : null}
              </button>
            ))}
            <div style={{ borderTop: "1px solid var(--hairline)" }} className="mt-1 pt-1">
              <button
                onClick={() => {
                  setOpen(false);
                  navigate("/brands");
                }}
                className="w-full text-left px-3 py-2 text-[13px] cursor-pointer"
                style={{ color: "var(--accent)" }}
              >
                + 管理品牌 / 产品 / 链接
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

export function SettingsModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { data } = useSettings();
  const save = useSaveSettings();
  const [form, setForm] = useState<any>({});
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (open && data) {
      setForm({ base_url: data.base_url, model: data.model, app_title: data.app_title, max_tokens: Number(data.max_tokens) || 4096, api_key: "", sellersprite_secret_key: "", ensembledata_token: "", youtube_api_key: "" });
      setSaved(false);
      setError("");
    }
  }, [open, data]);
  const set = (k: string, v: any) => { setForm((f: any) => ({ ...f, [k]: v })); setSaved(false); };
  return (
    <Modal open={open} onClose={onClose} title="大模型设置">
      <div className="space-y-3">
        <div className="flex items-center gap-2 text-[13px]" style={{ color: "var(--mute)" }}>
          状态：{data?.configured ? <Badge tone="positive">已配置 Token</Badge> : <Badge tone="warning">未配置 Token</Badge>}
          {data?.configured && data?.key_hint && <span style={{ color: "var(--mute)" }}>当前：{data.key_hint}</span>}
        </div>
        <Field label={data?.configured ? "API Key（已保存，留空表示不修改）" : "API Key"}>
          <Input type="password" value={form.api_key || ""} onChange={(e) => set("api_key", e.target.value)} placeholder={data?.configured ? "已保存，如需更换请输入新 Key" : "sk-..."} />
        </Field>
        <Field label="Base URL"><Input value={form.base_url || ""} onChange={(e) => set("base_url", e.target.value)} placeholder="https://agent-api.shuiditech.com/api/v1/messages" /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="模型"><Input value={form.model || ""} onChange={(e) => set("model", e.target.value)} placeholder="claude-opus-4.8" /></Field>
          <Field label="X-WP-Title"><Input value={form.app_title || ""} onChange={(e) => set("app_title", e.target.value)} placeholder="monitor-hub" /></Field>
        </div>
        <Field label="max_tokens"><Input type="number" value={form.max_tokens || 4096} onChange={(e) => set("max_tokens", Number(e.target.value))} /></Field>

        <div className="pt-2" style={{ borderTop: "1px solid var(--hairline)" }}>
          <div className="flex items-center gap-2 text-[13px] mb-2" style={{ color: "var(--mute)" }}>
            卖家精灵 API：{data?.sellersprite_configured ? <Badge tone="positive">已配置</Badge> : <Badge tone="warning">未配置（销售监控将走爬取）</Badge>}
            {data?.sellersprite_configured && data?.sellersprite_key_hint && <span>当前：{data.sellersprite_key_hint}</span>}
          </div>
          <Field label={data?.sellersprite_configured ? "secret-key（已保存，留空表示不修改）" : "secret-key（可选，付费 OpenAPI）"}>
            <Input type="password" value={form.sellersprite_secret_key || ""} onChange={(e) => set("sellersprite_secret_key", e.target.value)} placeholder="配置后 Amazon 销量/排名优先使用卖家精灵" />
          </Field>
        </div>

        <div className="pt-2" style={{ borderTop: "1px solid var(--hairline)" }}>
          <div className="flex items-center gap-2 text-[13px] mb-2" style={{ color: "var(--mute)" }}>
            YouTube Data API：{data?.youtube_configured ? <Badge tone="positive">已配置</Badge> : <Badge tone="warning">未配置（YouTube 红人不可采）</Badge>}
            {data?.youtube_configured && data?.youtube_key_hint && <span>当前：{data.youtube_key_hint}</span>}
          </div>
          <Field label={data?.youtube_configured ? "API key（已保存，留空表示不修改）" : "API key（免费档，红人达人 YouTube 采集）"}>
            <Input type="password" value={form.youtube_api_key || ""} onChange={(e) => set("youtube_api_key", e.target.value)} placeholder="Google Cloud 启用 YouTube Data API v3 后的 key" />
          </Field>
        </div>

        <div className="pt-2" style={{ borderTop: "1px solid var(--hairline)" }}>
          <div className="flex items-center gap-2 text-[13px] mb-2" style={{ color: "var(--mute)" }}>
            红人第三方源（Ensemble Data）：{data?.ensembledata_configured ? <Badge tone="positive">已配置</Badge> : <Badge tone="warning">未配置（Instagram / TikTok / X 红人不可采）</Badge>}
            {data?.ensembledata_configured && data?.ensembledata_key_hint && <span>当前：{data.ensembledata_key_hint}</span>}
          </div>
          <Field label={data?.ensembledata_configured ? "token（已保存，留空表示不修改）" : "token（可选，付费聚合源）"}>
            <Input type="password" value={form.ensembledata_token || ""} onChange={(e) => set("ensembledata_token", e.target.value)} placeholder="配置后 Instagram / TikTok / X 红人内容经第三方采集" />
          </Field>
        </div>
        {error && <div className="text-[13px] p-2 rounded-md" style={{ background: "var(--danger-soft)", color: "var(--danger)" }}>{error}</div>}
        <div className="flex justify-end items-center gap-2 pt-1">
          {saved && <span className="text-[13px]" style={{ color: "var(--accent)" }}>已保存 ✓</span>}
          <Button onClick={onClose}>关闭</Button>
          <Button
            variant="primary"
            disabled={save.isPending}
            onClick={async () => {
              setError("");
              const payload: any = { ...form };
              if (!payload.api_key) delete payload.api_key;
              if (!payload.sellersprite_secret_key) delete payload.sellersprite_secret_key;
              if (!payload.ensembledata_token) delete payload.ensembledata_token;
              if (!payload.youtube_api_key) delete payload.youtube_api_key;
              try {
                await save.mutateAsync(payload);
                setForm((f: any) => ({ ...f, api_key: "", sellersprite_secret_key: "", ensembledata_token: "", youtube_api_key: "" }));
                setSaved(true);
              } catch (e: any) {
                setError(e?.message || "保存失败");
              }
            }}
          >
            {save.isPending ? "保存中…" : "保存"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

export function Topbar({ brandId }: { brandId?: string }) {
  const { theme, toggle } = useTheme();
  const [settingsOpen, setSettingsOpen] = useState(false);
  return (
    <header
      className="h-14 flex items-center justify-between px-5 sticky top-0 z-30"
      style={{ background: "var(--bg)", borderBottom: "1px solid var(--hairline)" }}
    >
      <div className="flex items-center gap-3">
        <NavLink to="/" className="flex items-center gap-2 pr-2 cursor-pointer">
          <div className="h-6 w-6 rounded-md grid place-items-center" style={{ background: "var(--ink)" }}>
            <span style={{ color: "var(--bg)" }} className="text-[13px] font-bold">M</span>
          </div>
          <span className="text-[14px] font-semibold tracking-tight" style={{ color: "var(--ink)" }}>Monitor Hub</span>
        </NavLink>
        <BrandSwitcher brandId={brandId} />
      </div>
      <div className="flex items-center gap-2">
        <NavLink
          to="/compare"
          className="inline-flex items-center gap-1.5 h-9 px-3 rounded-md text-[14px] font-medium cursor-pointer"
          style={({ isActive }) => ({ color: isActive ? "var(--ink)" : "var(--body)", background: isActive ? "var(--bg-soft-2)" : "transparent" })}
        >
          <Users size={15} /> 竞品对比
        </NavLink>
        <button onClick={toggle} className="h-9 w-9 grid place-items-center rounded-md cursor-pointer" style={{ border: "1px solid var(--hairline-strong)", color: "var(--body)" }}>
          {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
        </button>
        <button onClick={() => setSettingsOpen(true)} title="设置" className="h-9 w-9 grid place-items-center rounded-md cursor-pointer" style={{ color: "var(--mute)" }}>
          <Settings size={15} />
        </button>
      </div>
      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </header>
  );
}

export function Sidebar({ brandId }: { brandId: string }) {
  return (
    <aside className="w-56 shrink-0 px-3 py-4" style={{ borderRight: "1px solid var(--hairline)" }}>
      <nav className="space-y-0.5">
        {NAV.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={`/brand/${brandId}/${to}`}
            className="flex items-center gap-2.5 px-3 h-9 rounded-md text-[14px] font-medium transition-colors"
            style={({ isActive }) => ({
              color: isActive ? "var(--ink)" : "var(--body)",
              background: isActive ? "var(--bg-soft-2)" : "transparent",
            })}
          >
            <Icon size={16} /> {label}
          </NavLink>
        ))}
      </nav>
    </aside>
  );
}

export function Shell({ children }: { children: React.ReactNode }) {
  const { brandId } = useParams();
  return (
    <div className="min-h-screen flex flex-col" style={{ background: "var(--bg)" }}>
      <Topbar brandId={brandId} />
      <div className="flex flex-1">
        {brandId && <Sidebar brandId={brandId} />}
        <main className="flex-1 min-w-0 p-6 max-w-[1400px]">{children}</main>
      </div>
    </div>
  );
}
