import { Link, useNavigate } from "react-router-dom";

import { api } from "../api/client";
import type { InvestigationSummary } from "../api/types";
import { Button, Card, DemoBadge, EmptyState, ErrorMessage, ExplanationBadge, explanationOf, Loading, Notice, PageHeader, SignedAmount, StatTile, StatusBadge, Table, td, tdNum, th } from "../components/ui";
import { formatDateTime, humanize } from "../lib/format";
import { useAction, useApi } from "../lib/useApi";

function InvestigationTable({ rows }: { rows: InvestigationSummary[] }) {
  return (
    <Table label="Investigations">
      <thead>
        <tr>
          <th className={th}>Investigation</th>
          <th className={th}>Evidence</th>
          <th className={th}>Review</th>
          <th className={th}>Difference</th>
          <th className={th}>Unexplained</th>
          <th className={th}>Created</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((i) => (
          <tr key={i.id}>
            <td className={`${td} max-w-md whitespace-normal`}>
              <Link to={`/investigations/${i.id}`} className="font-medium text-blue-800 hover:underline">
                {i.title}
              </Link>
              {i.is_demo && (
                <span className="ml-2">
                  <DemoBadge />
                </span>
              )}
            </td>
            <td className={td}>
              <ExplanationBadge delta={i.baseline_delta} unexplained={i.unexplained_delta} />
            </td>
            <td className={td}>
              <StatusBadge status={i.status} />
            </td>
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
  );
}

export default function Overview() {
  const navigate = useNavigate();
  const investigations = useApi(() => api.investigations(), []);
  const readiness = useApi(() => api.readiness(), []);
  const connections = useApi(async () => {
    const list = await api.connections();
    return Promise.all(list.map(async (c) => ({ ...c, test: await api.testConnection(c.name).catch((e: Error) => ({ error: e.message })) })));
  }, []);
  const demo = useAction(api.loadDemo);
  const patterns = useApi(() => api.patterns(true), []);

  const rows = investigations.data ?? [];
  const unresolved = rows.filter((i) => i.status === "open" || i.status === "in_review");
  const explanation = (e: string) => rows.filter((i) => explanationOf(i.baseline_delta, i.unexplained_delta) === e).length;
  const hasDemo = rows.some((i) => i.is_demo);
  const findings = (patterns.data ?? []).reduce((acc, p) => ({ confirmed: acc.confirmed + p.confirmed, suspected: acc.suspected + p.suspected, unexplained: acc.unexplained + p.unexplained }), { confirmed: 0, suspected: 0, unexplained: 0 });
  const recurring = (patterns.data ?? []).filter((p) => p.recurring).slice(0, 6);

  return (
    <div className="space-y-5">
      <PageHeader
        title="Overview"
        subtitle="Why do two reports of the same metric disagree, and how much of the difference can be proven?"
        actions={
          <Button variant="primary" onClick={() => navigate("/investigations/new")}>
            New investigation
          </Button>
        }
      />
      {investigations.error && <ErrorMessage error={investigations.error} title="Could not load investigations" />}
      {!investigations.data && !investigations.error && <Loading />}

      {investigations.data && rows.length > 0 && (
        <section aria-labelledby="kpi-heading" className="space-y-2">
          <h2 id="kpi-heading" className="text-sm font-semibold">
            Reconciliation status
          </h2>
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            <StatTile label="Fully explained" value={explanation("fully_explained")} note="Evidence accounts for the whole difference" />
            <StatTile label="With an unexplained remainder" value={explanation("remainder")} note="Part of the difference has no attributable cause" />
            <StatTile label="Awaiting review" value={unresolved.length} note="Open or in review" />
            <StatTile label="Formally resolved" value={rows.filter((i) => i.status === "resolved").length} note="Closed by a reviewer with a note" />
          </div>
          <p className="text-xs text-slate-500">
            {rows.length} investigation{rows.length === 1 ? "" : "s"}
            {explanation("no_difference") ? ` · ${explanation("no_difference")} with no difference` : ""}. Fully explained is computed from evidence; resolved is a review decision. Amounts are not added across investigations, because they can measure different metrics and currencies.
          </p>
        </section>
      )}

      {patterns.data && patterns.data.length > 0 && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Card title="Findings by evidence status">
            <div className="grid grid-cols-3 gap-2">
              <StatTile label="Confirmed" value={findings.confirmed} />
              <StatTile label="Suspected" value={findings.suspected} />
              <StatTile label="Unexplained" value={findings.unexplained} />
            </div>
            <p className="mt-2 text-xs text-slate-500">Counted across all stored investigations. Suspected findings carry no attributed amount.</p>
          </Card>
          <Card title="Recurring discrepancies" actions={<Link to="/history" className="text-sm text-blue-800 hover:underline">All patterns</Link>}>
            {recurring.length === 0 ? (
              <p className="text-sm text-slate-500">No rule has produced findings in more than one investigation yet.</p>
            ) : (
              <Table label="Recurring discrepancies">
                <thead>
                  <tr>
                    <th className={th}>Rule</th>
                    <th className={th}>Investigations</th>
                    <th className={th}>Confirmed</th>
                    <th className={th}>Suspected</th>
                  </tr>
                </thead>
                <tbody>
                  {recurring.map((p) => (
                    <tr key={p.rule_id + p.finding_type}>
                      <td className={td}>{humanize(p.rule_id)}</td>
                      <td className={tdNum}>{p.investigations}</td>
                      <td className={tdNum}>{p.confirmed}</td>
                      <td className={tdNum}>{p.suspected}</td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
          </Card>
        </div>
      )}

      {investigations.data && rows.length === 0 && (
        <EmptyState title="No investigations yet">
          <p>Start one from uploaded files or database queries, or load the synthetic demo scenarios.</p>
        </EmptyState>
      )}

      {investigations.data && !hasDemo && (
        <Card title="Demo data">
          <p className="mb-3 text-sm text-slate-600">
            Loads three synthetic investigations over the demo source database: one fully explained by refund treatment, join duplication and reporting cutoffs; one with an unexplained remainder; one where a source reports amounts net of refunds.
          </p>
          <Button
            pending={demo.pending}
            onClick={async () => {
              if (await demo.run()) investigations.reload();
            }}
          >
            Load demo investigations
          </Button>
          <div className="mt-2">
            <ErrorMessage error={demo.error} title="Demo not loaded" />
          </div>
        </Card>
      )}

      {unresolved.length > 0 && (
        <Card title="Unresolved investigations" actions={<Link to="/history" className="text-sm text-blue-800 hover:underline">All history</Link>}>
          <InvestigationTable rows={unresolved.slice(0, 10)} />
        </Card>
      )}
      {rows.some((i) => i.status === "resolved" || i.status === "archived") && (
        <Card title="Recently resolved or archived" actions={<Link to="/history" className="text-sm text-blue-800 hover:underline">All history</Link>}>
          <InvestigationTable rows={rows.filter((i) => i.status === "resolved" || i.status === "archived").slice(0, 5)} />
        </Card>
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Card title="Application">
          {readiness.error ? (
            <ErrorMessage error={readiness.error} title="API unavailable" />
          ) : readiness.data ? (
            <p className="text-sm">
              API {readiness.data.status} · metadata database {readiness.data.database} · schema <code>{readiness.data.schema_revision}</code>
            </p>
          ) : (
            <Loading />
          )}
        </Card>
        <Card title="Source connections">
          {connections.error && <ErrorMessage error={connections.error} />}
          {!connections.data && !connections.error && <Loading label="Testing connections" />}
          {connections.data?.length === 0 && <p className="text-sm text-slate-500">No source databases are configured.</p>}
          <ul className="space-y-1 text-sm">
            {connections.data?.map((c) => (
              <li key={c.name}>
                <span className="font-medium">{c.name}</span> ({c.host}/{c.database}):{" "}
                {"error" in c.test ? (
                  <span className="text-red-800">✕ {c.test.error}</span>
                ) : c.test.ok ? (
                  <span className="text-green-800">✓ reachable as {c.test.user}, read-only checks passed</span>
                ) : (
                  <span className="text-amber-900">! reachable but refused: {c.test.problems.join("; ")}</span>
                )}
              </li>
            ))}
          </ul>
        </Card>
      </div>
      {hasDemo && <Notice>Investigations marked “Synthetic demo” use generated data; their expected results are listed in sample_data/manifest.json.</Notice>}
    </div>
  );
}
