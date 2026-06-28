import { useEffect, useRef, useState } from "react";
import { RANGE_PRESETS, presetRange, useTimeRange, type RangePreset } from "../lib/timeRange";
import { Button } from "./ui";

export function TimeRangePicker() {
  const [range, setRange] = useTimeRange();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState({ start: range.start_date, end: range.end_date });
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const choose = (p: RangePreset) => {
    setRange(presetRange(p));
    setOpen(false);
  };

  const applyCustom = () => {
    if (draft.start && draft.end) {
      setRange(presetRange("custom", draft.start, draft.end));
      setOpen(false);
    }
  };

  const inputStyle: React.CSSProperties = { background: "var(--bg-soft)", color: "var(--ink)", border: "1px solid var(--hairline-strong)" };

  return (
    <div ref={ref} className="relative">
      <div className="inline-flex p-0.5 rounded-md" style={{ background: "var(--bg-soft-2)", border: "1px solid var(--hairline)" }}>
        {RANGE_PRESETS.map((p) => (
          <button
            key={p.value}
            onClick={() => choose(p.value)}
            className="px-2 h-7 text-[12px] rounded-[5px] font-medium transition-colors cursor-pointer"
            style={range.preset === p.value ? { background: "var(--panel)", color: "var(--ink)", boxShadow: "var(--shadow)" } : { color: "var(--mute)" }}
          >
            {p.label}
          </button>
        ))}
        <button
          onClick={() => { setDraft({ start: range.start_date, end: range.end_date }); setOpen((o) => !o); }}
          className="px-2 h-7 text-[12px] rounded-[5px] font-medium transition-colors cursor-pointer"
          style={range.preset === "custom" ? { background: "var(--panel)", color: "var(--ink)", boxShadow: "var(--shadow)" } : { color: "var(--mute)" }}
        >
          自定义
        </button>
      </div>

      {open && (
        <div className="absolute right-0 top-[115%] z-50 panel p-3" style={{ boxShadow: "var(--shadow)", minWidth: 268 }}>
          <div className="flex items-center gap-2">
            <input type="date" value={draft.start} max={draft.end || undefined} onChange={(e) => setDraft((d) => ({ ...d, start: e.target.value }))} className="h-8 px-2 text-[13px] rounded-md outline-none flex-1" style={inputStyle} />
            <span className="text-[12px]" style={{ color: "var(--mute)" }}>至</span>
            <input type="date" value={draft.end} min={draft.start || undefined} onChange={(e) => setDraft((d) => ({ ...d, end: e.target.value }))} className="h-8 px-2 text-[13px] rounded-md outline-none flex-1" style={inputStyle} />
          </div>
          <div className="flex justify-end mt-2">
            <Button size="sm" variant="primary" onClick={applyCustom}>应用</Button>
          </div>
        </div>
      )}
    </div>
  );
}
