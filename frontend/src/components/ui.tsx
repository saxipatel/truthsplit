import type { ReactNode } from "react";

import type { EvidenceStatus, InvestigationStatus } from "../api/types";
import { formatAmount, isNegative } from "../lib/format";

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight break-words">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-slate-600">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function Card({ title, actions, children, className = "" }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`min-w-0 rounded-md border border-slate-200 bg-white ${className}`}>
      {(title || actions) && (
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-4 py-2.5">
          {title && <h2 className="text-sm font-semibold">{title}</h2>}
          {actions}
        </div>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost"; pending?: boolean };

export function Button({ variant = "secondary", pending, children, className = "", disabled, ...rest }: ButtonProps) {
  const styles = {
    primary: "bg-slate-900 text-white hover:bg-slate-700 disabled:bg-slate-400",
    secondary: "border border-slate-300 bg-white text-slate-800 hover:bg-slate-50 disabled:text-slate-400",
    ghost: "text-slate-700 hover:bg-slate-100 disabled:text-slate-400",
  }[variant];
  return (
    <button
      type="button"
      className={`inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium focus:outline-2 focus:outline-offset-2 focus:outline-blue-600 disabled:cursor-not-allowed ${styles} ${className}`}
      disabled={disabled || pending}
      {...rest}
    >
      {pending && <span aria-hidden className="h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />}
      {children}
    </button>
  );
}

export function LinkButton({ href, children, download }: { href: string; children: ReactNode; download?: boolean }) {
  return (
    <a
      href={href}
      download={download}
      className="inline-flex items-center rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-800 hover:bg-slate-50"
    >
      {children}
    </a>
  );
}

export function Field({ label, htmlFor, hint, error, children }: { label: string; htmlFor: string; hint?: ReactNode; error?: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <label htmlFor={htmlFor} className="mb-1 block text-xs font-medium text-slate-700">
        {label}
      </label>
      {children}
      {hint && !error && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
      {error && (
        <p className="mt-1 text-xs text-red-700" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

export const inputClass =
  "w-full rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm focus:border-blue-600 focus:outline-none focus:ring-1 focus:ring-blue-600 disabled:bg-slate-100";

export function ErrorMessage({ error, title = "Something went wrong" }: { error?: string; title?: string }) {
  if (!error) return null;
  return (
    <div role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
      <span className="font-medium">{title}:</span> {error}
    </div>
  );
}

export function Notice({ children, tone = "info" }: { children: ReactNode; tone?: "info" | "warning" | "success" }) {
  const styles = {
    info: "border-blue-200 bg-blue-50 text-blue-900",
    warning: "border-amber-300 bg-amber-50 text-amber-900",
    success: "border-green-200 bg-green-50 text-green-900",
  }[tone];
  return <div className={`rounded-md border px-3 py-2 text-sm ${styles}`}>{children}</div>;
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <p className="flex items-center gap-2 py-6 text-sm text-slate-500" role="status">
      <span aria-hidden className="h-4 w-4 animate-spin rounded-full border-2 border-slate-400 border-t-transparent" />
      {label}…
    </p>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-md border border-dashed border-slate-300 px-4 py-8 text-center">
      <p className="text-sm font-medium text-slate-700">{title}</p>
      {children && <div className="mt-2 text-sm text-slate-500">{children}</div>}
    </div>
  );
}

export function Amount({ value, signed, className = "" }: { value: string | null | undefined; signed?: boolean; className?: string }) {
  return <span className={`tabular-nums ${className}`}>{formatAmount(value, { signed })}</span>;
}

export function StatTile({ label, value, note, emphasis }: { label: string; value: ReactNode; note?: ReactNode; emphasis?: boolean }) {
  return (
    <div role="group" aria-label={label} className={`min-w-0 rounded-md border px-3 py-2.5 ${emphasis ? "border-slate-400 bg-white" : "border-slate-200 bg-white"}`}>
      <p className="text-xs text-slate-500">{label}</p>
      <p className="mt-0.5 truncate text-lg font-semibold">{value}</p>
      {note && <p className="mt-0.5 text-xs text-slate-500">{note}</p>}
    </div>
  );
}

const EVIDENCE: Record<EvidenceStatus, { icon: string; label: string; className: string }> = {
  confirmed: { icon: "✓", label: "Confirmed", className: "border-green-700/30 bg-green-50 text-green-800" },
  suspected: { icon: "!", label: "Suspected", className: "border-amber-500/50 bg-amber-50 text-amber-900" },
  unexplained: { icon: "?", label: "Unexplained", className: "border-slate-400 bg-slate-100 text-slate-800" },
};

export function EvidenceBadge({ status }: { status: EvidenceStatus }) {
  const s = EVIDENCE[status];
  return (
    <span className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-xs font-medium ${s.className}`}>
      <span aria-hidden className="font-bold">
        {s.icon}
      </span>
      {s.label}
    </span>
  );
}

const STATUS: Record<InvestigationStatus, string> = {
  open: "Open",
  in_review: "In review",
  resolved: "Resolved",
  archived: "Archived",
};

export function StatusBadge({ status }: { status: InvestigationStatus }) {
  const style = {
    open: "bg-blue-50 text-blue-800 border-blue-200",
    in_review: "bg-violet-50 text-violet-800 border-violet-200",
    resolved: "bg-green-50 text-green-800 border-green-200",
    archived: "bg-slate-100 text-slate-700 border-slate-300",
  }[status];
  return (
    <span className={`inline-flex rounded border px-1.5 py-0.5 text-xs font-medium ${style}`} title="Review status, set by a person">
      {STATUS[status]}
    </span>
  );
}

export function DemoBadge() {
  return (
    <span className="inline-flex rounded border border-amber-400 bg-amber-50 px-1.5 py-0.5 text-xs font-medium text-amber-900" title="Synthetic demo data">
      Synthetic demo
    </span>
  );
}

export function SignedAmount({ value }: { value: string | null | undefined }) {
  return <Amount value={value} signed className={isNegative(value) ? "text-red-800" : ""} />;
}

export function Table({ children, label }: { children: ReactNode; label: string }) {
  return (
    <div className="max-w-full overflow-x-auto rounded-md border border-slate-200">
      <table className="w-full min-w-max border-collapse text-sm" aria-label={label}>
        {children}
      </table>
    </div>
  );
}

export const th = "border-b border-slate-200 bg-slate-50 px-3 py-2 text-left text-xs font-semibold text-slate-600 whitespace-nowrap";
export const td = "border-b border-slate-100 px-3 py-1.5 align-top";
export const tdNum = `${td} text-right tabular-nums whitespace-nowrap`;

export function Tabs<T extends string>({ tabs, active, onChange, label }: { tabs: { id: T; label: string }[]; active: T; onChange: (id: T) => void; label: string }) {
  return (
    <div role="tablist" aria-label={label} className="mb-4 flex flex-wrap gap-1 border-b border-slate-200">
      {tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          role="tab"
          aria-selected={active === t.id}
          onClick={() => onChange(t.id)}
          className={`-mb-px border-b-2 px-3 py-2 text-sm ${
            active === t.id ? "border-slate-900 font-medium text-slate-900" : "border-transparent text-slate-500 hover:text-slate-800"
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function KeyValue({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 text-sm sm:grid-cols-[max-content_1fr]">
      {items.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-slate-500">{k}</dt>
          <dd className="min-w-0 break-words">{v}</dd>
        </div>
      ))}
    </dl>
  );
}


export type Explanation = "no_difference" | "fully_explained" | "remainder" | "unknown";

/** Whether the evidence accounts for the whole difference. Computed from the stored totals; independent of review status. */
export function explanationOf(delta: string | null | undefined, unexplained: string | null | undefined): Explanation {
  if (delta === null || delta === undefined || unexplained === null || unexplained === undefined) return "unknown";
  const zero = (v: string) => /^[+-]?0*(\.0*)?$/.test(v.trim());
  if (zero(delta) && zero(unexplained)) return "no_difference";
  return zero(unexplained) ? "fully_explained" : "remainder";
}

const EXPLANATION: Record<Explanation, { icon: string; label: string; className: string }> = {
  no_difference: { icon: "=", label: "No difference", className: "border-slate-300 bg-white text-slate-700" },
  fully_explained: { icon: "✓", label: "Fully explained", className: "border-green-700/30 bg-white text-green-800" },
  remainder: { icon: "?", label: "Unexplained remainder", className: "border-amber-500/60 bg-white text-amber-900" },
  unknown: { icon: "·", label: "Not evaluated", className: "border-slate-300 bg-white text-slate-600" },
};

export function ExplanationBadge({ delta, unexplained }: { delta: string | null | undefined; unexplained: string | null | undefined }) {
  const e = EXPLANATION[explanationOf(delta, unexplained)];
  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-xs font-medium ${e.className}`}
      title="Computed from the evidence: whether attributed findings account for the whole difference. Separate from the review status."
    >
      <span aria-hidden className="font-bold">
        {e.icon}
      </span>
      {e.label}
    </span>
  );
}
