import { humanize } from "../../lib/format";

function render(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) return value.map((v) => (typeof v === "object" ? JSON.stringify(v) : String(v))).join("; ") || "none";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** A finding's stored calculation as labelled values; the raw JSON stays available. */
export default function Calculation({ calculation }: { calculation: Record<string, unknown> }) {
  const entries = Object.entries(calculation);
  if (!entries.length) return <p className="text-xs text-slate-500">No calculation details stored.</p>;
  return (
    <div>
      <dl className="grid grid-cols-1 gap-x-4 gap-y-1 text-sm sm:grid-cols-[max-content_1fr]">
        {entries.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-slate-500">{humanize(k)}</dt>
            <dd className="min-w-0 font-mono text-xs break-words">{render(v)}</dd>
          </div>
        ))}
      </dl>
      <details className="mt-1">
        <summary className="cursor-pointer text-xs text-slate-500">Raw JSON</summary>
        <pre className="mt-1 max-w-full overflow-x-auto rounded bg-slate-50 p-2 text-xs">{JSON.stringify(calculation, null, 2)}</pre>
      </details>
    </div>
  );
}
