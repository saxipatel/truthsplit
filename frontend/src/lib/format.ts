// Formatting of exact decimal strings without converting them to floating point.

function groupThousands(integer: string): string {
  return integer.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** "1397648.8600" -> "1,397,648.86"; keeps 3rd/4th decimals only when they are not zero. */
export function formatAmount(value: string | null | undefined, opts: { signed?: boolean } = {}): string {
  if (value === null || value === undefined || value === "") return "—";
  const match = /^([+-]?)(\d+)(?:\.(\d+))?$/.exec(value.trim());
  if (!match) return value;
  const [, sign, integer, fraction = ""] = match;
  let frac = fraction.replace(/0+$/, "");
  if (frac.length < 2) frac = frac.padEnd(2, "0");
  const isZero = /^0+$/.test(integer) && /^0*$/.test(fraction);
  const prefix = sign === "-" && !isZero ? "−" : opts.signed && !isZero ? "+" : "";
  return `${prefix}${groupThousands(integer.replace(/^0+(?=\d)/, ""))}.${frac}`;
}

export function isNegative(value: string | null | undefined): boolean {
  return !!value && value.trim().startsWith("-") && !/^-0*(\.0*)?$/.test(value.trim());
}

export function isZero(value: string | null | undefined): boolean {
  return !!value && /^[+-]?0*(\.0*)?$/.test(value.trim());
}

/** Number for chart geometry only; displayed values always use formatAmount. */
export function toNumber(value: string | null | undefined): number {
  return value ? Number(value) : 0;
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function formatCount(n: number | null | undefined): string {
  return n === null || n === undefined ? "—" : n.toLocaleString();
}

export function humanize(id: string): string {
  const text = id.replace(/^(data|definition)\./, "").replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function shortHash(hash: string | null | undefined, n = 12): string {
  return hash ? hash.slice(0, n) : "—";
}

/** "2026-03-01 01:54 UTC": for values labelled as UTC, independent of the viewer's time zone. */
export function formatUtc(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : `${d.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}
