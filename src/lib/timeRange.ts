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

// Keep the selected range in sessionStorage so every browser tab owns its own
// copy. localStorage is shared by all tabs and caused changing the picker in
// one tab to overwrite the range shown by every other tab.
const KEY = "monitor.timeRange";
const EVENT = "monitor:timeRange";

function readStoredRange(): string | null {
  try {
    const tabValue = sessionStorage.getItem(KEY);
    if (tabValue) return tabValue;

    // Migrate the old shared value once for existing sessions. After this
    // snapshot, updates stay in this tab's sessionStorage only.
    const legacyValue = localStorage.getItem(KEY);
    if (legacyValue) {
      sessionStorage.setItem(KEY, legacyValue);
      return legacyValue;
    }
  } catch {
    /* ignore unavailable storage */
  }
  return null;
}

export function loadRange(): TimeRange {
  try {
    const raw = readStoredRange();
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

/** Tab-local time range synced across pages in the same tab via a window event. */
export function useTimeRange(): [TimeRange, (r: TimeRange) => void] {
  const [range, setRange] = useState<TimeRange>(loadRange);
  useEffect(() => {
    const handler = () => setRange(loadRange());
    window.addEventListener(EVENT, handler);
    return () => {
      window.removeEventListener(EVENT, handler);
    };
  }, []);
  const update = (r: TimeRange) => {
    try {
      sessionStorage.setItem(KEY, JSON.stringify(r));
    } catch {
      /* Keep the in-memory selection when storage is unavailable. */
    }
    setRange(r);
    window.dispatchEvent(new Event(EVENT));
  };
  return [range, update];
}
