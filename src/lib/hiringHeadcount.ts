type Signals = { open_roles: number; postings_total: number; known_people: number };
type Range = { low: number; high: number };

function hiringWeight(row: Signals) {
  return row.open_roles + (row.postings_total - row.open_roles) / 4;
}

export function roughHeadcount(row: Signals, parent?: { range: Range; signals: Signals; label: string }) {
  const people = row.known_people;
  const jobs = hiringWeight(row);
  let low: number;
  let high: number;
  let rationale: string;
  if (parent) {
    const totalWeight = hiringWeight(parent.signals) + parent.signals.known_people;
    const share = totalWeight ? (jobs + people) / totalWeight : 0;
    low = Math.floor(parent.range.low * share * 0.5);
    high = Math.ceil(parent.range.high * Math.min(1, share * 1.5));
    const scale = parent.range.low === parent.range.high ? parent.range.low : `${parent.range.low}–${parent.range.high}`;
    rationale = `以${parent.label}（${scale} 人）为基数，本组占加权线索的 ${(share * 100).toFixed(1)}%。按该占比的 0.5–1.5 倍粗估，以反映各组招聘强度的差异。`;
  } else {
    low = Math.floor(jobs * 2);
    high = Math.max(people * 2, Math.ceil(jobs * 10));
    rationale = "缺少总人数参考，按每条加权职位对应 2–10 人的情景粗估。有公开员工样本时，上界至少取样本数的两倍。";
  }
  if (!row.open_roles && !people) low = 0;
  return {
    low: Math.max(people, low),
    high: Math.max(people, high),
    confidence: "low",
    rationale,
    evidence_ids: [] as string[],
    rough: true as const,
  };
}
