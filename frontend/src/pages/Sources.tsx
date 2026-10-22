import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import type { CatalogTable, ConnectionTest, Dataset, Freshness } from "../api/types";
import { Button, Card, DemoBadge, EmptyState, ErrorMessage, Loading, Notice, PageHeader, Table, Tabs, td, tdNum, th } from "../components/ui";
import UploadAndMap from "../features/files/UploadAndMap";
import DatasetProfile from "../features/sources/DatasetProfile";
import QuerySnapshot from "../features/sources/QuerySnapshot";
import { formatCount, formatDateTime, shortHash } from "../lib/format";
import { useAction, useApi } from "../lib/useApi";

const FRESHNESS: Record<Freshness["status"], string> = {
  current: "✓ Current: the query returns the same data",
  source_changed: "! Source changed since this snapshot; take a new snapshot to compare current data",
  query_changed: "! The registered SQL changed since this snapshot",
  query_removed: "✕ The registered query no longer exists",
};

function Issues({ dataset }: { dataset: Dataset }) {
  const issues = useApi(() => api.datasetIssues(dataset.id), [dataset.id]);
  if (issues.error) return <ErrorMessage error={issues.error} />;
  if (!issues.data) return <Loading />;
  if (!issues.data.total) return <p className="text-sm text-slate-500">No rejected values.</p>;
  return (
    <>
      <Table label={`Rejected values in ${dataset.label}`}>
        <thead>
          <tr>
            <th className={th}>Row</th>
            <th className={th}>Column</th>
            <th className={th}>Raw value</th>
            <th className={th}>Reason</th>
          </tr>
        </thead>
        <tbody>
          {issues.data.items.map((i, n) => (
            <tr key={n}>
              <td className={tdNum}>{i.row_number}</td>
              <td className={td}>{i.column_name ?? "—"}</td>
              <td className={td}>
                <code>{i.raw_value}</code>
              </td>
              <td className={td}>{i.message}</td>
            </tr>
          ))}
        </tbody>
      </Table>
      <p className="mt-1 text-xs text-slate-500">
        Showing {issues.data.items.length} of {formatCount(issues.data.total)} issues.
      </p>
    </>
  );
}

function DatasetDetails({ d, onClose }: { d: Dataset; onClose: () => void }) {
  const [fresh, setFresh] = useState<Freshness>();
  const check = useAction(async () => setFresh(await api.freshness(d.id)));
  const isQuery = d.source_type === "postgres_query";
  return (
    <section aria-label={`Details of ${d.label}`} className="mt-3 space-y-3 rounded-md border border-slate-200 p-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold">{d.label}</h3>
        <div className="flex gap-2">
          {isQuery && (
            <Button pending={check.pending} onClick={() => check.run()}>
              Check freshness
            </Button>
          )}
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
      {check.error && <ErrorMessage error={check.error} title="Freshness check failed" />}
      {fresh && (
        <Notice tone={fresh.status === "current" ? "success" : "warning"}>
          {FRESHNESS[fresh.status]} (checked {formatDateTime(fresh.checked_at)}; snapshot {formatCount(fresh.snapshot_rows)} rows, now {formatCount(fresh.current_rows)}).
        </Notice>
      )}
      <DatasetProfile dataset={d} />
      <p>
        Mapping: <code className="text-xs break-all">{JSON.stringify(d.column_mapping)}</code>
      </p>
      <p className="font-medium">Rejected values</p>
      <Issues dataset={d} />
    </section>
  );
}

function Datasets({ datasets }: { datasets: Dataset[] }) {
  const [openId, setOpenId] = useState<string>();
  if (!datasets.length) return <EmptyState title="No datasets yet">Upload a file or snapshot a registered query.</EmptyState>;
  const open = datasets.find((d) => d.id === openId);
  return (
    <>
      <Table label="Datasets">
        <thead>
          <tr>
            <th className={th}>Dataset</th>
            <th className={th}>Source</th>
            <th className={th}>Rows</th>
            <th className={th}>Rejected</th>
            <th className={th}>Imported / snapshot</th>
            <th className={th}></th>
          </tr>
        </thead>
        <tbody>
          {datasets.map((d) => {
            const isQuery = d.source_type === "postgres_query";
            return (
              <tr key={d.id} className={openId === d.id ? "bg-blue-50" : ""}>
                <td className={`${td} max-w-sm whitespace-normal`}>
                  <span className="font-medium">{d.label}</span> {d.is_demo && <DemoBadge />}
                  <span className="block text-xs text-slate-500">fingerprint {shortHash(d.fingerprint)}</span>
                </td>
                <td className={td}>{isQuery ? "Query snapshot" : d.source_type.toUpperCase()}</td>
                <td className={tdNum}>{formatCount(d.row_count)}</td>
                <td className={tdNum}>{formatCount(d.rejected_row_count)}</td>
                <td className={`${td} whitespace-nowrap`}>{formatDateTime(isQuery ? (d.source_schema.executed_at as string) : d.created_at)}</td>
                <td className={td}>
                  <Button variant="ghost" aria-expanded={openId === d.id} onClick={() => setOpenId(openId === d.id ? undefined : d.id)}>
                    {openId === d.id ? "Hide profile" : "Profile"}
                  </Button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </Table>
      {open && <DatasetDetails d={open} onClose={() => setOpenId(undefined)} />}
    </>
  );
}

function Connections() {
  const connections = useApi(() => api.connections(), []);
  const [tests, setTests] = useState<Record<string, ConnectionTest | string>>({});
  const [catalogs, setCatalogs] = useState<Record<string, CatalogTable[] | string>>({});
  if (connections.error) return <ErrorMessage error={connections.error} />;
  if (!connections.data) return <Loading />;
  if (!connections.data.length) return <EmptyState title="No source connections configured">Add a TRUTHSPLIT_SOURCE_&lt;NAME&gt;_URL environment variable; see docs/security.md.</EmptyState>;
  return (
    <div className="space-y-3">
      {connections.data.map((c) => {
        const t = tests[c.name];
        const cat = catalogs[c.name];
        return (
          <div key={c.name} className="rounded border border-slate-200 p-3 text-sm">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p>
                <span className="font-medium">{c.name}</span> · {c.host}:{c.port}/{c.database} {c.host_allowed ? "" : <span className="text-red-800">· host not in the allowlist</span>}
              </p>
              <div className="flex gap-2">
                <Button onClick={() => api.testConnection(c.name).then((r) => setTests((s) => ({ ...s, [c.name]: r })), (e: Error) => setTests((s) => ({ ...s, [c.name]: e.message })))}>Test connection</Button>
                <Button onClick={() => api.catalog(c.name).then((r) => setCatalogs((s) => ({ ...s, [c.name]: r })), (e: Error) => setCatalogs((s) => ({ ...s, [c.name]: e.message })))}>Show tables</Button>
              </div>
            </div>
            {typeof t === "string" && <div className="mt-2"><ErrorMessage error={t} title="Connection failed" /></div>}
            {t && typeof t !== "string" && (
              <div className="mt-2">
                <Notice tone={t.ok ? "success" : "warning"}>
                  {t.ok ? `✓ Connected as ${t.user} (PostgreSQL ${t.server_version}); the role cannot write, create or escalate.` : `! Connected as ${t.user}, but TruthSplit will not query it: ${t.problems.join("; ")}.`}
                  {t.ok && !t.role_read_only_default ? " Note: the role does not default to read-only sessions; TruthSplit forces read-only transactions anyway." : ""}
                </Notice>
              </div>
            )}
            {typeof cat === "string" && <div className="mt-2"><ErrorMessage error={cat} title="Catalog failed" /></div>}
            {Array.isArray(cat) && (
              <ul className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
                {cat.map((tbl) => (
                  <li key={`${tbl.schema}.${tbl.table}`} className="rounded bg-slate-50 p-2 text-xs">
                    <span className="font-medium">
                      {tbl.schema}.{tbl.table}
                    </span>
                    <span className="block text-slate-600">{tbl.columns.map((col) => `${col.name} (${col.type})`).join(", ")}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default function Sources() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "databases" ? "databases" : "files";
  const datasets = useApi(() => api.datasets(), []);
  const queries = useApi(() => api.queries(), []);
  const all = datasets.data ?? [];
  return (
    <div>
      <PageHeader title="Sources" subtitle="Files and read-only database queries become immutable datasets that investigations compare." />
      <Tabs
        label="Source type"
        active={tab}
        onChange={(t) => setParams({ tab: t }, { replace: true })}
        tabs={[
          { id: "files", label: "Files" },
          { id: "databases", label: "Databases" },
        ]}
      />
      {tab === "files" ? (
        <div className="space-y-4">
          <Card title="Upload and map a file">
            <UploadAndMap onImported={datasets.reload} />
          </Card>
          <Card title="File datasets">
            {datasets.error && <ErrorMessage error={datasets.error} />}
            {datasets.data ? <Datasets datasets={all.filter((d) => d.source_type !== "postgres_query")} /> : !datasets.error && <Loading />}
          </Card>
        </div>
      ) : (
        <div className="space-y-4">
          <Card title="Connections">
            <Connections />
          </Card>
          <Card title="Snapshot a registered query">
            {queries.error && <ErrorMessage error={queries.error} />}
            {queries.data ? <QuerySnapshot queries={queries.data} onSnapshot={datasets.reload} /> : !queries.error && <Loading />}
          </Card>
          <Card title="Query snapshots">
            {datasets.error && <ErrorMessage error={datasets.error} />}
            {datasets.data ? <Datasets datasets={all.filter((d) => d.source_type === "postgres_query")} /> : !datasets.error && <Loading />}
          </Card>
        </div>
      )}
    </div>
  );
}
