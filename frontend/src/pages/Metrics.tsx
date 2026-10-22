import { useState } from "react";

import { api } from "../api/client";
import type { MetricDefinition } from "../api/types";
import { Card, EmptyState, ErrorMessage, Field, inputClass, Loading, PageHeader, Table, td, th } from "../components/ui";
import { DnaDifferences, DnaTable } from "../features/investigation/Summary";
import MetricForm from "../features/metrics/MetricForm";
import { formatDateTime } from "../lib/format";
import { useApi } from "../lib/useApi";

function describeFilters(m: MetricDefinition) {
  return m.filters.length ? m.filters.map((f) => `${f.field} ${f.op}${f.value !== undefined && f.value !== null ? ` ${Array.isArray(f.value) ? f.value.join(",") : f.value}` : ""}`).join("; ") : "none";
}

function windowText(m: MetricDefinition) {
  if (!m.window_start && !m.window_end) return "none";
  return `${m.window_start?.slice(0, 10) ?? "…"} to ${m.window_end?.slice(0, 10) ?? "…"}${m.window_end_inclusive ? " (inclusive)" : ""} ${m.timezone}`;
}

export default function Metrics() {
  const metrics = useApi(() => api.metrics(), []);
  const [left, setLeft] = useState("");
  const [right, setRight] = useState("");
  const dna = useApi(async () => (left && right ? api.compareMetrics(left, right) : undefined), [left, right]);
  const byId = new Map((metrics.data ?? []).map((m) => [m.id, m]));

  return (
    <div className="space-y-4">
      <PageHeader title="Metric definitions" subtitle="Metric DNA: what a number counts. Definitions are versioned and never edited in place." />
      <Card title="Definitions">
        {metrics.error && <ErrorMessage error={metrics.error} />}
        {!metrics.data && !metrics.error && <Loading />}
        {metrics.data?.length === 0 && <EmptyState title="No definitions yet">Create one below or load the demo from the Overview.</EmptyState>}
        {metrics.data && metrics.data.length > 0 && (
          <Table label="Metric definitions">
            <thead>
              <tr>
                <th className={th}>Name</th>
                <th className={th}>Version</th>
                <th className={th}>Aggregation</th>
                <th className={th}>Refunds</th>
                <th className={th}>Grain</th>
                <th className={th}>Window</th>
                <th className={th}>Filters</th>
                <th className={th}>Created</th>
              </tr>
            </thead>
            <tbody>
              {metrics.data.map((m) => (
                <tr key={m.id}>
                  <td className={td}>
                    <span className="font-medium">{m.name}</span>
                    {m.description && <span className="block text-xs text-slate-500">{m.description}</span>}
                  </td>
                  <td className={td}>v{m.version}</td>
                  <td className={td}>{m.aggregation}</td>
                  <td className={td}>{m.refund_policy.replace(/_/g, " ")}</td>
                  <td className={td}>{m.grain.replace("_", " ")}</td>
                  <td className={td}>{windowText(m)}</td>
                  <td className={`${td} max-w-xs whitespace-normal`}>{describeFilters(m)}</td>
                  <td className={`${td} whitespace-nowrap`}>{formatDateTime(m.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
      <Card title="Compare two definitions">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {[
            ["Left", left, setLeft],
            ["Right", right, setRight],
          ].map(([label, value, set]) => (
            <Field key={label as string} label={label as string} htmlFor={`cmp-${label}`}>
              <select id={`cmp-${label}`} className={inputClass} value={value as string} onChange={(e) => (set as (v: string) => void)(e.target.value)}>
                <option value="">— choose —</option>
                {metrics.data?.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name} v{m.version}
                  </option>
                ))}
              </select>
            </Field>
          ))}
        </div>
        {dna.error && <ErrorMessage error={dna.error} />}
        {dna.data && (
          <div className="mt-4 space-y-3">
            <DnaDifferences dna={dna.data} />
            <DnaTable dna={dna.data} leftLabel={`${byId.get(left)?.name} v${byId.get(left)?.version}`} rightLabel={`${byId.get(right)?.name} v${byId.get(right)?.version}`} />
          </div>
        )}
      </Card>
      <Card title="New definition">
        <MetricForm onCreated={() => metrics.reload()} />
      </Card>
    </div>
  );
}
