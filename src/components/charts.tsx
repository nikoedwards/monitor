import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const AXIS = { fontSize: 11, fill: "var(--mute)" };
const GRID = "var(--hairline)";

function TooltipBox({ active, payload, label, valueFormatter }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="panel px-3 py-2 text-[12px]" style={{ boxShadow: "var(--shadow)" }}>
      <div className="font-medium mb-1" style={{ color: "var(--ink)" }}>{label}</div>
      {payload.map((p: any) => (
        <div key={p.dataKey} className="flex items-center gap-2" style={{ color: "var(--body)" }}>
          <span className="inline-block h-2 w-2 rounded-full" style={{ background: p.color }} />
          {p.name}: <span className="tabular-nums font-medium">{valueFormatter ? valueFormatter(p.value) : p.value}</span>
        </div>
      ))}
    </div>
  );
}

export function TrendChart({ data, keys, valueFormatter }: { data: any[]; keys: { key: string; name: string; color: string }[]; valueFormatter?: (value: number) => string }) {
  return (
    <ResponsiveContainer width="100%" height={220}>
      <AreaChart data={data} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
        <defs>
          {keys.map((k) => (
            <linearGradient key={k.key} id={`g-${k.key}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={k.color} stopOpacity={0.25} />
              <stop offset="100%" stopColor={k.color} stopOpacity={0} />
            </linearGradient>
          ))}
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke={GRID} vertical={false} />
        <XAxis dataKey="date" tick={AXIS} tickFormatter={(v) => String(v).slice(5)} axisLine={false} tickLine={false} minTickGap={24} />
        <YAxis tick={AXIS} tickFormatter={valueFormatter} axisLine={false} tickLine={false} allowDecimals={false} width={valueFormatter ? 52 : 36} />
        <Tooltip content={<TooltipBox valueFormatter={valueFormatter} />} />
        {keys.map((k) => (
          <Area key={k.key} type="monotone" dataKey={k.key} name={k.name} stroke={k.color} fill={`url(#g-${k.key})`} strokeWidth={2} />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function SimpleLine({ data, dataKey, name, color }: { data: any[]; dataKey: string; name: string; color: string }) {
  return (
    <ResponsiveContainer width="100%" height={200}>
      <LineChart data={data} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={GRID} vertical={false} />
        <XAxis dataKey="date" tick={AXIS} tickFormatter={(v) => String(v).slice(5)} axisLine={false} tickLine={false} minTickGap={24} />
        <YAxis tick={AXIS} axisLine={false} tickLine={false} width={44} />
        <Tooltip content={<TooltipBox />} />
        <Line type="monotone" dataKey={dataKey} name={name} stroke={color} strokeWidth={2} dot={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function Bars({ data, dataKey, nameKey, name, color }: { data: any[]; dataKey: string; nameKey: string; name: string; color: string }) {
  return (
    <ResponsiveContainer width="100%" height={Math.max(160, data.length * 34)}>
      <BarChart data={data} layout="vertical" margin={{ top: 0, right: 12, left: 8, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={GRID} horizontal={false} />
        <XAxis type="number" tick={AXIS} axisLine={false} tickLine={false} allowDecimals={false} />
        <YAxis type="category" dataKey={nameKey} tick={AXIS} axisLine={false} tickLine={false} width={110} />
        <Tooltip content={<TooltipBox />} cursor={{ fill: "var(--bg-soft-2)" }} />
        <Bar dataKey={dataKey} name={name} fill={color} radius={[0, 4, 4, 0]} barSize={16}>
          {data.map((_, i) => (
            <Cell key={i} fill={color} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
