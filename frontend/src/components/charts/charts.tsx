import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, LabelList, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { add, sign } from "../../lib/decimal";
import { formatAmount, toNumber } from "../../lib/format";

// Validated with the dataviz palette validator (light surface #fcfcfb): blue/red/yellow pass separation
// all-pairs; yellow is below 3:1 contrast, so every bar also carries a visible value label.
export const COLORS = {
  up: "#2a78d6",
  down: "#e34948",
  unexplained: "#eda100",
  total: "#0b0b0b",
  before: "#86b6ef", // blue step 250: the light shade of the dumbbell's single hue
  after: "#1c5cab", // blue step 550
  series1: "#2a78d6",
  series2: "#eb6834",
  grid: "#e1e0d9",
  axis: "#898781",
  ink: "#52514e",
};

const BAR = 18;

/** True below 640px, where the category labels get a narrower column. */
function useNarrow(): boolean {
  const query = "(max-width: 639px)";
  const [narrow, setNarrow] = useState(() => typeof window !== "undefined" && window.matchMedia(query).matches);
  useEffect(() => {
    const m = window.matchMedia(query);
    const update = () => setNarrow(m.matches);
    m.addEventListener("change", update);
    return () => m.removeEventListener("change", update);
  }, []);
  return narrow;
}

/** Smallest of 1, 2 or 5 × 10^k that is at least `v`. */
function niceStep(v: number): number {
  if (v <= 0) return 1;
  const mag = 10 ** Math.floor(Math.log10(v));
  return [1, 2, 5, 10].find((m) => m * mag >= v)! * mag;
}

/** Axis from min to max with room for value labels on each side, on clean tick steps. */
function labelledAxis(values: number[]): { domain: [number, number]; ticks: number[] } {
  const min = Math.min(0, ...values);
  const max = Math.max(0, ...values);
  const lo = min < 0 ? min * 1.6 : 0;
  const hi = max > 0 ? max * 1.4 : 0;
  const step = niceStep((hi - lo) / 5);
  const start = Math.floor(lo / step) * step;
  const end = Math.ceil(hi / step) * step;
  const ticks = [];
  for (let t = start; t <= end + step / 2; t += step) ticks.push(Math.round(t));
  return { domain: [start, end], ticks };
}

type LabelProps = { x?: number; y?: number; width?: number; height?: number; value?: string | number };

/** Value at the bar's data end: right of positive bars, left of negative ones. */
function TipLabel({ x = 0, y = 0, width = 0, height = 0, value }: LabelProps) {
  const negative = width < 0;
  return (
    <text x={x + width + (negative ? -4 : 4)} y={y + height / 2} dy={4} textAnchor={negative ? "end" : "start"} fontSize={11} fill="#0b0b0b">
      {value}
    </text>
  );
}

function truncate(label: string, max: number) {
  return label.length > max ? `${label.slice(0, max - 1)}…` : label;
}

function compact(n: number): string {
  const a = Math.abs(n);
  if (a >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (a >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return String(n);
}

type ShapeProps = { x?: number; y?: number; width?: number; height?: number; fill?: string };

/** A bar with a 4px rounded data end and a square baseline end, for positive and negative values. */
function DataEndBar({ x = 0, y = 0, width = 0, height = 0, fill }: ShapeProps) {
  const negative = width < 0;
  const w = Math.abs(width);
  const left = negative ? x - w : x;
  const r = Math.min(4, w / 2, height / 2);
  if (w === 0) return null;
  const d = negative
    ? `M${left + w},${y} H${left + r} Q${left},${y} ${left},${y + r} V${y + height - r} Q${left},${y + height} ${left + r},${y + height} H${left + w} Z`
    : `M${left},${y} H${left + w - r} Q${left + w},${y} ${left + w},${y + r} V${y + height - r} Q${left + w},${y + height} ${left + w - r},${y + height} H${left} Z`;
  return <path d={d} fill={fill} />;
}

function LegendRow({ items }: { items: { color: string; label: string }[] }) {
  return (
    <ul className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600" aria-label="Legend">
      {items.map((i) => (
        <li key={i.label} className="flex items-center gap-1.5">
          <span aria-hidden className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: i.color }} />
          {i.label}
        </li>
      ))}
    </ul>
  );
}

function TooltipBox({ active, payload }: { active?: boolean; payload?: { payload: Record<string, string> }[] }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="rounded border border-slate-200 bg-white px-2.5 py-1.5 text-xs shadow-sm">
      <p className="font-medium text-slate-900">{p.title}</p>
      <p className="text-slate-600">{p.detail}</p>
    </div>
  );
}

export type ComparisonItem = { label: string; baseline: string | null; simulated: string | null };

/** Baseline vs simulated attributed amount per rule. */
export function SimulationChart({ items }: { items: ComparisonItem[] }) {
  const narrow = useNarrow();
  const data = items.map((i) => ({
    ...i,
    label: truncate(i.label, narrow ? 18 : 30),
    b: toNumber(i.baseline),
    s: toNumber(i.simulated),
    title: i.label,
    detail: `baseline ${formatAmount(i.baseline, { signed: true })} · simulated ${formatAmount(i.simulated, { signed: true })}`,
  }));
  const height = Math.max(140, data.length * 56 + 40);
  return (
    <figure>
      <LegendRow
        items={[
          { color: COLORS.series1, label: "Baseline (stored investigation)" },
          { color: COLORS.series2, label: "Simulated" },
        ]}
      />
      <div style={{ height }} className="w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, bottom: 4, left: 0 }} barSize={14} barGap={2}>
            <CartesianGrid horizontal={false} stroke={COLORS.grid} />
            <XAxis type="number" tickFormatter={compact} stroke={COLORS.axis} tick={{ fontSize: 11, fill: COLORS.ink }} />
            <YAxis type="category" dataKey="label" width={narrow ? 104 : 160} stroke={COLORS.axis} tick={{ fontSize: 11, fill: COLORS.ink }} />
            <ReferenceLine x={0} stroke={COLORS.axis} />
            <Tooltip content={<TooltipBox />} cursor={{ fill: "rgba(11,11,11,0.04)" }} />
            <Bar dataKey="b" fill={COLORS.series1} shape={<DataEndBar />} isAnimationActive={false} />
            <Bar dataKey="s" fill={COLORS.series2} shape={<DataEndBar />} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="mt-1 text-xs text-slate-500">Attributed amount per rule before and after the correction; exact values are in the table.</figcaption>
    </figure>
  );
}

/** How many investigations each rule appears in. Single series, so no legend. */
export function PatternsChart({ items }: { items: { label: string; count: number }[] }) {
  const narrow = useNarrow();
  const data = items.map((i) => ({ ...i, label: truncate(i.label, narrow ? 18 : 30), title: i.label, detail: `${i.count} investigation${i.count === 1 ? "" : "s"}` }));
  const height = Math.max(120, data.length * 32 + 40);
  return (
    <figure>
      <div style={{ height }} className="w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ top: 4, right: 40, bottom: 4, left: 8 }} barSize={BAR}>
            <CartesianGrid horizontal={false} stroke={COLORS.grid} />
            <XAxis type="number" allowDecimals={false} stroke={COLORS.axis} tick={{ fontSize: 11, fill: COLORS.ink }} />
            <YAxis type="category" dataKey="label" width={narrow ? 104 : 180} stroke={COLORS.axis} tick={{ fontSize: 11, fill: COLORS.ink }} />
            <Tooltip content={<TooltipBox />} cursor={{ fill: "rgba(11,11,11,0.04)" }} />
            <Bar dataKey="count" fill={COLORS.series1} shape={<DataEndBar />} isAnimationActive={false}>
              <LabelList dataKey="count" content={<TipLabel />} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="mt-1 text-xs text-slate-500">Investigations in which each rule produced a finding.</figcaption>
    </figure>
  );
}


export type WaterfallStep = { label: string; title: string; amount: string; kind: "cause" | "unexplained"; status: string };

export type WaterfallRow = { label: string; title: string; change: string; start: string; end: string; kind: "cause" | "unexplained" | "total"; status: string };

/** Running totals from zero: each step moves the running total by its contribution; the last row is the total. */
export function waterfallRows(steps: WaterfallStep[], totalLabel: string): WaterfallRow[] {
  let running = "0.0000";
  const rows: WaterfallRow[] = steps.map((s) => {
    const start = running;
    running = add(running, s.amount);
    return { label: s.label, title: s.title, change: s.amount, start, end: running, kind: s.kind, status: s.status };
  });
  rows.push({ label: totalLabel, title: totalLabel, change: running, start: "0.0000", end: running, kind: "total", status: "" });
  return rows;
}

type RangeShapeProps = ShapeProps & { kind?: string; dir?: number; payload?: { kind: string; dir: number } };

/** A floating bar from the running total before a step to the one after it; the rounded end marks where it lands. */
function StepBar(props: RangeShapeProps) {
  const { x = 0, y = 0, width = 0, height = 0 } = props;
  // Recharts passes the data row both spread into the props and as `payload`, depending on version.
  const payload = props.payload ?? (props.kind ? { kind: props.kind, dir: props.dir ?? 1 } : undefined);
  if (!payload) return null;
  const w = Math.max(width, 1);
  if (payload.kind === "total") {
    return <rect x={x} y={y} width={w} height={height} fill="none" stroke={COLORS.total} strokeWidth={1.5} rx={2} />;
  }
  const fill = payload.kind === "unexplained" ? COLORS.unexplained : payload.dir < 0 ? COLORS.down : COLORS.up;
  const r = Math.min(4, w / 2, height / 2);
  const right = payload.dir >= 0; // rounded end on the side the running total moves to
  const d = right
    ? `M${x},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + height - r} Q${x + w},${y + height} ${x + w - r},${y + height} H${x} Z`
    : `M${x + w},${y} H${x + r} Q${x},${y} ${x},${y + r} V${y + height - r} Q${x},${y + height} ${x + r},${y + height} H${x + w} Z`;
  return <path d={d} fill={fill} />;
}

function StepLabel({ x = 0, y = 0, width = 0, height = 0, value, index, rows }: LabelProps & { index?: number; rows: { dir: number }[] }) {
  const dir = rows[index ?? 0]?.dir ?? 1;
  const atRight = dir >= 0;
  return (
    <text x={atRight ? x + width + 4 : x - 4} y={y + height / 2} dy={4} textAnchor={atRight ? "start" : "end"} fontSize={11} fill="#0b0b0b">
      {value}
    </text>
  );
}

/** Reconciliation waterfall: verified causes, then the unexplained remainder, building up to the difference. */
export function WaterfallChart({ rows }: { rows: WaterfallRow[] }) {
  const narrow = useNarrow();
  const data = rows.map((r) => {
    const a = toNumber(r.start);
    const b = toNumber(r.end);
    const dir = r.kind === "total" ? sign(r.end) || 1 : sign(r.change) || 1;
    return {
      ...r,
      label: truncate(r.label, narrow ? 18 : 34),
      range: [Math.min(a, b), Math.max(a, b)] as [number, number],
      dir,
      shown: r.kind === "total" ? formatAmount(r.end, { signed: true }) : formatAmount(r.change, { signed: true }),
      detail: r.kind === "total" ? `difference ${formatAmount(r.end, { signed: true })}` : `${formatAmount(r.change, { signed: true })} → running total ${formatAmount(r.end, { signed: true })}`,
    };
  });
  const axis = labelledAxis(data.flatMap((d) => d.range));
  const height = Math.max(140, data.length * 38 + 48);
  const legend = [
    { color: COLORS.up, label: "Verified cause raising the right side", shown: data.some((d) => d.kind === "cause" && d.dir > 0) },
    { color: COLORS.down, label: "Verified cause lowering the right side", shown: data.some((d) => d.kind === "cause" && d.dir < 0) },
    { color: COLORS.unexplained, label: "Unexplained remainder", shown: data.some((d) => d.kind === "unexplained") },
  ].filter((i) => i.shown);
  return (
    <figure>
      <ul className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600" aria-label="Legend">
        {legend.map((i) => (
          <li key={i.label} className="flex items-center gap-1.5">
            <span aria-hidden className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: i.color }} />
            {i.label}
          </li>
        ))}
        <li className="flex items-center gap-1.5">
          <span aria-hidden className="inline-block h-2.5 w-3.5 rounded-sm border-[1.5px] border-slate-900" />
          Total difference (right − left)
        </li>
      </ul>
      <div style={{ height }} className="w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ top: 4, right: 8, bottom: 4, left: 0 }} barSize={BAR}>
            <CartesianGrid horizontal={false} stroke={COLORS.grid} />
            <XAxis type="number" {...(narrow ? {} : axis)} tickFormatter={compact} stroke={COLORS.axis} tick={{ fontSize: 11, fill: COLORS.ink }} />
            <YAxis type="category" dataKey="label" width={narrow ? 104 : 190} stroke={COLORS.axis} tick={{ fontSize: 11, fill: COLORS.ink }} />
            <ReferenceLine x={0} stroke={COLORS.axis} />
            <Tooltip content={<TooltipBox />} cursor={{ fill: "rgba(11,11,11,0.04)" }} />
            <Bar dataKey="range" shape={<StepBar />} isAnimationActive={false}>
              {!narrow && <LabelList dataKey="shown" content={<StepLabel rows={data} />} />}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </figure>
  );
}

export type DumbbellItem = { label: string; title: string; before: string; after: string };

function Dumbbell(props: ShapeProps & { beforeIsLeft?: boolean; payload?: { beforeIsLeft: boolean } }) {
  const { x = 0, y = 0, width = 0, height = 0 } = props;
  const payload = props.payload ?? (props.beforeIsLeft !== undefined ? { beforeIsLeft: props.beforeIsLeft } : undefined);
  if (!payload) return null;
  const cy = y + height / 2;
  const bx = payload.beforeIsLeft ? x : x + width;
  const ax = payload.beforeIsLeft ? x + width : x;
  return (
    <g>
      <line x1={x} x2={x + width} y1={cy} y2={cy} stroke={COLORS.axis} strokeWidth={2} />
      <circle cx={bx} cy={cy} r={5} fill={COLORS.before} stroke="#fcfcfb" strokeWidth={2} />
      <circle cx={ax} cy={cy} r={5} fill={COLORS.after} stroke="#fcfcfb" strokeWidth={2} />
    </g>
  );
}

/** Difference before → after for each saved simulation. All start from the same stored baseline. */
export function DumbbellChart({ items }: { items: DumbbellItem[] }) {
  const narrow = useNarrow();
  const data = items.map((i) => {
    const b = toNumber(i.before);
    const a = toNumber(i.after);
    return {
      ...i,
      label: truncate(i.label, narrow ? 18 : 34),
      range: [Math.min(a, b), Math.max(a, b)] as [number, number],
      beforeIsLeft: b <= a,
      detail: `before ${formatAmount(i.before, { signed: true })} → after ${formatAmount(i.after, { signed: true })}`,
    };
  });
  const axis = labelledAxis(data.flatMap((d) => d.range));
  const height = Math.max(120, data.length * 34 + 48);
  return (
    <figure>
      <LegendRow
        items={[
          { color: COLORS.before, label: "Difference before (stored baseline)" },
          { color: COLORS.after, label: "Difference after the correction" },
        ]}
      />
      <div style={{ height }} className="w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, bottom: 4, left: 0 }} barSize={12}>
            <CartesianGrid horizontal={false} stroke={COLORS.grid} />
            <XAxis type="number" {...axis} tickFormatter={compact} stroke={COLORS.axis} tick={{ fontSize: 11, fill: COLORS.ink }} />
            <YAxis type="category" dataKey="label" width={narrow ? 104 : 190} stroke={COLORS.axis} tick={{ fontSize: 11, fill: COLORS.ink }} />
            <ReferenceLine x={0} stroke={COLORS.axis} />
            <Tooltip content={<TooltipBox />} cursor={{ fill: "rgba(11,11,11,0.04)" }} />
            <Bar dataKey="range" shape={<Dumbbell />} isAnimationActive={false} minPointSize={1} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="mt-1 text-xs text-slate-500">Zero means the two reports agree. Exact values are in the table of saved simulations.</figcaption>
    </figure>
  );
}
