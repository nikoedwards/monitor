import { useEffect, useRef, useState } from "react";
import { fmtDateTime } from "../lib/format";

type StatusMeta = { label: string; tone: string; reason: string; fix: string };

const STATUS_MAP: Record<string, StatusMeta> = {
  ok: { label: "已采集", tone: "var(--accent)", reason: "数据已正常入库。", fix: "" },
  empty: {
    label: "未取到内容",
    tone: "var(--warning)",
    reason: "本次未解析到可采集内容（可能为动态渲染、暂无更新或需要登录）。",
    fix: "确认链接是公开内容页；纯前端渲染的站点正文可能需后续启用浏览器渲染抓取。",
  },
  blocked: {
    label: "被限流/拦截",
    tone: "var(--warning)",
    reason: "来源返回 403 或触发了反爬限流。",
    fix: "系统已自动回退到公开 RSS；配置对应 Token（如 REDDIT_BEARER_TOKEN）可显著提升成功率。",
  },
  needs_credential: {
    label: "需配置凭证",
    tone: "var(--warning)",
    reason: "该来源需要配置访问凭证后才能采集。",
    fix: "在「设置」或环境变量中配置相应凭证后重试。",
  },
  network: {
    label: "网络异常",
    tone: "var(--warning)",
    reason: "连接来源时出现网络/超时/SSL 错误。",
    fix: "确认链接可正常访问、网络/代理可达，稍后重试。",
  },
  error: {
    label: "采集异常",
    tone: "var(--danger)",
    reason: "采集过程中出现错误。",
    fix: "检查链接是否可正常访问，或到「数据源采集控制台」手动重试。",
  },
};

export function CollectionStatusChip({ lastStatus, lastError, lastCollectAt }: { lastStatus?: string; lastError?: string; lastCollectAt?: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  if (!lastCollectAt && !lastStatus) {
    return <span className="text-[12px] shrink-0" style={{ color: "var(--mute)" }}>待采集</span>;
  }

  const meta = STATUS_MAP[lastStatus || "ok"] || STATUS_MAP.error;

  return (
    <div ref={ref} className="relative shrink-0">
      <button
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1 px-2 h-[22px] rounded-full text-[12px] font-medium cursor-pointer"
        style={{ background: "var(--bg-soft-2)", color: meta.tone, border: "1px solid var(--hairline)" }}
      >
        <span className="h-1.5 w-1.5 rounded-full" style={{ background: meta.tone }} />
        {meta.label}
      </button>
      {open && (
        <div className="absolute right-0 top-[125%] z-50 panel p-3 text-[12px] leading-relaxed" style={{ width: 280, boxShadow: "var(--shadow)" }}>
          <div className="font-semibold mb-1" style={{ color: meta.tone }}>{meta.label}</div>
          {lastCollectAt && <div style={{ color: "var(--mute)" }}>上次采集：{fmtDateTime(lastCollectAt)}</div>}
          <div className="mt-1" style={{ color: "var(--body)" }}><span style={{ color: "var(--mute)" }}>原因：</span>{meta.reason}</div>
          {meta.fix && <div className="mt-1" style={{ color: "var(--body)" }}><span style={{ color: "var(--mute)" }}>解决办法：</span>{meta.fix}</div>}
          {lastError && lastStatus !== "ok" && (
            <div className="mt-2 pt-2 break-words" style={{ borderTop: "1px solid var(--hairline)", color: "var(--mute)" }}>
              详情：{lastError}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
