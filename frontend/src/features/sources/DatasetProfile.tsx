import { api } from "../../api/client";
import type { Dataset } from "../../api/types";
import { ErrorMessage, KeyValue, Loading, Table, td, tdNum, th } from "../../components/ui";
import { formatAmount, formatCount, formatUtc } from "../../lib/format";
import { useApi } from "../../lib/useApi";

function Counts({ label, rows }: { label: string; rows: { value: string | null; rows: number }[] }) {
  return (
    <Table label={label}>
      <thead>
        <tr>
          <th className={th}>{label}</th>
          <th className={th}>Rows</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.value ?? "∅"}>
            <td className={td}>{r.value ?? <span className="text-slate-400">empty</span>}</td>
            <td className={tdNum}>{formatCount(r.rows)}</td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

/** Statistics computed by the backend from the stored normalized records. */
export default function DatasetProfile({ dataset }: { dataset: Dataset }) {
  const p = useApi(() => api.datasetProfile(dataset.id), [dataset.id]);
  if (p.error) return <ErrorMessage error={p.error} title="Profile unavailable" />;
  if (!p.data) return <Loading label="Profiling" />;
  const d = p.data;
  const pct = (n: number) => (d.rows ? ` (${((n / d.rows) * 100).toFixed(1)}%)` : "");
  return (
    <div className="space-y-3" aria-label={`Profile of ${dataset.label}`}>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <KeyValue
          items={[
            ["Rows stored", formatCount(d.rows)],
            ["Rejected at import", formatCount(dataset.rejected_row_count)],
            ["Distinct business keys", formatCount(d.business_keys.distinct)],
            ["Rows without a key", `${formatCount(d.business_keys.rows_without_key)}${pct(d.business_keys.rows_without_key)}`],
            ["Keys on more than one row", formatCount(d.business_keys.repeated_keys)],
            ["Exact duplicate rows", formatCount(d.exact_duplicate_rows)],
          ]}
        />
        <KeyValue
          items={[
            ["Amount sum", formatAmount(d.amount.sum)],
            ["Amount range", d.amount.values ? `${formatAmount(d.amount.min)} to ${formatAmount(d.amount.max)}` : "—"],
            ["Missing / zero / negative amounts", `${formatCount(d.amount.missing)} / ${formatCount(d.amount.zero)} / ${formatCount(d.amount.negative)}`],
            ["Rows with a refund", `${formatCount(d.refund.refunded_rows)} (sum ${formatAmount(d.refund.sum)})`],
            ["Time range", d.occurred_at.values ? `${formatUtc(d.occurred_at.first)} to ${formatUtc(d.occurred_at.last)}` : "no timestamps"],
            ["Missing timestamps", formatCount(d.occurred_at.missing)],
          ]}
        />
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <Counts label="Currency" rows={d.currencies} />
        <Counts label="Status" rows={d.statuses} />
        {d.attributes.length > 0 && (
          <Table label="Attributes">
            <thead>
              <tr>
                <th className={th}>Attribute</th>
                <th className={th}>Distinct</th>
                <th className={th}>Empty</th>
              </tr>
            </thead>
            <tbody>
              {d.attributes.map((a) => (
                <tr key={a.name}>
                  <td className={td}>{a.name}</td>
                  <td className={tdNum}>{formatCount(a.distinct_values)}</td>
                  <td className={tdNum}>{formatCount(a.empty)}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </div>
      {d.rejected_by_column.length > 0 && (
        <p className="text-xs text-slate-600">
          Rejected values by column: {d.rejected_by_column.map((r) => `${r.column ?? "whole row"} ${formatCount(r.rows)}`).join(", ")}.
        </p>
      )}
      <p className="text-xs text-slate-500">Counts are over accepted rows only; currency and status show the 10 most frequent values.</p>
    </div>
  );
}
