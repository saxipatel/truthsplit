import { api } from "../../api/client";
import type { Dataset, DnaComparison, Investigation } from "../../api/types";
import { Card, ErrorMessage, KeyValue, Loading, Table, td, tdNum, th } from "../../components/ui";
import { formatAmount, formatCount, formatDateTime, humanize, shortHash } from "../../lib/format";
import { useApi } from "../../lib/useApi";

function show(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (Array.isArray(v)) return v.length ? v.join("; ") : "none";
  return String(v);
}

const REFUND: Record<string, string> = { gross: "gross (refunds ignored)", net_of_refunds: "net of refunds", exclude_refunded: "excluding refunded rows" };
const AGG: Record<string, string> = { sum: "a sum", count: "a row count", count_distinct: "a count of distinct business keys" };
const GRAIN: Record<string, string> = { record: "every row", business_key: "one row per business key" };

function windowParts(text: unknown): string {
  if (!text) return "no date window (all dates)";
  const [local, utc] = String(text).split(" = ");
  return utc ? `${local} (${utc})` : String(text);
}

/** One plain-language line per differing field, saying what the difference does to the numbers. */
export function describeDifferences(dna: DnaComparison): { field: string; label: string; text: string }[] {
  const out: { field: string; label: string; text: string }[] = [];
  for (const f of dna.fields.filter((x) => x.differs)) {
    const l = f.left as string;
    const r = f.right as string;
    switch (f.field) {
      case "refund_policy":
        out.push({ field: f.field, label: "Refund treatment", text: `Left is ${REFUND[l] ?? l}; right is ${REFUND[r] ?? r}. Refunded amounts count differently on the two sides.` });
        break;
      case "aggregation":
        out.push({ field: f.field, label: "Aggregation", text: `Left is ${AGG[l] ?? l}; right is ${AGG[r] ?? r}. These cannot be compared; an investigation will refuse them.` });
        break;
      case "grain":
        out.push({ field: f.field, label: "Grain", text: `Left counts ${GRAIN[l] ?? l}; right counts ${GRAIN[r] ?? r}. Repeated keys contribute once on the business-key side only.` });
        break;
      case "window":
        out.push({ field: f.field, label: "Date window", text: `Left: ${windowParts(f.left)}. Right: ${windowParts(f.right)}. Records between the two windows' bounds count on one side only.` });
        break;
      case "filters":
        out.push({
          field: f.field,
          label: "Filters",
          text: [
            dna.filters_left_only.length ? `Only on the left: ${dna.filters_left_only.join("; ")}.` : "",
            dna.filters_right_only.length ? `Only on the right: ${dna.filters_right_only.join("; ")}.` : "",
          ]
            .filter(Boolean)
            .join(" "),
        });
        break;
      case "null_policy":
        out.push({ field: f.field, label: "Missing values", text: `Left ${l === "reject" ? "fails on" : "excludes"} rows without a value; right ${r === "reject" ? "fails on" : "excludes"} them.` });
        break;
      case "value_field":
        out.push({ field: f.field, label: "Measured field", text: `Left measures ${l}; right measures ${r}.` });
        break;
      case "currency":
        out.push({ field: f.field, label: "Currency", text: `Left is declared in ${l ?? "no currency"}; right in ${r ?? "no currency"}. Money in different currencies is never added together.` });
        break;
      default:
        out.push({ field: f.field, label: humanize(f.field), text: `Left: ${String(f.left)}. Right: ${String(f.right)}.` });
    }
  }
  return out;
}

export function DnaDifferences({ dna }: { dna: DnaComparison }) {
  const diffs = describeDifferences(dna);
  if (!diffs.length) return <p className="text-sm text-slate-600">The definitions are identical; any difference between the metrics comes from the data.</p>;
  return (
    <ul className="space-y-2" aria-label="Definition differences">
      {diffs.map((d) => (
        <li key={d.field} className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm">
          <span className="font-medium text-amber-900">
            {d.label} {d.field === "filters" ? "differ" : "differs"}.
          </span> <span className="text-slate-800">{d.text}</span>
        </li>
      ))}
    </ul>
  );
}

export function DnaTable({ dna, leftLabel, rightLabel }: { dna: DnaComparison; leftLabel: string; rightLabel: string }) {
  return (
    <Table label="Metric DNA comparison">
      <thead>
        <tr>
          <th className={th}>Field</th>
          <th className={th}>{leftLabel}</th>
          <th className={th}>{rightLabel}</th>
        </tr>
      </thead>
      <tbody>
        {dna.fields.map((f) => (
          <tr key={f.field} className={f.differs ? "bg-amber-50" : ""}>
            <td className={`${td} font-medium`}>
              {humanize(f.field)}
              {f.differs && <span className="ml-1.5 text-xs font-normal text-amber-800">differs</span>}
            </td>
            <td className={`${td} max-w-96 break-words whitespace-normal`}>{show(f.left)}</td>
            <td className={`${td} max-w-96 break-words whitespace-normal`}>{show(f.right)}</td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

function SourceFacts({ facts }: { facts: Record<string, unknown> }) {
  const window = facts.query_window as { start: string; end: string; timezone: string } | null | undefined;
  const joins = (facts.joins as { left: string; right: string; cardinality: string }[] | undefined) ?? [];
  const items: [string, string][] = [
    ["Source", show(facts.source_type)],
    ["Dataset", show(facts.label)],
    ["Rows", formatCount(facts.rows as number)],
  ];
  if (facts.source_type === "postgres_query") {
    items.push(
      ["Registered query", show(facts.query_key)],
      ["Parameters", JSON.stringify(facts.query_params ?? {})],
      ["Grain", show(facts.grain)],
      ["Joins", joins.length ? joins.map((j) => `${j.left} → ${j.right} (${j.cardinality})`).join("; ") : "none"],
      ["Query window", window ? `[${window.start}, ${window.end}) · ${window.timezone}` : "none"],
      ["Snapshot taken", formatDateTime(facts.executed_at as string)],
      ["SQL sha256", shortHash(facts.sql_sha256 as string, 16)],
    );
  }
  return <KeyValue items={items} />;
}

export default function Summary({ inv }: { inv: Investigation }) {
  const datasets = useApi(() => Promise.all(inv.inputs.map((i) => api.dataset(i.dataset_id))), [inv.id]);
  const dna = inv.config_json.metric_dna;
  const replay = inv.config_json.replay ?? [];
  const c = inv.comparison;
  const byId = new Map<string, Dataset>((datasets.data ?? []).map((d) => [d.id, d]));

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {(["left", "right"] as const).map((side) => {
          const input = inv.inputs.find((i) => i.side === side)!;
          const metric = side === "left" ? inv.left_metric : inv.right_metric;
          const ds = byId.get(input.dataset_id);
          return (
            <Card key={side} title={`${side === "left" ? "Left" : "Right"} input`}>
              <KeyValue
                items={[
                  ["Metric definition", `${metric.name} v${metric.version}`],
                  ["Dataset", ds?.label ?? input.dataset_id],
                  ["Rows in dataset", formatCount(input.row_count)],
                  ["Rejected at import", ds ? formatCount(ds.rejected_row_count) : "—"],
                  ["Dataset fingerprint", <code key="f" className="text-xs">{shortHash(input.dataset_fingerprint, 16)}</code>],
                  ["Excluded by definition", Object.entries(inv.config_json.excluded?.[side] ?? {}).map(([k, v]) => `${humanize(k)}: ${v}`).join(", ") || "none"],
                ]}
              />
              {dna?.sources && (
                <div className="mt-3 border-t border-slate-100 pt-3">
                  <SourceFacts facts={dna.sources[side]} />
                </div>
              )}
            </Card>
          );
        })}
      </div>
      {datasets.error && <ErrorMessage error={datasets.error} />}
      {datasets.loading && !datasets.data && <Loading label="Loading dataset details" />}

      {c && (
        <Card title="Record matching">
          <Table label="Record matching statistics">
            <thead>
              <tr>
                <th className={th}>Matched keys</th>
                <th className={th}>Left only</th>
                <th className={th}>Right only</th>
                <th className={th}>Mismatched</th>
                <th className={th}>Repeated keys (L / R)</th>
                <th className={th}>Rows without key (L / R)</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td className={tdNum}>{formatCount(c.matched_keys)}</td>
                <td className={tdNum}>{formatCount(c.left_only_keys)}</td>
                <td className={tdNum}>{formatCount(c.right_only_keys)}</td>
                <td className={tdNum}>{formatCount(c.mismatched_keys)}</td>
                <td className={tdNum}>
                  {formatCount(c.left_duplicate_keys)} / {formatCount(c.right_duplicate_keys)}
                </td>
                <td className={tdNum}>
                  {formatCount(c.left_null_keys)} / {formatCount(c.right_null_keys)}
                </td>
              </tr>
            </tbody>
          </Table>
          <p className="mt-2 text-xs text-slate-500">Counted after definition replay and removal of confirmed join copies, on business_key.</p>
        </Card>
      )}

      {dna && (
        <Card title="Metric DNA">
          <div className="mb-3">
            <DnaDifferences dna={dna} />
          </div>
          <DnaTable dna={dna} leftLabel={`Left: ${inv.left_metric.name} v${inv.left_metric.version}`} rightLabel={`Right: ${inv.right_metric.name} v${inv.right_metric.version}`} />
        </Card>
      )}

      <Card title="Definition replay">
        {replay.length === 0 ? (
          <p className="text-sm text-slate-600">Both definitions agree on every replayable rule, so no definition differences were attributed.</p>
        ) : (
          <Table label="Definition replay steps">
            <thead>
              <tr>
                <th className={th}>Step</th>
                <th className={th}>Result</th>
                <th className={th}>Left before</th>
                <th className={th}>Left after</th>
                <th className={th}>Contribution</th>
                <th className={th}>Alone</th>
              </tr>
            </thead>
            <tbody>
              {replay.map((s) => (
                <tr key={s.step}>
                  <td className={td}>{humanize(s.step)}</td>
                  <td className={td}>{s.detail ? `${humanize(s.status)}: ${s.detail}` : humanize(s.status)}</td>
                  <td className={tdNum}>{formatAmount(s.metric_before)}</td>
                  <td className={tdNum}>{formatAmount(s.metric_after)}</td>
                  <td className={tdNum}>{formatAmount(s.contribution, { signed: true })}</td>
                  <td className={tdNum}>{formatAmount(s.standalone, { signed: true })}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}
