import { readFileSync } from "node:fs";
import path from "node:path";

export const ROOT = path.join(__dirname, "..", "..");
export const manifest = JSON.parse(readFileSync(path.join(ROOT, "sample_data", "manifest.json"), "utf8"));

export function scenario(id: string) {
  return manifest.scenarios.find((s: { id: string }) => s.id === id);
}

// Exact decimal arithmetic for expectations: amounts as integers of 1/10,000.
export function units(text: string): bigint {
  const s = text.replace(/[,+\s]/g, "").replace("−", "-");
  const neg = s.startsWith("-");
  const [i, f = ""] = s.replace("-", "").split(".");
  const v = BigInt(i + f.padEnd(4, "0").slice(0, 4));
  return neg ? -v : v;
}

/** The UI's money format: thousands separators, two decimals, U+2212 minus, optional plus sign. */
export function money(value: string, signed = true): string {
  const u = units(value);
  const neg = u < 0n;
  const abs = (neg ? -u : u).toString().padStart(5, "0");
  let frac = abs.slice(-4).replace(/0+$/, "");
  if (frac.length < 2) frac = frac.padEnd(2, "0");
  const int = abs.slice(0, -4).replace(/^0+(?=\d)/, "").replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const prefix = u === 0n ? "" : neg ? "−" : signed ? "+" : "";
  return `${prefix}${int}.${frac}`;
}

export function parseCsv(text: string): Record<string, string>[] {
  const [header, ...lines] = text.trim().split("\n");
  const cols = header.split(",");
  return lines.map((line) => Object.fromEntries(line.split(",").map((v, i) => [cols[i], v])));
}
