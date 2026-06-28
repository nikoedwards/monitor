import { useEffect, useState } from "react";

export type RangePreset = "24h" | "7d" | "30d" | "3m" | "6m" | "1y" | "custom";

export interface TimeRange {
  preset: RangePreset;
  start_date: string; // YYYY-MM-DD
  end_date: string; // YYYY-MM-DD
}

export const RANGE_PRESETS: { value: Exclude<RangePreset, "custom">; label: string }[] = [
  { value: "24h", label: "24h" },
  { value: "7d", label: "7天" },
  { value: "30d", label: "30天" },
  { value: "3m", label: "3个月" },
  { value: "6m", label: "半年" },
  { value: "1y", label: "1年" },
];

function fmt(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function presetRange(preset: RangePreset, start?: string, end?: string): TimeRange {
  const today = new Date();
  const endStr = fmt(today);
  if (preset === "custom") {
    return { preset, start_date: start || endStr, end_date: end || endStr };
  }
  const d = new Date(today);
  switch (preset) {
    case "24h":
      d.setDate(d.getDate() - 1);
      break;
    case "7d":
      d.setDate(d.getDate() - 6);
      break;
    case "30d":
      d.setDate(d.getDate() - 29);
      break;
    case "3m":
      d.setMonth(d.getMonth() - 3);
      break;
    case "6m":
      d.setMonth(d.getMonth() - 6);
      break;
    case "1y":
      d.setFullYear(d.getFullYear() - 1);
      break;
  }
  return { preset, start_date: fmt(d), end_date: endStr };
}

export function rangeLabel(range: TimeRange): string {
  const found = RANGE_PRESETS.find((p) => p.value === range.preset);
  if (found) return `最近${found.label}`;
  return `${range.start_date} ~ ${range.end_date}`;
}

export function rangeParams(range?: TimeRange): { start_date?: string; end_date?: string } {
  if (!range) return {};
  return { start_date: range.start_date, end_date: range.end_date };
}

const KEY = "monitor.timeRange";
const EVENT = "monitor:timeRange";

export function loadRange(): TimeRange {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as TimeRange;
      // Recompute relative presets so "最近30天" stays anchored to today across sessions.
      if (parsed.preset && parsed.preset !== "custom") return presetRange(parsed.preset);
      if (parsed.preset === "custom" && parsed.start_date && parsed.end_date) return parsed;
    }
  } catch {
    /* ignore malformed storage */
  }
  return presetRange("30d");
}

/** Shared, localStorage-backed time range synced across all pages via a window event. */
export function useTimeRange(): [TimeRange, (r: TimeRange) => void] {
  const [range, setRange] = useState<TimeRange>(loadRange);
  useEffect(() => {
    const handler = () => setRange(loadRange());
    window.addEventListener(EVENT, handler);
    window.addEventListener("storage", handler);
    return () => {
      window.removeEventListener(EVENT, handler);
      window.removeEventListener("storage", handler);
    };
  }, []);
  const update = (r: TimeRange) => {
    localStorage.setItem(KEY, JSON.stringify(r));
    setRange(r);
    window.dispatchEvent(new Event(EVENT));
  };
  return [range, update];
}
