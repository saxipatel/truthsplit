import { useState } from "react";

import { api } from "../../api/client";
import type { Dataset, RegisteredQuery } from "../../api/types";
import { Button, ErrorMessage, Field, inputClass, Notice, Table, td, th } from "../../components/ui";
import { formatCount } from "../../lib/format";
import { useAction } from "../../lib/useApi";

export function QueryDetails({ query }: { query: RegisteredQuery }) {
  return (
    <details className="text-sm">
      <summary className="cursor-pointer text-slate-600">SQL, joins and grain</summary>
      <div className="mt-2 space-y-2">
        <pre className="max-w-full overflow-x-auto rounded bg-slate-50 p-2 text-xs">{query.sql}</pre>
        <p className="text-xs text-slate-500">
          SQL sha256 <code>{query.sql_sha256.slice(0, 16)}</code> · tables {query.tables.join(", ")} · one row per <strong>{query.grain}</strong>
        </p>
        {query.joins.length > 0 && (
          <ul className="list-inside list-disc text-xs text-slate-600">
            {query.joins.map((j) => (
              <li key={`${j.left}-${j.right}`}>
                {j.left} → {j.right} ({j.cardinality})
              </li>
            ))}
          </ul>
        )}
        {query.notes.map((n) => (
          <p key={n} className="text-xs text-amber-800">
            {n}
          </p>
        ))}
      </div>
    </details>
  );
}

export default function QuerySnapshot({ queries, onSnapshot, idPrefix = "q" }: { queries: RegisteredQuery[]; onSnapshot: (d: Dataset) => void; idPrefix?: string }) {
  const [key, setKey] = useState(queries[0]?.key ?? "");
  const [params, setParams] = useState<Record<string, string>>({ start_date: "2026-03-01", end_date: "2026-04-01" });
  const [preview, setPreview] = useState<{ columns: string[]; rows: (string | null)[][]; truncated: boolean }>();
  const [result, setResult] = useState<{ dataset: Dataset; already: boolean }>();
  const query = queries.find((q) => q.key === key);

  const previewAction = useAction(async () => setPreview(await api.previewQuery(key, pick())));
  const snapshotAction = useAction(async () => {
    const resp = await api.snapshot(key, pick());
    setResult({ dataset: resp.dataset, already: resp.already_imported });
    onSnapshot(resp.dataset);
  });

  const pick = () => Object.fromEntries((query?.params ?? []).map((p) => [p.name, params[p.name] ?? ""]));
  const missing = (query?.params ?? []).filter((p) => !params[p.name]);

  if (!queries.length) return <Notice tone="warning">No registered queries are available.</Notice>;
  return (
    <div className="space-y-3">
      <Field label="Registered query" htmlFor={`${idPrefix}-key`}>
        <select
          id={`${idPrefix}-key`}
          className={inputClass}
          value={key}
          onChange={(e) => {
            setKey(e.target.value);
            setPreview(undefined);
            setResult(undefined);
          }}
        >
          {queries.map((q) => (
            <option key={q.key} value={q.key}>
              {q.title}
            </option>
          ))}
        </select>
      </Field>
      {query && (
        <>
          <p className="text-sm text-slate-600">{query.description}</p>
          <QueryDetails query={query} />
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {query.params.map((p) => (
              <Field key={p.name} label={p.description} htmlFor={`${idPrefix}-${p.name}`} error={params[p.name] ? undefined : "Required"}>
                <input
                  id={`${idPrefix}-${p.name}`}
                  type={p.type === "date" ? "date" : p.type === "integer" ? "number" : "text"}
                  className={inputClass}
                  value={params[p.name] ?? ""}
                  required
                  onChange={(e) => setParams((cur) => ({ ...cur, [p.name]: e.target.value }))}
                />
              </Field>
            ))}
          </div>
          <div className="flex flex-wrap gap-2">
            <Button disabled={missing.length > 0} pending={previewAction.pending} onClick={() => previewAction.run()}>
              Preview rows
            </Button>
            <Button variant="primary" disabled={missing.length > 0} pending={snapshotAction.pending} onClick={() => snapshotAction.run()}>
              Run and save snapshot
            </Button>
          </div>
          <p className="text-xs text-slate-500">Runs read-only against the source as its configured login, with a statement timeout and row limit. TruthSplit sends no SQL other than this registered query.</p>
          <ErrorMessage error={previewAction.error} title="Preview failed" />
          <ErrorMessage error={snapshotAction.error} title="Snapshot failed" />
          {result && (
            <Notice tone="success">
              {result.already ? "Same query, parameters and data as an existing snapshot; reusing it. " : "Snapshot saved. "}
              <strong>{formatCount(result.dataset.row_count)}</strong> rows, {formatCount(result.dataset.rejected_row_count)} rejected.
            </Notice>
          )}
          {preview && (
            <>
              <Table label="Query preview">
                <thead>
                  <tr>
                    {preview.columns.map((c) => (
                      <th key={c} className={th}>
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {preview.rows.slice(0, 10).map((row, i) => (
                    <tr key={i}>
                      {row.map((cell, j) => (
                        <td key={j} className={td}>
                          {cell}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </Table>
              <p className="text-xs text-slate-500">First {Math.min(10, preview.rows.length)} rows{preview.truncated ? " (more available)" : ""}.</p>
            </>
          )}
        </>
      )}
    </div>
  );
}
