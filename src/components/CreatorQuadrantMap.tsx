import type { CreatorMapPoint } from "../lib/api";
import { fmtNum } from "../lib/format";
import { EmptyState } from "./ui";

export const QUADRANT_META = {
  core: { label: "核心伙伴", color: "var(--accent)" },
  potential: { label: "潜力黑马", color: "var(--violet)" },
  scale: { label: "铺量达人", color: "var(--warning)" },
  observe: { label: "观察池", color: "var(--mute)" },
} as const;

export function CreatorQuadrantMap({
  points,
  quadrants,
  emptyTitle = "暂无可绘制达人",
  emptyHint = "审核通过红人后，会按合作深度和内容效果生成策展四象限。",
}: {
  points: CreatorMapPoint[];
  quadrants: { key: keyof typeof QUADRANT_META; label: string; total: number }[];
  emptyTitle?: string;
  emptyHint?: string;
}) {
  if (!points.length) return <EmptyState title={emptyTitle} hint={emptyHint} />;
  return (
    <div>
      <div className="relative h-[420px] ml-8 mb-8 rounded-lg" style={{ border: "1px solid var(--hairline-strong)", background: "var(--bg-soft)" }}>
        <div className="absolute inset-0 grid grid-cols-2 grid-rows-2 pointer-events-none">
          <div className="p-3 text-[12px] font-medium" style={{ color: "var(--violet)", borderRight: "1px dashed var(--hairline-strong)", borderBottom: "1px dashed var(--hairline-strong)" }}>潜力黑马</div>
          <div className="p-3 text-right text-[12px] font-medium" style={{ color: "var(--accent)", borderBottom: "1px dashed var(--hairline-strong)" }}>核心伙伴</div>
          <div className="p-3 self-end text-[12px] font-medium" style={{ color: "var(--mute)", borderRight: "1px dashed var(--hairline-strong)" }}>观察池</div>
          <div className="p-3 self-end text-right text-[12px] font-medium" style={{ color: "var(--warning)" }}>铺量达人</div>
        </div>
        {points.map((point) => {
          const meta = QUADRANT_META[point.quadrant];
          const bubbleStyle = {
            width: point.size,
            height: point.size,
            background: meta.color,
            color: "white",
            boxShadow: "0 0 0 3px var(--panel)",
          };
          const title = `${point.name} · ${point.platform}\n合作 ${point.collab_count}/${point.post_count} · 互动 ${fmtNum(point.total_engagement)} · 触达 ${fmtNum(point.total_views)}`;
          const node = (
            <span className="grid h-full w-full place-items-center rounded-full text-[10px] font-semibold" style={bubbleStyle}>
              {point.size >= 18 ? point.name.slice(0, 1).toUpperCase() : ""}
            </span>
          );
          return point.url ? (
            <a key={point.id} href={point.url} target="_blank" rel="noreferrer" aria-label={point.name} title={title} className="absolute -translate-x-1/2 translate-y-1/2 transition-transform hover:scale-125 focus:scale-125" style={{ left: `${point.x}%`, bottom: `${point.y}%` }}>
              {node}
            </a>
          ) : (
            <span key={point.id} title={title} className="absolute -translate-x-1/2 translate-y-1/2" style={{ left: `${point.x}%`, bottom: `${point.y}%` }}>
              {node}
            </span>
          );
        })}
        <div className="absolute -bottom-7 left-1/2 -translate-x-1/2 whitespace-nowrap text-[12px]" style={{ color: "var(--mute)" }}>合作深度 →</div>
        <div className="absolute top-1/2 -left-16 -translate-y-1/2 -rotate-90 whitespace-nowrap text-[12px]" style={{ color: "var(--mute)" }}>内容效果 →</div>
      </div>
      <div className="flex flex-wrap gap-3 text-[12px]" style={{ color: "var(--body)" }}>
        {quadrants.map((item) => (
          <div key={item.key} className="flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-full" style={{ background: QUADRANT_META[item.key].color }} />
            {item.label} {item.total}
          </div>
        ))}
        <span style={{ color: "var(--mute)" }}>气泡越大，达人累计触达越高；悬停查看明细。</span>
      </div>
    </div>
  );
}
