import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import { PatternsChart } from "../components/charts/charts";
import { Card, DemoBadge, ExplanationBadge, EmptyState, ErrorMessage, Field, inputClass, Loading, PageHeader, SignedAmount, StatusBadge, Table, td, tdNum, th } from "../components/ui";
import { formatAmount, formatDateTime, humanize } from "../lib/format";
import { useApi } from "../lib/useApi";

const TYPES = ["date_cutoff", "filter_difference", "refund_policy", "join_amplification", "duplicate_records", "missing_records", "extra_records", "amount_mismatch", "definition_difference", "key_quality", "unexplained"];

export default function History() {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [type, setType] = useState("");
  const [demo, setDemo] = useState("");
  const list = useApi(() => api.investigations({ q, status, finding_type: type, is_demo: demo === "" ? null : demo === "yes" }), [q, status, type, demo]);
  const patterns = useApi(() => api.patterns(true), []);

  return (
    <div className="space-y-4">
      <PageHeader title="Investigation history" subtitle="Stored investigations and the discrepancy patterns that recur across them." />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Field label="Search titles" htmlFor="h-q">
          <input id="h-q" className={inputClass} value={q} onChange={(e) => setQ(e.target.value)} />
        </Field>
        <Field label="Status" htmlFor="h-status">
          <select id="h-status" className={inputClass} value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Any</option>
            {["open", "in_review", "resolved", "archived"].map((s) => (
              <option key={s} value={s}>
                {humanize(s)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Has a finding of type" htmlFor="h-type">
          <select id="h-type" className={inputClass} value={type} onChange={(e) => setType(e.target.value)}>
            <option value="">Any</option>
            {TYPES.map((t) => (
              <option key={t} value={t}>
                {humanize(t)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Data" htmlFor="h-demo">
          <select id="h-demo" className={inputClass} value={demo} onChange={(e) => setDemo(e.target.value)}>
            <option value="">All</option>
            <option value="no">Real data only</option>
            <option value="yes">Synthetic demo only</option>
          </select>
        </Field>
      </div>
      <Card title="Investigations">
        {list.error && <ErrorMessage error={list.error} />}
        {!list.data && !list.error && <Loading />}
        {list.data?.length === 0 && <EmptyState title="No investigations match these filters" />}
        {list.data && list.data.length > 0 && (
          <div className={list.loading ? "opacity-60" : ""}>
            <Table label="Investigation history">
              <thead>
                <tr>
                  <th className={th}>Investigation</th>
                  <th className={th}>Evidence</th>
                  <th className={th}>Review</th>
                  <th className={th}>Left</th>
                  <th className={th}>Right</th>
                  <th className={th}>Difference</th>
                  <th className={th}>Unexplained</th>
                  <th className={th}>Created</th>
                </tr>
              </thead>
              <tbody>
                {list.data.map((i) => (
                  <tr key={i.id}>
                    <td className={`${td} max-w-md whitespace-normal`}>
                      <Link to={`/investigations/${i.id}`} className="font-medium text-blue-800 hover:underline">
                        {i.title}
                      </Link>{" "}
                      {i.is_demo && <DemoBadge />}
                    </td>
                    <td className={td}>
                      <ExplanationBadge delta={i.baseline_delta} unexplained={i.unexplained_delta} />
                    </td>
                    <td className={td}>
                      <StatusBadge status={i.status} />
                    </td>
                    <td className={tdNum}>{formatAmount(i.baseline_left)}</td>
                    <td className={tdNum}>{formatAmount(i.baseline_right)}</td>
                    <td className={tdNum}>
                      <SignedAmount value={i.baseline_delta} />
                    </td>
                    <td className={tdNum}>
                      <SignedAmount value={i.unexplained_delta} />
                    </td>
                    <td className={`${td} whitespace-nowrap`}>{formatDateTime(i.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </div>
        )}
      </Card>
      <Card title="Recurring discrepancy patterns">
        {patterns.error && <ErrorMessage error={patterns.error} />}
        {!patterns.data && !patterns.error && <Loading />}
        {patterns.data?.length === 0 && <p className="text-sm text-slate-500">No findings stored yet.</p>}
        {patterns.data && patterns.data.length > 0 && (
          <div className="space-y-4">
            <PatternsChart items={patterns.data.map((p) => ({ label: humanize(p.rule_id), count: p.investigations }))} />
            <Table label="Recurring patterns">
              <thead>
                <tr>
                  <th className={th}>Rule</th>
                  <th className={th}>Finding type</th>
                  <th className={th}>Investigations</th>
                  <th className={th}>Confirmed</th>
                  <th className={th}>Suspected</th>
                  <th className={th}>Unexplained</th>
                  <th className={th}>Σ |attributed amount|</th>
                  <th className={th}>Last seen</th>
                </tr>
              </thead>
              <tbody>
                {patterns.data.map((p) => (
                  <tr key={p.rule_id + p.finding_type}>
                    <td className={td}>
                      <code>{p.rule_id}</code>
                      {p.recurring && <span className="ml-1.5 text-xs text-slate-500">recurring</span>}
                    </td>
                    <td className={td}>{humanize(p.finding_type)}</td>
                    <td className={tdNum}>{p.investigations}</td>
                    <td className={tdNum}>{p.confirmed}</td>
                    <td className={tdNum}>{p.suspected}</td>
                    <td className={tdNum}>{p.unexplained}</td>
                    <td className={tdNum}>{formatAmount(p.total_absolute_amount)}</td>
                    <td className={`${td} whitespace-nowrap`}>{formatDateTime(p.last_seen)}</td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </div>
        )}
      </Card>
    </div>
  );
}
