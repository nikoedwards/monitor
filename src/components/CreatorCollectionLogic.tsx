import type { ReactNode } from "react";
import { ChevronDown, Clock3, Hash, Search, ShieldCheck } from "lucide-react";

type Props = { compact?: boolean };

const PLATFORMS = [
  {
    name: "YouTube",
    color: "#ff0033",
    summary: "直接搜索视频，优先使用官方 API",
    steps: [
      "配置 YouTube API Key 时调用官方搜索；没有 Key 时使用 yt-dlp 公开搜索，只读取公开视频元数据，不下载视频。",
      "搜索词来自品牌名、监控关键词、产品名和产品别名；结果数量会受限，避免过度请求。",
      "读取标题、描述、作者、发布时间、播放量、点赞和评论，入库前再次按发布时间窗口过滤。",
    ],
    note: "没有单独抓取 YouTube hashtag 流；监控关键词中配置的 #标签会作为普通搜索词参与搜索。",
  },
  {
    name: "Instagram",
    color: "#d946ef",
    summary: "聚合器按关键词 / Hashtag 搜索公开内容",
    steps: [
      "按品牌名、监控关键词、产品名和产品别名分别搜索，并同时查询紧凑 Hashtag 形式。",
      "通过 Instagram 关键词聚合接口读取公开帖子、作者和互动指标，只保留公开内容。",
      "按帖子发布时间过滤当前任务窗口；未配置聚合器时仅保留有限的公开主页发现结果。",
    ],
    note: "完整关键词 / Hashtag 监控需要配置 Ensemble Data Token；公开主页 fallback 不能代表全站结果。",
  },
  {
    name: "TikTok",
    color: "var(--ink)",
    summary: "聚合器按关键词 / Hashtag 搜索公开视频",
    steps: [
      "按品牌名、监控关键词、产品名和产品别名分别搜索，并同时查询紧凑 Hashtag 形式。",
      "通过 TikTok 关键词聚合接口读取公开视频、作者和播放/互动指标，只保留公开内容。",
      "按发布时间保留当前窗口内的视频，并用平台内容 ID 去重。未配置聚合器时仅做有限主页发现。",
    ],
    note: "完整关键词 / Hashtag 监控需要配置 Ensemble Data Token；公开主页 fallback 不能代表全站结果。",
  },
  {
    name: "X",
    color: "#111827",
    summary: "通过官方 X API v2 搜索公开帖子",
    steps: [
      "在系统设置中配置 X Bearer Token；X 的未登录网页没有稳定的免费搜索接口。",
      "按品牌名、监控关键词、产品名和别名调用 recent search，读取正文、发布时间、作者和公开互动指标。",
      "X recent search 最多覆盖最近 7 天，系统仍按当前任务窗口做二次过滤。",
    ],
    note: "需要 X API 权限；无 Token 时 X 连接器会显示需配置，不会把网页失败误报成 0 条数据。",
  },
] as const;

function Rule({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return (
    <div className="flex gap-2.5">
      <span className="mt-0.5 shrink-0" style={{ color: "var(--accent)" }}>{icon}</span>
      <div className="min-w-0">
        <div className="text-[12px] font-medium" style={{ color: "var(--body)" }}>{title}</div>
        <div className="mt-0.5 text-[12px] leading-relaxed" style={{ color: "var(--mute)" }}>{children}</div>
      </div>
    </div>
  );
}
export function CreatorCollectionLogic({ compact = false }: Props) {
  return (
    <details className="rounded-lg" style={{ background: "var(--bg-soft)", border: "1px solid var(--hairline)" }}>
      <summary
        className={`flex cursor-pointer select-none items-center justify-between gap-3 ${compact ? "px-3 py-2.5" : "px-4 py-3"}`}
        style={{ listStyle: "none" }}
      >
        <span className="flex min-w-0 items-center gap-2">
          <Search size={14} style={{ color: "var(--accent)" }} />
          <span className="text-[13px] font-medium" style={{ color: "var(--ink)" }}>平台采集逻辑</span>
          <span className="truncate text-[11px]" style={{ color: "var(--mute)" }}>公开内容 · 每日窗口 · X 需 Bearer Token</span>
        </span>
        <ChevronDown size={15} className="shrink-0" style={{ color: "var(--mute)" }} />
      </summary>

      <div className={`${compact ? "px-3 pb-3" : "px-4 pb-4"} space-y-3`}>
        <div className="grid grid-cols-1 gap-2.5 md:grid-cols-4">
          {PLATFORMS.map((platform) => (
            <div key={platform.name} className="rounded-md p-3" style={{ background: "var(--panel)", border: "1px solid var(--hairline)" }}>
              <div className="flex items-center gap-2">
                <span className="h-2 w-2 rounded-full" style={{ background: platform.color }} />
                <span className="text-[13px] font-medium" style={{ color: "var(--ink)" }}>{platform.name}</span>
              </div>
              <div className="mt-1 text-[12px] leading-relaxed" style={{ color: "var(--body)" }}>{platform.summary}</div>
              <ul className="mt-2 space-y-1.5 pl-4 text-[11px] leading-relaxed" style={{ color: "var(--mute)" }}>
                {platform.steps.map((step) => <li key={step}>{step}</li>)}
              </ul>
              <div className="mt-2 pt-2 text-[11px] leading-relaxed" style={{ borderTop: "1px solid var(--hairline)", color: "var(--mute)" }}>
                <span style={{ color: "var(--body)" }}>标签说明：</span>{platform.note}
              </div>
            </div>
          ))}
        </div>

        <div className="grid grid-cols-1 gap-2.5 text-[11px] leading-relaxed md:grid-cols-3" style={{ color: "var(--mute)" }}>
          <Rule icon={<Clock3 size={13} />} title="时间窗口">
            每日任务默认只保留最近 24 小时发布的内容；改成每小时或每周时，搜索初筛和最终入库过滤会同步调整。
          </Rule>
          <Rule icon={<Hash size={13} />} title="关键词与标签">
            搜索词来自品牌名、监控关键词、产品名和别名。#标签可以参与搜索，但当前不承诺完整抓取平台 hashtag 流。
          </Rule>
          <Rule icon={<ShieldCheck size={13} />} title="公开范围与去重">
            只抓公开页面，不处理登录、验证码和私密账号；手动导入的 IG / TikTok 主页会在下次同步时直接读取并补充内容证据。
          </Rule>
        </div>
      </div>
    </details>
  );
}
