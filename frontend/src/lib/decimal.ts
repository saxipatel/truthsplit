// Exact arithmetic on decimal strings (scale 4, as stored by the backend), using BigInt.

const SCALE = 4;

function toUnits(value: string): bigint {
  const s = value.trim();
  const negative = s.startsWith("-");
  const [int, frac = ""] = s.replace(/^[+-]/, "").split(".");
  const units = BigInt((int || "0") + frac.padEnd(SCALE, "0").slice(0, SCALE));
  return negative ? -units : units;
}

function fromUnits(units: bigint): string {
  const negative = units < 0n;
  const digits = (negative ? -units : units).toString().padStart(SCALE + 1, "0");
  return `${negative ? "-" : ""}${digits.slice(0, -SCALE)}.${digits.slice(-SCALE)}`;
}

export function add(...values: (string | null | undefined)[]): string {
  return fromUnits(values.reduce<bigint>((sum, v) => sum + (v ? toUnits(v) : 0n), 0n));
}

export function sub(a: string, b: string): string {
  return fromUnits(toUnits(a) - toUnits(b));
}

export function equal(a: string | null | undefined, b: string | null | undefined): boolean {
  return toUnits(a ?? "0") === toUnits(b ?? "0");
}

export function sign(value: string | null | undefined): -1 | 0 | 1 {
  const u = toUnits(value ?? "0");
  return u === 0n ? 0 : u < 0n ? -1 : 1;
}
